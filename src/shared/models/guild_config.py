from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, model_validator

from .base import SchemaVersionedModel, SnowflakeString, UTCDateTime
from .localization import LanguageCode, to_ai_language_locale

ADMIN_PATCH_FIELDS = ("language", "api_key", "model", "webhook_url", "enabled", "updated_at")
METADATA_PATCH_FIELDS = ("name", "icon_url", "member_count", "owner_id", "updated_at", "left_at")


class GuildConfigDocument(SchemaVersionedModel):
    schema_version: int = 1
    id: SnowflakeString
    guild_id: SnowflakeString
    language: LanguageCode = LanguageCode.en
    api_key: str = ""
    model: str = ""
    webhook_url: str = ""
    enabled: bool = False
    name: str = ""
    icon_url: str | None = None
    member_count: int = 0
    owner_id: SnowflakeString = "0"
    created_at: UTCDateTime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: UTCDateTime = Field(default_factory=lambda: datetime.now(UTC))
    left_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def _validate_identity(self) -> GuildConfigDocument:
        if self.id != self.guild_id:
            raise ValueError("id and guild_id must match")
        return self

    def is_active(self) -> bool:
        return self.enabled and bool(self.api_key)

    def webhook_configured(self) -> bool:
        return bool(self.webhook_url)

    def ai_language_locale(self) -> str:
        return to_ai_language_locale(self.language)


def create_guild_config_document(
    guild_id: str,
    *,
    name: str = "",
    icon_url: str | None = None,
    member_count: int = 0,
    owner_id: str = "0",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> GuildConfigDocument:
    now = created_at or datetime.now(UTC)
    return GuildConfigDocument(
        id=guild_id,
        guild_id=guild_id,
        name=name,
        icon_url=icon_url,
        member_count=member_count,
        owner_id=owner_id,
        created_at=now,
        updated_at=updated_at or now,
    )


def redact_for_web(document: GuildConfigDocument) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload.pop("api_key", None)
    payload.pop("webhook_url", None)
    payload["webhook_configured"] = document.webhook_configured()
    return payload


def guild_config_defaults() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "language": LanguageCode.en.value,
        "api_key": "",
        "model": "",
        "webhook_url": "",
        "enabled": False,
    }


__all__ = [
    "ADMIN_PATCH_FIELDS",
    "GuildConfigDocument",
    "METADATA_PATCH_FIELDS",
    "create_guild_config_document",
    "guild_config_defaults",
    "redact_for_web",
]
