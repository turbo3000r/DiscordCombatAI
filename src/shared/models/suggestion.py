from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import SchemaVersionedModel, SnowflakeString, UTCDateTime, UUIDString
from .catalog import CatalogEntry

TICKET_UID_RE = re.compile(r"^SUG-[0-9A-F]{8}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactInfo(_StrictModel):
    method: Literal["dm"]
    user_id: SnowflakeString


class SubmitterSnapshot(_StrictModel):
    id: SnowflakeString
    name: str
    display_name: str
    global_name: str | None = None
    discriminator: str


class LocaleInfo(_StrictModel):
    user: str
    guild: str | None
    stored: str


class GuildSnapshot(_StrictModel):
    id: SnowflakeString
    name: str


class SubmitContext(_StrictModel):
    interaction_id: UUIDString
    channel_id: SnowflakeString | None
    in_guild: bool


class ConversationEntry(_StrictModel):
    entry_id: UUIDString
    author_role: Literal["user", "staff"]
    direction: Literal["incoming", "outgoing"]
    text: str
    created_at: UTCDateTime
    source: Literal["suggestion_modal", "web_panel"]
    metadata: dict[str, Any] = Field(default_factory=dict)
    acted_by_oid: str | None = None
    acted_by_upn: str | None = None


def generate_ticket_uid() -> str:
    return f"SUG-{secrets.token_hex(4).upper()}"


class SuggestionDocument(SchemaVersionedModel):
    CURRENT_SCHEMA_VERSION: ClassVar[int] = 2
    schema_version: int = 2
    id: UUIDString
    ticket_uid: str
    guild_id: SnowflakeString | Literal["dm"]
    title: str
    details: str
    type: str
    categories: list[str]
    submitter: SubmitterSnapshot
    contact: ContactInfo
    locale: LocaleInfo
    guild_snapshot: GuildSnapshot | None
    context: SubmitContext
    status: Literal["pending", "done"]
    conversation: list[ConversationEntry]
    response_text: str | None
    acted_by_oid: str | None
    acted_by_upn: str | None
    acted_at: UTCDateTime | None
    notification_status: Literal[None, "pending", "claiming", "sent", "failed"]
    notification_attempts: int = 0
    notification_last_error: str | None = None
    notification_claimed_at: UTCDateTime | None = None
    notification_claimed_by: str | None = None
    created_at: UTCDateTime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: UTCDateTime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("ticket_uid")
    @classmethod
    def _validate_ticket_uid(cls, value: str) -> str:
        if not TICKET_UID_RE.fullmatch(value):
            raise ValueError("ticket_uid must match SUG-[0-9A-F]{8}")
        return value

    @model_validator(mode="after")
    def _validate_context(self) -> SuggestionDocument:
        if self.status == "pending" and self.notification_status not in (None, "pending"):
            raise ValueError("pending tickets may only have null or pending notification_status")
        return self

    def with_notification_claim(
        self, claimed_by: str, claimed_at: datetime | None = None
    ) -> SuggestionDocument:
        if self.notification_status != "pending":
            raise ValueError("notification_status must be pending to claim")
        now = claimed_at or datetime.now(UTC)
        return self.model_copy(
            update={
                "notification_status": "claiming",
                "notification_claimed_by": claimed_by,
                "notification_claimed_at": now,
                "updated_at": now,
            }
        )

    def with_notification_sent(self, *, updated_at: datetime | None = None) -> SuggestionDocument:
        now = updated_at or datetime.now(UTC)
        return self.model_copy(
            update={
                "notification_status": "sent",
                "notification_last_error": None,
                "updated_at": now,
            }
        )

    def with_notification_retry(
        self, error: str, max_attempts: int, *, updated_at: datetime | None = None
    ) -> SuggestionDocument:
        now = updated_at or datetime.now(UTC)
        attempts = self.notification_attempts + 1
        status = "pending" if attempts < max_attempts else "failed"
        return self.model_copy(
            update={
                "notification_status": status,
                "notification_attempts": attempts,
                "notification_last_error": error,
                "updated_at": now,
            }
        )

    def with_done_no_feedback(self, *, updated_at: datetime | None = None) -> SuggestionDocument:
        now = updated_at or datetime.now(UTC)
        return self.model_copy(
            update={
                "status": "done",
                "notification_status": None,
                "updated_at": now,
            }
        )

    def with_done_and_pending_feedback(
        self, *, updated_at: datetime | None = None
    ) -> SuggestionDocument:
        now = updated_at or datetime.now(UTC)
        return self.model_copy(
            update={
                "status": "done",
                "notification_status": "pending",
                "updated_at": now,
            }
        )


class SuggestionQueueMessage(SchemaVersionedModel):
    CURRENT_SCHEMA_VERSION: ClassVar[int] = 2
    schema_version: int = 2
    id: UUIDString
    ticket_uid: str
    guild_id: SnowflakeString | Literal["dm"]
    enqueued_at: UTCDateTime

    @field_validator("ticket_uid")
    @classmethod
    def _validate_ticket_uid(cls, value: str) -> str:
        if not TICKET_UID_RE.fullmatch(value):
            raise ValueError("ticket_uid must match SUG-[0-9A-F]{8}")
        return value


def build_suggestion_seed_catalog() -> dict[str, list[CatalogEntry]]:
    return {
        "types": [
            CatalogEntry(value="minor_issue", label="minor issue"),
            CatalogEntry(value="major_issue", label="major issue"),
            CatalogEntry(value="request", label="Request"),
            CatalogEntry(value="improvement", label="Improvement"),
            CatalogEntry(value="feedback", label="Feedback"),
        ],
        "categories": [
            CatalogEntry(value="prompts", label="Prompts"),
            CatalogEntry(value="gamemodes", label="Gamemodes"),
            CatalogEntry(value="settings", label="Battle settings"),
            CatalogEntry(value="generic_environments", label="Generic environments"),
            CatalogEntry(value="commands", label="Commands"),
            CatalogEntry(value="functionalities", label="Functionalities"),
            CatalogEntry(value="localization", label="Localization"),
            CatalogEntry(value="other", label="Other"),
        ],
    }


__all__ = [
    "ContactInfo",
    "ConversationEntry",
    "GuildSnapshot",
    "LocaleInfo",
    "SubmitContext",
    "SubmitterSnapshot",
    "SuggestionDocument",
    "SuggestionQueueMessage",
    "build_suggestion_seed_catalog",
    "generate_ticket_uid",
]
