"""Azure Queue Storage poller for suggestion notification delivery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from shared.models import SuggestionQueueMessage, UnknownSchemaVersionError

from .notification_delivery import DeliveryOutcome, NotificationDeliveryService

logger = logging.getLogger(__name__)

VISIBILITY_TIMEOUT_SEC = 60
POISON_DEQUEUE_THRESHOLD = 5


class QueueClientPort(Protocol):
    async def receive_messages(
        self, queue_name: str, *, visibility_timeout: int = 60, max_messages: int = 1
    ) -> list[Any]: ...

    async def delete_message(self, queue_name: str, message: Any) -> None: ...

    async def send_message(self, queue_name: str, message_text: str) -> None: ...


class QueueHealthPort(Protocol):
    azure_queue_ok: bool


class SuggestionQueuePoller:
    def __init__(
        self,
        *,
        queue_client: QueueClientPort,
        queue_name: str,
        delivery: NotificationDeliveryService,
        health: QueueHealthPort,
        poll_interval_sec: float,
        accepting: Callable[[], bool],
        max_messages: int = 8,
        poison_suffix: str = "-poison",
    ) -> None:
        self._queue = queue_client
        self._queue_name = queue_name
        self._poison_queue = f"{queue_name}{poison_suffix}"
        self._delivery = delivery
        self._health = health
        self._poll_interval_sec = poll_interval_sec
        self._accepting = accepting
        self._max_messages = max_messages
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self.cycles = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="suggestion-queue-poller")

    async def stop(self, *, wait_sec: float = 5.0) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=wait_sec)

    async def poll_once(self) -> int:
        """Receive and process one batch. Returns processed message count."""
        if not self._accepting():
            return 0
        try:
            messages = await self._queue.receive_messages(
                self._queue_name,
                visibility_timeout=VISIBILITY_TIMEOUT_SEC,
                max_messages=self._max_messages,
            )
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._health.azure_queue_ok = False
            logger.warning(
                "suggestion queue receive failed consecutive=%s error_class=%s",
                self._consecutive_failures,
                type(exc).__name__,
            )
            return 0

        self._consecutive_failures = 0
        self._health.azure_queue_ok = True
        processed = 0
        for message in messages:
            if not self._accepting():
                break
            await self._handle_message(message)
            processed += 1
        self.cycles += 1
        return processed

    async def _handle_message(self, message: Any) -> None:
        raw = getattr(message, "content", None)
        if raw is None:
            raw = getattr(message, "message_text", None) or ""
        dequeue_count = int(getattr(message, "dequeue_count", 1) or 1)

        parsed: SuggestionQueueMessage | None = None
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            parsed = SuggestionQueueMessage.model_validate_json(str(raw))
        except UnknownSchemaVersionError:
            logger.warning(
                "suggestion queue unknown schema; poison path dequeue=%s",
                dequeue_count,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "suggestion queue malformed message; poison path dequeue=%s",
                dequeue_count,
            )

        if parsed is None:
            if dequeue_count >= POISON_DEQUEUE_THRESHOLD:
                await self._poison_and_delete(message, str(raw))
            return

        outcome = await self._delivery.deliver(
            guild_id=str(parsed.guild_id),
            suggestion_id=str(parsed.id),
        )
        await self._maybe_delete(message, outcome, raw=str(raw), dequeue_count=dequeue_count)

    async def _maybe_delete(
        self,
        message: Any,
        outcome: DeliveryOutcome,
        *,
        raw: str,
        dequeue_count: int,
    ) -> None:
        if outcome.status in {"sent", "already_sent", "already_failed"}:
            await self._queue.delete_message(self._queue_name, message)
            return
        if (
            outcome.status == "dm_failed"
            and outcome.document is not None
            and outcome.document.notification_status == "failed"
        ):
            await self._queue.delete_message(self._queue_name, message)
            return
        if dequeue_count >= POISON_DEQUEUE_THRESHOLD and outcome.status not in {
            "claims_disabled",
            "claim_conflict",
            "foreign_claim",
            "already_claiming",
        }:
            await self._poison_and_delete(message, raw)

    async def _poison_and_delete(self, message: Any, raw: str) -> None:
        await self._queue.send_message(self._poison_queue, raw)
        await self._queue.delete_message(self._queue_name, message)
        logger.warning("suggestion queue message moved to poison queue=%s", self._poison_queue)

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("suggestion queue poller cycle crashed")
            await asyncio.sleep(self._poll_interval_sec)


__all__ = ["SuggestionQueuePoller", "VISIBILITY_TIMEOUT_SEC", "POISON_DEQUEUE_THRESHOLD"]
