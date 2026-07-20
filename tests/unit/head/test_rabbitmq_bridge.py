from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from helpers import FakeClock

from head.logs import RabbitMqLogBridge, RabbitSeverity
from head.rabbitmq_bridge import RabbitMqEventBridge


class FakeEventTransport:
    def __init__(self) -> None:
        self.events: asyncio.Queue[tuple[str, dict[str, object], datetime] | None] = (
            asyncio.Queue()
        )
        self.connected = False
        self.closed = 0
        self.fail_next_connect = False

    async def connect(self) -> None:
        if self.fail_next_connect:
            self.fail_next_connect = False
            raise ConnectionError("broker unavailable")
        self.connected = True

    async def consume_once(self) -> tuple[str, dict[str, object], datetime] | None:
        item = await self.events.get()
        return item

    async def close(self) -> None:
        self.connected = False
        self.closed += 1


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.fail = False

    async def publish_log_wire(self, topic: str, payload: bytes) -> None:
        if self.fail:
            raise ConnectionError("mqtt down")
        self.published.append((topic, payload))


@pytest.mark.asyncio
async def test_event_bridge_republishes_filtered_events_to_mosquitto_topics() -> None:
    transport = FakeEventTransport()
    publisher = FakePublisher()
    clock = FakeClock()
    bridge = RabbitMqEventBridge(
        transport=transport,
        publisher=publisher,
        clock=clock,
        bridge=RabbitMqLogBridge(),
        reconnect_initial_sec=0.01,
        reconnect_max_sec=0.01,
    )

    await bridge.start()
    await transport.events.put(
        (
            "connection.created",
            {"name": "noise"},
            datetime(2026, 7, 19, tzinfo=UTC),
        )
    )
    await transport.events.put(
        (
            "alarm.set",
            {"name": "memory", "password": "secret-value"},
            datetime(2026, 7, 19, tzinfo=UTC),
        )
    )

    for _ in range(50):
        if publisher.published:
            break
        await asyncio.sleep(0.01)

    await bridge.close()

    assert len(publisher.published) == 1
    topic, payload = publisher.published[0]
    assert topic == "logs/errors/rabbitmq"
    wire = payload.decode("utf-8")
    assert "secret-value" not in wire
    assert "routing_key=alarm.set" in wire
    assert RabbitSeverity.error.value in wire


@pytest.mark.asyncio
async def test_event_bridge_survives_mqtt_publish_failure() -> None:
    transport = FakeEventTransport()
    publisher = FakePublisher()
    publisher.fail = True
    clock = FakeClock()
    bridge = RabbitMqEventBridge(
        transport=transport,
        publisher=publisher,
        clock=clock,
        reconnect_initial_sec=0.01,
        reconnect_max_sec=0.01,
    )
    await bridge.start()
    await transport.events.put(
        ("queue.deleted", {"name": "ai_tasks"}, datetime(2026, 7, 19, tzinfo=UTC))
    )
    await asyncio.sleep(0.05)
    await bridge.close()
    assert publisher.published == []
