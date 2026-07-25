from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.lifecycle import LifecycleController
from bot.modules.services.task_tracker import TaskTracker
from shared.models import EnvironmentAiTaskEnvelope


class FakeBroker:
    def __init__(self) -> None:
        self.purged = 0
        self.revokes: list[str] = []

    async def purge_ai_tasks(self) -> None:
        self.purged += 1

    async def revoke_tasks(self, task_ids: list[str]) -> None:
        self.revokes.extend(task_ids)


@pytest.mark.asyncio
async def test_hard_stop_purge_revoke_and_synthetic() -> None:
    broker = FakeBroker()
    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)
    terminals: list[str] = []

    async def on_complete(result: Any) -> None:
        terminals.append(result.node)

    env = EnvironmentAiTaskEnvelope(
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
    await tracker.create_from_dispatch(envelope=env, completion_callback=on_complete)
    gate: list[str] = []

    async def revoke() -> None:
        gate.append("down")

    async def authorize() -> None:
        gate.append("up")

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
        tracker=tracker,
        broker=broker,
    )
    await life.on_hard_stop()
    assert broker.purged == 1
    assert broker.revokes == [str(env.task_id)]
    assert terminals == ["worker_terminated"]
    assert tracker.in_flight_workflows == 0
    assert gate == ["down"]
    # No revoke on stall/overall alone is covered elsewhere; drain timeout only escalates.
    await life.on_hard_stop()
    assert broker.purged == 1
