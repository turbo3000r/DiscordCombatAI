from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_worker.graphs.foundation import (
    AttemptRecord,
    GraphExecutionContext,
    ModificationRequest,
    NodeExecutionError,
    ValidatorVerdict,
    append_unvalidated_attempt,
    fill_attempt_verdict,
)
from ai_worker.llm import invoke_structured
from ai_worker.nodes.decider import decide_candidate
from ai_worker.nodes.validation import validate_candidate
from ai_worker.prompts import PromptLoader, PromptLoadError


class FakeGemini:
    def __init__(self, replies: Iterable[object]) -> None:
        self._replies = iter(replies)
        self.calls: list[dict[str, object]] = []

    def generate_structured(
        self, *, api_key: str, model: str, prompt: str, response_schema: type[object]
    ) -> str:
        self.calls.append(
            {
                "api_key": api_key,
                "model": model,
                "prompt": prompt,
                "response_schema": response_schema,
            }
        )
        reply = next(self._replies)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, str)
        return reply


class TransientGeminiError(RuntimeError):
    status_code = 503


def _context(node: str = "Validator") -> GraphExecutionContext:
    return GraphExecutionContext(
        task_id="task-1",
        graph="environment",
        trace_id="trace-1",
        guild_id="guild-1",
        api_key="secret-key",
        model="gemini-test",
        executing_node=node,
    )


def _valid_verdict() -> str:
    return json.dumps({"is_valid": True, "issues": [], "fix_request": None})


def _invalid_verdict() -> str:
    return json.dumps(
        {
            "is_valid": False,
            "issues": ["missing requested detail"],
            "fix_request": {
                "instruction": "Add the requested detail.",
                "origin": "validator",
                "reason": "missing requested detail",
            },
        }
    )


def test_structured_parsing_retries_malformed_then_succeeds() -> None:
    fake = FakeGemini(["not-json", _valid_verdict()])
    delays: list[float] = []

    verdict = invoke_structured(
        context=_context(),
        prompt="private user prompt",
        schema=ValidatorVerdict,
        client=fake,
        max_retries=1,
        sleep=delays.append,
    )

    assert verdict.is_valid is True
    assert len(fake.calls) == 2
    assert delays == [1.0]
    assert fake.calls[0]["api_key"] == "secret-key"
    assert fake.calls[0]["model"] == "gemini-test"


def test_malformed_output_exhaustion_and_zero_retry_boundary() -> None:
    with pytest.raises(NodeExecutionError, match="Validator"):
        invoke_structured(
            context=_context(),
            prompt="prompt",
            schema=ValidatorVerdict,
            client=FakeGemini(["[]"]),
            max_retries=0,
            sleep=lambda _: None,
        )


def test_transient_retries_but_permanent_failure_does_not() -> None:
    transient = FakeGemini([TransientGeminiError("temporary"), _valid_verdict()])
    assert (
        invoke_structured(
            context=_context(),
            prompt="prompt",
            schema=ValidatorVerdict,
            client=transient,
            max_retries=1,
            sleep=lambda _: None,
        ).is_valid
        is True
    )
    permanent = FakeGemini([RuntimeError("bad request"), _valid_verdict()])
    with pytest.raises(NodeExecutionError, match="permanent"):
        invoke_structured(
            context=_context(),
            prompt="prompt",
            schema=ValidatorVerdict,
            client=permanent,
            max_retries=2,
            sleep=lambda _: None,
        )
    assert len(permanent.calls) == 1


def test_validator_invariants_and_fix_request_origin_reason() -> None:
    with pytest.raises(ValidationError):
        ValidatorVerdict.model_validate(
            {"is_valid": True, "issues": ["issue"], "fix_request": None}, strict=True
        )
    with pytest.raises(ValidationError):
        ModificationRequest.model_validate(
            {"instruction": "x", "origin": "validator", "reason": None}, strict=True
        )

    verdict = validate_candidate(
        context=_context(),
        base_prompt="base",
        criteria="criteria",
        candidate={"description": "candidate"},
        candidate_serializer=lambda candidate: f"candidate={candidate['description']}",
        max_retries=0,
        client=FakeGemini([_invalid_verdict()]),
    )
    assert verdict.is_valid is False
    assert verdict.fix_request is not None
    assert verdict.fix_request.origin == "validator"
    assert verdict.fix_request.reason == "missing requested detail"


def test_attempt_lifecycle_and_attempts_used_accounting() -> None:
    attempts = append_unvalidated_attempt([], candidate="first", source="initial")
    assert attempts[0].validator_verdict is None
    invalid_verdict = ValidatorVerdict.model_validate_json(_invalid_verdict())
    filled = fill_attempt_verdict(attempts, invalid_verdict)
    assert filled[0].validator_verdict is not None
    assert len(filled) == 1  # A graph's attempts_used is always len(attempts).
    with pytest.raises(ValueError, match="unvalidated"):
        fill_attempt_verdict(filled, invalid_verdict)


