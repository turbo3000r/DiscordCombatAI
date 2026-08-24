"""Bot Celery dispatch and ai_tasks_results consumer with asyncio handoff."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from celery import Celery
from kombu import Connection, Consumer, Queue
from kombu.exceptions import KombuError
from pydantic import ValidationError

from shared.messaging import (
    AI_TASKS_QUEUE,
    AI_TASKS_RESULTS_QUEUE,
    AI_WORKER_RUN_GRAPH_TASK,
    RABBITMQ_QUEUE_ARGS,
)
from shared.models import (
    AiTaskResult,
    BattleAiTaskEnvelope,
    EnvironmentAiTaskEnvelope,
    TaskProgressMessage,
    UnknownSchemaVersionError,
    parse_ai_task_result,
)

logger = logging.getLogger(__name__)

CONFIRM_WAIT_SEC = 5.0
RECONNECT_BASE_SEC = 1.0
RECONNECT_MAX_SEC = 60.0
RECONNECT_JITTER = 0.2


CompletionCallback = Callable[[AiTaskResult], Awaitable[None]]
ResultHandler = Callable[[AiTaskResult], Awaitable[None]]
ProgressHandler = Callable[[TaskProgressMessage], Awaitable[None]]
AdmitFn = Callable[[], bool]
DispatchEnvelope = EnvironmentAiTaskEnvelope | BattleAiTaskEnvelope


class CeleryPublisher(Protocol):
    def send_task(self, *args: Any, **kwargs: Any) -> Any: ...

    def control(self) -> Any: ...

    def close(self) -> None: ...


@dataclass
class PendingDispatch:
    task_id: str
    envelope: DispatchEnvelope
    completion_callback: CompletionCallback
    command: str = "harness"
    buffered_progress: list[TaskProgressMessage] = field(default_factory=list)
    buffered_result: AiTaskResult | None = None
    confirmed: bool = False


def build_bot_celery_app(broker_url: str) -> Celery:
    app = Celery("bot", broker=broker_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        result_backend=None,
        task_ignore_result=True,
        task_track_started=False,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"confirm_publish": True},
        task_default_queue=AI_TASKS_QUEUE,
    )
    return app


def next_reconnect_delay(attempt: int) -> float:
    """Infinite 1→2→…→60s with ±20% jitter."""
    delay = min(RECONNECT_BASE_SEC * (2 ** max(attempt - 1, 0)), RECONNECT_MAX_SEC)
    jitter = delay * RECONNECT_JITTER
    return float(max(0.1, delay + random.uniform(-jitter, jitter)))


def _publish_task(
    celery_app: Celery,
    *,
    envelope: DispatchEnvelope,
    task_id: str,
) -> None:
    payload = envelope.model_dump(mode="json")
    celery_app.send_task(
        AI_WORKER_RUN_GRAPH_TASK,
        kwargs={"envelope": payload},
        task_id=task_id,
        queue=AI_TASKS_QUEUE,
        retry=False,
    )


class AiTransport:
    """Loop-owned pending reservations; Celery/Kombu I/O on dedicated threads."""

    def __init__(
        self,
        *,
        broker_url: str,
        admit_dispatch: AdmitFn,
        on_confirmed_dispatch: Callable[[PendingDispatch], Awaitable[None]],
        on_result: ResultHandler,
        on_progress: ProgressHandler | None = None,
        celery_app: Celery | None = None,
        publisher_executor: ThreadPoolExecutor | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._broker_url = broker_url
        self._admit_dispatch = admit_dispatch
        self._on_confirmed_dispatch = on_confirmed_dispatch
        self._on_result = on_result
        self._on_progress = on_progress
        self._celery = celery_app or build_bot_celery_app(broker_url)
        self._owns_celery = celery_app is None
        self._executor = publisher_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="bot-celery-pub"
        )
        self._owns_executor = publisher_executor is None
        self._sleep = sleep

        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, PendingDispatch] = {}
        self._consumer_stop = threading.Event()
        self._consumer_thread: threading.Thread | None = None
        self.rabbitmq_connected = False
        self._started = False
        self._accepting = True
        self.last_send_kwargs: dict[str, Any] | None = None
        self.rejected_malformed = 0
        self.discarded_unknown = 0

    @property
    def pending_ids(self) -> set[str]:
        return set(self._pending)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._started:
            return
        self._loop = loop
        self._consumer_stop.clear()
        self._consumer_thread = threading.Thread(
            target=self._consumer_main,
            name="bot-result-consumer",
            daemon=True,
        )
        self._consumer_thread.start()
        self._started = True

    async def close(self) -> None:
        self._accepting = False
        self._consumer_stop.set()
        if self._consumer_thread is not None:
            await asyncio.to_thread(self._consumer_thread.join, 5.0)
            self._consumer_thread = None
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_celery:
            with contextlib.suppress(Exception):
                self._celery.close()
        self._started = False
        self.rabbitmq_connected = False

    async def dispatch_environment(
        self,
        envelope: EnvironmentAiTaskEnvelope,
        completion_callback: CompletionCallback,
    ) -> None:
        """Harness-only environment dispatch. Requires active admission and exact task identity."""
        if envelope.graph != "environment":
            raise ValueError("Phase 2 harness accepts environment graph only")
        await self.dispatch(envelope, completion_callback, command="harness")

    async def dispatch(
        self,
        envelope: DispatchEnvelope,
        completion_callback: CompletionCallback,
        *,
        command: str = "harness",
    ) -> None:
        """Dispatch an environment or battle envelope after publisher confirm."""
        if not self._accepting or not self._admit_dispatch():
            raise PermissionError("dispatch not admitted")
        task_id = str(envelope.task_id)
        if envelope.graph not in {"environment", "battle"}:
            raise ValueError(f"unsupported graph: {envelope.graph}")
        if task_id in self._pending:
            raise ValueError(f"duplicate pending dispatch for {task_id}")

        pending = PendingDispatch(
            task_id=task_id,
            envelope=envelope,
            completion_callback=completion_callback,
            command=command,
        )
        self._pending[task_id] = pending
        send_kwargs = {
            "name": AI_WORKER_RUN_GRAPH_TASK,
            "kwargs": {"envelope": envelope.model_dump(mode="json")},
            "task_id": task_id,
            "queue": AI_TASKS_QUEUE,
        }
        self.last_send_kwargs = send_kwargs
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor,
                lambda: _publish_task(self._celery, envelope=envelope, task_id=task_id),
            )
        except Exception:
            self._pending.pop(task_id, None)
            raise
        pending.confirmed = True
        buffered_progress = list(pending.buffered_progress)
        buffered_result = pending.buffered_result
        # Leave the pending map before tracker creation/replay so concurrent
        # consumer events route to the tracker instead of double-buffering.
        self._pending.pop(task_id, None)
        await self._on_confirmed_dispatch(pending)
        for progress in buffered_progress:
            await self._on_progress_safe(progress)
        if buffered_result is not None:
            await self._on_result(buffered_result)

    async def handle_progress(self, progress: TaskProgressMessage) -> None:
        task_id = str(progress.task_id)
        pending = self._pending.get(task_id)
        if pending is not None and not pending.confirmed:
            pending.buffered_progress.append(progress)
            return
        await self._on_progress_safe(progress)

    async def handle_result_event(self, result: AiTaskResult) -> None:
        task_id = str(result.task_id)
        pending = self._pending.get(task_id)
        if pending is not None and not pending.confirmed:
            pending.buffered_result = result
            return
        await self._on_result(result)

    async def purge_ai_tasks(self) -> None:
        await asyncio.get_running_loop().run_in_executor(
            self._executor,
            self._purge_ai_tasks_sync,
        )

    async def revoke_tasks(self, task_ids: list[str]) -> None:
        await asyncio.get_running_loop().run_in_executor(
            self._executor,
            lambda: self._revoke_tasks_sync(task_ids),
        )

    def _purge_ai_tasks_sync(self) -> None:
        try:
            with Connection(self._broker_url, connect_timeout=CONFIRM_WAIT_SEC) as connection:
                connection.ensure_connection(max_retries=1)
                queue = Queue(
                    AI_TASKS_QUEUE,
                    durable=True,
                    queue_arguments=dict(RABBITMQ_QUEUE_ARGS),
                )
                bound = queue(connection)
                bound.queue_declare(passive=True)
                bound.purge()
        except (KombuError, OSError, TimeoutError) as exc:
            logger.warning("ai_tasks purge failed: %s", type(exc).__name__)

    def _revoke_tasks_sync(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            try:
                self._celery.control.revoke(task_id, terminate=True)
            except Exception as exc:  # noqa: BLE001 — best-effort hard-stop
                logger.warning("revoke failed for task: %s", type(exc).__name__)

    async def _on_progress_safe(self, progress: TaskProgressMessage) -> None:
        if self._on_progress is not None:
            await self._on_progress(progress)

    def _consumer_main(self) -> None:
        attempt = 0
        while not self._consumer_stop.is_set():
            try:
                self._consume_once()
                attempt = 0
            except Exception as exc:  # noqa: BLE001 — reconnect loop
                self.rabbitmq_connected = False
                logger.warning("result consumer disconnected: %s", type(exc).__name__)
                delay = next_reconnect_delay(attempt)
                attempt += 1
                self._sleep(delay)

    def _consume_once(self) -> None:
        queue = Queue(
            AI_TASKS_RESULTS_QUEUE,
            durable=True,
            queue_arguments=dict(RABBITMQ_QUEUE_ARGS),
        )
        with Connection(self._broker_url, connect_timeout=CONFIRM_WAIT_SEC) as connection:
            connection.ensure_connection(max_retries=1)
            queue(connection).queue_declare(passive=True)
            self.rabbitmq_connected = True

            def _on_message(body: Any, message: Any) -> None:
                self._handle_raw_result(body, message)

            with Consumer(
                connection,
                queues=[queue],
                callbacks=[_on_message],
                accept=["json"],
                prefetch_count=10,
            ):
                while not self._consumer_stop.is_set():
                    try:
                        connection.drain_events(timeout=1.0)
                    except TimeoutError:
                        continue

    def _handle_raw_result(self, body: Any, message: Any) -> None:
        try:
            if isinstance(body, (bytes, bytearray)):
                data = json.loads(body.decode("utf-8"))
            elif isinstance(body, str):
                data = json.loads(body)
            elif isinstance(body, dict):
                data = body
            else:
                raise ValueError("unsupported result body type")
            result = parse_ai_task_result(data)
            correlation = message.properties.get("correlation_id")
            if correlation is not None and str(correlation) != str(result.task_id):
                raise ValueError("correlation_id mismatch")
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            UnknownSchemaVersionError,
            ValueError,
            TypeError,
        ):
            self.rejected_malformed += 1
            message.reject(requeue=False)
            return

        message.ack()
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        future: Future[None] = asyncio.run_coroutine_threadsafe(
            self.handle_result_event(result),
            loop,
        )
        try:
            future.result(timeout=30)
        except Exception:  # noqa: BLE001 — never block ack path longer than needed
            logger.exception("result handoff failed after ack")


__all__ = [
    "AiTransport",
    "CONFIRM_WAIT_SEC",
    "CompletionCallback",
    "PendingDispatch",
    "build_bot_celery_app",
    "next_reconnect_delay",
]
