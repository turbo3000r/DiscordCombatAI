"""Build and persist suggestion tickets."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from shared.azure.errors import AzurePermanentError
from shared.models.suggestion import (
    ContactInfo,
    ConversationEntry,
    GuildSnapshot,
    LocaleInfo,
    SubmitContext,
    SubmitterSnapshot,
    SuggestionDocument,
)
from shared.storage.local_adapters import LocalHttpError
from shared.storage.protocols import SuggestionRepository

# Fixed application namespace for deterministic interaction idempotency.
SUGGEST_NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def deterministic_suggestion_id(interaction_id: str) -> str:
    return str(uuid5(SUGGEST_NAMESPACE, f"discord-interaction:{interaction_id}"))


def ticket_uid_from_id(suggestion_id: str) -> str:
    hex_part = suggestion_id.replace("-", "")[:8].upper()
    return f"SUG-{hex_part}"


def build_suggestion_document(
    *,
    interaction_id: str,
    guild_id: str,
    title: str,
    details: str,
    type_value: str,
    categories: list[str],
    submitter: SubmitterSnapshot,
    locale: LocaleInfo,
    guild_snapshot: GuildSnapshot | None,
    channel_id: str | None,
    in_guild: bool,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    suggestion_id = deterministic_suggestion_id(interaction_id)
    return {
        "id": suggestion_id,
        "ticket_uid": ticket_uid_from_id(suggestion_id),
        "guild_id": guild_id,
        "title": title,
        "details": details,
        "type": type_value,
        "categories": categories,
        "submitter": submitter.model_dump(mode="python"),
        "contact": ContactInfo(method="dm", user_id=submitter.id).model_dump(mode="python"),
        "locale": locale.model_dump(mode="python"),
        "guild_snapshot": (
            guild_snapshot.model_dump(mode="python") if guild_snapshot is not None else None
        ),
        "context": SubmitContext(
            interaction_id=interaction_id,
            channel_id=channel_id,
            in_guild=in_guild,
        ).model_dump(mode="python"),
        "status": "pending",
        "conversation": [
            ConversationEntry(
                entry_id=str(uuid4()),
                author_role="user",
                direction="incoming",
                text=details,
                created_at=now,
                source="suggestion_modal",
                metadata={"title": title},
            ).model_dump(mode="python")
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
    }


def _is_create_conflict(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) == 409


async def create_suggestion(
    repo: SuggestionRepository, payload: dict[str, Any]
) -> SuggestionDocument:
    try:
        return await repo.create(payload)
    except AzurePermanentError as exc:
        if _is_create_conflict(exc):
            return await repo.get(str(payload["guild_id"]), str(payload["id"]))
        raise
    except LocalHttpError as exc:
        # Development create-only uniqueness surfaces as LocalHttpError 409.
        if _is_create_conflict(exc):
            return await repo.get(str(payload["guild_id"]), str(payload["id"]))
        raise


__all__ = [
    "SUGGEST_NAMESPACE",
    "build_suggestion_document",
    "create_suggestion",
    "deterministic_suggestion_id",
    "ticket_uid_from_id",
]
