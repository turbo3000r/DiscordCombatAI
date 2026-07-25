"""Production adapters that delegate to shared Azure services."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any

from shared.azure.services.guilds import GuildConfigService
from shared.azure.services.metrics import MetricsService
from shared.azure.services.status import StatusService
from shared.azure.services.suggestions import SuggestionRecord, SuggestionService
from shared.models.guild_config import GuildConfigDocument
from shared.models.status_document import StatusDocument
from shared.models.suggestion import SuggestionDocument
from shared.models.telemetry import NodeMetricsEntity


class AzureGuildRepository:
    def __init__(self, service: GuildConfigService) -> None:
        self._service = service

    async def get(self, guild_id: str) -> GuildConfigDocument:
        return await self._service.get(guild_id)

    async def list(self, *, include_left: bool = False) -> list[GuildConfigDocument]:
        return await self._service.list(include_left=include_left)

    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> GuildConfigDocument:
        return await self._service.ensure_active_guild(
            guild_id=guild_id,
            name=name,
            icon_url=icon_url,
            member_count=member_count,
            owner_id=owner_id,
        )

    async def patch_metadata(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument:
        return await self._service.patch_metadata(guild_id, patch)

    async def patch_admin_config(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument:
        return await self._service.patch_admin_config(guild_id, patch)

    async def mark_left(self, guild_id: str) -> GuildConfigDocument:
        return await self._service.mark_left(guild_id)


class AzureSuggestionRepository:
    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def create(self, payload: dict[str, Any]) -> SuggestionDocument:
        return await self._service.create(payload)

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        return await self._service.get(guild_id, suggestion_id)

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord:
        return await self._service.get_with_etag(guild_id, suggestion_id)

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord:
        return await self._service.get_by_id(suggestion_id)

    async def list(self, guild_id: str) -> builtins.list[SuggestionDocument]:
        return await self._service.list(guild_id)

    async def list_all(self) -> builtins.list[SuggestionDocument]:
        return await self._service.list_all()

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: builtins.list[dict[str, Any]],
    ) -> SuggestionRecord:
        return await self._service.patch_respond(
            guild_id, suggestion_id, etag=etag, operations=operations
        )

    async def delete(self, guild_id: str, suggestion_id: str) -> None:
        await self._service.delete(guild_id, suggestion_id)

    async def enqueue(self, document: SuggestionDocument) -> None:
        await self._service.enqueue(document)

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None:
        return await self._service.claim_pending(guild_id, suggestion_id, claimed_by)

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument:
        return await self._service.mark_sent(guild_id, suggestion_id, claimed_by=claimed_by)

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument:
        return await self._service.mark_failed(
            guild_id,
            suggestion_id,
            error,
            claimed_by=claimed_by,
            requeue=requeue,
        )

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        return await self._service.list_pending_for_sweep(min_age_sec=min_age_sec, now=now)

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        return await self._service.list_expired_claims(
            claim_timeout_sec=claim_timeout_sec, now=now
        )

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> SuggestionDocument | None:
        return await self._service.reset_expired_claim(guild_id, suggestion_id, etag=etag)


class AzureStatusRepository:
    def __init__(self, service: StatusService) -> None:
        self._service = service

    async def ensure_seeded(self) -> StatusDocument:
        return await self._service.ensure_seeded()

    async def get_identity(self) -> Any:
        return await self._service.get_identity()

    async def get_status(self) -> Any:
        return await self._service.get_status()

    async def get_suggestion_catalog(self) -> Any:
        return await self._service.get_suggestion_catalog()

    async def update_identity(
        self, identity: dict[str, Any], *, actor_oid: str | None = None
    ) -> Any:
        return await self._service.update_identity(identity, actor_oid=actor_oid)

    async def update_status(self, status: dict[str, Any]) -> Any:
        return await self._service.update_status(status)

    async def update_suggestion_catalog(self, suggestion_catalog: dict[str, Any]) -> Any:
        return await self._service.update_suggestion_catalog(suggestion_catalog)


class AzureMetricsRepository:
    def __init__(self, service: MetricsService) -> None:
        self._service = service

    async def batch_upsert(self, entities: list[dict[str, Any]]) -> list[NodeMetricsEntity]:
        return await self._service.batch_upsert(entities)

    async def query_history(
        self, node_id: str, *, start_row_key: str, end_row_key: str
    ) -> list[NodeMetricsEntity]:
        return await self._service.query_history(
            node_id, start_row_key=start_row_key, end_row_key=end_row_key
        )


__all__ = [
    "AzureGuildRepository",
    "AzureMetricsRepository",
    "AzureStatusRepository",
    "AzureSuggestionRepository",
]
