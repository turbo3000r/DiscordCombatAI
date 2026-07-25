"""Development adapters that call Compose-only dev-support over HTTP."""

from __future__ import annotations

from typing import Any

import httpx

from shared.models.guild_config import GuildConfigDocument
from shared.models.status_document import StatusDocument
from shared.models.suggestion import SuggestionDocument
from shared.models.telemetry import NodeMetricsEntity

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class LocalHttpError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _LocalClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=_TIMEOUT)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise LocalHttpError(
                f"dev-support {method} {path} failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()


class LocalGuildRepository(_LocalClient):
    async def get(self, guild_id: str) -> GuildConfigDocument:
        data = await self._request("GET", f"/internal/v1/guilds/{guild_id}")
        return GuildConfigDocument.model_validate(data)

    async def list(self, *, include_left: bool = False) -> list[GuildConfigDocument]:
        data = await self._request(
            "GET", "/internal/v1/guilds", params={"include_left": str(include_left).lower()}
        )
        return [GuildConfigDocument.model_validate(item) for item in data]

    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> GuildConfigDocument:
        data = await self._request(
            "PUT",
            f"/internal/v1/guilds/{guild_id}",
            json={
                "name": name,
                "icon_url": icon_url,
                "member_count": member_count,
                "owner_id": owner_id,
            },
        )
        return GuildConfigDocument.model_validate(data)

    async def patch_metadata(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument:
        data = await self._request(
            "PATCH", f"/internal/v1/guilds/{guild_id}/metadata", json=patch
        )
        return GuildConfigDocument.model_validate(data)

    async def patch_admin_config(
        self, guild_id: str, patch: dict[str, Any]
    ) -> GuildConfigDocument:
        data = await self._request("PATCH", f"/internal/v1/guilds/{guild_id}/admin", json=patch)
        return GuildConfigDocument.model_validate(data)

    async def mark_left(self, guild_id: str) -> GuildConfigDocument:
        data = await self._request("POST", f"/internal/v1/guilds/{guild_id}/leave")
        return GuildConfigDocument.model_validate(data)


class LocalSuggestionRepository(_LocalClient):
    async def create(self, payload: dict[str, Any]) -> SuggestionDocument:
        guild_id = str(payload.get("guild_id") or payload.get("guild", {}).get("id", ""))
        data = await self._request(
            "POST", f"/internal/v1/guilds/{guild_id}/suggestions", json=payload
        )
        return SuggestionDocument.model_validate(data)

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        data = await self._request(
            "GET", f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}"
        )
        return SuggestionDocument.model_validate(data)

    async def list(self, guild_id: str) -> list[SuggestionDocument]:
        data = await self._request("GET", f"/internal/v1/guilds/{guild_id}/suggestions")
        return [SuggestionDocument.model_validate(item) for item in data]

    async def update(self, payload: dict[str, Any]) -> SuggestionDocument:
        guild_id = str(payload["guild_id"])
        suggestion_id = str(payload["id"])
        data = await self._request(
            "PUT",
            f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}",
            json=payload,
        )
        return SuggestionDocument.model_validate(data)

    async def delete(self, guild_id: str, suggestion_id: str) -> None:
        await self._request("DELETE", f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}")


class LocalStatusRepository(_LocalClient):
    async def ensure_seeded(self) -> StatusDocument:
        data = await self._request("GET", "/internal/v1/status")
        return StatusDocument.model_validate(data)

    async def get_identity(self) -> Any:
        return await self._request("GET", "/internal/v1/status/identity")

    async def get_status(self) -> Any:
        return await self._request("GET", "/internal/v1/status/status")

    async def get_suggestion_catalog(self) -> Any:
        return await self._request("GET", "/internal/v1/status/suggestion_catalog")

    async def update_identity(
        self, identity: dict[str, Any], *, actor_oid: str | None = None
    ) -> Any:
        params = {"actor_oid": actor_oid} if actor_oid else None
        return await self._request(
            "PUT", "/internal/v1/status/identity", json=identity, params=params
        )

    async def update_status(self, status: dict[str, Any]) -> Any:
        return await self._request("PUT", "/internal/v1/status/status", json=status)

    async def update_suggestion_catalog(self, suggestion_catalog: dict[str, Any]) -> Any:
        return await self._request(
            "PUT", "/internal/v1/status/suggestion_catalog", json=suggestion_catalog
        )


class LocalMetricsRepository(_LocalClient):
    async def batch_upsert(self, entities: list[dict[str, Any]]) -> list[NodeMetricsEntity]:
        data = await self._request(
            "POST", "/internal/v1/metrics/batch", json={"entities": entities}
        )
        return [NodeMetricsEntity.model_validate(item) for item in data]

    async def query_history(
        self, node_id: str, *, start_row_key: str, end_row_key: str
    ) -> list[NodeMetricsEntity]:
        data = await self._request(
            "GET",
            f"/internal/v1/metrics/{node_id}/history",
            params={"start_row_key": start_row_key, "end_row_key": end_row_key},
        )
        return [NodeMetricsEntity.model_validate(item) for item in data]


__all__ = [
    "LocalGuildRepository",
    "LocalHttpError",
    "LocalMetricsRepository",
    "LocalStatusRepository",
    "LocalSuggestionRepository",
]
