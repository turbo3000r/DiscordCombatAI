"""Unit tests for suggestion DM delivery coordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.commands.suggestions.service.notification_delivery import (
    NotificationDeliveryService,
    classify_discord_error,
)


def _doc(**overrides: Any) -> Any:
    from shared.models import (
        ContactInfo,
        LocaleInfo,
        SubmitContext,
        SubmitterSnapshot,
        SuggestionDocument,
    )

    base = {
        "id": str(uuid4()),
        "ticket_uid": "SUG-AABBCCDD",
        "guild_id": "111",
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
        "locale": LocaleInfo(user="en-US", guild="en-US", stored="en"),
        "guild_snapshot": None,
        "context": SubmitContext(
            interaction_id="333",
            channel_id="444",
            in_guild=True,
        ),
        "status": "done",
        "conversation": [],
        "response_text": "Thanks for the idea",
        "acted_by_oid": "oid",
        "acted_by_upn": "a@b.c",
        "acted_at": datetime.now(tz=UTC),
        "notification_status": "pending",
        "notification_attempts": 0,
        "notification_last_error": None,
        "notification_claimed_at": None,
        "notification_claimed_by": None,
    }
    base.update(overrides)
    return SuggestionDocument.model_validate(base)


class FakeRepo:
    def __init__(self, document: Any) -> None:
        self.document = document
        self.claims = 0
        self.sent = 0
        self.failed: list[tuple[str, bool]] = []

    async def get(self, guild_id: str, suggestion_id: str) -> Any:
        assert guild_id == self.document.guild_id
        assert suggestion_id == self.document.id
        return self.document

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> Any | None:
        self.claims += 1
        if self.document.notification_status != "pending":
            return None
        self.document = self.document.model_copy(
            update={
                "notification_status": "claiming",
                "notification_claimed_by": claimed_by,
                "notification_claimed_at": datetime.now(tz=UTC),
            }
        )
        return self.document

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> Any:
        assert claimed_by == "node-a"
        self.sent += 1
        self.document = self.document.model_copy(
            update={
                "notification_status": "sent",
                "notification_claimed_by": None,
                "notification_claimed_at": None,
            }
        )
        return self.document

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> Any:
        self.failed.append((error, requeue))
        attempts = self.document.notification_attempts + 1
        self.document = self.document.model_copy(
            update={
                "notification_status": "pending" if requeue else "failed",
                "notification_attempts": attempts,
                "notification_last_error": error,
                "notification_claimed_by": None,
                "notification_claimed_at": None,
            }
        )
        return self.document


class FakeL10n:
    def t(self, key: str, *, locale: str | None = None, **variables: Any) -> str:
        return f"{key}:{variables.get('ticket_uid')}:{variables.get('response')}"


class FakeUser:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.fail: Exception | None = None

    async def send(self, content: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.messages.append(content)


class FakeClient:
    def __init__(self, user: FakeUser) -> None:
        self.user = user

    async def fetch_user(self, user_id: int) -> FakeUser:
        assert user_id == 222
        return self.user


def test_classify_discord_error_forbidden_and_transient() -> None:
    class Forbidden(Exception):
        pass

    class HTTPException(Exception):
        def __init__(self, status: int) -> None:
            self.status = status

    assert classify_discord_error(Forbidden())[1] is False
    assert classify_discord_error(TimeoutError())[1] is True
    assert classify_discord_error(HTTPException(503))[1] is True


@pytest.mark.asyncio
async def test_delivery_claim_before_dm_then_sent_order() -> None:
    repo = FakeRepo(_doc())
    user = FakeUser()
    service = NotificationDeliveryService(
        repository=repo,
        l10n=FakeL10n(),
        node_id="node-a",
        max_attempts=5,
        client_provider=lambda: FakeClient(user),
        accepting_claims=lambda: True,
    )
    outcome = await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert outcome.status == "sent"
    assert repo.claims == 1
    assert repo.sent == 1
    assert user.messages
    assert "SUG-AABBCCDD" in user.messages[0]


@pytest.mark.asyncio
async def test_delivery_claim_conflict_skips_dm() -> None:
    repo = FakeRepo(_doc())

    async def no_claim(guild_id: str, suggestion_id: str, claimed_by: str) -> Any | None:
        return None

    repo.claim_pending = no_claim  # type: ignore[method-assign]
    user = FakeUser()
    service = NotificationDeliveryService(
        repository=repo,
        l10n=FakeL10n(),
        node_id="node-a",
        max_attempts=5,
        client_provider=lambda: FakeClient(user),
        accepting_claims=lambda: True,
    )
    outcome = await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert outcome.status == "claim_conflict"
    assert not user.messages


@pytest.mark.asyncio
async def test_delivery_forbidden_requeues_until_max_then_failed() -> None:
    class Forbidden(Exception):
        pass

    repo = FakeRepo(_doc())
    user = FakeUser()
    user.fail = Forbidden()
    service = NotificationDeliveryService(
        repository=repo,
        l10n=FakeL10n(),
        node_id="node-a",
        max_attempts=2,
        client_provider=lambda: FakeClient(user),
        accepting_claims=lambda: True,
    )
    first = await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert first.status == "dm_failed"
    assert repo.document.notification_status == "pending"
    assert repo.document.notification_attempts == 1

    second = await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert second.status == "dm_failed"
    assert repo.document.notification_status == "failed"
    assert repo.document.notification_attempts == 2


@pytest.mark.asyncio
async def test_already_sent_skips_without_dm() -> None:
    repo = FakeRepo(_doc(notification_status="sent"))
    user = FakeUser()
    service = NotificationDeliveryService(
        repository=repo,
        l10n=FakeL10n(),
        node_id="node-a",
        max_attempts=5,
        client_provider=lambda: FakeClient(user),
        accepting_claims=lambda: True,
    )
    outcome = await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert outcome.status == "already_sent"
    assert not user.messages


@pytest.mark.asyncio
async def test_crash_after_dm_before_sent_boundary_is_at_least_once() -> None:
    """Documented bounded duplicate window: DM accepted, mark_sent not yet applied."""
    repo = FakeRepo(_doc())
    user = FakeUser()

    async def crash_sent(guild_id: str, suggestion_id: str, *, claimed_by: str) -> Any:
        raise RuntimeError("crash-after-dm-before-sent")

    repo.mark_sent = crash_sent  # type: ignore[method-assign]
    service = NotificationDeliveryService(
        repository=repo,
        l10n=FakeL10n(),
        node_id="node-a",
        max_attempts=5,
        client_provider=lambda: FakeClient(user),
        accepting_claims=lambda: True,
    )
    with pytest.raises(RuntimeError, match="crash-after-dm-before-sent"):
        await service.deliver(guild_id="111", suggestion_id=repo.document.id)
    assert user.messages  # Discord already accepted
    assert repo.document.notification_status == "claiming"