def test_attempt_pool_is_bounded_to_four_candidates() -> None:
    attempts: list[AttemptRecord] = []
    for index in range(4):
        attempts = append_unvalidated_attempt(
            attempts,
            candidate=f"candidate-{index}",
            source="initial" if index == 0 else "fixer",
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        append_unvalidated_attempt(attempts, candidate="candidate-4", source="fixer")


def test_decider_uses_single_bounded_pool_and_validates_selection() -> None:
    invalid = ValidatorVerdict.model_validate_json(_invalid_verdict())
    attempts = [
        AttemptRecord(
            attempt_index=0,
            candidate="a",
            source="initial",
            validator_verdict=invalid,
        ),
        AttemptRecord(
            attempt_index=1,
            candidate="b",
            source="fixer",
            validator_verdict=invalid,
        ),
    ]
    fake = FakeGemini([json.dumps({"selected_attempt_index": 1, "reason": "better"})])

    selection = decide_candidate(
        context=_context("Decider"),
        base_prompt="base",
        criteria="criteria",
        attempts=attempts,
        candidate_serializer=lambda attempt: (
            f"attempt {attempt.attempt_index}: {attempt.candidate}"
        ),
        max_retries=0,
        client=fake,
    )

    assert selection.selected_attempt_index == 1
    assert len(fake.calls) == 1
    assert "attempt 0: a" in str(fake.calls[0]["prompt"])
    assert "attempt 1: b" in str(fake.calls[0]["prompt"])


@pytest.mark.parametrize(
    "selection",
    [
        {"selected_attempt_index": 9, "reason": "out of range"},
        {"selected_attempt_index": 1, "reason": "unknown pool index"},
    ],
)
def test_decider_retries_unknown_or_out_of_range_selection(selection: dict[str, object]) -> None:
    invalid = ValidatorVerdict.model_validate_json(_invalid_verdict())
    attempts = [
        AttemptRecord(
            attempt_index=0,
            candidate="a",
            source="initial",
            validator_verdict=invalid,
        )
    ]
    fake = FakeGemini(
        [
            json.dumps(selection),
            json.dumps({"selected_attempt_index": 0, "reason": "ok"}),
        ]
    )
    result = decide_candidate(
        context=_context("Decider"),
        base_prompt="base",
        criteria="criteria",
        attempts=attempts,
        candidate_serializer=lambda attempt: str(attempt.candidate),
        max_retries=1,
        client=fake,
        sleep=lambda _: None,
    )
    assert result.selected_attempt_index == 0
    assert len(fake.calls) == 2


def test_decider_rejects_unvalidated_or_more_than_four_attempts() -> None:
    attempt = AttemptRecord(attempt_index=0, candidate="a", source="initial")
    with pytest.raises(NodeExecutionError, match="Decider"):
        decide_candidate(
            context=_context("Decider"),
            base_prompt="base",
            criteria="criteria",
            attempts=[attempt],
            candidate_serializer=lambda _: "a",
            max_retries=0,
            client=FakeGemini([]),
        )


def test_prompt_assembly_order_missing_file_and_traversal(tmp_path: Path) -> None:
    (tmp_path / "nodes").mkdir()
    (tmp_path / "elements").mkdir()
    (tmp_path / "nodes" / "base.txt").write_text("BASE", encoding="utf-8")
    (tmp_path / "nodes" / "criteria.txt").write_text("CRITERIA", encoding="utf-8")
    (tmp_path / "elements" / "env.txt").write_text("## Environment:\n{env}", encoding="utf-8")
    loader = PromptLoader(tmp_path)

    assert loader.assemble(
        base_path="nodes/base.txt",
        criteria_path="nodes/criteria.txt",
        elements=[("elements/env.txt", {"env": "Forest"})],
    ) == "BASE\n\nCRITERIA\n\n## Environment:\nForest"
    with pytest.raises(PromptLoadError, match="missing"):
        loader.read("nodes/missing.txt")
    with pytest.raises(PromptLoadError, match="traversal"):
        loader.read("../secret.txt")


def test_secret_and_prompt_content_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    with pytest.raises(NodeExecutionError):
        invoke_structured(
            context=_context(),
            prompt="full user prompt must not be logged",
            schema=ValidatorVerdict,
            client=FakeGemini([RuntimeError("api_key=secret-key")]),
            max_retries=0,
        )
    assert "secret-key" not in caplog.text
    assert "full user prompt" not in caplog.text
    assert "[REDACTED]" in caplog.text
