from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from shared.azure._helpers import utc_now
from shared.azure.clients.cosmos import CosmosClient
from shared.azure.errors import AzurePermanentError
from shared.models import GuildConfigDocument
from shared.utils.retry import RetryCategory, retry_async


class GuildConfigService:
    def __init__(self, *, cosmos_client: CosmosClient, container_name: str) -> None:
        self.cosmos_client = cosmos_client
        self.container_name = container_name

    def _validate(self, document: dict[str, Any]) -> GuildConfigDocument:
        try:
            clean = {key: value for key, value in document.items() if not key.startswith("_")}
            model = GuildConfigDocument.model_validate(clean)
        except ValidationError as exc:
            raise AzurePermanentError("malformed guild config document") from exc
        if model.schema_version != 1:
            raise AzurePermanentError("unsupported guild config schema_version")
        return model

    async def get(self, guild_id: str) -> GuildConfigDocument:
        document = await self.cosmos_client.point_read(self.container_name, guild_id, guild_id)
        return self._validate(document)

    async def list(self, *, include_left: bool = False) -> list[GuildConfigDocument]:
        query = "SELECT * FROM c" if include_left else "SELECT * FROM c WHERE c.left_at = null"
        documents = await self.cosmos_client.query(self.container_name, query)
        return [self._validate(document) for document in documents]

    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> GuildConfigDocument:
        now = utc_now()
        try:
            await self.get(guild_id)
        except Exception as exc:
            if "not found" not in str(exc).lower() and "404" not in str(exc):
                raise
            payload = GuildConfigDocument(
                id=guild_id,
                guild_id=guild_id,
                name=name,
                icon_url=icon_url,
                member_count=member_count,
                owner_id=owner_id,
                created_at=now,
                updated_at=now,
            )
            return self._validate(
                await self.cosmos_client.upsert(
                    self.container_name, payload.model_dump(mode="python")
                )
            )

        return await self.patch_metadata(
            guild_id,
            {
                "name": name,
                "icon_url": icon_url,
                "member_count": member_count,
                "owner_id": owner_id,
                "left_at": None,
            },
        )

    async def patch_admin_config(self, guild_id: str, patch: dict[str, Any]) -> GuildConfigDocument:
        allowed = {"language", "api_key", "model", "webhook_url", "enabled"}
        fields = {key: value for key, value in patch.items() if key in allowed}
        if len(fields) != len(patch):
            raise AzurePermanentError("malformed admin config patch")
        return await self._patch_with_retry(guild_id, fields)

    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> GuildConfigDocument:
        allowed = {"name", "icon_url", "member_count", "owner_id", "left_at"}
        fields = {key: value for key, value in patch.items() if key in allowed}
        if len(fields) != len(patch):
            raise AzurePermanentError("malformed metadata patch")
        return await self._patch_with_retry(guild_id, fields)

    async def mark_left(self, guild_id: str) -> GuildConfigDocument:
        return await self._patch_with_retry(guild_id, {"left_at": utc_now()})

    async def _patch_with_retry(self, guild_id: str, patch: dict[str, Any]) -> GuildConfigDocument:
        async def _mutate() -> GuildConfigDocument:
            current_raw = await self.cosmos_client.point_read(
                self.container_name, guild_id, guild_id
            )
            etag = str(current_raw.get("_etag", ""))
            operations = [
                {"op": "set", "path": f"/{key}", "value": value} for key, value in patch.items()
            ]
            operations.append({"op": "set", "path": "/updated_at", "value": utc_now()})
            result = await self.cosmos_client.patch_if_match(
                self.container_name, guild_id, guild_id, operations, etag=etag
            )
            return self._validate(result)

        return await retry_async(_mutate, category=RetryCategory.ETag_RMW)
