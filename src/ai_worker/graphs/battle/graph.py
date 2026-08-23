"""Phase 4C battle graph, using the shared LLM and refiner primitives."""

from __future__ import annotations

import json
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

from ai_worker.graphs.environment.graph import LANGUAGE_LOCALES, SETTINGS, Environment
from ai_worker.graphs.foundation import (
    GraphExecutionContext,
    NodeExecutionError,
    append_unvalidated_attempt,
    fill_attempt_verdict,
)
from ai_worker.llm import GeminiClient, ResourceLedger, StructuredOutputError, invoke_structured
from ai_worker.nodes.decider import decide_candidate
from ai_worker.nodes.validation import validate_candidate
from ai_worker.prompts import PromptLoader
from shared.models import BattleAiTaskEnvelope, TaskPhase

BATTLE_TASK_DEADLINE_SEC, BATTLE_MAX_INPUT_TOKENS, BATTLE_MAX_OUTPUT_TOKENS = 840, 350_000, 90_000
NODE_MAX_OUTPUT_TOKENS = {
    "Predefine": 1024,
    "CreateSkeleton": 4096,
    "ImplementFirstEpisode": 4096,
    "ImplementNextEpisode": 4096,
    "ImplementLastEpisode": 4096,
    "Validator": 2048,
    "Modifier": 16384,
    "Decider": 1024,
    "ResolveWinners": 1024,
}


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _safe_text(value: str) -> str:
    result = _normalise(value)
    if not result or any(ord(character) < 32 for character in result):
        raise ValueError("must be non-blank and contain no control characters")
    return result


class Fighter(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    player_id: str = Field(pattern=r"^[0-9]+$")
    player_nick: str = Field(min_length=1, max_length=80)
    fighter_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    strategy: str | None = Field(default=None, max_length=500)

    @field_validator("player_id", "player_nick", "fighter_name", "description", "strategy")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value)


class BattleInvocation(BaseModel):
    """Strict graph-local input; the wire envelope remains independently canonical."""

    model_config = ConfigDict(extra="forbid", strict=True)

    fighters: list[Fighter] = Field(min_length=1, max_length=10)
    environment: Environment
    setting: str
    language_locale: str
    random_winner_mode: bool
    max_modifier_retries: int = Field(ge=0, le=3)
    task_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    guild_id: str = Field(min_length=1)
    api_key: SecretStr
    model: str = Field(min_length=1)

    @field_validator("setting")
    @classmethod
    def validate_setting(cls, value: str) -> str:
        if value not in SETTINGS:
            raise ValueError("setting is not a supported battle setting")
        return value

    @field_validator("language_locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        if value not in LANGUAGE_LOCALES:
            raise ValueError("language_locale must be the Bot-mapped v1 locale")
        return value

    @field_validator("task_id", "trace_id", "guild_id", "model")
    @classmethod
    def validate_metadata(cls, value: str) -> str:
        return _safe_text(value)

    @model_validator(mode="after")
    def validate_fighter_ids(self) -> BattleInvocation:
        if len({fighter.player_id for fighter in self.fighters}) != len(self.fighters):
            raise ValueError("fighters must have unique player_id values")
        if self.environment.setting != self.setting:
            raise ValueError("environment setting must exactly match battle setting")
        return self


class PredefineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome_type: Literal["none", "one", "multiple"]
    episode_count: int = Field(ge=2, le=5)
    predetermined_winners: list[str] | None


class EpisodeSkeleton(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    episode_index: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=500)


class SkeletonOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    episodes: list[EpisodeSkeleton]


class EpisodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    episode_index: int
    text: str = Field(min_length=1, max_length=3500)


class StoryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    story: str = Field(min_length=1, max_length=12000)


class WinnerResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    winner_ids: list[str]


@dataclass(frozen=True)
class BattleGraphRuntime:
    prompt_root: Path
    llm_max_retries: int
    max_modifier_retries: int = 3
    deadline_sec: int = BATTLE_TASK_DEADLINE_SEC
    max_input_tokens: int = BATTLE_MAX_INPUT_TOKENS
    max_output_tokens: int = BATTLE_MAX_OUTPUT_TOKENS
    client: GeminiClient | None = None
    publish_phase: Callable[[TaskPhase], None] | None = None
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], float] | None = None


