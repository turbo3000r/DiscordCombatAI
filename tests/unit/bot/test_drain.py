from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.services.lifecycle import LifecycleController
from bot.modules.services.task_tracker import TaskTracker
from shared.models import EnvironmentAiTaskEnvelope


class FakeMqtt:
    def __init__(self) -> None:
        self.topics: list[str] = []

    async def publish(
        self, topic: str, payload: bytes, *, qos: int, retain: bool
    ) -> None:
        self.topics.append(topic)
        assert qos == 1
        assert retain is False


class FakeTracker(TaskTracker):
    pass


@pytest.mark.asyncio
async def test_drain_progress_publishes_decreasing_count() -> None:
    mqtt = FakeMqtt()
    tracker = TaskTracker(stall_timeout_sec=120, overall_timeout_sec=900)

    async def on_complete(_r: Any) -> None:
        return None

    async def authorize() -> None:
        return None

    async def revoke() -> None:
        return None

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

    life = LifecycleController(
        node_id="node-local",
        drain_progress_interval_sec=5,
        shutdown_grace_sec=1,
        revoke_gateway=revoke,
        authorize_gateway=authorize,
        mqtt=mqtt,
        tracker=tracker,
        leadership_term_provider=lambda: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    progress = await life.publish_drain_progress_once()
    assert progress is not None
    assert progress.in_flight_workflows == 1
    tracker.records.clear()
    progress2 = await life.publish_drain_progress_once()
    assert progress2 is not None
    assert progress2.in_flight_workflows == 0
    assert mqtt.topics == ["status/bot/drain_progress", "status/bot/drain_progress"]
