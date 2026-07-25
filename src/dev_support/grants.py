"""Mosquitto grant publisher for development Bot activation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    ActivationGrant,
    ActivationGrantMode,
    BotDesiredState,
    BotDesiredStateValue,
)

logger = logging.getLogger(__name__)

DESIRED_POLICY = MQTT_TOPIC_POLICIES["control_bot_desired_state"]
GRANT_POLICY = MQTT_TOPIC_POLICIES["control_bot_activation_grant"]


class GrantPublisher:
    """Publishes retained safe inactive, then renews non-retained active grants."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        node_id: str,
        ttl_sec: int = 45,
        renew_sec: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.node_id = node_id
        self.ttl_sec = ttl_sec
        self.renew_sec = renew_sec
        self.instance_id = str(uuid4())
        self.leadership_term = str(uuid4())
        self.command_seq = 0
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._client: Any = None
        self._loop_started = False

    async def start(self) -> None:
        import paho.mqtt.client as mqtt

        self._client = mqtt.Client(  # type: ignore[misc]
            mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=f"dev-support-grants-{self.instance_id[:8]}",
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        await asyncio.to_thread(self._client.connect, self.host, self.port, 60)
        self._client.loop_start()
        self._loop_started = True
        await self._publish_safe_desired()
        self._stop.clear()
        self._task = asyncio.create_task(self._renew_loop(), name="dev-support-grants")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None and self._loop_started:
            await asyncio.to_thread(self._client.disconnect)
            self._client.loop_stop()
            self._loop_started = False

    async def _publish_safe_desired(self) -> None:
        desired = BotDesiredState(
            state=BotDesiredStateValue.inactive,
            head_instance_id=self.instance_id,
            leadership_term=None,
            command_seq=0,
            reason="dev_support_startup",
            issued_at=datetime.now(UTC),
        )
        await self._publish(
            DESIRED_POLICY.topic,
            desired.model_dump_json().encode("utf-8"),
            qos=DESIRED_POLICY.qos,
            retain=DESIRED_POLICY.retain,
        )
        logger.info("published retained inactive desired_state")

    async def _issue_grant(self) -> ActivationGrant:
        self.command_seq += 1
        grant = ActivationGrant(
            grant_id=str(uuid4()),
            node_id=self.node_id,
            head_instance_id=self.instance_id,
            leadership_term=self.leadership_term,
            command_seq=self.command_seq,
            mode=ActivationGrantMode.active,
            ttl_sec=self.ttl_sec,
            issued_at=datetime.now(UTC),
        )
        await self._publish(
            GRANT_POLICY.topic,
            grant.model_dump_json().encode("utf-8"),
            qos=GRANT_POLICY.qos,
            retain=GRANT_POLICY.retain,
        )
        return grant

    async def _renew_loop(self) -> None:
        while not self._stop.is_set():
            try:
                grant = await self._issue_grant()
                logger.debug(
                    "published activation grant seq=%s ttl=%s",
                    grant.command_seq,
                    grant.ttl_sec,
                )
            except Exception:
                logger.exception("failed to publish activation grant")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.renew_sec)
            except TimeoutError:
                continue

    async def _publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        if self._client is None:
            raise RuntimeError("mqtt client not started")
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        await asyncio.to_thread(info.wait_for_publish, 5)
        if not info.is_published():
            raise TimeoutError(f"mqtt publish to {topic} not confirmed")


__all__ = ["GrantPublisher"]
