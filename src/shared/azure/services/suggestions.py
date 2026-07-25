from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from shared.azure._helpers import utc_now
from shared.azure.clients.cosmos import CosmosClient
from shared.azure.clients.queue import QueueClient
from shared.azure.errors import AzurePermanentError, AzureTransientError, classify_azure_error
from shared.models import SuggestionDocument, SuggestionQueueMessage
from shared.models.suggestion import generate_ticket_uid
from shared.security.redact import redact_sensitive
from shared.utils.retry import RetryCategory, retry_async

PatchOperations = builtins.list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SuggestionRecord:
    document: SuggestionDocument
    etag: str


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

    def _record(self, document: dict[str, Any]) -> SuggestionRecord:
        return SuggestionRecord(
            document=self._validate(document),
            etag=str(document.get("_etag", "")),
        )

    def _safe_validate(self, document: dict[str, Any]) -> SuggestionDocument | None:
        try:
            return self._validate(document)
        except AzurePermanentError:
            return None

    async def create(self, payload: dict[str, Any]) -> SuggestionDocument:
        now = utc_now()
        data = dict(payload)
        conversation = data.pop("conversation", [])
        ticket_id = str(data.pop("id", None) or uuid4())
        ticket_uid = str(data.pop("ticket_uid", None) or generate_ticket_uid())
        data.pop("schema_version", None)
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
        try:
            saved_raw = await self.cosmos_client.create_item(
                self.container_name, document.model_dump(mode="python")
            )
        except Exception as exc:
            classified = classify_azure_error(exc, operation="suggestion.create")
            if classified.status_code == 409 or "conflict" in str(classified).lower():
                raise AzurePermanentError(
                    "suggestion already exists",
                    operation="suggestion.create",
                    status_code=409,
                ) from exc
            raise classified from exc
        saved = self._validate(saved_raw)
        if saved.notification_status == "pending":
            await self.enqueue(saved)
        return saved

    async def get(self, guild_id: str, suggestion_id: str) -> SuggestionDocument:
        document = await self.cosmos_client.point_read(self.container_name, suggestion_id, guild_id)
        return self._validate(document)

    async def get_with_etag(self, guild_id: str, suggestion_id: str) -> SuggestionRecord:
        document = await self.cosmos_client.point_read(self.container_name, suggestion_id, guild_id)
        return self._record(document)

    async def get_by_id(self, suggestion_id: str) -> SuggestionRecord:
        query = "SELECT * FROM c WHERE c.id = @id"
        documents = await self.cosmos_client.query(
            self.container_name, query, [{"name": "@id", "value": suggestion_id}]
        )
        for document in documents:
            record = self._safe_validate(document)
            if record is not None:
                return SuggestionRecord(document=record, etag=str(document.get("_etag", "")))
        raise AzurePermanentError("suggestion not found", status_code=404)

    async def list(self, guild_id: str) -> builtins.list[SuggestionDocument]:
        query = "SELECT * FROM c WHERE c.guild_id = @guild_id"
        documents = await self.cosmos_client.query(
            self.container_name, query, [{"name": "@guild_id", "value": guild_id}]
        )
        results: builtins.list[SuggestionDocument] = []
        for document in documents:
            model = self._safe_validate(document)
            if model is not None:
                results.append(model)
        return results

    async def list_all(self) -> builtins.list[SuggestionDocument]:
        documents = await self.cosmos_client.query(self.container_name, "SELECT * FROM c")
        results: builtins.list[SuggestionDocument] = []
        for document in documents:
            model = self._safe_validate(document)
            if model is not None:
                results.append(model)
        return results

    async def patch_respond(
        self,
        guild_id: str,
        suggestion_id: str,
        *,
        etag: str,
        operations: PatchOperations,
    ) -> SuggestionRecord:
        try:
            saved = await self.cosmos_client.patch_if_match(
                self.container_name, suggestion_id, guild_id, operations, etag=etag
            )
        except Exception as exc:
            classified = classify_azure_error(exc, operation="suggestion.patch_respond")
            if classified.status_code == 412:
                raise AzureTransientError(
                    "suggestion etag conflict",
                    operation="suggestion.patch_respond",
                    status_code=412,
                ) from exc
            raise classified from exc
        return self._record(saved)

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
            operations: builtins.list[dict[str, Any]] = [
                {"op": "set", "path": "/notification_status", "value": "claiming"},
                {"op": "set", "path": "/notification_claimed_at", "value": utc_now()},
                {"op": "set", "path": "/notification_claimed_by", "value": claimed_by},
                {"op": "set", "path": "/updated_at", "value": utc_now()},
            ]
            try:
                saved = await self.cosmos_client.patch_if_match(
                    self.container_name, suggestion_id, guild_id, operations, etag=etag
                )
            except Exception as exc:
                classified = classify_azure_error(exc, operation="suggestion.claim")
                if classified.status_code == 412:
                    return None
                raise classified from exc
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
                {"op": "set", "path": "/notification_claimed_at", "value": None},
                {"op": "set", "path": "/notification_claimed_by", "value": None},
                {"op": "set", "path": "/notification_last_error", "value": None},
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
                {"op": "set", "path": "/notification_claimed_at", "value": None},
                {"op": "set", "path": "/notification_claimed_by", "value": None},
                {"op": "set", "path": "/updated_at", "value": utc_now()},
            ],
        )

    async def list_pending_for_sweep(
        self, *, min_age_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=min_age_sec)
        cutoff_iso = cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z")
        query = (
            "SELECT * FROM c WHERE c.notification_status = @status "
            "AND c.updated_at <= @cutoff"
        )
        documents = await self.cosmos_client.query(
            self.container_name,
            query,
            [
                {"name": "@status", "value": "pending"},
                {"name": "@cutoff", "value": cutoff_iso},
            ],
        )
        results: builtins.list[SuggestionRecord] = []
        for document in documents:
            model = self._safe_validate(document)
            if model is not None:
                results.append(
                    SuggestionRecord(document=model, etag=str(document.get("_etag", "")))
                )
        return results

    async def list_expired_claims(
        self, *, claim_timeout_sec: float, now: datetime | None = None
    ) -> builtins.list[SuggestionRecord]:
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=claim_timeout_sec)
        cutoff_iso = cutoff.astimezone(UTC).isoformat().replace("+00:00", "Z")
        query = (
            "SELECT * FROM c WHERE c.notification_status = @status "
            "AND c.notification_claimed_at <= @cutoff"
        )
        documents = await self.cosmos_client.query(
            self.container_name,
            query,
            [
                {"name": "@status", "value": "claiming"},
                {"name": "@cutoff", "value": cutoff_iso},
            ],
        )
        results: builtins.list[SuggestionRecord] = []
        for document in documents:
            model = self._safe_validate(document)
            if model is not None:
                results.append(
                    SuggestionRecord(document=model, etag=str(document.get("_etag", "")))
                )
        return results

    async def reset_expired_claim(
        self, guild_id: str, suggestion_id: str, *, etag: str
    ) -> SuggestionDocument | None:
        operations: PatchOperations = [
            {"op": "set", "path": "/notification_status", "value": "pending"},
            {"op": "set", "path": "/notification_claimed_at", "value": None},
            {"op": "set", "path": "/notification_claimed_by", "value": None},
            {"op": "set", "path": "/updated_at", "value": utc_now()},
        ]
        try:
            saved = await self.cosmos_client.patch_if_match(
                self.container_name, suggestion_id, guild_id, operations, etag=etag
            )
        except Exception as exc:
            classified = classify_azure_error(exc, operation="suggestion.reset_claim")
            if classified.status_code == 412:
                return None
            raise classified from exc
        return self._validate(saved)

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


__all__ = ["SuggestionRecord", "SuggestionService"]
