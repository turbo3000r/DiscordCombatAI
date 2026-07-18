from __future__ import annotations

import pytest

from shared.azure.errors import AzurePermanentError, classify_azure_error
from shared.azure.services.suggestions import SuggestionService


@pytest.mark.acceptance()
def test_s06_classification_invariant() -> None:
    """Scenario S06 relies on permanent-vs-transient Azure classification."""
    error = classify_azure_error(RuntimeError("401 unauthorized"))
    assert isinstance(error, AzurePermanentError)


@pytest.mark.acceptance()
@pytest.mark.asyncio()
async def test_s12_claim_via_fake_cosmos() -> None:
    """Scenario S12 depends on an ETag-style claim race resolving to one owner."""

    class _Cosmos:
        def __init__(self) -> None:
            self.doc = {
                "schema_version": 2,
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "ticket_uid": "SUG-ABCDEF12",
                "guild_id": "123456789012345678",
                "title": "Title",
                "details": "Details",
                "type": "request",
                "categories": ["commands"],
                "submitter": {
                    "id": "223456789012345678",
                    "name": "User",
                    "display_name": "User",
                    "global_name": None,
                    "discriminator": "0",
                },
                "contact": {"method": "dm", "user_id": "223456789012345678"},
                "locale": {"user": "en", "guild": None, "stored": "en"},
                "guild_snapshot": None,
                "context": {
                    "interaction_id": "33333333-3333-4333-8333-333333333333",
                    "channel_id": None,
                    "in_guild": True,
                },
                "status": "done",
                "conversation": [],
                "response_text": "Response",
                "acted_by_oid": None,
                "acted_by_upn": None,
                "acted_at": None,
                "notification_status": "pending",
                "notification_attempts": 0,
                "notification_last_error": None,
                "notification_claimed_at": None,
                "notification_claimed_by": None,
                "created_at": "2026-07-18T00:00:00Z",
                "updated_at": "2026-07-18T00:00:00Z",
                "_etag": "etag-1",
            }

        async def point_read(self, container_name: str, item_id: str, partition_key: str):
            return self.doc

        async def query(
            self, container_name: str, query: str, parameters: list[dict] | None = None
        ):
            return []

        async def upsert(self, container_name: str, document: dict):
            self.doc = {**document, "_etag": "etag-2"}
            return self.doc

        async def patch_if_match(
            self,
            container_name: str,
            item_id: str,
            partition_key: str,
            operations: list[dict],
            etag: str,
        ):
            for op in operations:
                self.doc[op["path"].lstrip("/")] = op["value"]
            self.doc["_etag"] = "etag-2"
            return self.doc

    class _Queue:
        async def send_message(self, queue_name: str, message_text: str) -> None:
            return None

    service = SuggestionService(
        cosmos_client=_Cosmos(),
        queue_client=_Queue(),
        container_name="Suggestions",
        queue_name="suggestions",
    )
    claimed = await service.claim_pending(
        "123456789012345678", "550e8400-e29b-41d4-a716-446655440000", "bot-1"
    )
    assert claimed is not None and claimed.notification_status == "claiming"


@pytest.mark.acceptance()
def test_s13_audit_redaction_shape_only() -> None:
    """Scenario S13 only checks redaction shape, not live Azure delivery."""
    from shared.azure.errors import AzureTransientError

    error = AzureTransientError("webhook_url=https://example.com?sig=secret")
    text = str(error)
    assert "secret" not in text
    assert "[REDACTED]" in text or "<redacted" in text
