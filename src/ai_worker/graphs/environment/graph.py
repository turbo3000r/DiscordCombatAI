"""Compiled Phase 4B environment LangGraph."""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ai_worker.graphs.foundation import (
    AttemptRecord,
    GraphExecutionContext,
    ModificationRequest,
    NodeExecutionError,
    append_unvalidated_attempt,
    fill_attempt_verdict,
)
from ai_worker.llm import (
    GeminiClient,
    ResourceLedger,
    StructuredOutputError,
    invoke_structured,
)
from ai_worker.nodes.decider import decide_candidate
from ai_worker.nodes.validation import validate_candidate
from ai_worker.prompts import PromptLoader
from shared.models import EnvironmentAiTaskEnvelope, TaskPhase

SETTINGS = frozenset(
    {
        "realistic",
        "realistic-urban",
        "realistic-nature",
        "dreamcore",
        "unpredictable-realistic",
        "unpredictable-dreamcore",
        "unpredictable-funny",
    }
)
LANGUAGE_LOCALES = frozenset({"en", "es", "uk-UA"})
ENVIRONMENT_TASK_DEADLINE_SEC = 600
ENVIRONMENT_MAX_INPUT_TOKENS = 120_000
ENVIRONMENT_MAX_OUTPUT_TOKENS = 30_000
NODE_MAX_OUTPUT_TOKENS = {
    "Generator": 4096,
    "Normalise": 2048,
    "Enhancer": 4096,
    "Validator": 2048,
    "Decider": 1024,
}


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _no_control_characters(value: str) -> str:
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        raise ValueError("control characters are not allowed")
    return value


class Environment(BaseModel):
    """The exact graph-local Environment candidate schema."""

    model_config = ConfigDict(extra="forbid", strict=True)

    description: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(min_length=1, max_length=12)
    setting: str

    @field_validator("description", "setting")
    @classmethod
    def normalise_text(cls, value: str) -> str:
        result = _no_control_characters(_normalise(value))
        if not result:
            raise ValueError("value must not be blank")
        return result

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        result = [_no_control_characters(_normalise(tag)) for tag in value]
        if any(not tag or len(tag) > 32 for tag in result):
            raise ValueError("tags must be non-blank strings of at most 32 characters")
        if len(set(result)) != len(result):
            raise ValueError("tags must be unique")
        return result


def environment_output_schema(required_setting: str) -> type[Environment]:
    """Environment whose schema admits only the invocation setting slug."""

    schema_extra = {"const": required_setting, "enum": [required_setting]}

    class BoundEnvironment(Environment):
        setting: str = Field(json_schema_extra=schema_extra)

        @field_validator("setting")
        @classmethod
        def match_invocation_setting(cls, value: str) -> str:
            if value != required_setting:
                raise ValueError("candidate setting must exactly match invocation setting")
            return value

    return BoundEnvironment


class EnvironmentInvocation(BaseModel):
    """Validated graph input, independent from the cross-service envelope schema."""

    model_config = ConfigDict(extra="forbid", strict=True)

    input_type: Literal["initial", "revision"]
    raw_input: list[str] = Field(min_length=1, max_length=10)
    setting: str
    language_locale: str
    existing_environment: Environment | None
    max_enhancer_retries: int = Field(ge=0, le=3)
    task_id: str
    trace_id: str
    guild_id: str
    api_key: SecretStr
    model: str

    @field_validator("raw_input")
    @classmethod
    def validate_raw_input(cls, value: list[str], info: Any) -> list[str]:
        maximum = 500 if info.data.get("input_type") == "initial" else 300
        result = [_no_control_characters(_normalise(item)) for item in value]
        if any(not item or len(item) > maximum for item in result):
            raise ValueError(f"raw_input entries must be 1..{maximum} characters")
        return result

    @field_validator("setting")
    @classmethod
    def validate_setting(cls, value: str) -> str:
        if value not in SETTINGS:
            raise ValueError("setting is not a supported environment setting")
        return value

    @field_validator("language_locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        if value not in LANGUAGE_LOCALES:
            raise ValueError("language_locale must be the Bot-mapped v1 locale")
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> EnvironmentInvocation:
        if self.input_type == "initial" and self.existing_environment is not None:
            raise ValueError("initial invocation requires existing_environment=null")
        if self.input_type == "revision" and self.existing_environment is None:
            raise ValueError("revision invocation requires existing_environment")
        return self


class EnvironmentGraphState(TypedDict, total=False):
    invocation: EnvironmentInvocation
    origin_request: ModificationRequest | None
    active_request: ModificationRequest | None
    current_environment: Environment
    attempts: list[AttemptRecord]
    retry_count: int
    final_environment: Environment
    attempts_used: int
    forced_selection: bool


@dataclass(frozen=True)
class EnvironmentGraphRuntime:
    prompt_root: Path
    llm_max_retries: int
    max_enhancer_retries: int = 3
    deadline_sec: int = ENVIRONMENT_TASK_DEADLINE_SEC
    max_input_tokens: int = ENVIRONMENT_MAX_INPUT_TOKENS
    max_output_tokens: int = ENVIRONMENT_MAX_OUTPUT_TOKENS
    client: GeminiClient | None = None
    publish_phase: Callable[[TaskPhase], None] | None = None
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], float] | None = None


