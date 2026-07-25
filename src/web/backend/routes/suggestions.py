"""Suggestions API routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from shared.domain.suggestion_response import (
    RespondConflictError,
    RespondMode,
    RespondNotFoundError,
    RespondRequest,
    RespondValidationError,
    SuggestionResponseService,
)
from web.backend.auth import LOCAL_ADMIN_HEADER, AuthDependency, AuthError

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


class RespondBody(BaseModel):
    mode: RespondMode
    response_text: str = ""


def _service(request: Request) -> SuggestionResponseService:
    return request.app.state.suggestion_response_service  # type: ignore[no-any-return]


def _auth(request: Request) -> AuthDependency:
    return request.app.state.auth  # type: ignore[no-any-return]


async def _require_admin(
    request: Request,
    authorization: str | None,
    local_oid: str | None,
) -> Any:
    try:
        return await _auth(request).authenticate(authorization, local_oid)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("")
async def list_suggestions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dca_local_admin_oid: str | None = Header(default=None, alias=LOCAL_ADMIN_HEADER),
) -> list[dict[str, Any]]:
    await _require_admin(request, authorization, x_dca_local_admin_oid)
    return await _service(request).list_suggestions()


@router.get("/{suggestion_id}")
async def get_suggestion(
    suggestion_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_dca_local_admin_oid: str | None = Header(default=None, alias=LOCAL_ADMIN_HEADER),
) -> dict[str, Any]:
    await _require_admin(request, authorization, x_dca_local_admin_oid)
    try:
        return await _service(request).get_suggestion(suggestion_id)
    except RespondNotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc


@router.post("/{suggestion_id}/respond")
async def respond(
    suggestion_id: str,
    body: RespondBody,
    request: Request,
    authorization: str | None = Header(default=None),
    x_dca_local_admin_oid: str | None = Header(default=None, alias=LOCAL_ADMIN_HEADER),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> Any:
    try:
        UUID(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be a UUID") from exc

    principal = await _require_admin(request, authorization, x_dca_local_admin_oid)
    try:
        result = await _service(request).respond(
            RespondRequest(
                suggestion_id=suggestion_id,
                mode=body.mode,
                response_text=body.response_text,
                actor_oid=principal.oid,
                actor_upn=principal.upn,
                idempotency_key=idempotency_key,
            )
        )
    except RespondNotFoundError as exc:
        raise HTTPException(status_code=404, detail="not found") from exc
    except RespondValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RespondConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # Transient azure → 503; permanent → 500
        from shared.azure.errors import AzureTransientError

        if isinstance(exc, AzureTransientError):
            raise HTTPException(status_code=503, detail="temporary failure") from exc
        raise HTTPException(status_code=500, detail="permanent failure") from exc

    status_code = 202 if result.outcome == "saved_notification_pending" else 200
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=result.public_snapshot)


__all__ = ["router"]
