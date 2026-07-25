"""Unit tests for suggestion respond domain service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from shared.azure.errors import AzureTransientError
from shared.azure.services.suggestions import SuggestionRecord
from shared.domain.suggestion_response import (
    IDEM_META_KEY,
    RespondConflictError,
    RespondMode,
    RespondRequest,
    RespondValidationError,
    SuggestionResponseService,
    public_suggestion_dto,
)
from shared.models.suggestion import (
    ContactInfo,
    ConversationEntry,
    LocaleInfo,
    SubmitContext,
    SubmitterSnapshot,
    SuggestionDocument,
)
from shared.runtime.settings import RuntimeMode


def _doc(**overrides: Any) -> SuggestionDocument:
    now = datetime.now(UTC)
    base = {
        "schema_version": 2,
        "id": str(uuid4()),
        "ticket_uid": "SUG-AABBCCDD",
        "guild_id": "111111111111111111",
        "title": "Title",
        "details": "Details",
        "type": "bug",
        "categories": ["ui"],
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
            channel_id=None,
            in_guild=False,
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


class FakeRepo:
    def __init__(self, document: SuggestionDocument) -> None:
        self.record = SuggestionRecord(document=document, etag="1")
        self.enqueued: list[SuggestionDocument] = []
        self.fail_enqueue = False
        self.conflict_once = False

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
        if self.conflict_once:
            self.conflict_once = False
            raise AzureTransientError("etag", status_code=412)
        data = self.record.document.model_dump(mode="python")
        for op in operations:
            path = op["path"].lstrip("/")
            data[path] = op["value"]
        doc = SuggestionDocument.model_validate(data)
        self.record = SuggestionRecord(document=doc, etag=str(int(etag) + 1))
        return self.record

    async def enqueue(self, document: SuggestionDocument) -> None:
        if self.fail_enqueue:
            raise RuntimeError("queue down")
        self.enqueued.append(document)

    async def list_all(self) -> list[SuggestionDocument]:
        return [self.record.document]


@pytest.mark.asyncio
async def test_send_enqueues_in_production() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    result = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="oid-1",
            actor_upn="a@b.c",
            idempotency_key=str(uuid4()),
        )
    )
    assert result.outcome == "saved"
    assert result.document.status == "done"
    assert result.document.notification_status == "pending"
    assert len(repo.enqueued) == 1
    assert IDEM_META_KEY not in str(public_suggestion_dto(result.document).get("x", ""))


@pytest.mark.asyncio
async def test_development_suppresses_enqueue() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.development)
    await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="local-dev-admin",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert repo.enqueued == []


@pytest.mark.asyncio
async def test_done_no_feedback_keeps_null_notification() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    result = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.done_no_feedback,
            response_text="",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert result.document.notification_status is None
    assert repo.enqueued == []


@pytest.mark.asyncio
async def test_idempotent_replay_same_key() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    key = str(uuid4())
    first = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=key,
        )
    )
    second = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=key,
        )
    )
    assert second.outcome == "replayed"
    assert len(repo.enqueued) == 1
    assert len(first.document.conversation) == len(second.document.conversation)


@pytest.mark.asyncio
async def test_idempotent_mismatch_conflicts() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    key = str(uuid4())
    await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=key,
        )
    )
    with pytest.raises(RespondConflictError):
        await service.respond(
            RespondRequest(
                suggestion_id=doc.id,
                mode=RespondMode.send,
                response_text="Different",
                actor_oid="oid-1",
                actor_upn=None,
                idempotency_key=key,
            )
        )


@pytest.mark.asyncio
async def test_enqueue_failure_returns_pending_outcome() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    repo.fail_enqueue = True
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    result = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Thanks",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert result.outcome == "saved_notification_pending"
    assert result.document.notification_status == "pending"


@pytest.mark.asyncio
async def test_failed_retry_resets_attempts() -> None:
    doc = _doc(
        status="done",
        notification_status="failed",
        notification_attempts=5,
        notification_last_error="boom",
        response_text="old",
    )
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    result = await service.respond(
        RespondRequest(
            suggestion_id=doc.id,
            mode=RespondMode.send,
            response_text="Retry body",
            actor_oid="oid-1",
            actor_upn=None,
            idempotency_key=str(uuid4()),
        )
    )
    assert result.document.notification_status == "pending"
    assert result.document.notification_attempts == 0
    assert result.document.notification_last_error is None


@pytest.mark.asyncio
async def test_empty_send_rejected() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.production)
    with pytest.raises(RespondValidationError):
        await service.respond(
            RespondRequest(
                suggestion_id=doc.id,
                mode=RespondMode.send,
                response_text="   ",
                actor_oid="oid-1",
                actor_upn=None,
                idempotency_key=str(uuid4()),
            )
        )


def test_dto_strips_idempotency_metadata() -> None:
    doc = _doc()
    entry = ConversationEntry(
        entry_id=str(uuid4()),
        author_role="staff",
        direction="outgoing",
        text="x",
        created_at=datetime.now(UTC),
        source="web_panel",
        metadata={IDEM_META_KEY: {"key": "k", "hash": "h", "snapshot": {}}},
        acted_by_oid="oid",
    )
    doc = doc.model_copy(update={"conversation": [*doc.conversation, entry]})
    dto = public_suggestion_dto(doc)
    assert IDEM_META_KEY not in dto["conversation"][-1]["metadata"]
