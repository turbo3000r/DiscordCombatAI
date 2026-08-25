from __future__ import annotations

import time

from ai_worker.progress import HeartbeatProgressPublisher, RecordingProgressPublisher
from shared.models import TaskPhase

TASK_ID = "6b44781e-40f8-4807-9b4b-9087430c14b6"


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        assert qos == 0
        assert retain is False
        self.messages.append((topic, payload))


def test_heartbeat_payload_is_parseable_json() -> None:
    from shared.models import parse_task_progress_message

    transport = FakeTransport()
    publisher = HeartbeatProgressPublisher(transport=transport, heartbeat_sec=60)
    publisher.publish_phase(task_id=TASK_ID, graph="battle", phase=TaskPhase.launching)
    publisher.stop()
    _topic, payload = transport.messages[0]
    parsed = parse_task_progress_message(payload)
    assert parsed.task_id == TASK_ID
    assert parsed.graph == "battle"
    assert parsed.phase is TaskPhase.launching


def test_recording_progress_order() -> None:
    publisher = RecordingProgressPublisher()
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.launching)
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.composing)
    assert publisher.phases == [TaskPhase.launching, TaskPhase.composing]


def test_heartbeat_progress_republishes_same_phase() -> None:
    transport = FakeTransport()
    publisher = HeartbeatProgressPublisher(transport=transport, heartbeat_sec=0.05)
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.refining)
    time.sleep(0.12)
    publisher.stop()
    assert len(transport.messages) >= 2
    assert all(topic == f"progress/ai_worker/{TASK_ID}" for topic, _ in transport.messages)


def test_heartbeat_stop_runs_on_stop_callback() -> None:
    transport = FakeTransport()
    stopped = {"n": 0}

    publisher = HeartbeatProgressPublisher(
        transport=transport,
        heartbeat_sec=60,
        on_stop=lambda: stopped.__setitem__("n", stopped["n"] + 1),
    )
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.launching)
    publisher.stop()
    assert stopped["n"] == 1


class _FailOnceTransport:
    def __init__(self) -> None:
        self.attempts = 0
        self.messages: list[tuple[str, bytes]] = []

    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("first publish failed")
        self.messages.append((topic, payload))


def test_heartbeat_starts_after_first_publish_failure() -> None:
    transport = _FailOnceTransport()
    publisher = HeartbeatProgressPublisher(transport=transport, heartbeat_sec=0.05)
    publisher.publish_phase(task_id=TASK_ID, graph="environment", phase=TaskPhase.launching)
    time.sleep(0.12)
    publisher.stop()
    assert transport.attempts >= 2
    assert len(transport.messages) >= 1
