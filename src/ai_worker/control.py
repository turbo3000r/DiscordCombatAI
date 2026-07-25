"""AI Worker pause/resume control via retained Mosquitto desired state."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    AiWorkerDesiredState,
    AiWorkerDesiredStateValue,
    PauseAck,
    UnknownSchemaVersionError,
)

logger = logging.getLogger(__name__)

DESIRED_STATE_TOPIC = MQTT_TOPIC_POLICIES["control_ai_worker_desired_state"].topic
PAUSE_ACK_TOPIC = MQTT_TOPIC_POLICIES["status_ai_worker_pause_ack"].topic


class QueueController(Protocol):
    def cancel_ai_tasks_queue(self) -> None: ...

    def restore_ai_tasks_queue(self) -> None: ...

    def active_task_count(self) -> int: ...


class PauseAckPublisher(Protocol):
    def publish_pause_ack(self, ack: PauseAck) -> None: ...


class AiWorkerControlState:
    """Parent-process pause/resume state machine."""

    def __init__(
        self,
        *,
        node_id: str,
        queue: QueueController,
        publisher: PauseAckPublisher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._node_id = node_id
        self._queue = queue
        self._publisher = publisher
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._lock = threading.Lock()
        self.state = AiWorkerDesiredStateValue.running
        self._pause_ack_sent = False

    def handle_desired_state_payload(self, payload: bytes) -> None:
        try:
            desired = AiWorkerDesiredState.parse_wire_json(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, UnknownSchemaVersionError, json.JSONDecodeError):
            logger.warning("ignoring malformed ai_worker desired_state")
            return
        if desired.state is AiWorkerDesiredStateValue.paused:
            self.enter_paused()
        else:
            self.enter_running()

    def enter_paused(self) -> None:
        with self._lock:
            self.state = AiWorkerDesiredStateValue.paused
            self._queue.cancel_ai_tasks_queue()
            if self._queue.active_task_count() == 0 and not self._pause_ack_sent:
                ack = PauseAck(node_id=self._node_id, paused_at=self._clock())
                try:
                    self._publisher.publish_pause_ack(ack)
                except Exception:
                    logger.warning("failed to publish pause_ack", exc_info=True)
                    return
                self._pause_ack_sent = True

    def enter_running(self) -> None:
        with self._lock:
            was_paused = self.state is AiWorkerDesiredStateValue.paused
            self.state = AiWorkerDesiredStateValue.running
            self._pause_ack_sent = False
            if was_paused:
                self._queue.restore_ai_tasks_queue()

    def on_task_finished(self) -> None:
        """Called when an active claim ends; may emit pause_ack if already paused."""
        with self._lock:
            if (
                self.state is AiWorkerDesiredStateValue.paused
                and self._queue.active_task_count() == 0
                and not self._pause_ack_sent
            ):
                ack = PauseAck(node_id=self._node_id, paused_at=self._clock())
                try:
                    self._publisher.publish_pause_ack(ack)
                    self._pause_ack_sent = True
                except Exception:
                    logger.warning("failed to publish pause_ack", exc_info=True)


__all__ = [
    "AiWorkerControlState",
    "DESIRED_STATE_TOPIC",
    "PAUSE_ACK_TOPIC",
    "PauseAckPublisher",
    "QueueController",
]
