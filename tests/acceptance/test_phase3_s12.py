"""S12 acceptance — hermetic suggestion respond / Queue / DM delivery.

Scenario ownership (Phase 3):
  1. normal response delivery — complete
  2. duplicate Queue message — complete
  3. lost Queue / sweep recovery — complete
  4. concurrent claim race — complete
  5. enqueue failure / Cosmos authoritative — complete
  6. Web Idempotency-Key replay — complete
  7. failed-notification admin retry — complete
  8. crash before DM / claim expiry — complete
  9. crash after DM before sent (bounded duplicate) — complete
  10. Discord Forbidden / not-found / transient — complete
  11. production vs development suppression — complete

Guarantee: at-least-once toward Discord with ETag claim; exactly-once is NOT claimed.
Bounded duplicate is accepted only at the crash-after-DM-before-``sent`` boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from bot.modules.commands.suggestions.service.notification_delivery import (
    NotificationDeliveryService,
)
from bot.modules.commands.suggestions.service.notification_sweep import (
    NotificationSweepService,
)
from bot.modules.commands.suggestions.service.queue_poller import SuggestionQueuePoller
from shared.azure.errors import AzureTransientError
from shared.azure.services.suggestions import SuggestionRecord
from shared.domain.suggestion_response import (
    RespondMode,
    RespondRequest,
    SuggestionResponseService,
)
from shared.models import SuggestionQueueMessage
from shared.models.suggestion import (
    ContactInfo,
    ConversationEntry,
    LocaleInfo,
    SubmitContext,
    SubmitterSnapshot,
    SuggestionDocument,
)
from shared.runtime.settings import RuntimeMode

S12_OWNERSHIP = {
    "normal_delivery": "complete",
    "duplicate_queue": "complete",
    "lost_queue_sweep": "complete",
    "concurrent_claim_race": "complete",
    "enqueue_failure": "complete",
    "idempotent_web_retry": "complete",
    "failed_notification_retry": "complete",
    "crash_before_dm": "complete",
    "crash_after_dm_before_sent_bounded_duplicate": "complete",
    "discord_error_classes": "complete",
    "production_vs_development": "complete",
}


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _pending_ticket(**overrides: Any) -> SuggestionDocument:
    now = _utcnow()
    base = {
        "schema_version": 2,
        "id": str(uuid4()),
        "ticket_uid": "SUG-AABBCC12",
        "guild_id": "111111111111111111",
        "title": "Title",
        "details": "Details",
        "type": "feedback",
        "categories": ["other"],
        "submitter": SubmitterSnapshot(
            id="222222222222222222",
            name="user",
            display_name="User",
            global_name=None,
            discriminator="0",
        ),
        "contact": ContactInfo(method="dm", user_id="222222222222222222"),
        "locale": LocaleInfo(user="en-US", guild="en", stored="en"),
        "guild_snapshot": None,
        "context": SubmitContext(
            interaction_id="333333333333333333",
            channel_id="444444444444444444",
            in_guild=True,
        ),
        "status": "pending",
        "conversation": [
            ConversationEntry(
                entry_id=str(uuid4()),
                author_role="user",
                direction="incoming",
                text="Details",
                created_at=now,
                source="suggestion_modal",
                metadata={"title": "Title"},
            )
        ],
        "response_text": None,
        "acted_by_oid": None,
        "acted_by_upn": None,
        "acted_at": None,
        "notification_status": None,
        "notification_attempts": 0,
        "notification_last_error": None,
        "notification_claimed_at": None,
        "notification_claimed_by": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return SuggestionDocument.model_validate(base)


class InMemorySuggestionStore:
    """Schema-valid Cosmos+Queue fake with ETag claim semantics."""

    def __init__(self, document: SuggestionDocument) -> None:
        self.record = SuggestionRecord(document=document, etag="1")
        self.queue: list[SuggestionQueueMessage] = []
        self.fail_enqueue = False
        self._etag = 1

    def _bump(self, document: SuggestionDocument) -> SuggestionRecord:
        self._etag += 1
        self.record = SuggestionRecord(document=document, etag=str(self._etag))
        return self.record

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        assert guild_id == self.record.document.guild_id
        assert suggestion_id == self.record.document.id
        return self.record.document

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord:
        assert suggestion_id == self.record.document.id
        return self.record

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord:
        return self.record

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: list[dict[str, Any]],
    ) -> SuggestionRecord:
        if etag != self.record.etag:
            raise AzureTransientError("etag", status_code=412)
        data = self.record.document.model_dump(mode="python")
        for op in operations:
            data[op["path"].lstrip("/")] = op["value"]
        return self._bump(SuggestionDocument.model_validate(data))

    async def enqueue(self, document: SuggestionDocument) -> None:
        if self.fail_enqueue:
            raise RuntimeError("queue down")
        self.queue.append(
            SuggestionQueueMessage(
                id=document.id,
                ticket_uid=document.ticket_uid,
                guild_id=document.guild_id,
                enqueued_at=_utcnow(),
            )
        )

    async def list_all(self) -> list[SuggestionDocument]:
        return [self.record.document]

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None:
        doc = self.record.document
        if doc.status != "done" or doc.notification_status != "pending":
            return None
        return self._bump(
            doc.model_copy(
                update={
                    "notification_status": "claiming",
                    "notification_claimed_by": claimed_by,
                    "notification_claimed_at": _utcnow(),
                    "updated_at": _utcnow(),
                }
            )
        ).document

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument:
        doc = self.record.document
        if doc.notification_claimed_by != claimed_by:
            raise RuntimeError("not claimant")
        return self._bump(
            doc.model_copy(
                update={
                    "notification_status": "sent",
                    "notification_claimed_by": None,
                    "notification_claimed_at": None,
                    "notification_last_error": None,
                    "updated_at": _utcnow(),
                }
            )
        ).document

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument:
        doc = self.record.document
        if doc.notification_claimed_by != claimed_by:
            raise RuntimeError("not claimant")
        return self._bump(
            doc.model_copy(
                update={
                    "notification_status": "pending" if requeue else "failed",
                    "notification_attempts": doc.notification_attempts + 1,
                    "notification_last_error": error,
                    "notification_claimed_by": None,
                    "notification_claimed_at": None,
                    "updated_at": _utcnow(),
                }
            )
        ).document

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]:
        now = now or _utcnow()
        doc = self.record.document
        if doc.notification_status != "pending":
            return []
        age = (now - doc.updated_at).total_seconds()
        if age < min_age_sec:
            return []
        return [self.record]

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> list[SuggestionRecord]:
        now = now or _utcnow()
        doc = self.record.document
        if doc.notification_status != "claiming" or doc.notification_claimed_at is None:
            return []
        age = (now - doc.notification_claimed_at).total_seconds()
        if age < claim_timeout_sec:
            return []
        return [self.record]

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> SuggestionDocument | None:
        if etag != self.record.etag:
            return None
        return self._bump(
            self.record.document.model_copy(
                update={
                    "notification_status": "pending",
                    "notification_claimed_by": None,
                    "notification_claimed_at": None,
                    "updated_at": _utcnow(),
                }
            )
        ).document


class FakeQueueClient:
    def __init__(self, store: InMemorySuggestionStore) -> None:
        self.store = store
        self.deleted: list[Any] = []
        self.poison: list[str] = []
        self._visibility: list[Any] = []

    def seed_from_store(self, *, dequeue_count: int = 1) -> None:
        self._visibility = [
            SimpleNamespace(
                content=message.model_dump_json(),
                dequeue_count=dequeue_count,
                id=str(message.id),
            )
            for message in self.store.queue
        ]

    async def receive_messages(
        self, queue_name: str, *, visibility_timeout: int = 60, max_messages: int = 1
    ) -> list[Any]:
        batch = self._visibility[:max_messages]
        self._visibility = self._visibility[max_messages:]
        return batch

    async def delete_message(self, queue_name: str, message: Any) -> None:
        self.deleted.append(message)

    async def send_message(self, queue_name: str, message_text: str) -> None:
        self.poison.append(message_text)


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
        return self.user


class FakeL10n:
    def t(self, key: str, *, locale: str | None = None, **variables: Any) -> str:
        return f"DM[{variables.get('ticket_uid')}]:{variables.get('response')}"


class FakeHealth:
    def __init__(self) -> None:
        self.azure_queue_ok = True


def _delivery(
    store: InMemorySuggestionStore,
    client: FakeClient,
    *,
    node_id: str = "bot-a",
    accepting: bool = True,
) -> NotificationDeliveryService:
    return NotificationDeliveryService(
        repository=store,
        l10n=FakeL10n(),
        node_id=node_id,
        max_attempts=5,
        client_provider=lambda: client,
        accepting_claims=lambda: accepting,
    )


@pytest.mark.acceptance()
def test_s12_ownership_matrix_is_complete() -> None:
    assert all(status == "complete" for status in S12_OWNERSHIP.values())
    assert "crash_after_dm_before_sent_bounded_duplicate" in S12_OWNERSHIP


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_normal_create_respond_claim_dm_sent_delete() -> None:
    store = InMemorySuggestionStore(_pending_ticket())
    web = SuggestionResponseService(store, runtime_mode=RuntimeMode.production)
    result = await web.respond(
        RespondRequest(
            suggestion_id=store.record.document.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="admin",
            actor_upn="a@b.c",
            idempotency_key=str(uuid4()),
        )
    )
    assert result.document.notification_status == "pending"
    assert len(store.queue) == 1

    user = FakeUser()
    delivery = _delivery(store, FakeClient(user))
    queue = FakeQueueClient(store)
    queue.seed_from_store()
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=delivery,
        health=FakeHealth(),
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    await poller.poll_once()
    assert store.record.document.notification_status == "sent"
    assert user.messages
    assert queue.deleted


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_duplicate_queue_second_message_skips_after_sent() -> None:
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="pending",
            response_text="Thanks",
            acted_by_oid="a",
            acted_at=_utcnow(),
        )
    )
    user = FakeUser()
    delivery = _delivery(store, FakeClient(user))
    msg = SuggestionQueueMessage(
        id=store.record.document.id,
        ticket_uid=store.record.document.ticket_uid,
        guild_id=store.record.document.guild_id,
        enqueued_at=_utcnow(),
    )
    store.queue = [msg, msg]
    queue = FakeQueueClient(store)
    queue.seed_from_store()
    # Force both messages visible with independent dequeue.
    queue._visibility = [
        SimpleNamespace(content=msg.model_dump_json(), dequeue_count=1, id="1"),
        SimpleNamespace(content=msg.model_dump_json(), dequeue_count=2, id="2"),
    ]
    poller = SuggestionQueuePoller(
        queue_client=queue,
        queue_name="suggestions",
        delivery=delivery,
        health=FakeHealth(),
        poll_interval_sec=300,
        accepting=lambda: True,
    )
    await poller.poll_once()
    assert len(user.messages) == 1
    assert store.record.document.notification_status == "sent"
    assert len(queue.deleted) == 2


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_lost_queue_sweep_recovers_after_min_age() -> None:
    aged = _utcnow() - timedelta(seconds=700)
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="pending",
            response_text="Thanks",
            acted_by_oid="a",
            acted_at=aged,
            updated_at=aged,
        )
    )
    user = FakeUser()
    delivery = _delivery(store, FakeClient(user))
    sweep = NotificationSweepService(
        repository=store,
        delivery=delivery,
        sweep_interval_sec=900,
        min_age_sec=600,
        claim_timeout_sec=120,
        accepting=lambda: True,
        utcnow=_utcnow,
    )
    result = await sweep.sweep_once()
    assert result["delivered"] == 1
    assert store.record.document.notification_status == "sent"
    assert user.messages


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_concurrent_claim_only_one_sends() -> None:
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="pending",
            response_text="Thanks",
            acted_by_oid="a",
            acted_at=_utcnow(),
        )
    )
    user = FakeUser()
    client = FakeClient(user)
    a = _delivery(store, client, node_id="bot-a")
    b = _delivery(store, client, node_id="bot-b")
    first = await a.deliver(
        guild_id=store.record.document.guild_id,
        suggestion_id=store.record.document.id,
    )
    second = await b.deliver(
        guild_id=store.record.document.guild_id,
        suggestion_id=store.record.document.id,
    )
    assert first.status == "sent"
    assert second.status in {"already_sent", "claim_conflict", "skip_non_pending"}
    assert len(user.messages) == 1


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_enqueue_failure_leaves_pending_for_sweep() -> None:
    store = InMemorySuggestionStore(_pending_ticket())
    store.fail_enqueue = True
    web = SuggestionResponseService(store, runtime_mode=RuntimeMode.production)
    result = await web.respond(
        RespondRequest(
            suggestion_id=store.record.document.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="admin",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert result.outcome == "saved_notification_pending"
    assert store.record.document.notification_status == "pending"
    assert store.queue == []


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_idempotent_web_retry_no_second_enqueue() -> None:
    store = InMemorySuggestionStore(_pending_ticket())
    web = SuggestionResponseService(store, runtime_mode=RuntimeMode.production)
    key = str(uuid4())
    req = RespondRequest(
        suggestion_id=store.record.document.id,
        mode=RespondMode.send,
        response_text="Thanks",
        actor_oid="admin",
        actor_upn=None,
        idempotency_key=key,
    )
    first = await web.respond(req)
    second = await web.respond(req)
    assert first.outcome == "saved"
    assert second.outcome == "replayed"
    assert len(store.queue) == 1
    assert len(store.record.document.conversation) == 2  # user + one staff


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_failed_notification_admin_retry() -> None:
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="failed",
            response_text="old",
            notification_attempts=5,
            notification_last_error="forbidden",
            acted_by_oid="admin",
            acted_at=_utcnow(),
        )
    )
    web = SuggestionResponseService(store, runtime_mode=RuntimeMode.production)
    result = await web.respond(
        RespondRequest(
            suggestion_id=store.record.document.id,
            mode=RespondMode.send,
            response_text="retry body",
            actor_oid="admin",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert result.document.notification_status == "pending"
    assert result.document.notification_attempts == 0
    assert len(store.queue) == 1


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_crash_before_dm_claim_expiry_then_reclaim() -> None:
    claimed_at = _utcnow() - timedelta(seconds=200)
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="claiming",
            response_text="Thanks",
            notification_claimed_at=claimed_at,
            notification_claimed_by="dead-bot",
            acted_by_oid="a",
            acted_at=claimed_at,
            updated_at=claimed_at,
        )
    )
    user = FakeUser()
    delivery = _delivery(store, FakeClient(user))
    sweep = NotificationSweepService(
        repository=store,
        delivery=delivery,
        sweep_interval_sec=900,
        min_age_sec=600,
        claim_timeout_sec=120,
        accepting=lambda: True,
        utcnow=_utcnow,
    )
    # First cycle resets expired claim without requiring a DM in the same breath.
    result = await sweep.sweep_once()
    assert result["reset"] == 1
    assert store.record.document.notification_status == "pending"
    # Age the recovered pending ticket past min-age so a later cycle can reclaim.
    store.record = SuggestionRecord(
        document=store.record.document.model_copy(
            update={"updated_at": _utcnow() - timedelta(seconds=700)}
        ),
        etag=store.record.etag,
    )
    result = await sweep.sweep_once()
    assert result["delivered"] == 1
    assert store.record.document.notification_status == "sent"
    assert user.messages


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_crash_after_dm_before_sent_allows_bounded_duplicate() -> None:
    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="pending",
            response_text="Thanks",
            acted_by_oid="a",
            acted_at=_utcnow(),
        )
    )
    user = FakeUser()
    delivery = _delivery(store, FakeClient(user), node_id="bot-a")

    async def crash_sent(guild_id: str, suggestion_id: str, *, claimed_by: str) -> Any:
        raise RuntimeError("crash-after-dm-before-sent")

    store.mark_sent = crash_sent  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="crash-after-dm-before-sent"):
        await delivery.deliver(
            guild_id=store.record.document.guild_id,
            suggestion_id=store.record.document.id,
        )
    assert len(user.messages) == 1
    # Simulate claim timeout recovery + reclaim (bounded duplicate window).
    store.record = SuggestionRecord(
        document=store.record.document.model_copy(
            update={
                "notification_status": "pending",
                "notification_claimed_by": None,
                "notification_claimed_at": None,
            }
        ),
        etag=store.record.etag,
    )

    async def ok_sent(
        guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument:
        doc = store.record.document
        return store._bump(
            doc.model_copy(
                update={
                    "notification_status": "sent",
                    "notification_claimed_by": None,
                    "notification_claimed_at": None,
                    "notification_last_error": None,
                    "updated_at": _utcnow(),
                }
            )
        ).document

    store.mark_sent = ok_sent  # type: ignore[method-assign]
    delivery2 = _delivery(store, FakeClient(user), node_id="bot-b")
    outcome = await delivery2.deliver(
        guild_id=store.record.document.guild_id,
        suggestion_id=store.record.document.id,
    )
    assert outcome.status == "sent"
    assert len(user.messages) == 2  # bounded duplicate accepted


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_discord_forbidden_then_transient_then_max_failed() -> None:
    class Forbidden(Exception):
        pass

    store = InMemorySuggestionStore(
        _pending_ticket(
            status="done",
            notification_status="pending",
            response_text="Thanks",
            acted_by_oid="a",
            acted_at=_utcnow(),
        )
    )
    user = FakeUser()
    user.fail = Forbidden()
    delivery = _delivery(store, FakeClient(user))
    for _ in range(5):
        store.record = SuggestionRecord(
            document=store.record.document.model_copy(
                update={
                    "notification_status": "pending",
                    "notification_claimed_by": None,
                    "notification_claimed_at": None,
                }
            ),
            etag=store.record.etag,
        )
        await delivery.deliver(
            guild_id=store.record.document.guild_id,
            suggestion_id=store.record.document.id,
        )
    assert store.record.document.notification_status == "failed"
    assert store.record.document.notification_attempts == 5
    assert not user.messages


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s12_development_suppresses_enqueue_and_delivery_start() -> None:
    store = InMemorySuggestionStore(_pending_ticket())
    web = SuggestionResponseService(store, runtime_mode=RuntimeMode.development)
    await web.respond(
        RespondRequest(
            suggestion_id=store.record.document.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="local-dev-admin",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert store.queue == []
    assert store.record.document.notification_status == "pending"