class EnvironmentGraph:
    """A compiled LangGraph that owns environment-only composition and refinement."""

    def __init__(self, runtime: EnvironmentGraphRuntime) -> None:
        self._runtime = runtime
        self._prompts = PromptLoader(runtime.prompt_root)
        self._last_phase: TaskPhase | None = None
        self._ledger = ResourceLedger(
            deadline_sec=runtime.deadline_sec,
            max_input_tokens=runtime.max_input_tokens,
            max_output_tokens=runtime.max_output_tokens,
            clock=runtime.clock if runtime.clock is not None else time.monotonic,
        )
        self.compiled = self._compile()

    def invoke(self, invocation: EnvironmentInvocation) -> dict[str, Any]:
        self._last_phase = None
        if invocation.max_enhancer_retries > self._runtime.max_enhancer_retries:
            raise NodeExecutionError(
                "invalid_input",
                "max_enhancer_retries exceeds the worker graph configuration",
            )
        return cast(
            dict[str, Any],
            self.compiled.invoke({"invocation": invocation, "attempts": []}),
        )

    def _compile(self) -> Any:
        graph = StateGraph(EnvironmentGraphState)
        graph.add_node("RouteInput", self._route_input)
        graph.add_node("Generator", self._generator)
        graph.add_node("Normalise", self._normalise)
        graph.add_node("Enhancer", self._enhancer)
        graph.add_node("Validator", self._validator)
        graph.add_node("Decider", self._decider)
        graph.add_edge(START, "RouteInput")
        graph.add_conditional_edges(
            "RouteInput",
            lambda state: state["invocation"].input_type,
            {"initial": "Generator", "revision": "Normalise"},
        )
        graph.add_edge("Generator", "Validator")
        graph.add_edge("Normalise", "Enhancer")
        graph.add_edge("Enhancer", "Validator")
        graph.add_conditional_edges(
            "Validator",
            self._after_validator,
            {"done": END, "enhance": "Enhancer", "decide": "Decider"},
        )
        graph.add_edge("Decider", END)
        return graph.compile()

    def _emit(self, phase: TaskPhase) -> None:
        if phase != self._last_phase and self._runtime.publish_phase is not None:
            self._runtime.publish_phase(phase)
        self._last_phase = phase

    def _context(self, invocation: EnvironmentInvocation, node: str) -> GraphExecutionContext:
        return GraphExecutionContext(
            task_id=invocation.task_id,
            graph="environment",
            trace_id=invocation.trace_id,
            guild_id=invocation.guild_id,
            api_key=invocation.api_key,
            model=invocation.model,
            executing_node=node,
        )

    def _call(
        self,
        *,
        invocation: EnvironmentInvocation,
        node: str,
        prompt: str,
        schema: type[BaseModel],
        validate: Callable[[BaseModel], None] | None = None,
    ) -> BaseModel:
        kwargs: dict[str, Any] = {
            "context": self._context(invocation, node),
            "prompt": prompt,
            "schema": schema,
            "client": self._runtime.client,
            "max_retries": self._runtime.llm_max_retries,
            "ledger": self._ledger,
            "max_output_tokens": NODE_MAX_OUTPUT_TOKENS[node],
        }
        if validate is not None:
            kwargs["validate"] = validate
        if self._runtime.sleep is not None:
            kwargs["sleep"] = self._runtime.sleep
        return invoke_structured(**kwargs)

    def _route_input(self, state: EnvironmentGraphState) -> dict[str, Any]:
        self._emit(TaskPhase.composing)
        return {}

    def _generator(self, state: EnvironmentGraphState) -> dict[str, Any]:
        invocation = state["invocation"]
        prompt = self._prompts.assemble(
            base_path="graphs/environment/generator.txt",
            elements=[
                (f"elements/setting/{invocation.setting}.txt", None),
                ("elements/language.txt", {"locale": invocation.language_locale}),
            ],
        )
        candidate = cast(
            Environment,
            self._call(
                invocation=invocation,
                node="Generator",
                prompt=(
                    f"{prompt}\n\n{self._requested_setting_block(invocation)}\n\n"
                    f"## PLAYER DESCRIPTIONS:\n" + "\n".join(invocation.raw_input)
                ),
                schema=environment_output_schema(invocation.setting),
                validate=lambda output: self._require_candidate_setting(
                    cast(Environment, output), invocation
                ),
            ),
        )
        attempts = append_unvalidated_attempt([], candidate=candidate, source="initial")
        return {"current_environment": candidate, "attempts": attempts, "retry_count": 0}

    def _normalise(self, state: EnvironmentGraphState) -> dict[str, Any]:
        invocation = state["invocation"]
        assert invocation.existing_environment is not None
        prompt = self._prompts.assemble(
            base_path="graphs/environment/normalise.txt",
            elements=[
                ("elements/language.txt", {"locale": invocation.language_locale}),
                ("elements/environment.txt", {"env": invocation.existing_environment.description}),
            ],
        )
        request = cast(
            ModificationRequest,
            self._call(
                invocation=invocation,
                node="Normalise",
                prompt=f"{prompt}\n\n## PLAYER COMMENTS:\n" + "\n".join(invocation.raw_input),
                schema=ModificationRequest,
            ),
        )
        if request.origin != "player" or request.reason is not None:
            raise NodeExecutionError("Normalise", "Normalise must produce a player request")
        return {
            "origin_request": request,
            "active_request": request,
            "current_environment": invocation.existing_environment,
            "attempts": [],
            "retry_count": 0,
        }

    def _enhancer(self, state: EnvironmentGraphState) -> dict[str, Any]:
        invocation = state["invocation"]
        self._emit(TaskPhase.composing if not state["attempts"] else TaskPhase.refining)
        active_request = state.get("active_request")
        if active_request is None:
            raise NodeExecutionError("Enhancer", "Enhancer requires an active modification request")
        current = state["current_environment"]
        origin_request = state.get("origin_request")
        original_instruction = (
            origin_request.instruction if origin_request is not None else active_request.instruction
        )
        prompt = self._prompts.assemble(
            base_path="graphs/environment/enhancer.txt",
            elements=[
                (f"elements/setting/{invocation.setting}.txt", None),
                ("elements/language.txt", {"locale": invocation.language_locale}),
                ("elements/environment.txt", {"env": current.description}),
            ],
        )
        candidate = cast(
            Environment,
            self._call(
                invocation=invocation,
                node="Enhancer",
                prompt=(
                    f"{prompt}\n\n{self._requested_setting_block(invocation)}\n\n"
                    f"## ORIGINAL PLAYER REQUEST:\n{original_instruction}"
                    f"\n\n## ACTIVE MODIFICATION REQUEST:\n{active_request.instruction}"
                ),
                schema=environment_output_schema(invocation.setting),
                validate=lambda output: self._require_candidate_setting(
                    cast(Environment, output), invocation
                ),
            ),
        )
        attempts = append_unvalidated_attempt(
            state["attempts"],
            candidate=candidate,
            source="initial" if not state["attempts"] else "fixer",
        )
        return {"current_environment": candidate, "attempts": attempts}

    def _validator(self, state: EnvironmentGraphState) -> dict[str, Any]:
        invocation = state["invocation"]
        self._emit(TaskPhase.refining)
        candidate = state["current_environment"]
        criteria = self._prompts.assemble(
            base_path="graphs/environment/validator_criteria.txt",
            elements=[
                (f"elements/setting/{invocation.setting}.txt", None),
                ("elements/language.txt", {"locale": invocation.language_locale}),
            ],
        )
        verdict = validate_candidate(
            context=self._context(invocation, "Validator"),
            base_prompt=self._prompts.read("nodes/validator_base.txt"),
            criteria=criteria,
            candidate=candidate,
            candidate_serializer=lambda environment: self._prompts.element(
                "elements/environment.txt", {"env": environment.description}
            ),
            max_retries=self._runtime.llm_max_retries,
            client=self._runtime.client,
            sleep=self._runtime.sleep,
            ledger=self._ledger,
            max_output_tokens=NODE_MAX_OUTPUT_TOKENS["Validator"],
        )
        attempts = fill_attempt_verdict(state["attempts"], verdict)
        if verdict.is_valid:
            return {
                "attempts": attempts,
                "final_environment": candidate,
                "attempts_used": len(attempts),
                "forced_selection": False,
            }
        assert verdict.fix_request is not None
        if state["retry_count"] < invocation.max_enhancer_retries:
            return {
                "attempts": attempts,
                "active_request": verdict.fix_request,
                "retry_count": state["retry_count"] + 1,
            }
        return {"attempts": attempts}

    def _after_validator(self, state: EnvironmentGraphState) -> str:
        if "final_environment" in state:
            return "done"
        # The latest record has just been validated. Its count gives the exact
        # documented bound even before LangGraph merges this node's retry_count update.
        if len(state["attempts"]) >= state["invocation"].max_enhancer_retries + 1:
            return "decide"
        return "enhance"

    def _decider(self, state: EnvironmentGraphState) -> dict[str, Any]:
        invocation = state["invocation"]
        self._emit(TaskPhase.finishing)
        selection = decide_candidate(
            context=self._context(invocation, "Decider"),
            base_prompt=self._prompts.read("nodes/decider_base.txt"),
            criteria=self._prompts.read("graphs/environment/decider_criteria.txt"),
            attempts=state["attempts"],
            candidate_serializer=lambda attempt: self._prompts.element(
                "elements/environment.txt",
                {"env": cast(Environment, attempt.candidate).description},
            ),
            max_retries=self._runtime.llm_max_retries,
            client=self._runtime.client,
            sleep=self._runtime.sleep,
            ledger=self._ledger,
            max_output_tokens=NODE_MAX_OUTPUT_TOKENS["Decider"],
        )
        chosen = next(
            attempt
            for attempt in state["attempts"]
            if attempt.attempt_index == selection.selected_attempt_index
        )
        return {
            "final_environment": cast(Environment, chosen.candidate),
            "attempts_used": len(state["attempts"]),
            "forced_selection": True,
        }

    @staticmethod
    def _requested_setting_block(invocation: EnvironmentInvocation) -> str:
        return f"## REQUESTED SETTING:\n{invocation.setting}"

    @staticmethod
    def _require_candidate_setting(
        candidate: Environment, invocation: EnvironmentInvocation
    ) -> None:
        if candidate.setting != invocation.setting:
            raise StructuredOutputError(
                "candidate setting must exactly match invocation setting"
            )


