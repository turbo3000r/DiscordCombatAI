from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.task_tracker import TaskTracker
from shared.models import EnvironmentAiTaskEnvelope


def _envelope() -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        setting="realistic",
        language_locale="uk-UA",
        trace_id="t",
        guild_id="123456789012345678",
        api_key="k",
        model="m",
        input_type="initial",
        raw_input=["x"],
        existing_environment=None,
        max_enhancer_retries=1,
    )


@pytest.mark.asyncio
async def test_stall_timeout_synthesizes_without_revoke() -> None:
    seen: list[str] = []

    async def on_complete(result: Any) -> None:
        seen.append(result.node)

    tracker = TaskTracker(stall_timeout_sec=0.05, overall_timeout_sec=30)
    env = _envelope()
    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)
    await asyncio.sleep(0.2)
    assert seen == ["bot_stall_timeout"]
    assert tracker.in_flight_workflows == 0
    assert tracker.revoke_calls == []


@pytest.mark.asyncio
async def test_overall_timeout_synthesizes_without_revoke() -> None:
    seen: list[str] = []

    async def on_complete(result: Any) -> None:
        seen.append(result.node)

    tracker = TaskTracker(stall_timeout_sec=30, overall_timeout_sec=0.05)
    env = _envelope()
    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)
    await asyncio.sleep(0.2)
    assert seen == ["bot_task_timeout"]
    assert tracker.revoke_calls == []
