from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from shared.azure._helpers import utc_now
from shared.azure.clients.blob import BlobClient
from shared.azure.errors import AzurePermanentError
from shared.models.status_document import StatusDocument, build_status_seed
from shared.utils.retry import RetryCategory, retry_async


class StatusService:
    def __init__(self, *, blob_client: BlobClient, container_name: str, blob_name: str) -> None:
        self.blob_client = blob_client
        self.container_name = container_name
        self.blob_name = blob_name

    def _seed(self) -> StatusDocument:
        return build_status_seed(seeded_at=utc_now())

    def _load_document(self, payload: str) -> StatusDocument:
        try:
            data = json.loads(payload)
            document = StatusDocument.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AzurePermanentError("malformed status document") from exc
        if document.schema_version != 1:
            raise AzurePermanentError("unsupported status document schema_version")
        return document

    async def _read_document(self) -> tuple[StatusDocument | None, str | None]:
        try:
            payload, etag = await self.blob_client.read_text(self.container_name, self.blob_name)
        except Exception as exc:
            message = str(exc).lower()
            if "not found" in message or "404" in message:
                return None, None
            raise
        return self._load_document(payload), etag

    async def ensure_seeded(self) -> StatusDocument:
        document, etag = await self._read_document()
        if document is not None:
            return document
        seed = self._seed()
        await self.blob_client.write_text(
            self.container_name, self.blob_name, seed.model_dump_json(), etag=etag
        )
        return seed

    async def get_identity(self) -> Any:
        document = await self.ensure_seeded()
        return document.identity

    async def get_status(self) -> Any:
        document = await self.ensure_seeded()
        return document.status

    async def get_suggestion_catalog(self) -> Any:
        document = await self.ensure_seeded()
        return document.suggestion_catalog

    async def _update_section(self, section: str, value: Any) -> StatusDocument:
        async def _mutate() -> StatusDocument:
            document, etag = await self._read_document()
            if document is None:
                document = self._seed()
                etag = None
            payload_data = document.model_dump(mode="python")
            payload_data[section] = (
                value.model_dump(mode="python") if hasattr(value, "model_dump") else value
            )
            document = StatusDocument.model_validate(payload_data)
            payload = document.model_dump_json()
            await self.blob_client.write_text(
                self.container_name, self.blob_name, payload, etag=etag
            )
            return document

        return await retry_async(_mutate, category=RetryCategory.ETag_RMW)

    async def update_identity(
        self, identity: dict[str, Any], *, actor_oid: str | None = None
    ) -> Any:
        document = await self._update_section("identity", identity)
        return document.identity

    async def update_status(self, status: dict[str, Any]) -> Any:
        document = await self._update_section("status", status)
        return document.status

    async def update_suggestion_catalog(self, suggestion_catalog: dict[str, Any]) -> Any:
        document = await self._update_section("suggestion_catalog", suggestion_catalog)
        return document.suggestion_catalog
