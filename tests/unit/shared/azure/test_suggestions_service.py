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
                    "interaction_id": "987654321098765432",
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
        self.creates: list[dict] = []
        self.create_conflict = False

    async def point_read(self, container_name: str, item_id: str, partition_key: str):
        return self.docs[item_id]

    async def query(self, container_name: str, query: str, parameters: list[dict] | None = None):
        return list(self.docs.values())

    async def create_item(self, container_name: str, document: dict):
        if self.create_conflict or document["id"] in self.docs:
            from shared.azure.errors import AzurePermanentError

            raise AzurePermanentError(
                "document already exists", operation="cosmos.create_item", status_code=409
            )
        self.creates.append(document)
        self.docs[document["id"]] = {**document, "_etag": "etag-new"}
        return self.docs[document["id"]]

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
        claimed_by="bot-1",
        requeue=False,
    )
    assert failed.notification_status == "failed"
    assert failed.notification_last_error is not None
    assert "leak-me" not in failed.notification_last_error
    assert "[REDACTED]" in failed.notification_last_error
    assert len(cosmos.patches) == 2
    assert failed.notification_claimed_by is None


@pytest.mark.asyncio()
async def test_suggestion_create_conflict_and_list_all() -> None:
    from shared.azure.errors import AzurePermanentError

    cosmos = _CosmosClient()
    queue = _QueueClient()
    service = SuggestionService(
        cosmos_client=cosmos,
        queue_client=queue,
        container_name="Suggestions",
        queue_name="suggestions",
    )
    payload = {
        "id": "660e8400-e29b-41d4-a716-446655440000",
        "guild_id": "123456789012345678",
        "title": "T",
        "details": "D",
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
            "interaction_id": "987654321098765432",
            "channel_id": None,
            "in_guild": False,
        },
        "status": "pending",
        "conversation": [],
        "response_text": None,
        "acted_by_oid": None,
        "acted_by_upn": None,
        "acted_at": None,
        "notification_status": None,
        "notification_attempts": 0,
    }
    created = await service.create(payload)
    assert created.id == "660e8400-e29b-41d4-a716-446655440000"
    assert queue.messages == []
    with pytest.raises(AzurePermanentError) as exc_info:
        await service.create(payload)
    assert exc_info.value.status_code == 409
    listed = await service.list_all()
    assert len(listed) >= 2


@pytest.mark.asyncio()
async def test_suggestion_sweep_and_reset_claim() -> None:
    from datetime import UTC, datetime, timedelta

    cosmos = _CosmosClient()
    queue = _QueueClient()
    service = SuggestionService(
        cosmos_client=cosmos,
        queue_client=queue,
        container_name="Suggestions",
        queue_name="suggestions",
    )
    now = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    pending = await service.list_pending_for_sweep(min_age_sec=600, now=now)
    assert len(pending) == 1
    claimed = await service.claim_pending(
        "123456789012345678", "550e8400-e29b-41d4-a716-446655440000", "bot-1"
    )
    assert claimed is not None
    cosmos.docs["550e8400-e29b-41d4-a716-446655440000"]["notification_claimed_at"] = (
        now - timedelta(seconds=200)
    ).isoformat().replace("+00:00", "Z")
    expired = await service.list_expired_claims(claim_timeout_sec=120, now=now)
    assert len(expired) == 1
    reset = await service.reset_expired_claim(
        "123456789012345678",
        "550e8400-e29b-41d4-a716-446655440000",
        etag=expired[0].etag,
    )
    assert reset is not None
    assert reset.notification_status == "pending"
