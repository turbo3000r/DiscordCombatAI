from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from shared.azure._helpers import utc_now
from shared.azure.clients.cosmos import CosmosClient
from shared.azure.clients.queue import QueueClient
from shared.azure.errors import AzurePermanentError
from shared.models import SuggestionDocument, SuggestionQueueMessage
from shared.security.redact import redact_sensitive
from shared.utils.retry import RetryCategory, retry_async

PatchOperations = list[dict[str, Any]]


class SuggestionService:
    def __init__(
        self,
        *,
        cosmos_client: CosmosClient,
        queue_client: QueueClient,
        container_name: str,
        queue_name: str,
    ) -> None:
        self.cosmos_client = cosmos_client
        self.queue_client = queue_client
        self.container_name = container_name
        self.queue_name = queue_name

    def _validate(self, document: dict[str, Any]) -> SuggestionDocument:
        try:
            clean = {key: value for key, value in document.items() if not key.startswith("_")}
            model = SuggestionDocument.model_validate(clean)
        except ValidationError as exc:
            raise AzurePermanentError("malformed suggestion document") from exc
        if model.schema_version != 2:
            raise AzurePermanentError("unsupported suggestion schema_version")
        return model

    async def create(self, payload: dict[str, Any]) -> SuggestionDocument:
        now = utc_now()
        ticket_id = str(uuid4())
        ticket_uid = f"SUG-{ticket_id.replace('-', '')[:8].upper()}"
        data = dict(payload)
        conversation = data.pop("conversation", [])
        data.pop("schema_version", None)
        data.pop("id", None)
        data.pop("ticket_uid", None)
        data.pop("created_at", None)
        data.pop("updated_at", None)
        document = SuggestionDocument(
            id=ticket_id,
            ticket_uid=ticket_uid,
            created_at=now,
            updated_at=now,
            conversation=conversation,
            **data,
        )
        saved = self._validate(
            await self.cosmos_client.upsert(self.container_name, document.model_dump(mode="python"))
        )
        if saved.notification_status == "pending":
            await self.enqueue(saved)
        return saved

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        document = await self.cosmos_client.point_read(self.container_name, suggestion_id, guild_id)
        return self._validate(document)

    async def list(self, guild_id: str) -> list[SuggestionDocument]:
        query = "SELECT * FROM c WHERE c.guild_id = @guild_id"
        documents = await self.cosmos_client.query(
            self.container_name, query, [{"name": "@guild_id", "value": guild_id}]
        )
        return [self._validate(document) for document in documents]

    async def update(self, payload: dict[str, Any]) -> SuggestionDocument:
        document = self._validate(payload)
        saved = await self.cosmos_client.upsert(
            self.container_name, document.model_dump(mode="python")
        )
        return self._validate(saved)

    async def delete(self, guild_id: str, suggestion_id: str) -> None:
        container = (
            self.cosmos_client._client()
            .get_database_client(self.cosmos_client.settings.azure_cosmos_database)
            .get_container_client(self.container_name)
        )
        result = container.delete_item(item=suggestion_id, partition_key=guild_id)
        if hasattr(result, "__await__"):
            await result

    async def enqueue(self, document: SuggestionDocument) -> None:
        message = SuggestionQueueMessage(
            id=document.id,
            ticket_uid=document.ticket_uid,
            guild_id=document.guild_id,
            enqueued_at=utc_now(),
        )
        await self.queue_client.send_message(self.queue_name, message.model_dump_json())

    async def claim_pending(
        self, guild_id: str, suggestion_id: str, claimed_by: str
    ) -> SuggestionDocument | None:
        async def _claim() -> SuggestionDocument | None:
            current_raw = await self.cosmos_client.point_read(
                self.container_name, suggestion_id, guild_id
            )
            current = self._validate(current_raw)
            if current.status != "done" or current.notification_status != "pending":
                return None
            etag = str(current_raw.get("_etag", ""))
            operations: list[dict[str, Any]] = [
                {"op": "set", "path": "/notification_status", "value": "claiming"},
                {"op": "set", "path": "/notification_claimed_at", "value": utc_now()},
                {"op": "set", "path": "/notification_claimed_by", "value": claimed_by},
                {"op": "set", "path": "/updated_at", "value": utc_now()},
            ]
            saved = await self.cosmos_client.patch_if_match(
                self.container_name, suggestion_id, guild_id, operations, etag=etag
            )
            return self._validate(saved)

        return await retry_async(_claim, category=RetryCategory.ETag_RMW)

    async def mark_sent(
        self, guild_id: str, suggestion_id: str, *, claimed_by: str
    ) -> SuggestionDocument:
        return await self._patch_notification(
            guild_id,
            suggestion_id,
            claimed_by=claimed_by,
            operations=[
                {"op": "set", "path": "/notification_status", "value": "sent"},
                {"op": "set", "path": "/updated_at", "value": utc_now()},
            ],
        )

    async def mark_failed(
        self,
        guild_id: str,
        suggestion_id: str,
        error: str,
        *,
        claimed_by: str,
        requeue: bool,
    ) -> SuggestionDocument:
        current = await self.get(guild_id, suggestion_id)
        return await self._patch_notification(
            guild_id,
            suggestion_id,
            claimed_by=claimed_by,
            operations=[
                {"op": "set", "path": "/notification_last_error", "value": redact_sensitive(error)},
                {
                    "op": "set",
                    "path": "/notification_attempts",
                    "value": current.notification_attempts + 1,
                },
                {
                    "op": "set",
                    "path": "/notification_status",
                    "value": "pending" if requeue else "failed",
                },
                {"op": "set", "path": "/updated_at", "value": utc_now()},
            ],
        )

    async def _patch_notification(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        claimed_by: str,
        operations: PatchOperations,
    ) -> SuggestionDocument:
        async def _mutate() -> SuggestionDocument:
            current_raw = await self.cosmos_client.point_read(
                self.container_name, suggestion_id, guild_id
            )
            current = self._validate(current_raw)
            if current.notification_status != "claiming":
                raise AzurePermanentError("invalid notification transition")
            if current.notification_claimed_by != claimed_by:
                raise AzurePermanentError("notification claim mismatch")
            etag = str(current_raw.get("_etag", ""))
            saved = await self.cosmos_client.patch_if_match(
                self.container_name, suggestion_id, guild_id, operations, etag=etag
            )
            return self._validate(saved)

        return await retry_async(_mutate, category=RetryCategory.ETag_RMW)
