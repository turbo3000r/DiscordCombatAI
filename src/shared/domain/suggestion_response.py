"""Suggestion respond domain service — ETag + ticket-scoped idempotency."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import uuid4

from shared.azure.errors import AzurePermanentError, AzureTransientError
from shared.azure.services.suggestions import SuggestionRecord
from shared.models.suggestion import ConversationEntry, SuggestionDocument
from shared.runtime.settings import RuntimeMode

logger = logging.getLogger(__name__)

IDEM_META_KEY = "_dca_idempotency"
IDEM_KEY_FIELD = "key"
IDEM_HASH_FIELD = "hash"
IDEM_SNAPSHOT_FIELD = "snapshot"


class RespondMode(StrEnum):
    send = "send"
    done_auto_feedback = "done_auto_feedback"
    done_no_feedback = "done_no_feedback"


class RespondValidationError(ValueError):
    pass


class RespondConflictError(RuntimeError):
    pass


class RespondNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class RespondRequest:
    suggestion_id: str
    mode: RespondMode
    response_text: str
    actor_oid: str
    actor_upn: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RespondResult:
    document: SuggestionDocument
    etag: str
    outcome: Literal["saved", "replayed", "saved_notification_pending"]
    public_snapshot: dict[str, Any]


class SuggestionRepo(Protocol):
    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord: ...

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord: ...

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: list[dict[str, Any]],
    ) -> SuggestionRecord: ...

    async def enqueue(self, document: SuggestionDocument) -> None: ...

    async def list_all(self) -> list[SuggestionDocument]: ...


def canonical_respond_hash(
    *,
    suggestion_id: str,
    mode: RespondMode,
    response_text: str,
    actor_oid: str,
) -> str:
    payload = {
        "suggestion_id": suggestion_id,
        "mode": mode.value,
        "response_text": response_text.strip(),
        "actor_oid": actor_oid,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def public_suggestion_dto(document: SuggestionDocument) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    cleaned_conversation: list[dict[str, Any]] = []
    for entry in payload.get("conversation", []):
        meta = dict(entry.get("metadata") or {})
        meta.pop(IDEM_META_KEY, None)
        entry = dict(entry)
        entry["metadata"] = meta
        cleaned_conversation.append(entry)
    payload["conversation"] = cleaned_conversation
    return payload


def _find_idempotent_entry(
    document: SuggestionDocument, idempotency_key: str
) -> dict[str, Any] | None:
    for entry in reversed(document.conversation):
        if entry.author_role != "staff":
            continue
        meta = entry.metadata or {}
        block = meta.get(IDEM_META_KEY)
        if isinstance(block, dict) and block.get(IDEM_KEY_FIELD) == idempotency_key:
            return block
    return None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SuggestionResponseService:
    def __init__(
        self,
        repository: SuggestionRepo,
        *,
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
    ) -> None:
        self._repo = repository
        self._mode = RuntimeMode(runtime_mode)

    async def list_suggestions(self) -> list[dict[str, Any]]:
        documents = await self._repo.list_all()
        return [public_suggestion_dto(doc) for doc in documents]

    async def get_suggestion(self, suggestion_id: str) -> dict[str, Any]:
        try:
            record = await self._repo.get_by_id(suggestion_id)
        except AzurePermanentError as exc:
            if getattr(exc, "status_code", None) == 404:
                raise RespondNotFoundError(suggestion_id) from exc
            raise
        return public_suggestion_dto(record.document)

    async def respond(self, request: RespondRequest) -> RespondResult:
        text = request.response_text.strip()
        if request.mode is not RespondMode.done_no_feedback and not text:
            raise RespondValidationError("response_text is required")
        if request.mode is RespondMode.done_no_feedback:
            text = text  # may be empty

        try:
            record = await self._repo.get_by_id(request.suggestion_id)
        except AzurePermanentError as exc:
            if getattr(exc, "status_code", None) == 404:
                raise RespondNotFoundError(request.suggestion_id) from exc
            raise

        existing = _find_idempotent_entry(record.document, request.idempotency_key)
        request_hash = canonical_respond_hash(
            suggestion_id=request.suggestion_id,
            mode=request.mode,
            response_text=text,
            actor_oid=request.actor_oid,
        )
        if existing is not None:
            if existing.get(IDEM_HASH_FIELD) != request_hash:
                raise RespondConflictError("idempotency key reused with different payload")
            snapshot = existing.get(IDEM_SNAPSHOT_FIELD)
            if not isinstance(snapshot, dict):
                snapshot = public_suggestion_dto(record.document)
            return RespondResult(
                document=record.document,
                etag=record.etag,
                outcome="replayed",
                public_snapshot=snapshot,
            )

        self._assert_preconditions(record.document, request.mode)

        try:
            return await self._commit_and_maybe_enqueue(
                record=record,
                request=request,
                text=text,
                request_hash=request_hash,
            )
        except AzureTransientError as exc:
            if getattr(exc, "status_code", None) != 412:
                raise
            # Same-key loser: reload and replay if key now present; else conflict.
            reloaded = await self._repo.get_by_id(request.suggestion_id)
            again = _find_idempotent_entry(reloaded.document, request.idempotency_key)
            if again is not None:
                if again.get(IDEM_HASH_FIELD) != request_hash:
                    raise RespondConflictError(
                        "idempotency key reused with different payload"
                    ) from exc
                snapshot = again.get(IDEM_SNAPSHOT_FIELD)
                if not isinstance(snapshot, dict):
                    snapshot = public_suggestion_dto(reloaded.document)
                return RespondResult(
                    document=reloaded.document,
                    etag=reloaded.etag,
                    outcome="replayed",
                    public_snapshot=snapshot,
                )
            raise RespondConflictError("etag conflict; refresh and retry") from exc

    def _assert_preconditions(self, document: SuggestionDocument, mode: RespondMode) -> None:
        if mode in {RespondMode.send, RespondMode.done_auto_feedback, RespondMode.done_no_feedback}:
            if document.status == "pending":
                return
            if (
                mode is RespondMode.send
                and document.status == "done"
                and document.notification_status == "failed"
            ):
                return
            raise RespondValidationError("invalid ticket state for respond mode")
        raise RespondValidationError("unknown mode")

    async def _commit_and_maybe_enqueue(
        self,
        *,
        record: SuggestionRecord,
        request: RespondRequest,
        text: str,
        request_hash: str,
    ) -> RespondResult:
        document = record.document
        now = _utcnow()
        is_retry = (
            request.mode is RespondMode.send
            and document.status == "done"
            and document.notification_status == "failed"
        )
        notifies = request.mode in {RespondMode.send, RespondMode.done_auto_feedback} or is_retry
        if request.mode is RespondMode.done_no_feedback:
            notifies = False

        entry_meta: dict[str, Any] = {
            "mode": request.mode.value,
            IDEM_META_KEY: {
                IDEM_KEY_FIELD: request.idempotency_key,
                IDEM_HASH_FIELD: request_hash,
                # snapshot filled after we know public shape; placeholder for patch
                IDEM_SNAPSHOT_FIELD: {},
            },
        }
        if is_retry:
            entry_meta["retry"] = True

        entry = ConversationEntry(
            entry_id=str(uuid4()),
            author_role="staff",
            direction="outgoing",
            text=text,
            created_at=now,
            source="web_panel",
            metadata=entry_meta,
            acted_by_oid=request.actor_oid,
            acted_by_upn=request.actor_upn,
        )

        if request.mode is RespondMode.done_no_feedback:
            notification_status = None
            notification_attempts = document.notification_attempts
            notification_last_error = document.notification_last_error
        elif is_retry:
            notification_status = "pending"
            notification_attempts = 0
            notification_last_error = None
        else:
            notification_status = "pending"
            notification_attempts = document.notification_attempts
            notification_last_error = document.notification_last_error

        # Build provisional snapshot without internal idempotency for client return,
        # but store snapshot inside metadata after strip of nested circularity.
        provisional = document.model_copy(
            update={
                "status": "done",
                "response_text": text if text else document.response_text,
                "conversation": [*document.conversation, entry],
                "acted_by_oid": request.actor_oid,
                "acted_by_upn": request.actor_upn,
                "acted_at": now,
                "notification_status": notification_status,
                "notification_attempts": notification_attempts,
                "notification_last_error": notification_last_error,
                "notification_claimed_at": None if is_retry else document.notification_claimed_at,
                "notification_claimed_by": None if is_retry else document.notification_claimed_by,
                "updated_at": now,
            }
        )
        snapshot = public_suggestion_dto(provisional)
        entry_meta[IDEM_META_KEY][IDEM_SNAPSHOT_FIELD] = snapshot
        entry = entry.model_copy(update={"metadata": entry_meta})

        operations: list[dict[str, Any]] = [
            {
                "op": "set",
                "path": "/conversation",
                "value": [
                    *(e.model_dump(mode="python") for e in document.conversation),
                    entry.model_dump(mode="python"),
                ],
            },
            {"op": "set", "path": "/status", "value": "done"},
            {
                "op": "set",
                "path": "/response_text",
                "value": text if text else document.response_text,
            },
            {"op": "set", "path": "/acted_by_oid", "value": request.actor_oid},
            {"op": "set", "path": "/acted_by_upn", "value": request.actor_upn},
            {"op": "set", "path": "/acted_at", "value": now},
            {"op": "set", "path": "/notification_status", "value": notification_status},
            {
                "op": "set",
                "path": "/notification_attempts",
                "value": notification_attempts,
            },
            {
                "op": "set",
                "path": "/notification_last_error",
                "value": notification_last_error,
            },
            {"op": "set", "path": "/updated_at", "value": now},
        ]
        if is_retry:
            operations.extend(
                [
                    {"op": "set", "path": "/notification_claimed_at", "value": None},
                    {"op": "set", "path": "/notification_claimed_by", "value": None},
                ]
            )

        saved = await self._repo.patch_respond(
            document.guild_id,
            document.id,
            etag=record.etag,
            operations=operations,
        )

        outcome: Literal["saved", "saved_notification_pending"] = "saved"
        if notifies and self._mode is RuntimeMode.production:
            try:
                await self._repo.enqueue(saved.document)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "suggestion enqueue failed after respond",
                    extra={"suggestion_id": document.id, "guild_id": document.guild_id},
                )
                outcome = "saved_notification_pending"

        return RespondResult(
            document=saved.document,
            etag=saved.etag,
            outcome=outcome,
            public_snapshot=public_suggestion_dto(saved.document),
        )


__all__ = [
    "IDEM_META_KEY",
    "RespondConflictError",
    "RespondMode",
    "RespondNotFoundError",
    "RespondRequest",
    "RespondResult",
    "RespondValidationError",
    "SuggestionResponseService",
    "canonical_respond_hash",
    "public_suggestion_dto",
]