class BattleGraphState(TypedDict, total=False):
    invocation: BattleInvocation
    plan: PredefineOutput
    skeleton: list[EpisodeSkeleton]
    episode_texts: list[str]
    current_episode_index: int
    current_story: str
    attempts: list[object]
    retry_count: int
    story: str
    forced_selection: bool
    winners: list[str]
    result: dict[str, object]


class BattleGraph:
    def __init__(self, runtime: BattleGraphRuntime) -> None:
        self.r = runtime
        self.prompts = PromptLoader(runtime.prompt_root)
        self.ledger = ResourceLedger(
            deadline_sec=runtime.deadline_sec,
            max_input_tokens=runtime.max_input_tokens,
            max_output_tokens=runtime.max_output_tokens,
            clock=runtime.clock or time.monotonic,
        )
        self.compiled = self._compile()

    def _compile(self) -> Any:
        """Compile the documented planner, storyteller, refiner, and finishing nodes."""
        graph = StateGraph(BattleGraphState)
        graph.add_node("Predefine", self._predefine)
        graph.add_node("CreateSkeleton", self._create_skeleton)
        graph.add_node("ImplementFirstEpisode", self._implement_first_episode)
        graph.add_node("ImplementNextEpisode", self._implement_next_episode)
        graph.add_node("ImplementLastEpisode", self._implement_last_episode)
        graph.add_node("Validator", self._validator)
        graph.add_node("Modifier", self._modifier)
        graph.add_node("Decider", self._decider)
        graph.add_node("ResolveWinners", self._resolve_winners)
        graph.add_edge(START, "Predefine")
        graph.add_edge("Predefine", "CreateSkeleton")
        graph.add_edge("CreateSkeleton", "ImplementFirstEpisode")
        graph.add_conditional_edges(
            "ImplementFirstEpisode",
            self._after_episode,
            {"next": "ImplementNextEpisode", "last": "ImplementLastEpisode"},
        )
        graph.add_conditional_edges(
            "ImplementNextEpisode",
            self._after_episode,
            {"next": "ImplementNextEpisode", "last": "ImplementLastEpisode"},
        )
        graph.add_edge("ImplementLastEpisode", "Validator")
        graph.add_conditional_edges(
            "Validator",
            self._after_validator,
            {"modify": "Modifier", "decide": "Decider", "resolve": "ResolveWinners"},
        )
        graph.add_edge("Modifier", "Validator")
        graph.add_edge("Decider", "ResolveWinners")
        graph.add_edge("ResolveWinners", END)
        return graph.compile()

    def _ctx(self, e: BattleInvocation, node: str) -> GraphExecutionContext:
        return GraphExecutionContext(
            task_id=str(e.task_id),
            graph="battle",
            trace_id=e.trace_id,
            guild_id=e.guild_id,
            api_key=e.api_key,
            model=e.model,
            executing_node=node,
        )

    def _call(
        self,
        e: BattleInvocation,
        node: str,
        prompt: str,
        schema: type[BaseModel],
        validate: Callable[[BaseModel], None] | None = None,
    ) -> BaseModel:
        kwargs = dict(
            context=self._ctx(e, node),
            prompt=prompt,
            schema=schema,
            client=self.r.client,
            max_retries=self.r.llm_max_retries,
            ledger=self.ledger,
            max_output_tokens=NODE_MAX_OUTPUT_TOKENS[node],
        )
        if validate is not None:
            kwargs["validate"] = validate
        if self.r.sleep is not None:
            kwargs["sleep"] = self.r.sleep
        return cast(BaseModel, invoke_structured(**cast(Any, kwargs)))

    def _prompt(self, path: str, e: BattleInvocation, extra: str = "") -> str:
        fighters = "\n".join(
            f"{f.player_id}: {f.player_nick} — {f.fighter_name}: {f.description}"
            for f in e.fighters
        )
        prompt = self.prompts.assemble(
            base_path=path,
            elements=[
                ("elements/environment.txt", {"env": e.environment.description}),
                (f"elements/setting/{e.setting}.txt", None),
                ("elements/language.txt", {"locale": e.language_locale}),
            ],
        )
        return (
            prompt
            + "\n\n"
            + self.prompts.element("elements/fighters.txt")
            + "\n"
            + fighters
            + ("\n\n" + extra if extra else "")
        )

    @staticmethod
    def _validate_outcome(output: PredefineOutput, fighters: list[Fighter], scripted: bool) -> None:
        ids = {f.player_id for f in fighters}
        winners = output.predetermined_winners
        if not scripted and winners is not None:
            raise StructuredOutputError("emergent mode requires null predetermined_winners")
        if scripted and winners is None:
            raise StructuredOutputError("scripted mode requires predetermined_winners")
        if winners is not None and (len(winners) != len(set(winners)) or not set(winners) <= ids):
            raise StructuredOutputError("invalid predetermined winner IDs")
        count = 0 if winners is None else len(winners)
        if output.outcome_type == "multiple" and len(fighters) < 2:
            raise StructuredOutputError("multiple outcome is invalid for a solo fighter")
        if scripted and (
            (output.outcome_type == "none" and count != 0)
            or (output.outcome_type == "one" and count != 1)
            or (output.outcome_type == "multiple" and not 2 <= count <= len(ids))
        ):
            raise StructuredOutputError("winner cardinality disagrees with outcome")

    def invoke(self, invocation: BattleInvocation) -> dict[str, object]:
        if invocation.max_modifier_retries > self.r.max_modifier_retries:
            raise NodeExecutionError(
                "invalid_input",
                "max_modifier_retries exceeds the worker graph configuration",
            )
        self.ledger = ResourceLedger(
            deadline_sec=self.r.deadline_sec,
            max_input_tokens=self.r.max_input_tokens,
            max_output_tokens=self.r.max_output_tokens,
            clock=self.r.clock or time.monotonic,
        )
        result = cast(BattleGraphState, self.compiled.invoke({"invocation": invocation}))
        return result["result"]

    def _predefine(self, state: BattleGraphState) -> dict[str, object]:
        e = state["invocation"]
        self.r.publish_phase and self.r.publish_phase(TaskPhase.composing)
        p = self._call(
            e,
            "Predefine",
            self._prompt("graphs/battle/predefine.txt", e),
            PredefineOutput,
            validate=lambda output: self._validate_outcome(
                cast(PredefineOutput, output), e.fighters, e.random_winner_mode
            ),
        )
        assert isinstance(p, PredefineOutput)
        return {"plan": p}

    def _create_skeleton(self, state: BattleGraphState) -> dict[str, object]:
        e, p = state["invocation"], state["plan"]
        skeleton = self._call(
            e,
            "CreateSkeleton",
            self._prompt("graphs/battle/create_skeleton.txt", e, p.model_dump_json()),
            SkeletonOutput,
            validate=lambda output: self._validate_skeleton(
                cast(SkeletonOutput, output), p.episode_count
            ),
        )
        assert isinstance(skeleton, SkeletonOutput)
        return {"skeleton": skeleton.episodes, "episode_texts": [], "current_episode_index": 0}

    def _implement_episode(
        self, state: BattleGraphState, *, node: str, path: str, index: int, include_prior: bool
    ) -> dict[str, object]:
        e = state["invocation"]
        extra = state["skeleton"][index].model_dump_json()
        if include_prior:
            extra += "\n\n## PRIOR EPISODES:\n" + "\n\n".join(state["episode_texts"])
        output = self._call(
            e,
            node,
            self._prompt(path, e, extra),
            EpisodeOutput,
            validate=self._episode_validator(index),
        )
        assert isinstance(output, EpisodeOutput)
        episodes = [*state["episode_texts"], output.text]
        if len("\n\n".join(episodes)) > 12000:
            raise NodeExecutionError(node, "story exceeds maximum length")
        return {"episode_texts": episodes, "current_episode_index": index + 1}

    def _implement_first_episode(self, state: BattleGraphState) -> dict[str, object]:
        return self._implement_episode(
            state,
            node="ImplementFirstEpisode",
            path="graphs/battle/implement_first_episode.txt",
            index=0,
            include_prior=False,
        )

    def _implement_next_episode(self, state: BattleGraphState) -> dict[str, object]:
        index = state["current_episode_index"]
        return self._implement_episode(
            state,
            node="ImplementNextEpisode",
            path="graphs/battle/implement_next_episode.txt",
            index=index,
            include_prior=True,
        )

    def _implement_last_episode(self, state: BattleGraphState) -> dict[str, object]:
        index = state["current_episode_index"]
        update = self._implement_episode(
            state,
            node="ImplementLastEpisode",
            path="graphs/battle/implement_last_episode.txt",
            index=index,
            include_prior=True,
        )
        story = "\n\n".join(cast(list[str], update["episode_texts"]))
        return {
            **update,
            "current_story": story,
            "attempts": append_unvalidated_attempt([], candidate=story, source="initial"),
            "retry_count": 0,
        }

    @staticmethod
    def _after_episode(state: BattleGraphState) -> str:
        return (
            "next" if state["current_episode_index"] < state["plan"].episode_count - 1 else "last"
        )

    def _validator(self, state: BattleGraphState) -> dict[str, object]:
        e, story = state["invocation"], state["current_story"]
        self.r.publish_phase and self.r.publish_phase(TaskPhase.refining)
        verdict = validate_candidate(
            context=self._ctx(e, "Validator"),
            base_prompt=self.prompts.read("nodes/validator_base.txt"),
            criteria=self.prompts.read("graphs/battle/validator_criteria.txt"),
            candidate=story,
            candidate_serializer=lambda x: x,
            max_retries=self.r.llm_max_retries,
            client=self.r.client,
            sleep=self.r.sleep,
            ledger=self.ledger,
            max_output_tokens=NODE_MAX_OUTPUT_TOKENS["Validator"],
        )
        attempts = fill_attempt_verdict(cast(Any, state["attempts"]), verdict)
        return {"attempts": attempts}

    def _after_validator(self, state: BattleGraphState) -> str:
        latest = cast(Any, state["attempts"])[-1]
        if latest.validator_verdict.is_valid:
            return "resolve"
        return (
            "decide"
            if state["retry_count"] >= state["invocation"].max_modifier_retries
            else "modify"
        )

    def _modifier(self, state: BattleGraphState) -> dict[str, object]:
        e, latest = state["invocation"], cast(Any, state["attempts"])[-1]
        verdict = latest.validator_verdict
        fixed = self._call(
            e,
            "Modifier",
            self._prompt(
                "graphs/battle/modifier.txt",
                e,
                json.dumps(
                    {"story": state["current_story"], "fix": verdict.fix_request.model_dump()}
                ),
            ),
            StoryOutput,
        )
        assert isinstance(fixed, StoryOutput)
        return {
            "current_story": fixed.story,
            "attempts": append_unvalidated_attempt(
                cast(Any, state["attempts"]), candidate=fixed.story, source="fixer"
            ),
            "retry_count": state["retry_count"] + 1,
        }

    def _decider(self, state: BattleGraphState) -> dict[str, object]:
        e = state["invocation"]
        self.r.publish_phase and self.r.publish_phase(TaskPhase.finishing)
        attempts = cast(Any, state["attempts"])
        selection = decide_candidate(
            context=self._ctx(e, "Decider"),
            base_prompt=self.prompts.read("nodes/decider_base.txt"),
            criteria=self.prompts.read("graphs/battle/decider_criteria.txt"),
            attempts=attempts,
            candidate_serializer=lambda a: str(a.candidate),
            max_retries=self.r.llm_max_retries,
            client=self.r.client,
            sleep=self.r.sleep,
            ledger=self.ledger,
            max_output_tokens=NODE_MAX_OUTPUT_TOKENS["Decider"],
        )
        story = str(
            next(
                a for a in attempts if a.attempt_index == selection.selected_attempt_index
            ).candidate
        )
        return {"story": story, "forced_selection": True}

    def _resolve_winners(self, state: BattleGraphState) -> dict[str, object]:
        e, p = state["invocation"], state["plan"]
        story = state.get("story", state["current_story"])
        self.r.publish_phase and self.r.publish_phase(TaskPhase.finishing)
        if e.random_winner_mode:
            winners = p.predetermined_winners or []
        else:
            resolution = self._call(
                e,
                "ResolveWinners",
                self._prompt("graphs/battle/resolve_winners.txt", e, story),
                WinnerResolution,
                validate=lambda output: self._validate_winner_resolution(
                    cast(WinnerResolution, output), p, e.fighters
                ),
            )
            assert isinstance(resolution, WinnerResolution)
            winners = resolution.winner_ids
        return {
            "result": {
                "story": story,
                "winners": winners,
                "attempts_used": len(state["attempts"]),
                "forced_selection": state.get("forced_selection", False),
            }
        }

    @staticmethod
    def _validate_skeleton(output: SkeletonOutput, episode_count: int) -> None:
        if len(output.episodes) != episode_count or [
            episode.episode_index for episode in output.episodes
        ] != list(range(episode_count)):
            raise StructuredOutputError(
                "skeleton indices must be contiguous and match episode count"
            )

    @staticmethod
    def _validate_episode(output: EpisodeOutput, expected_index: int) -> None:
        if output.episode_index != expected_index:
            raise StructuredOutputError("episode index does not match request")

    def _episode_validator(self, expected_index: int) -> Callable[[BaseModel], None]:
        def validate(output: BaseModel) -> None:
            self._validate_episode(cast(EpisodeOutput, output), expected_index)

        return validate

    @staticmethod
    def _validate_winner_resolution(
        resolution: WinnerResolution, plan: PredefineOutput, fighters: list[Fighter]
    ) -> None:
        BattleGraph._validate_outcome(
            PredefineOutput(
                outcome_type=plan.outcome_type,
                episode_count=plan.episode_count,
                predetermined_winners=resolution.winner_ids,
            ),
            fighters,
            True,
        )

