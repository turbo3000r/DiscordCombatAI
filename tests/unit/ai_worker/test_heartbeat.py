from __future__ import annotations

from datetime import UTC, datetime

from ai_worker.heartbeat import HEARTBEAT_TOPIC, AiWorkerHeartbeatService
from shared.models import AiWorkerHeartbeat, AiWorkerHeartbeatState


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, int, bool]] = []

    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        self.messages.append((topic, payload, qos, retain))


def test_heartbeat_schema_and_topic() -> None:
    transport = FakeTransport()
    service = AiWorkerHeartbeatService(
        node_id="node-local",
        application_version="v0.1.0",
        transport=transport,
        state_provider=lambda: AiWorkerHeartbeatState.running,
        active_tasks_provider=lambda: 1,
        rabbitmq_connected_provider=lambda: True,
        clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    service.publish_once()
    assert len(transport.messages) == 1
    topic, payload, qos, retain = transport.messages[0]
    assert topic == HEARTBEAT_TOPIC
    assert qos == 0
    assert retain is False
    heartbeat = AiWorkerHeartbeat.parse_wire_json(payload.decode("utf-8"))
    assert heartbeat.node_id == "node-local"
    assert heartbeat.state is AiWorkerHeartbeatState.running
    assert heartbeat.active_tasks == 1
    assert heartbeat.dependencies.rabbitmq_connected is True
