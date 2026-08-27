"""Gemini structured-output adapter tests."""

from __future__ import annotations

import logging
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ai_worker.graphs.battle.graph import PredefineOutput, SkeletonOutput
from ai_worker.graphs.foundation import GraphExecutionContext, NodeExecutionError
from ai_worker.llm import StructuredOutputError, gemini_compatible_schema, invoke_structured


def _contains_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def test_gemini_schema_strips_additional_properties_from_forbid_models() -> None:
    class Sample(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        outcome_type: Literal["none", "one", "multiple"]
        episode_count: int = Field(ge=2, le=5)

    raw = Sample.model_json_schema()
    assert raw.get("additionalProperties") is False
    cleaned = gemini_compatible_schema(Sample)
    assert not _contains_key(cleaned, "additionalProperties")
    assert not _contains_key(cleaned, "additional_properties")
    assert cleaned["properties"]["outcome_type"]["enum"] == ["none", "one", "multiple"]
    assert cleaned["properties"]["episode_count"]["minimum"] == 2


def test_battle_predefine_and_nested_skeleton_schemas_are_gemini_safe() -> None:
    for model in (PredefineOutput, SkeletonOutput):
        cleaned = gemini_compatible_schema(model)
        assert not _contains_key(cleaned, "additionalProperties")
        assert not _contains_key(cleaned, "additional_properties")
        assert cleaned["type"] == "object"


def test_structured_failure_logs_model_response(caplog: pytest.LogCaptureFixture) -> None:
    class Payload(BaseModel):
        winner_ids: list[str]

    class FakeClient:
        def generate_structured(self, **kwargs: Any) -> str:
            assert "AIza" not in str(kwargs.get("prompt", ""))
            return '{"winner_ids":["u1","u2"]}'

    def reject(_payload: Payload) -> None:
        raise StructuredOutputError("winner cardinality disagrees with outcome")

    with caplog.at_level(logging.WARNING), pytest.raises(NodeExecutionError):
        invoke_structured(
            context=GraphExecutionContext(
                task_id="task-1",
                graph="battle",
                trace_id="trace-1",
                guild_id="guild-1",
                api_key=SecretStr("secret-key"),
                model="gemini-test",
                executing_node="ResolveWinners",
            ),
            prompt="private user prompt",
            schema=Payload,
            client=FakeClient(),
            max_retries=0,
            sleep=lambda _: None,
            validate=reject,
        )
    text = caplog.text
    assert "ResolveWinners" in text
    assert "winner cardinality disagrees with outcome" in text
    assert '"winner_ids":["u1","u2"]' in text.replace(" ", "")
    assert "secret-key" not in text
