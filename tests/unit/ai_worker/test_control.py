from __future__ import annotations

from datetime import UTC, datetime

from ai_worker.control import AiWorkerControlState
from shared.models import AiWorkerDesiredStateValue, PauseAck


class FakeQueue:
    def __init__(self) -> None:
        self.cancelled = False
        self.active = 0
        self.cancel_calls = 0
        self.restore_calls = 0

    def cancel_ai_tasks_queue(self) -> None:
        self.cancelled = True
        self.cancel_calls += 1

    def restore_ai_tasks_queue(self) -> None:
        self.cancelled = False
        self.restore_calls += 1

    def active_task_count(self) -> int:
        return self.active


class FakePausePublisher:
    def __init__(self) -> None:
        self.acks: list[PauseAck] = []

    def publish_pause_ack(self, ack: PauseAck) -> None:
        self.acks.append(ack)


def test_pause_finishes_current_then_acks_once() -> None:
    queue = FakeQueue()
    queue.active = 1
    publisher = FakePausePublisher()
    control = AiWorkerControlState(
        node_id="node-local",
        queue=queue,
        publisher=publisher,
        clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )

    control.enter_paused()
    assert queue.cancelled is True
    assert publisher.acks == []

    queue.active = 0
    control.on_task_finished()
    assert len(publisher.acks) == 1
    assert publisher.acks[0].node_id == "node-local"

    control.enter_paused()  # retained duplicate
    assert len(publisher.acks) == 1


def test_resume_restores_queue() -> None:
    queue = FakeQueue()
    publisher = FakePausePublisher()
    control = AiWorkerControlState(
        node_id="node-local",
        queue=queue,
        publisher=publisher,
    )
    control.enter_paused()
    control.enter_running()
    assert control.state is AiWorkerDesiredStateValue.running
    assert queue.restore_calls == 1
    assert queue.cancelled is False


def test_idle_pause_acks_immediately() -> None:
    queue = FakeQueue()
    publisher = FakePausePublisher()
    control = AiWorkerControlState(
        node_id="node-local",
        queue=queue,
        publisher=publisher,
        clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    control.handle_desired_state_payload(
        b'{"schema_version":1,"state":"paused"}'
    )
    assert len(publisher.acks) == 1


def test_malformed_desired_state_ignored() -> None:
    queue = FakeQueue()
    publisher = FakePausePublisher()
    control = AiWorkerControlState(
        node_id="node-local",
        queue=queue,
        publisher=publisher,
    )
    control.handle_desired_state_payload(b"not-json")
    assert queue.cancel_calls == 0
    assert publisher.acks == []
