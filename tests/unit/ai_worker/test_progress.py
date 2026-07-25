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
