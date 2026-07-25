from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.task_tracker import TaskTracker
from shared.models import (
    AiTaskResultSuccess,
    EnvironmentAiTaskEnvelope,
    TaskPhase,
    TaskProgressMessage,
)


def _envelope(task_id: str | None = None) -> EnvironmentAiTaskEnvelope:
    return EnvironmentAiTaskEnvelope(
        task_id=task_id or str(uuid4()),
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
async def test_create_progress_terminal_and_duplicate() -> None:
    completions: list[str] = []
    phases: list[str] = []

    async def on_complete(result: Any) -> None:
        completions.append(str(result.task_id))

    async def on_phase(task_id: str, phase: TaskPhase) -> None:
        phases.append(f"{task_id}:{phase.value}")

    tracker = TaskTracker(
        stall_timeout_sec=120,
        overall_timeout_sec=900,
        on_phase_change=on_phase,
    )
    env = _envelope()
    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)
    assert tracker.in_flight_workflows == 1
    assert tracker.records[str(env.task_id)].current_phase is TaskPhase.queued

    await tracker.handle_progress(
        TaskProgressMessage(
            task_id=env.task_id,
            graph="environment",
            phase=TaskPhase.launching,
            timestamp=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
        )
    )
    await tracker.handle_progress(
        TaskProgressMessage(
            task_id=env.task_id,
            graph="environment",
            phase=TaskPhase.launching,
            timestamp=datetime(2026, 7, 20, 10, 1, tzinfo=UTC),
        )
    )
    assert phases == [f"{env.task_id}:launching"]

    result = AiTaskResultSuccess(
        task_id=env.task_id,
        graph="environment",
        result={"final_environment": {"description": "d", "tags": [], "setting": "realistic"}},
        completed_at=datetime(2026, 7, 20, 10, 2, tzinfo=UTC),
    )
    await tracker.handle_result(result)
    assert tracker.in_flight_workflows == 0
    assert completions == [str(env.task_id)]
    # Duplicate terminal discarded
    await tracker.handle_result(result)
    assert completions == [str(env.task_id)]


@pytest.mark.asyncio
async def test_unknown_progress_discarded() -> None:
    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    await tracker.handle_progress(
        TaskProgressMessage(
            task_id=str(uuid4()),
            graph="environment",
            phase=TaskPhase.composing,
            timestamp=datetime(2026, 7, 20, tzinfo=UTC),
        )
    )
    assert tracker.in_flight_workflows == 0


@pytest.mark.asyncio
async def test_callback_exception_isolated() -> None:
    async def boom(_r: Any) -> None:
        raise RuntimeError("callback boom")

    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    env = _envelope()
    await tracker.create_from_dispatch(envelope=env, completion_callback=boom)
    await tracker.handle_result(
        AiTaskResultSuccess(
            task_id=env.task_id,
            graph="environment",
            result={"final_environment": {"description": "d", "tags": [], "setting": "realistic"}},
            completed_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
    )
    assert tracker.in_flight_workflows == 0
