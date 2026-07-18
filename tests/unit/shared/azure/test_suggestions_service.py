from __future__ import annotations

import pytest

from shared.azure.services.suggestions import SuggestionService


class _CosmosClient:
    def __init__(self) -> None:
        self.docs = {
            "550e8400-e29b-41d4-a716-446655440000": {
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
        }
        self.patches: list[list[dict]] = []
        self.upserts: list[dict] = []

    async def point_read(self, container_name: str, item_id: str, partition_key: str):
        return self.docs[item_id]

    async def query(self, container_name: str, query: str, parameters: list[dict] | None = None):
        return [self.docs["550e8400-e29b-41d4-a716-446655440000"]]

    async def upsert(self, container_name: str, document: dict):
        self.upserts.append(document)
        self.docs[document["id"]] = {**document, "_etag": "etag-2"}
        return self.docs[document["id"]]

    async def patch_if_match(
        self,
        container_name: str,
        item_id: str,
        partition_key: str,
        operations: list[dict],
        etag: str,
    ):
        self.patches.append(operations)
        doc = {**self.docs[item_id]}
        for op in operations:
            doc[op["path"].lstrip("/")] = op["value"]
        doc["_etag"] = "etag-2"
        self.docs[item_id] = doc
        return doc


class _QueueClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, queue_name: str, message_text: str) -> None:
        self.messages.append(message_text)


@pytest.mark.asyncio()
async def test_suggestion_claim_race_and_enqueue() -> None:
    cosmos = _CosmosClient()
    queue = _QueueClient()
    service = SuggestionService(
        cosmos_client=cosmos,
        queue_client=queue,
        container_name="Suggestions",
        queue_name="suggestions",
    )

    claimed = await service.claim_pending(
        "123456789012345678", "550e8400-e29b-41d4-a716-446655440000", "bot-1"
    )
    assert claimed is not None
    assert claimed.notification_status == "claiming"

    second = await service.claim_pending(
        "123456789012345678", "550e8400-e29b-41d4-a716-446655440000", "bot-2"
    )
    assert second is None
    assert cosmos.patches[0][0]["path"] == "/notification_status"

    failed = await service.mark_failed(
        "123456789012345678",
        "550e8400-e29b-41d4-a716-446655440000",
        "delivery failed api_key=leak-me",
        requeue=False,
    )
    assert failed.notification_status == "failed"
    assert failed.notification_last_error is not None
    assert "leak-me" not in failed.notification_last_error
    assert "[REDACTED]" in failed.notification_last_error
