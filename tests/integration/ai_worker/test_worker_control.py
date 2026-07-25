"""Integration stubs for AI Worker MQTT control (requires local brokers)."""

from __future__ import annotations

import pytest

from ai_worker.control import AiWorkerControlState
from shared.models import AiWorkerDesiredStateValue


class FakeQueue:
    def __init__(self) -> None:
        self.cancelled = False
        self.active = 0

    def cancel_ai_tasks_queue(self) -> None:
        self.cancelled = True

    def restore_ai_tasks_queue(self) -> None:
        self.cancelled = False

    def active_task_count(self) -> int:
        return self.active


class FakePausePublisher:
    def __init__(self) -> None:
        self.count = 0

    def publish_pause_ack(self, ack: object) -> None:
        self.count += 1


@pytest.mark.integration()
def test_pause_ack_is_informational_only() -> None:
    """pause_ack never gates drain; emitting it must not raise or block."""
    control = AiWorkerControlState(
        node_id="node-local",
        queue=FakeQueue(),
        publisher=FakePausePublisher(),
    )
    control.enter_paused()
    assert control.state is AiWorkerDesiredStateValue.paused