def battle_invocation_from_envelope(envelope: BattleAiTaskEnvelope) -> BattleInvocation:
    """Translate the canonical task envelope into strict graph-local input."""
    try:
        return BattleInvocation(
            fighters=[
                Fighter.model_validate(fighter.model_dump(), strict=True)
                for fighter in envelope.fighters
            ],
            environment=Environment.model_validate(envelope.environment.model_dump(), strict=True),
            setting=envelope.setting,
            language_locale=envelope.language_locale,
            random_winner_mode=envelope.random_winner_mode,
            max_modifier_retries=envelope.max_modifier_retries,
            task_id=str(envelope.task_id),
            trace_id=envelope.trace_id,
            guild_id=envelope.guild_id,
            api_key=SecretStr(envelope.api_key),
            model=envelope.model,
        )
    except ValidationError as exc:
        raise NodeExecutionError("invalid_input", "invalid battle graph invocation") from exc


def default_prompt_root() -> Path:
    return Path(__file__).resolve().parents[4] / "prompts"


def run_battle_graph(
    envelope: BattleAiTaskEnvelope,
    *,
    llm_max_retries: int,
    publish_phase: Callable[[TaskPhase], None] | None = None,
    client: GeminiClient | None = None,
    prompt_root: Path | None = None,
    sleep: Callable[[float], None] | None = None,
    max_modifier_retries: int = 3,
    deadline_sec: int = BATTLE_TASK_DEADLINE_SEC,
    max_input_tokens: int = BATTLE_MAX_INPUT_TOKENS,
    max_output_tokens: int = BATTLE_MAX_OUTPUT_TOKENS,
) -> dict[str, object]:
    graph = BattleGraph(
        BattleGraphRuntime(
            prompt_root=prompt_root or default_prompt_root(),
            llm_max_retries=llm_max_retries,
            max_modifier_retries=max_modifier_retries,
            client=client,
            publish_phase=publish_phase,
            sleep=sleep,
            deadline_sec=deadline_sec,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )
    )
    return graph.invoke(battle_invocation_from_envelope(envelope))
