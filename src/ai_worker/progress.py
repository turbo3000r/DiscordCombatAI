"""Best-effort Mosquitto progress publishing for AI tasks."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import TaskPhase, TaskProgressMessage

logger = logging.getLogger(__name__)

PROGRESS_TOPIC_PREFIX = "progress/ai_worker/"


class ProgressTransport(Protocol):
    def publish(self, topic: str, payload: bytes, *, qos: int, retain: bool) -> None: ...


class ProgressPublisher(Protocol):
    def publish_phase(self, *, task_id: str, graph: str, phase: TaskPhase) -> None: ...

    def stop(self) -> None: ...


class NoOpProgressPublisher:
    def publish_phase(self, *, task_id: str, graph: str, phase: TaskPhase) -> None:
        return None

    def stop(self) -> None:
        return None


class RecordingProgressPublisher:
    """Test double capturing phase order; failures are non-fatal by design."""

    def __init__(self, *, fail: bool = False) -> None:
        self.phases: list[TaskPhase] = []
        self.fail = fail
        self.stopped = False
        self.messages: list[TaskProgressMessage] = []

    def publish_phase(self, *, task_id: str, graph: str, phase: TaskPhase) -> None:
        if self.fail:
            raise ConnectionError("forced progress publish failure")
        message = TaskProgressMessage(
            task_id=task_id,
            graph=graph,
            phase=phase,
            timestamp=datetime.now(tz=UTC),
        )
        self.messages.append(message)
        self.phases.append(phase)

    def stop(self) -> None:
        self.stopped = True


class HeartbeatProgressPublisher:
    """Publish phase changes and same-phase heartbeats on a cadence."""

    def __init__(
        self,
        *,
        transport: ProgressTransport,
        heartbeat_sec: float,
    ) -> None:
        self._transport = transport
        self._heartbeat_sec = heartbeat_sec
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._current: tuple[str, str, TaskPhase] | None = None
        self._stopped = False

    def publish_phase(self, *, task_id: str, graph: str, phase: TaskPhase) -> None:
        with self._lock:
            if self._stopped:
                return
            self._current = (task_id, graph, phase)
            self._publish_locked(task_id, graph, phase)
            self._reschedule_locked()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._cancel_timer_locked()
            self._current = None

    def _reschedule_locked(self) -> None:
        self._cancel_timer_locked()
        if self._stopped or self._heartbeat_sec <= 0 or self._current is None:
            return
        timer = threading.Timer(self._heartbeat_sec, self._heartbeat_tick)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _heartbeat_tick(self) -> None:
        with self._lock:
            if self._stopped or self._current is None:
                return
            task_id, graph, phase = self._current
            try:
                self._publish_locked(task_id, graph, phase)
            except Exception:
                logger.warning("progress heartbeat publish failed", exc_info=True)
            self._reschedule_locked()

    def _publish_locked(self, task_id: str, graph: str, phase: TaskPhase) -> None:
        message = TaskProgressMessage(
            task_id=task_id,
            graph=graph,
            phase=phase,
            timestamp=datetime.now(tz=UTC),
        )
        topic = f"{PROGRESS_TOPIC_PREFIX}{task_id}"
        qos = MQTT_TOPIC_POLICIES["progress_ai_worker"].qos
        retain = MQTT_TOPIC_POLICIES["progress_ai_worker"].retain
        self._transport.publish(
            topic,
            message.model_dump_json().encode("utf-8"),
            qos=qos,
            retain=retain,
        )


def safe_publish_phase(
    publisher: ProgressPublisher,
    *,
    task_id: str,
    graph: str,
    phase: TaskPhase,
) -> None:
    try:
        publisher.publish_phase(task_id=task_id, graph=graph, phase=phase)
    except Exception:
        # Progress must never block or fail the task itself.
        return


__all__ = [
    "HeartbeatProgressPublisher",
    "NoOpProgressPublisher",
    "PROGRESS_TOPIC_PREFIX",
    "ProgressPublisher",
    "ProgressTransport",
    "RecordingProgressPublisher",
    "safe_publish_phase",
]
