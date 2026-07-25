"""Cosmos reconciliation sweep for suggestion notifications."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Any, Protocol

from shared.azure.services.suggestions import SuggestionRecord

from .notification_delivery import NotificationDeliveryService

logger = logging.getLogger(__name__)


class SweepRepository(Protocol):
    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]: ...

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]: ...

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> Any | None: ...


class NotificationSweepService:
    def __init__(
        self,
        *,
        repository: SweepRepository,
        delivery: NotificationDeliveryService,
        sweep_interval_sec: float,
        min_age_sec: float,
        claim_timeout_sec: float,
        accepting: Callable[[], bool],
        utcnow: Callable[[], datetime],
        batch_size: int = 25,
    ) -> None:
        self._repository = repository
        self._delivery = delivery
        self._sweep_interval_sec = sweep_interval_sec
        self._min_age_sec = min_age_sec
        self._claim_timeout_sec = claim_timeout_sec
        self._accepting = accepting
        self._utcnow = utcnow
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self.cycles = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="suggestion-notification-sweep")

    async def stop(self, *, wait_sec: float = 5.0) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=wait_sec)

    async def sweep_once(self) -> dict[str, int]:
        now = self._utcnow()
        reset = 0
        delivered = 0
        skipped = 0

        try:
            expired = await self._repository.list_expired_claims(
                claim_timeout_sec=self._claim_timeout_sec, now=now
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "suggestion expired-claim query failed error_class=%s",
                type(exc).__name__,
            )
            expired = []

        for record in expired[: self._batch_size]:
            try:
                result = await self._repository.reset_expired_claim(
                    record.document.guild_id,
                    record.document.id,
                    etag=record.etag,
                )
                if result is not None:
                    reset += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "suggestion claim reset failed id=%s error_class=%s",
                    record.document.id,
                    type(exc).__name__,
                )

        if self._accepting():
            try:
                pending = await self._repository.list_pending_for_sweep(
                    min_age_sec=self._min_age_sec, now=now
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "suggestion pending-sweep query failed error_class=%s",
                    type(exc).__name__,
                )
                pending = []

            for record in pending[: self._batch_size]:
                if not self._accepting():
                    break
                outcome = await self._delivery.deliver(
                    guild_id=record.document.guild_id,
                    suggestion_id=record.document.id,
                )
                if outcome.status == "sent":
                    delivered += 1
                else:
                    skipped += 1

        self.cycles += 1
        return {"reset": reset, "delivered": delivered, "skipped": skipped}

    async def _loop(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("suggestion notification sweep cycle crashed")
            await asyncio.sleep(self._sweep_interval_sec)


__all__ = ["NotificationSweepService"]
