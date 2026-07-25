"""Celery bootsteps for parent-process MQTT control and heartbeat."""

from __future__ import annotations

import logging
import threading
from typing import Any

from celery import bootsteps

from shared.models import AiWorkerHeartbeatState, PauseAck

from .control import (
    DESIRED_STATE_TOPIC,
    PAUSE_ACK_TOPIC,
    AiWorkerControlState,
)
from .heartbeat import AiWorkerHeartbeatService
from .settings import AiWorkerSettings

logger = logging.getLogger(__name__)


class _RecordingMqttTransport:
    """Minimal paho wrapper used by bootsteps; replaced in tests."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []
        self._handler: Any = None

    def set_message_handler(self, handler: Any) -> None:
        self._handler = handler

    def connect(self, host: str, port: int) -> None:
        return None

    def subscribe(self, topic: str, *, qos: int) -> None:
        return None

    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
        self.published.append((topic, payload, qos, retain))

    def close(self) -> None:
        return None


class _CeleryQueueController:
    def __init__(self, consumer: Any) -> None:
        self._consumer = consumer
        self.cancelled = False

    def cancel_ai_tasks_queue(self) -> None:
        cancel = getattr(self._consumer, "cancel_task_queue", None)
        if cancel is not None:
            cancel("ai_tasks")
        self.cancelled = True

    def restore_ai_tasks_queue(self) -> None:
        add = getattr(self._consumer, "add_task_queue", None)
        if add is not None:
            add("ai_tasks")
        self.cancelled = False

    def active_task_count(self) -> int:
        from celery import current_app

        try:
            return len(current_app.control.inspect().active() or {})
        except Exception:
            reserved = getattr(self._consumer, "active_requests", None)
            if reserved is None:
                return 0
            return len(reserved)


class _PauseAckMqttPublisher:
    def __init__(self, transport: _RecordingMqttTransport, *, node_id: str) -> None:
        self._transport = transport
        self._node_id = node_id

    def publish_pause_ack(self, ack: PauseAck) -> None:
        self._transport.publish(
            PAUSE_ACK_TOPIC,
            ack.model_dump_json().encode("utf-8"),
            qos=1,
            retain=False,
        )


class ControlBootstep(bootsteps.StartStopStep):  # type: ignore[misc]
    """Parent consumer bootstep: desired_state subscription + heartbeat."""

    requires = ("celery.worker.consumer.tasks:Tasks",)

    def __init__(self, consumer: Any, **kwargs: Any) -> None:
        super().__init__(consumer, **kwargs)
        self._consumer = consumer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.control: AiWorkerControlState | None = None
        self.heartbeat: AiWorkerHeartbeatService | None = None
        self.transport = _RecordingMqttTransport()

    def start(self, consumer: Any) -> None:
        try:
            settings = AiWorkerSettings()  # type: ignore[call-arg]
        except Exception:
            logger.warning("control bootstep skipped: settings unavailable", exc_info=True)
            return

        queue = _CeleryQueueController(consumer)
        publisher = _PauseAckMqttPublisher(self.transport, node_id=settings.node_id)
        self.control = AiWorkerControlState(
            node_id=settings.node_id,
            queue=queue,
            publisher=publisher,
        )
        self.heartbeat = AiWorkerHeartbeatService(
            node_id=settings.node_id,
            application_version=settings.application_version,
            transport=self.transport,
            state_provider=lambda: (
                AiWorkerHeartbeatState.paused
                if self.control and self.control.state.value == "paused"
                else AiWorkerHeartbeatState.running
            ),
            active_tasks_provider=queue.active_task_count,
            rabbitmq_connected_provider=lambda: True,
        )

        def _on_message(topic: str, payload: bytes) -> None:
            if topic == DESIRED_STATE_TOPIC and self.control is not None:
                self.control.handle_desired_state_payload(payload)

        self.transport.set_message_handler(_on_message)
        try:
            self.transport.connect(settings.mosquitto_host, settings.mosquitto_port)
            self.transport.subscribe(DESIRED_STATE_TOPIC, qos=1)
        except Exception:
            logger.warning("mqtt control connect failed", exc_info=True)

        interval = settings.heartbeat_interval_sec

        def _loop() -> None:
            while not self._stop.wait(interval):
                if self.heartbeat is not None:
                    self.heartbeat.publish_once()

        self._thread = threading.Thread(target=_loop, name="ai-worker-heartbeat", daemon=True)
        self._thread.start()

    def stop(self, consumer: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self.transport.close()


def register_bootsteps(app: Any) -> None:
    app.steps["consumer"].add(ControlBootstep)


__all__ = ["ControlBootstep", "register_bootsteps"]
