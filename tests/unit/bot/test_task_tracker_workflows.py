"""Workflow-level in_flight_workflows accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.task_tracker import TaskTracker
from shared.models import EnvironmentAiTaskEnvelope


def _envelope() -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=str(uuid4()),
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
        setting="realistic",
        language_locale="en",
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
async def test_workflow_unit_ignores_nested_task_records() -> None:
    async def _noop(_r: Any) -> None:
        return None

    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    tracker.start_workflow("wf-1")
    await tracker.create_from_dispatch(envelope=_envelope(), completion_callback=_noop, command="quick-battle")
    await tracker.create_from_dispatch(envelope=_envelope(), completion_callback=_noop, command="quick-battle")
    assert tracker.in_flight_workflows == 1
    tracker.finish_workflow("wf-1")
    assert tracker.in_flight_workflows == 0
