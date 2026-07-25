"""Bot heartbeat and status Blob push loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import BotHeartbeat, BotHeartbeatDependencies, BotStatusSection

logger = logging.getLogger(__name__)

HEARTBEAT_TOPIC = MQTT_TOPIC_POLICIES["status_bot_heartbeat"].topic
HEARTBEAT_POLICY = MQTT_TOPIC_POLICIES["status_bot_heartbeat"]


class MqttPublisher(Protocol):
    async def publish(
        self, topic: str, payload: bytes, *, qos: int, retain: bool
    ) -> None: ...


class StatusUpdater(Protocol):
    async def update_status(self, status: dict[str, Any]) -> Any: ...


class HealthSnapshot(Protocol):
    rabbitmq_connected: bool
    cosmos_ok: bool
    azure_queue_ok: bool
    status_blob_ok: bool


class GatewaySnapshot(Protocol):
    @property
    def gateway_connected(self) -> bool: ...

    def guild_count(self) -> int: ...

    def latency_ms(self) -> int | None: ...


class BotHeartbeatService:
    """Publishes QoS 0/non-retained heartbeats and pushes status section only."""

    def __init__(
        self,
        *,
        node_id: str,
        application_version: str,
        heartbeat_interval_sec: float,
        status_push_interval_sec: float,
        mqtt: MqttPublisher | None,
        status_service: StatusUpdater | None,
        health: HealthSnapshot,
        gateway: GatewaySnapshot,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.node_id = node_id
        self.application_version = application_version
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.status_push_interval_sec = status_push_interval_sec
        self._mqtt = mqtt
        self._status_service = status_service
        self._health = health
        self._gateway = gateway
        self._utcnow = utcnow or (lambda: datetime.now(tz=UTC))
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self.last_heartbeat: BotHeartbeat | None = None
        self.last_status_push: BotStatusSection | None = None

    def start(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="bot-heartbeat"
            )
        if self._status_task is None:
            self._status_task = asyncio.create_task(
                self._status_loop(), name="bot-status-push"
            )

    async def stop(self) -> None:
        for task in (self._heartbeat_task, self._status_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._heartbeat_task = None
        self._status_task = None

    def build_heartbeat(self) -> BotHeartbeat:
        return BotHeartbeat(
            node_id=self.node_id,
            application_version=self.application_version,
            observed_at=self._utcnow(),
            gateway_connected=self._gateway.gateway_connected,
            latency_ms=self._gateway.latency_ms(),
            guild_count=self._gateway.guild_count(),
            dependencies=BotHeartbeatDependencies(
                rabbitmq_connected=self._health.rabbitmq_connected,
                cosmos_ok=self._health.cosmos_ok,
                azure_queue_ok=self._health.azure_queue_ok,
                status_blob_ok=self._health.status_blob_ok,
            ),
        )

    async def publish_once(self) -> BotHeartbeat:
        heartbeat = self.build_heartbeat()
        self.last_heartbeat = heartbeat
        if self._mqtt is not None:
            try:
                await self._mqtt.publish(
                    HEARTBEAT_TOPIC,
                    heartbeat.model_dump_json().encode("utf-8"),
                    qos=HEARTBEAT_POLICY.qos,
                    retain=HEARTBEAT_POLICY.retain,
                )
            except Exception:  # noqa: BLE001 — best-effort
                logger.warning("bot heartbeat publish failed")
        return heartbeat

    async def push_status_once(self) -> BotStatusSection | None:
        section = BotStatusSection(
            latency_ms=int(self._gateway.latency_ms() or 0),
            guild_count=self._gateway.guild_count(),
            updated_at=self._utcnow(),
        )
        self.last_status_push = section
        if self._status_service is None:
            return section
        try:
            await self._status_service.update_status(section.model_dump(mode="json"))
            if hasattr(self._health, "status_blob_ok"):
                self._health.status_blob_ok = True
        except Exception:  # noqa: BLE001 — reflect on next heartbeat
            logger.warning("status blob push failed")
            if hasattr(self._health, "status_blob_ok"):
                self._health.status_blob_ok = False
        return section

    async def _heartbeat_loop(self) -> None:
        while True:
            await self.publish_once()
            await asyncio.sleep(self.heartbeat_interval_sec)

    async def _status_loop(self) -> None:
        while True:
            await self.push_status_once()
            await asyncio.sleep(self.status_push_interval_sec)


__all__ = ["BotHeartbeatService", "HEARTBEAT_TOPIC"]
