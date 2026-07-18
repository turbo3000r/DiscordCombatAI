from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .base import SchemaVersionedModel, UTCDateTime
from .catalog import CatalogEntry


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BotIdentitySection(_StrictModel):
    name: str = ""
    description: str = ""
    id: str = ""
    invite_url: str = ""


class BotStatusSection(_StrictModel):
    latency_ms: int = 0
    guild_count: int = 0
    updated_at: UTCDateTime = Field(default_factory=lambda: datetime.now(UTC))


class SuggestionCatalogSection(_StrictModel):
    types: list[CatalogEntry]
    categories: list[CatalogEntry]


class StatusDocument(SchemaVersionedModel):
    schema_version: int = 1
    identity: BotIdentitySection
    status: BotStatusSection
    suggestion_catalog: SuggestionCatalogSection


def build_status_seed(*, seeded_at: datetime | None = None) -> StatusDocument:
    now = seeded_at or datetime.now(UTC)
    return StatusDocument(
        identity=BotIdentitySection(),
        status=BotStatusSection(updated_at=now),
        suggestion_catalog=SuggestionCatalogSection(
            types=[
                CatalogEntry(value="minor_issue", label="minor issue"),
                CatalogEntry(value="major_issue", label="major issue"),
                CatalogEntry(value="request", label="Request"),
                CatalogEntry(value="improvement", label="Improvement"),
                CatalogEntry(value="feedback", label="Feedback"),
            ],
            categories=[
                CatalogEntry(value="prompts", label="Prompts"),
                CatalogEntry(value="gamemodes", label="Gamemodes"),
                CatalogEntry(value="settings", label="Battle settings"),
                CatalogEntry(value="generic_environments", label="Generic environments"),
                CatalogEntry(value="commands", label="Commands"),
                CatalogEntry(value="functionalities", label="Functionalities"),
                CatalogEntry(value="localization", label="Localization"),
                CatalogEntry(value="other", label="Other"),
            ],
        ),
    )


__all__ = [
    "BotIdentitySection",
    "BotStatusSection",
    "StatusDocument",
    "SuggestionCatalogSection",
    "build_status_seed",
]
