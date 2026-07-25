"""AI Worker service heartbeat publisher."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    AiWorkerHeartbeat,
    AiWorkerHeartbeatDependencies,
    AiWorkerHeartbeatState,
)

logger = logging.getLogger(__name__)

HEARTBEAT_TOPIC = MQTT_TOPIC_POLICIES["status_ai_worker_heartbeat"].topic


class HeartbeatTransport(Protocol):
    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None: ...


class AiWorkerHeartbeatService:
    def __init__(
        self,
        *,
        node_id: str,
        application_version: str,
        transport: HeartbeatTransport,
        state_provider: Callable[[], AiWorkerHeartbeatState],
        active_tasks_provider: Callable[[], int],
        rabbitmq_connected_provider: Callable[[], bool],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._node_id = node_id
        self._application_version = application_version
        self._transport = transport
        self._state_provider = state_provider
        self._active_tasks_provider = active_tasks_provider
        self._rabbitmq_connected_provider = rabbitmq_connected_provider
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def publish_once(self) -> None:
        heartbeat = AiWorkerHeartbeat(
            node_id=self._node_id,
            application_version=self._application_version,
            observed_at=self._clock(),
            state=self._state_provider(),
            active_tasks=self._active_tasks_provider(),
            dependencies=AiWorkerHeartbeatDependencies(
                rabbitmq_connected=self._rabbitmq_connected_provider()
            ),
        )
        try:
            self._transport.publish(
                HEARTBEAT_TOPIC,
                heartbeat.model_dump_json().encode("utf-8"),
                qos=0,
                retain=False,
            )
        except Exception:
            logger.warning("failed to publish ai_worker heartbeat", exc_info=True)


__all__ = ["AiWorkerHeartbeatService", "HEARTBEAT_TOPIC", "HeartbeatTransport"]
