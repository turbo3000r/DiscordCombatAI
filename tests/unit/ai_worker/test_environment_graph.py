from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from ai_worker.graphs.environment.graph import (
    Environment,
    EnvironmentGraph,
    EnvironmentGraphRuntime,
    environment_invocation_from_envelope,
    run_environment_graph,
)
from ai_worker.graphs.foundation import NodeExecutionError
from ai_worker.llm import GeminiResponse, GeminiUsage, ResourceLedger
from shared.models import EnvironmentAiTaskEnvelope, EnvironmentState, TaskPhase

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "contracts"
PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


class FakeGemini:
    def __init__(self, replies: Iterable[object]) -> None:
        self._replies = iter(replies)
        self.calls: list[dict[str, object]] = []

    def generate_structured(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> GeminiResponse | str:
        self.calls.append(
            {
                "api_key": api_key,
                "model": model,
                "prompt": prompt,
                "max_output_tokens": max_output_tokens,
            }
        )
        reply = next(self._replies)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, (str, GeminiResponse))
        return reply


def _envelope(**overrides: Any) -> EnvironmentAiTaskEnvelope:
    payload = json.loads((FIXTURES / "ai_task_environment.json").read_text(encoding="utf-8"))
    payload.update(overrides)
    return EnvironmentAiTaskEnvelope.model_validate(payload)


def _environment(description: str = "A moonlit forest") -> str:
    return json.dumps(
        {
            "description": description,
            "tags": ["forest", "moonlight"],
            "setting": "realistic",
        }
    )


def _valid() -> str:
    return json.dumps({"is_valid": True, "issues": [], "fix_request": None})


def _invalid() -> str:
    return json.dumps(
        {
            "is_valid": False,
            "issues": ["add a river"],
            "fix_request": {
                "instruction": "Add a river.",
                "origin": "validator",
                "reason": "add a river",
            },
        }
    )


def _graph(fake: FakeGemini, phases: list[TaskPhase]) -> EnvironmentGraph:
    return EnvironmentGraph(
        EnvironmentGraphRuntime(
            prompt_root=PROMPTS,
            llm_max_retries=0,
            client=fake,
            publish_phase=phases.append,
            sleep=lambda _: None,
        )
    )


def test_initial_approved_attempt_zero_and_labelled_prompt_assembly() -> None:
    fake = FakeGemini([_environment(), _valid()])
    phases: list[TaskPhase] = []

    state = _graph(fake, phases).invoke(environment_invocation_from_envelope(_envelope()))

    assert state["attempts_used"] == 1
    assert state["forced_selection"] is False
    assert state["final_environment"].description == "A moonlit forest"
    assert phases == [TaskPhase.composing, TaskPhase.refining]
    assert "## SETTING:" in str(fake.calls[0]["prompt"])
    assert "## LANGUAGE-LOCALE:\nuk-UA" in str(fake.calls[0]["prompt"])


def test_revision_normalises_irrelevant_comment_and_enhances() -> None:
    existing = Environment(description="An old arena", tags=["stone"], setting="realistic")
    envelope = _envelope(
        input_type="revision",
        raw_input=["peach"],
        existing_environment=existing.model_dump(),
    )
    fake = FakeGemini(
        [
            json.dumps({"instruction": "Add a peach orchard.", "origin": "player", "reason": None}),
            _environment("An arena beside a peach orchard"),
            _valid(),
        ]
    )
    phases: list[TaskPhase] = []

    state = _graph(fake, phases).invoke(environment_invocation_from_envelope(envelope))

    assert state["final_environment"].description.endswith("peach orchard")
    assert state["attempts_used"] == 1
    assert "peach" in str(fake.calls[0]["prompt"])


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "input_type": "initial",
            "existing_environment": EnvironmentState(
                description="Existing",
                tags=["existing"],
                setting="realistic",
            ),
        },
        {"input_type": "revision", "existing_environment": None},
        {"setting": "unknown"},
        {"language_locale": "ua"},
        {"max_enhancer_retries": 4},
    ],
)
def test_invalid_invocation_fails_before_client_call(overrides: dict[str, Any]) -> None:
    fake = FakeGemini([])
    envelope = _envelope().model_copy(update=overrides)
    with pytest.raises(NodeExecutionError, match="invalid_input"):
        run_environment_graph(
            envelope,
            llm_max_retries=0,
            client=fake,
            prompt_root=PROMPTS,
        )
    assert fake.calls == []


