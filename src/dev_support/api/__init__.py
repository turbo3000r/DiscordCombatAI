"""Internal REST API for domain repositories."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dev_support.store import DevStore, NotFoundError

router = APIRouter(prefix="/internal/v1")


def _store(request: Request) -> DevStore:
    return request.app.state.store  # type: ignore[no-any-return]


class EnsureGuildBody(BaseModel):
    name: str = ""
    icon_url: str | None = None
    member_count: int = 0
    owner_id: str = "0"


class MetricsBatchBody(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/guilds")
async def list_guilds(request: Request, include_left: bool = False) -> list[dict[str, Any]]:
    docs = _store(request).list_guilds(include_left=include_left)
    return [doc.model_dump(mode="json") for doc in docs]


@router.get("/guilds/{guild_id}")
async def get_guild(guild_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store(request).get_guild(guild_id).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/guilds/{guild_id}")
async def ensure_guild(guild_id: str, body: EnsureGuildBody, request: Request) -> dict[str, Any]:
    doc = _store(request).ensure_active_guild(
        guild_id=guild_id,
        name=body.name,
        icon_url=body.icon_url,
        member_count=body.member_count,
        owner_id=body.owner_id,
    )
    return doc.model_dump(mode="json")


@router.patch("/guilds/{guild_id}/metadata")
async def patch_metadata(
    guild_id: str, patch: dict[str, Any], request: Request
) -> dict[str, Any]:
    try:
        return _store(request).patch_guild(guild_id, patch).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/guilds/{guild_id}/admin")
async def patch_admin(guild_id: str, patch: dict[str, Any], request: Request) -> dict[str, Any]:
    try:
        return _store(request).patch_admin(guild_id, patch).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/guilds/{guild_id}/leave")
async def leave_guild(guild_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store(request).mark_left(guild_id).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/guilds/{guild_id}/suggestions")
async def list_suggestions(guild_id: str, request: Request) -> list[dict[str, Any]]:
    docs = _store(request).list_suggestions(guild_id)
    return [doc.model_dump(mode="json") for doc in docs]


@router.post("/guilds/{guild_id}/suggestions")
async def create_suggestion(
    guild_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    payload = {**payload, "guild_id": payload.get("guild_id", guild_id)}
    try:
        return _store(request).create_suggestion(payload).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/guilds/{guild_id}/suggestions/{suggestion_id}")
async def get_suggestion(
    guild_id: str, suggestion_id: str, request: Request
) -> dict[str, Any]:
    try:
        return _store(request).get_suggestion(guild_id, suggestion_id).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/guilds/{guild_id}/suggestions/{suggestion_id}")
async def update_suggestion(
    guild_id: str, suggestion_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    payload = {**payload, "guild_id": guild_id, "id": suggestion_id}
    try:
        return _store(request).update_suggestion(payload).model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/guilds/{guild_id}/suggestions/{suggestion_id}", status_code=204)
async def delete_suggestion(guild_id: str, suggestion_id: str, request: Request) -> None:
    try:
        _store(request).delete_suggestion(guild_id, suggestion_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/status")
async def get_full_status(request: Request) -> dict[str, Any]:
    return _store(request).get_status().model_dump(mode="json")


@router.get("/status/{section}")
async def get_status_section(section: str, request: Request) -> Any:
    document = _store(request).get_status()
    if section == "identity":
        return document.identity.model_dump(mode="json")
    if section == "status":
        return document.status.model_dump(mode="json")
    if section == "suggestion_catalog":
        return document.suggestion_catalog.model_dump(mode="json")
    raise HTTPException(status_code=404, detail=f"unknown section {section}")


@router.put("/status/{section}")
async def put_status_section(section: str, payload: dict[str, Any], request: Request) -> Any:
    try:
        value = _store(request).update_status_section(section, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@router.post("/metrics/batch")
async def metrics_batch(body: MetricsBatchBody, request: Request) -> list[dict[str, Any]]:
    entities = _store(request).batch_upsert_metrics(body.entities)
    return [entity.model_dump(mode="json") for entity in entities]


@router.get("/metrics/{node_id}/history")
async def metrics_history(
    node_id: str,
    request: Request,
    start_row_key: str,
    end_row_key: str,
) -> list[dict[str, Any]]:
    entities = _store(request).query_metrics_history(
        node_id, start_row_key=start_row_key, end_row_key=end_row_key
    )
    return [entity.model_dump(mode="json") for entity in entities]


__all__ = ["router"]
