from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.modules.services.heartbeat import BotHeartbeatService


class FakeHealth:
    rabbitmq_connected = True
    cosmos_ok = True
    azure_queue_ok = False
    status_blob_ok = True


class FakeGateway:
    gateway_connected = True

    def guild_count(self) -> int:
        return 3

    def latency_ms(self) -> int | None:
        return 42


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []

    async def publish(
        self, topic: str, payload: bytes, *, qos: int, retain: bool
    ) -> None:
        self.published.append((topic, payload, qos, retain))


@pytest.mark.asyncio
async def test_heartbeat_schema_and_qos() -> None:
    mqtt = FakeMqtt()
    service = BotHeartbeatService(
        node_id="node-local",
        application_version="v0.1.0",
        heartbeat_interval_sec=30,
        status_push_interval_sec=60,
        mqtt=mqtt,
        status_service=None,
        health=FakeHealth(),
        gateway=FakeGateway(),
        utcnow=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    hb = await service.publish_once()
    assert hb.gateway_connected is True
    assert hb.guild_count == 3
    assert hb.dependencies.azure_queue_ok is False
    assert mqtt.published
    topic, _payload, qos, retain = mqtt.published[0]
    assert topic == "status/bot/heartbeat"
    assert qos == 0
    assert retain is False