def environment_invocation_from_envelope(
    envelope: EnvironmentAiTaskEnvelope,
) -> EnvironmentInvocation:
    """Translate the canonical envelope without changing its cross-service shape."""
    try:
        existing = (
            None
            if envelope.existing_environment is None
            else Environment.model_validate(envelope.existing_environment.model_dump(), strict=True)
        )
        return EnvironmentInvocation(
            input_type=envelope.input_type,
            raw_input=envelope.raw_input,
            setting=envelope.setting,
            language_locale=envelope.language_locale,
            existing_environment=existing,
            max_enhancer_retries=envelope.max_enhancer_retries,
            task_id=str(envelope.task_id),
            trace_id=envelope.trace_id,
            guild_id=envelope.guild_id,
            api_key=SecretStr(envelope.api_key),
            model=envelope.model,
        )
    except ValidationError as exc:
        raise NodeExecutionError("invalid_input", "invalid environment graph invocation") from exc


def default_prompt_root() -> Path:
    return Path(__file__).resolve().parents[4] / "prompts"


def run_environment_graph(
    envelope: EnvironmentAiTaskEnvelope,
    *,
    llm_max_retries: int,
    publish_phase: Callable[[TaskPhase], None] | None = None,
    client: GeminiClient | None = None,
    prompt_root: Path | None = None,
    sleep: Callable[[float], None] | None = None,
    max_enhancer_retries: int = 3,
    deadline_sec: int = ENVIRONMENT_TASK_DEADLINE_SEC,
    max_input_tokens: int = ENVIRONMENT_MAX_INPUT_TOKENS,
    max_output_tokens: int = ENVIRONMENT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Validate and run one real environment graph execution."""
    invocation = environment_invocation_from_envelope(envelope)
    graph = EnvironmentGraph(
        EnvironmentGraphRuntime(
            prompt_root=prompt_root or default_prompt_root(),
            llm_max_retries=llm_max_retries,
            max_enhancer_retries=max_enhancer_retries,
            client=client,
            publish_phase=publish_phase,
            sleep=sleep,
            deadline_sec=deadline_sec,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )
    )
    state = graph.invoke(invocation)
    final_environment = cast(Environment, state["final_environment"])
    return {
        "final_environment": final_environment.model_dump(),
        "attempts_used": state["attempts_used"],
        "forced_selection": state["forced_selection"],
    }


__all__ = [
    "Environment",
    "EnvironmentGraph",
    "EnvironmentGraphRuntime",
    "EnvironmentGraphState",
    "EnvironmentInvocation",
    "SETTINGS",
    "default_prompt_root",
    "environment_invocation_from_envelope",
    "environment_output_schema",
    "run_environment_graph",
]
