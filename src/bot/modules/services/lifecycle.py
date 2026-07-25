"""Bot lifecycle helpers — soft/hard-stop orchestration and shutdown order."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import DrainProgress

logger = logging.getLogger(__name__)

DRAIN_PROGRESS_TOPIC = MQTT_TOPIC_POLICIES["status_bot_drain_progress"].topic
DRAIN_PROGRESS_POLICY = MQTT_TOPIC_POLICIES["status_bot_drain_progress"]


class MqttPublisher(Protocol):
    async def publish(
        self, topic: str, payload: bytes, *, qos: int, retain: bool
    ) -> None: ...


class InFlightProvider(Protocol):
    @property
    def in_flight_workflows(self) -> int: ...

    def task_ids(self) -> list[str]: ...

    async def synthesize_worker_terminated(
        self, task_ids: list[str] | None = None
    ) -> None: ...

    def clear_all_timers(self) -> None: ...


class BrokerCancel(Protocol):
    async def purge_ai_tasks(self) -> None: ...

    async def revoke_tasks(self, task_ids: list[str]) -> None: ...


class LifecycleController:
    """Coordinates admission flags, drain progress, and hard-stop."""

    def __init__(
        self,
        *,
        node_id: str,
        drain_progress_interval_sec: float,
        shutdown_grace_sec: float,
        revoke_gateway: Callable[[], Awaitable[None]],
        authorize_gateway: Callable[[], Awaitable[None]],
        mqtt: MqttPublisher | None = None,
        tracker: InFlightProvider | None = None,
        broker: BrokerCancel | None = None,
        leadership_term_provider: Callable[[], str | None] | None = None,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self.node_id = node_id
        self.drain_progress_interval_sec = drain_progress_interval_sec
        self.shutdown_grace_sec = shutdown_grace_sec
        self._revoke_gateway = revoke_gateway
        self._authorize_gateway = authorize_gateway
        self._mqtt = mqtt
        self._tracker = tracker
        self._broker = broker
        self._leadership_term_provider = leadership_term_provider
        self._utcnow = utcnow or (lambda: datetime.now(tz=UTC))

        self.accepting_ai_work = False
        self.draining = False
        self.hard_stop_count = 0
        self.soft_stop_count = 0
        self._hard_stop_lock = asyncio.Lock()
        self._hard_stop_done = False
        self._drain_task: asyncio.Task[None] | None = None
        self.shutdown_journal: list[str] = []
        self.last_drain_progress: DrainProgress | None = None
        self.revoked_task_ids: list[str] = []

    def bind(
        self,
        *,
        mqtt: MqttPublisher | None = None,
        tracker: InFlightProvider | None = None,
        broker: BrokerCancel | None = None,
        leadership_term_provider: Callable[[], str | None] | None = None,
    ) -> None:
        if mqtt is not None:
            self._mqtt = mqtt
        if tracker is not None:
            self._tracker = tracker
        if broker is not None:
            self._broker = broker
        if leadership_term_provider is not None:
            self._leadership_term_provider = leadership_term_provider

    async def on_activate(self) -> None:
        self.accepting_ai_work = True
        self.draining = False
        self._hard_stop_done = False
        await self._stop_drain_loop()
        await self._authorize_gateway()

    async def on_soft_stop(self) -> None:
        self.accepting_ai_work = False
        self.draining = True
        self.soft_stop_count += 1
        await self._ensure_drain_loop()

    async def on_hard_stop(self) -> None:
        async with self._hard_stop_lock:
            if self._hard_stop_done:
                return
            self._hard_stop_done = True
            self.accepting_ai_work = False
            self.draining = False
            self.hard_stop_count += 1
            await self._stop_drain_loop()

            task_ids: list[str] = []
            if self._tracker is not None:
                task_ids = list(self._tracker.task_ids())
            if self._broker is not None:
                try:
                    await self._broker.purge_ai_tasks()
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning("purge failed during hard-stop")
                try:
                    await self._broker.revoke_tasks(task_ids)
                    self.revoked_task_ids.extend(task_ids)
                except Exception:  # noqa: BLE001 — best-effort
                    logger.warning("revoke failed during hard-stop")
            if self._tracker is not None:
                await self._tracker.synthesize_worker_terminated(task_ids)
                # Bounded wait for completion callbacks already invoked synchronously
                # by synthesize; still honor grace for any residual awaitables.
                await asyncio.sleep(0)
                self._tracker.clear_all_timers()
            await self._revoke_gateway()

    async def publish_drain_progress_once(self) -> DrainProgress | None:
        term = None
        if self._leadership_term_provider is not None:
            term = self._leadership_term_provider()
        if term is None:
            return None
        count = 0 if self._tracker is None else self._tracker.in_flight_workflows
        progress = DrainProgress(
            node_id=self.node_id,
            leadership_term=term,
            in_flight_workflows=count,
            observed_at=self._utcnow(),
        )
        self.last_drain_progress = progress
        if self._mqtt is not None:
            try:
                await self._mqtt.publish(
                    DRAIN_PROGRESS_TOPIC,
                    progress.model_dump_json().encode("utf-8"),
                    qos=DRAIN_PROGRESS_POLICY.qos,
                    retain=DRAIN_PROGRESS_POLICY.retain,
                )
            except Exception:  # noqa: BLE001 — best-effort
                logger.warning("drain progress publish failed")
        return progress

    async def run_shutdown(self, *, close_mqtt: Callable[[], Awaitable[None]],
                           close_transport: Callable[[], Awaitable[None]],
                           close_gateway: Callable[[], Awaitable[None]],
                           close_azure: Callable[[], Awaitable[None]] | None = None,
                           hard_stop: bool = True) -> list[str]:
        """Documented shutdown order."""
        journal: list[str] = []
        self.accepting_ai_work = False
        journal.append("admissions_off")
        if hard_stop:
            await self.on_hard_stop()
            journal.append("hard_stop_complete")
        else:
            await self.on_soft_stop()
            journal.append("drain_started")
        if self._tracker is not None:
            self._tracker.clear_all_timers()
        journal.append("timers_off")
        await close_mqtt()
        journal.append("mqtt_closed")
        await close_transport()
        journal.append("transport_closed")
        await close_gateway()
        journal.append("gateway_closed")
        if close_azure is not None:
            await close_azure()
            journal.append("azure_closed")
        journal.append("exit")
        self.shutdown_journal = journal
        return journal

    async def _ensure_drain_loop(self) -> None:
        if self._drain_task is not None and not self._drain_task.done():
            return
        self._drain_task = asyncio.create_task(self._drain_loop(), name="bot-drain")

    async def _stop_drain_loop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None

    async def _drain_loop(self) -> None:
        await self.publish_drain_progress_once()
        while self.draining:
            await asyncio.sleep(self.drain_progress_interval_sec)
            if not self.draining:
                break
            await self.publish_drain_progress_once()


async def handoff_to_loop(
    loop: asyncio.AbstractEventLoop,
    coro: Coroutine[Any, Any, None],
) -> None:
    """Schedule a coroutine from a foreign thread onto the Bot event loop."""
    future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(coro, loop)
    await asyncio.wrap_future(future)


__all__ = ["DRAIN_PROGRESS_TOPIC", "LifecycleController", "handoff_to_loop"]