def test_validator_to_enhancer_loop_and_exact_attempts() -> None:
    fake = FakeGemini([_environment("First"), _invalid(), _environment("Fixed"), _valid()])
    phases: list[TaskPhase] = []
    state = _graph(fake, phases).invoke(environment_invocation_from_envelope(_envelope()))

    assert state["attempts_used"] == 2
    assert state["forced_selection"] is False
    assert state["retry_count"] == 1
    assert phases == [TaskPhase.composing, TaskPhase.refining]


def test_zero_retry_routes_invalid_attempt_to_decider() -> None:
    fake = FakeGemini(
        [
            _environment("Candidate"),
            _invalid(),
            json.dumps({"selected_attempt_index": 0, "reason": "best available"}),
        ]
    )
    phases: list[TaskPhase] = []
    envelope = _envelope(max_enhancer_retries=0)
    state = _graph(fake, phases).invoke(environment_invocation_from_envelope(envelope))

    assert state["attempts_used"] == 1
    assert state["forced_selection"] is True
    assert phases == [TaskPhase.composing, TaskPhase.refining, TaskPhase.finishing]


def test_max_retry_exhaustion_uses_decider_over_all_four_attempts() -> None:
    fake = FakeGemini(
        [
            _environment("Attempt zero"),
            _invalid(),
            _environment("Attempt one"),
            _invalid(),
            _environment("Attempt two"),
            _invalid(),
            _environment("Attempt three"),
            _invalid(),
            json.dumps({"selected_attempt_index": 1, "reason": "closest fit"}),
        ]
    )
    phases: list[TaskPhase] = []
    state = _graph(fake, phases).invoke(environment_invocation_from_envelope(_envelope()))

    assert state["attempts_used"] == 4
    assert state["forced_selection"] is True
    assert state["final_environment"].description == "Attempt one"
    assert [call["max_output_tokens"] for call in fake.calls] == [
        4096,
        2048,
        4096,
        2048,
        4096,
        2048,
        4096,
        2048,
        1024,
    ]


def test_generator_malformed_output_retries_via_shared_wrapper() -> None:
    fake = FakeGemini(["not-json", _environment(), _valid()])
    graph = EnvironmentGraph(
        EnvironmentGraphRuntime(
            prompt_root=PROMPTS,
            llm_max_retries=1,
            client=fake,
            sleep=lambda _: None,
        )
    )

    state = graph.invoke(environment_invocation_from_envelope(_envelope()))

    assert state["attempts_used"] == 1
    assert len(fake.calls) == 3


def test_resource_ledger_deadline_and_usage_exhaustion_are_deterministic() -> None:
    now = [0.0]
    ledger = ResourceLedger(
        deadline_sec=10,
        max_input_tokens=5,
        max_output_tokens=10,
        clock=lambda: now[0],
    )
    ledger.reserve_attempt(node="Generator", max_output_tokens=10)
    with pytest.raises(NodeExecutionError, match="input token"):
        ledger.record_usage(
            node="Generator",
            usage=GeminiUsage(input_tokens=6, output_tokens=1),
        )
    now[0] = 10
    with pytest.raises(NodeExecutionError, match="deadline"):
        ledger.reserve_attempt(node="Enhancer", max_output_tokens=1)


def test_resource_ledger_releases_unused_reservation_and_enforces_output_ceiling() -> None:
    ledger = ResourceLedger(
        deadline_sec=10,
        max_input_tokens=10,
        max_output_tokens=5,
        clock=lambda: 0.0,
    )
    ledger.reserve_attempt(node="Generator", max_output_tokens=5)
    ledger.record_usage(
        node="Generator",
        usage=GeminiUsage(input_tokens=1, output_tokens=2),
    )
    ledger.reserve_attempt(node="Validator", max_output_tokens=3)
    with pytest.raises(NodeExecutionError, match="output token"):
        ledger.reserve_attempt(node="Validator", max_output_tokens=4)


def test_malformed_response_still_accumulates_provider_usage_before_retry() -> None:
    fake = FakeGemini(
        [
            GeminiResponse("not-json", GeminiUsage(input_tokens=4, output_tokens=2)),
            GeminiResponse(_environment(), GeminiUsage(input_tokens=3, output_tokens=2)),
            GeminiResponse(_valid(), GeminiUsage(input_tokens=2, output_tokens=1)),
        ]
    )
    graph = EnvironmentGraph(
        EnvironmentGraphRuntime(
            prompt_root=PROMPTS,
            llm_max_retries=1,
            client=fake,
            sleep=lambda _: None,
        )
    )

    state = graph.invoke(environment_invocation_from_envelope(_envelope()))

    assert state["attempts_used"] == 1
