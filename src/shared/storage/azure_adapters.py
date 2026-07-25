"""Production adapters that delegate to shared Azure services."""

from __future__ import annotations

from typing import Any

from shared.azure.services.guilds import GuildConfigService
from shared.azure.services.metrics import MetricsService
from shared.azure.services.status import StatusService
from shared.azure.services.suggestions import SuggestionService
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

    async def list(self, guild_id: str) -> list[SuggestionDocument]:
        return await self._service.list(guild_id)

    async def update(self, payload: dict[str, Any]) -> SuggestionDocument:
        return await self._service.update(payload)

    async def delete(self, guild_id: str, suggestion_id: str) -> None:
        await self._service.delete(guild_id, suggestion_id)


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
