"""Development adapters that call Compose-only dev-support over HTTP."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any

import httpx

from shared.azure.services.suggestions import SuggestionRecord
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
        guild_id = str(payload.get("guild_id") or "")
        data = await self._request(
            "POST", f"/internal/v1/guilds/{guild_id}/suggestions", json=payload
        )
        return SuggestionDocument.model_validate(data)

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        data = await self._request(
            "GET", f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}"
        )
        data.pop("_etag", None)
        data.pop("revision", None)
        return SuggestionDocument.model_validate(data)

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord:
        data = await self._request(
            "GET", f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}"
        )
        etag = str(data.pop("_etag", data.get("revision", "1")))
        return SuggestionRecord(document=SuggestionDocument.model_validate(data), etag=etag)

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord:
        data = await self._request("GET", f"/internal/v1/suggestions/{suggestion_id}")
        etag = str(data.pop("_etag", data.get("revision", "1")))
        return SuggestionRecord(document=SuggestionDocument.model_validate(data), etag=etag)

    async def list(self, guild_id: str) -> builtins.list[SuggestionDocument]:
        data = await self._request("GET", f"/internal/v1/guilds/{guild_id}/suggestions")
        return [SuggestionDocument.model_validate(item) for item in data]

    async def list_all(self) -> builtins.list[SuggestionDocument]:
        data = await self._request("GET", "/internal/v1/suggestions")
        return [SuggestionDocument.model_validate(item) for item in data]

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: builtins.list[dict[str, Any]],
    ) -> SuggestionRecord:
        data = await self._request(
            "PATCH",
            f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}/respond",
            json={"operations": operations},
            headers={"If-Match": etag},
        )
        next_etag = str(data.pop("_etag", data.get("revision", etag)))
        return SuggestionRecord(
            document=SuggestionDocument.model_validate(data), etag=next_etag
        )

    async def delete(self, guild_id: str, suggestion_id: str) -> None:
        await self._request("DELETE", f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}")

    async def enqueue(self, document: SuggestionDocument) -> None:
        # Development suppresses Queue enqueue by contract.
        return None

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None:
        try:
            data = await self._request(
                "POST",
                f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}/claim",
                json={"claimed_by": claimed_by},
            )
        except LocalHttpError as exc:
            if exc.status_code in {404, 409}:
                return None
            raise
        return SuggestionDocument.model_validate(data)

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument:
        data = await self._request(
            "POST",
            f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}/mark-sent",
            json={"claimed_by": claimed_by},
        )
        return SuggestionDocument.model_validate(data)

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument:
        data = await self._request(
            "POST",
            f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}/mark-failed",
            json={"claimed_by": claimed_by, "error": error, "requeue": requeue},
        )
        return SuggestionDocument.model_validate(data)

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        params: dict[str, str] = {"min_age_sec": str(min_age_sec)}
        if now is not None:
            params["now"] = now.astimezone().isoformat().replace("+00:00", "Z")
        data = await self._request("GET", "/internal/v1/suggestions/pending-sweep", params=params)
        results: builtins.list[SuggestionRecord] = []
        for item in data:
            etag = str(item.pop("_etag", "1"))
            results.append(
                SuggestionRecord(document=SuggestionDocument.model_validate(item), etag=etag)
            )
        return results

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        params: dict[str, str] = {"claim_timeout_sec": str(claim_timeout_sec)}
        if now is not None:
            params["now"] = now.astimezone().isoformat().replace("+00:00", "Z")
        data = await self._request("GET", "/internal/v1/suggestions/expired-claims", params=params)
        results: builtins.list[SuggestionRecord] = []
        for item in data:
            etag = str(item.pop("_etag", "1"))
            results.append(
                SuggestionRecord(document=SuggestionDocument.model_validate(item), etag=etag)
            )
        return results

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> SuggestionDocument | None:
        try:
            data = await self._request(
                "POST",
                f"/internal/v1/guilds/{guild_id}/suggestions/{suggestion_id}/reset-claim",
                headers={"If-Match": etag},
            )
        except LocalHttpError as exc:
            if exc.status_code in {404, 409, 412}:
                return None
            raise
        return SuggestionDocument.model_validate(data)


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
