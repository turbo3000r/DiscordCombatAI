from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import datetime
from typing import Protocol

from shared.models import format_log_line

from .clock import Clock
from .logs import RabbitBrokerEvent, RabbitMqLogBridge, RabbitSeverity


class EventExchangeTransport(Protocol):
    async def connect(self) -> None: ...

    async def consume_once(self) -> tuple[str, Mapping[str, object], datetime] | None: ...

    async def close(self) -> None: ...


class LogWirePublisher(Protocol):
    async def publish_log_wire(self, topic: str, payload: bytes) -> None: ...


ReconnectSleep = Callable[[float], Awaitable[None]]


class RabbitMqEventBridge:
    """Subscribes to amq.rabbitmq.event and republishes onto Mosquitto log topics."""

    def __init__(
        self,
        *,
        transport: EventExchangeTransport,
        publisher: LogWirePublisher,
        clock: Clock,
        bridge: RabbitMqLogBridge | None = None,
        reconnect_initial_sec: float = 1.0,
        reconnect_max_sec: float = 60.0,
        sleep: ReconnectSleep | None = None,
    ) -> None:
        self._transport = transport
        self._publisher = publisher
        self._clock = clock
        self._bridge = bridge or RabbitMqLogBridge()
        self._reconnect_initial_sec = reconnect_initial_sec
        self._reconnect_max_sec = reconnect_max_sec
        self._sleep = sleep or clock.sleep
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self.connected = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._closed = False
        self._task = asyncio.create_task(self._run(), name="head-rabbitmq-event-bridge")

    async def close(self) -> None:
        self._closed = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.connected = False
        await self._transport.close()

    async def _run(self) -> None:
        delay = self._reconnect_initial_sec
        while not self._closed:
            try:
                await self._transport.connect()
                self.connected = True
                delay = self._reconnect_initial_sec
                while not self._closed:
                    event = await self._transport.consume_once()
                    if event is None:
                        continue
                    routing_key, headers, occurred_at = event
                    await self._handle_event(routing_key, headers, occurred_at)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                if self._closed:
                    return
                jitter = 1 + random.uniform(-0.2, 0.2)
                await self._sleep(min(delay, self._reconnect_max_sec) * jitter)
                delay = min(delay * 2, self._reconnect_max_sec)
                with suppress(Exception):
                    await self._transport.close()

    async def _handle_event(
        self,
        routing_key: str,
        headers: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        severity = self._bridge.classify(routing_key)
        if severity is None:
            return
        line = self._bridge.normalize(
            RabbitBrokerEvent(
                routing_key=routing_key,
                severity=severity,
                occurred_at=occurred_at,
                headers=headers,
            )
        )
        if line is None:
            return
        topic = self._bridge.mqtt_topic(RabbitSeverity(line.level))
        with suppress(Exception):
            await self._publisher.publish_log_wire(
                topic, format_log_line(line).encode("utf-8")
            )


__all__ = [
    "EventExchangeTransport",
    "LogWirePublisher",
    "RabbitMqEventBridge",
]
