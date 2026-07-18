from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from .base import SchemaVersionedModel, UTCDateTime, UUIDString

ALLOWED_DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "discordapp.com",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminActor(_StrictModel):
    oid: str
    upn: str | None = None


def is_allowed_discord_webhook_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if not parts.hostname or parts.hostname in {"localhost"}:
        return False
    try:
        ipaddress.ip_address(parts.hostname)
        return False
    except ValueError:
        pass
    if parts.hostname not in ALLOWED_DISCORD_WEBHOOK_HOSTS:
        return False
    segments = [segment for segment in parts.path.split("/") if segment]
    return len(segments) >= 3 and segments[0] == "api" and segments[1] == "webhooks"


def validate_discord_webhook_url(url: str) -> str:
    if not is_allowed_discord_webhook_url(url):
        raise ValueError("Webhook URL must be a Discord webhook on an allowed host")
    return url


class WebhookAuditRecord(SchemaVersionedModel):
    schema_version: int = 1
    acted_by_oid: str
    acted_by_upn: str | None
    acted_at: UTCDateTime
    action: Literal["webhook_send", "webhook_update"]
    destination: Literal["ALL", "SELECTED"]
    guild_ids: list[str]
    request_id: UUIDString
    idempotency_key: UUIDString

    @field_validator("guild_ids")
    @classmethod
    def _validate_guild_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("guild_ids must not be empty")
        return value


__all__ = [
    "ALLOWED_DISCORD_WEBHOOK_HOSTS",
    "AdminActor",
    "WebhookAuditRecord",
    "is_allowed_discord_webhook_url",
    "validate_discord_webhook_url",
]
