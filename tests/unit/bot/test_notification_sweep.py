"""Unit tests for suggestion notification sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.commands.suggestions.service.notification_delivery import DeliveryOutcome
from bot.modules.commands.suggestions.service.notification_sweep import (
    NotificationSweepService,
)
from shared.azure.services.suggestions import SuggestionRecord
from shared.models import (
    ContactInfo,
    LocaleInfo,
    SubmitContext,
    SubmitterSnapshot,
    SuggestionDocument,
)


def _doc(**overrides: Any) -> SuggestionDocument:
    base = {
        "id": str(uuid4()),
        "ticket_uid": "SUG-FFEEDDCC",
        "guild_id": "555",
        "title": "t",
        "details": "d",
        "type": "feedback",
        "categories": ["other"],
        "submitter": SubmitterSnapshot(
            id="222",
            name="user",
            display_name="User",
            global_name=None,
            discriminator="0",
        ),
        "contact": ContactInfo(method="dm", user_id="222"),
        "locale": LocaleInfo(user="en-US", guild=None, stored="en"),
        "guild_snapshot": None,
        "context": SubmitContext(
            interaction_id="333",
            channel_id=None,
            in_guild=False,
        ),
        "status": "done",
        "conversation": [],
        "response_text": "ok",
        "acted_by_oid": "oid",
        "acted_by_upn": None,
        "acted_at": datetime.now(tz=UTC),
        "notification_status": "pending",
        "notification_attempts": 0,
        "notification_last_error": None,
        "notification_claimed_at": None,
        "notification_claimed_by": None,
        "updated_at": datetime.now(tz=UTC) - timedelta(seconds=700),
    }
    base.update(overrides)
    return SuggestionDocument.model_validate(base)


class FakeRepo:
    def __init__(self) -> None:
        self.pending: list[SuggestionRecord] = []
        self.expired: list[SuggestionRecord] = []
        self.resets = 0

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]:
        return list(self.pending)

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]:
        return list(self.expired)

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> Any:
        self.resets += 1
        return _doc(id=suggestion_id, guild_id=guild_id, notification_status="pending")


class FakeDelivery:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def deliver(self, *, guild_id: str, suggestion_id: str) -> DeliveryOutcome:
        self.calls.append(suggestion_id)
        return DeliveryOutcome("sent")


@pytest.mark.asyncio
async def test_sweep_delivers_aged_pending_independent_of_queue() -> None:
    repo = FakeRepo()
    doc = _doc()
    repo.pending = [SuggestionRecord(document=doc, etag='"1"')]
    delivery = FakeDelivery()
    now = datetime.now(tz=UTC)
    sweep = NotificationSweepService(
        repository=repo,
        delivery=delivery,  # type: ignore[arg-type]
        sweep_interval_sec=900,
        min_age_sec=600,
        claim_timeout_sec=120,
        accepting=lambda: True,
        utcnow=lambda: now,
    )
    result = await sweep.sweep_once()
    assert result["delivered"] == 1
    assert delivery.calls == [doc.id]


@pytest.mark.asyncio
async def test_sweep_resets_expired_claim_without_dm() -> None:
    repo = FakeRepo()
    doc = _doc(
        notification_status="claiming",
        notification_claimed_at=datetime.now(tz=UTC) - timedelta(seconds=200),
        notification_claimed_by="other-node",
    )
    repo.expired = [SuggestionRecord(document=doc, etag='"2"')]
    delivery = FakeDelivery()
    sweep = NotificationSweepService(
        repository=repo,
        delivery=delivery,  # type: ignore[arg-type]
        sweep_interval_sec=900,
        min_age_sec=600,
        claim_timeout_sec=120,
        accepting=lambda: True,
        utcnow=lambda: datetime.now(tz=UTC),
    )
    result = await sweep.sweep_once()
    assert result["reset"] == 1
    assert not delivery.calls


@pytest.mark.asyncio
async def test_sweep_respects_accepting_flag() -> None:
    repo = FakeRepo()
    repo.pending = [SuggestionRecord(document=_doc(), etag='"1"')]
    delivery = FakeDelivery()
    sweep = NotificationSweepService(
        repository=repo,
        delivery=delivery,  # type: ignore[arg-type]
        sweep_interval_sec=900,
        min_age_sec=600,
        claim_timeout_sec=120,
        accepting=lambda: False,
        utcnow=lambda: datetime.now(tz=UTC),
    )
    result = await sweep.sweep_once()
    assert result["delivered"] == 0
    assert not delivery.calls
