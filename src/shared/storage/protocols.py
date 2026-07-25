"""Repository protocol definitions matching existing Azure service APIs."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any, Protocol

from shared.azure.services.suggestions import SuggestionRecord
from shared.models.guild_config import GuildConfigDocument
from shared.models.status_document import StatusDocument
from shared.models.suggestion import SuggestionDocument
from shared.models.telemetry import NodeMetricsEntity


class GuildRepository(Protocol):
    async def get(self, guild_id: str) -> GuildConfigDocument: ...

    async def list(self, *, include_left: bool = False) -> list[GuildConfigDocument]: ...

    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> GuildConfigDocument: ...

    async def patch_metadata(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument: ...

    async def patch_admin_config(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument: ...

    async def mark_left(self, guild_id: str) -> GuildConfigDocument: ...


class SuggestionRepository(Protocol):
    async def create(self, payload: dict[str, Any]) -> SuggestionDocument: ...

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument: ...

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord: ...

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord: ...

    async def list(self, guild_id: str) -> builtins.list[SuggestionDocument]: ...

    async def list_all(self) -> builtins.list[SuggestionDocument]: ...

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: builtins.list[dict[str, Any]],
    ) -> SuggestionRecord: ...

    async def delete(self, guild_id: str, suggestion_id: str) -> None: ...

    async def enqueue(self, document: SuggestionDocument) -> None: ...

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None: ...

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument: ...

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument: ...

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]: ...

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]: ...

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> SuggestionDocument | None: ...


class StatusRepository(Protocol):
    async def ensure_seeded(self) -> StatusDocument: ...

    async def get_identity(self) -> Any: ...

    async def get_status(self) -> Any: ...

    async def get_suggestion_catalog(self) -> Any: ...

    async def update_identity(
        self, identity: dict[str, Any], *, actor_oid: str | None = None
    ) -> Any: ...

    async def update_status(self, status: dict[str, Any]) -> Any: ...

    async def update_suggestion_catalog(self, suggestion_catalog: dict[str, Any]) -> Any: ...


class MetricsRepository(Protocol):
    async def batch_upsert(self, entities: list[dict[str, Any]]) -> list[NodeMetricsEntity]: ...

    async def query_history(
        self, node_id: str, *, start_row_key: str, end_row_key: str
    ) -> list[NodeMetricsEntity]: ...


__all__ = [
    "GuildRepository",
    "MetricsRepository",
    "StatusRepository",
    "SuggestionRepository",
]
