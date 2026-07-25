"""Web API auth and respond mapping tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.azure.services.suggestions import SuggestionRecord
from shared.domain.suggestion_response import SuggestionResponseService
from shared.models.suggestion import (
    SuggestionDocument,
)
from shared.runtime.settings import RuntimeMode
from web.backend.auth import LOCAL_ADMIN_HEADER, AuthDependency
from web.backend.routes.suggestions import router
from web.backend.settings import WebSettings


def _doc() -> SuggestionDocument:
    now = datetime.now(UTC)
    return SuggestionDocument.model_validate(
        {
            "schema_version": 2,
            "id": str(uuid4()),
            "ticket_uid": "SUG-11223344",
            "guild_id": "dm",
            "title": "T",
            "details": "D",
            "type": "bug",
            "categories": ["ui"],
            "submitter": {
                "id": "222222222222222222",
                "name": "u",
                "display_name": "U",
                "global_name": None,
                "discriminator": "0",
            },
            "contact": {"method": "dm", "user_id": "222222222222222222"},
            "locale": {"user": "en-US", "guild": None, "stored": "en"},
            "guild_snapshot": None,
            "context": {
                "interaction_id": "333333333333333333",
                "channel_id": None,
                "in_guild": False,
            },
            "status": "pending",
            "conversation": [
                {
                    "entry_id": str(uuid4()),
                    "author_role": "user",
                    "direction": "incoming",
                    "text": "D",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "source": "suggestion_modal",
                    "metadata": {},
                }
            ],
            "response_text": None,
            "acted_by_oid": None,
            "acted_by_upn": None,
            "acted_at": None,
            "notification_status": None,
            "notification_attempts": 0,
            "notification_last_error": None,
            "notification_claimed_at": None,
            "notification_claimed_by": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
        }
    )


class FakeRepo:
    def __init__(self, document: SuggestionDocument) -> None:
        self.record = SuggestionRecord(document=document, etag="1")
        self.enqueued: list[Any] = []

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord:
        return self.record

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord:
        return self.record

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: list[dict[str, Any]],
    ) -> SuggestionRecord:
        data = self.record.document.model_dump(mode="python")
        for op in operations:
            data[op["path"].lstrip("/")] = op["value"]
        doc = SuggestionDocument.model_validate(data)
        self.record = SuggestionRecord(document=doc, etag="2")
        return self.record

    async def enqueue(self, document: SuggestionDocument) -> None:
        self.enqueued.append(document)

    async def list_all(self) -> list[SuggestionDocument]:
        return [self.record.document]


def _dev_app(repo: FakeRepo) -> FastAPI:
    settings = WebSettings.model_validate({"WEB_LOCAL_ADMIN_OID": "local-dev-admin"})
    auth = AuthDependency(runtime_mode=RuntimeMode.development, settings=settings)
    service = SuggestionResponseService(repo, runtime_mode=RuntimeMode.development)
    app = FastAPI()
    app.state.auth = auth
    app.state.suggestion_response_service = service
    app.include_router(router)
    return app


def test_dev_requires_local_admin_header() -> None:
    app = _dev_app(FakeRepo(_doc()))
    client = TestClient(app)
    assert client.get("/api/suggestions").status_code == 401
    ok = client.get(
        "/api/suggestions",
        headers={LOCAL_ADMIN_HEADER: "local-dev-admin"},
    )
    assert ok.status_code == 200


def test_respond_requires_uuid_idempotency_key() -> None:
    doc = _doc()
    app = _dev_app(FakeRepo(doc))
    client = TestClient(app)
    headers = {LOCAL_ADMIN_HEADER: "local-dev-admin", "Idempotency-Key": "not-a-uuid"}
    resp = client.post(
        f"/api/suggestions/{doc.id}/respond",
        headers=headers,
        json={"mode": "send", "response_text": "Thanks"},
    )
    assert resp.status_code == 422


def test_respond_success_dev() -> None:
    doc = _doc()
    repo = FakeRepo(doc)
    app = _dev_app(repo)
    client = TestClient(app)
    key = str(uuid4())
    resp = client.post(
        f"/api/suggestions/{doc.id}/respond",
        headers={LOCAL_ADMIN_HEADER: "local-dev-admin", "Idempotency-Key": key},
        json={"mode": "send", "response_text": "Thanks"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert repo.enqueued == []
