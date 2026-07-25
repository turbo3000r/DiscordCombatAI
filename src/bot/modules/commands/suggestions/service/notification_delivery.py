"""Shared notification delivery coordinator (queue + sweep)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from shared.models import SuggestionDocument
from shared.security.redact import redact_sensitive

logger = logging.getLogger(__name__)


class SuggestionDeliveryRepository(Protocol):
    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument: ...

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None: ...

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument: ...

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument: ...


class LocalizationPort(Protocol):
    def t(self, key: str, *, locale: str | None = None, **variables: Any) -> str: ...


class DiscordUserPort(Protocol):
    async def send(self, content: str) -> Any: ...


class DiscordClientPort(Protocol):
    async def fetch_user(self, user_id: int) -> DiscordUserPort: ...


class DeliveryOutcome:
    __slots__ = ("status", "document", "error_class")

    def __init__(
        self,
        status: str,
        *,
        document: SuggestionDocument | None = None,
        error_class: str | None = None,
    ) -> None:
        self.status = status
        self.document = document
        self.error_class = error_class


def classify_discord_error(exc: BaseException) -> tuple[str, bool]:
    """Return (error_class, retryable)."""
    name = type(exc).__name__
    module = type(exc).__module__
    text = f"{module}.{name}".lower()
    if "forbidden" in text or name == "Forbidden":
        return ("forbidden", False)
    if "notfound" in text or name == "NotFound":
        return ("not_found", False)
    status = getattr(exc, "status", None)
    if isinstance(status, int) and status >= 500:
        return ("http_5xx", True)
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ("transient_network", True)
    if "http" in text and status is not None:
        return ("http_error", True)
    return ("discord_error", True)


class NotificationDeliveryService:
    """Claim → localized DM → claimant-only terminal transition.

    Delivery is at-least-once. A crash after Discord accepts the DM and before
    Cosmos records ``sent`` can produce a bounded duplicate DM on reclaim.
    """

    def __init__(
        self,
        *,
        repository: SuggestionDeliveryRepository,
        l10n: LocalizationPort,
        node_id: str,
        max_attempts: int,
        client_provider: Callable[[], DiscordClientPort | None],
        accepting_claims: Callable[[], bool],
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._l10n = l10n
        self._node_id = node_id
        self._max_attempts = max_attempts
        self._client_provider = client_provider
        self._accepting_claims = accepting_claims
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def deliver(
        self, *, guild_id: str, suggestion_id: str
    ) -> DeliveryOutcome:
        if not self._accepting_claims():
            return DeliveryOutcome("claims_disabled")

        try:
            current = await self._repository.get(guild_id, suggestion_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "suggestion load failed id=%s guild_id=%s error_class=%s",
                suggestion_id,
                guild_id,
                type(exc).__name__,
            )
            return DeliveryOutcome("load_failed", error_class=type(exc).__name__)

        if current.notification_status == "sent":
            return DeliveryOutcome("already_sent", document=current)
        if current.notification_status == "failed":
            return DeliveryOutcome("already_failed", document=current)
        if current.notification_status == "claiming":
            if current.notification_claimed_by != self._node_id:
                return DeliveryOutcome("foreign_claim", document=current)
            # Same node holding claim is unusual mid-call; skip to avoid double DM.
            return DeliveryOutcome("already_claiming", document=current)
        if current.notification_status != "pending":
            return DeliveryOutcome("skip_non_pending", document=current)
        if current.status != "done":
            return DeliveryOutcome("skip_not_done", document=current)

        claimed = await self._repository.claim_pending(
            guild_id, suggestion_id, self._node_id
        )
        if claimed is None:
            return DeliveryOutcome("claim_conflict")

        self._in_flight += 1
        try:
            return await self._send_dm_and_finalize(claimed)
        finally:
            self._in_flight = max(0, self._in_flight - 1)

    async def _send_dm_and_finalize(
        self, document: SuggestionDocument
    ) -> DeliveryOutcome:
        client = self._client_provider()
        if client is None:
            await self._fail_attempt(
                document,
                error="gateway_unavailable",
                retryable=True,
            )
            return DeliveryOutcome("gateway_unavailable", document=document)

        response = document.response_text or ""
        content = self._l10n.t(
            "commands.suggest.notification_dm",
            locale=document.locale.stored,
            ticket_uid=document.ticket_uid,
            response=response,
        )
        try:
            user = await client.fetch_user(int(document.contact.user_id))
            await user.send(content)
        except Exception as exc:  # noqa: BLE001
            error_class, retryable = classify_discord_error(exc)
            await self._fail_attempt(
                document,
                error=error_class,
                retryable=retryable,
            )
            logger.warning(
                "suggestion dm failed id=%s guild_id=%s error_class=%s",
                document.id,
                document.guild_id,
                error_class,
            )
            return DeliveryOutcome(
                "dm_failed",
                document=document,
                error_class=error_class,
            )

        # Crash after accepted DM / before mark_sent may cause bounded duplicate.
        saved = await self._repository.mark_sent(
            document.guild_id,
            document.id,
            claimed_by=self._node_id,
        )
        logger.info(
            "suggestion dm sent id=%s guild_id=%s ticket_uid=%s",
            document.id,
            document.guild_id,
            document.ticket_uid,
        )
        return DeliveryOutcome("sent", document=saved)

    async def _fail_attempt(
        self,
        document: SuggestionDocument,
        *,
        error: str,
        retryable: bool,
    ) -> SuggestionDocument:
        next_attempts = document.notification_attempts + 1
        requeue = retryable and next_attempts < self._max_attempts
        # Permanent classification still increments; below max returns to pending
        # so sweep/queue can retry the attempt budget, matching contract §3.
        if not retryable:
            requeue = next_attempts < self._max_attempts
        return await self._repository.mark_failed(
            document.guild_id,
            document.id,
            redact_sensitive(error),
            claimed_by=self._node_id,
            requeue=requeue,
        )


__all__ = [
    "DeliveryOutcome",
    "NotificationDeliveryService",
    "classify_discord_error",
]
