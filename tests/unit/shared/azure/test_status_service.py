from __future__ import annotations

import json

import pytest

from shared.azure.services.status import StatusService


class _BlobClient:
    def __init__(self) -> None:
        self.payload = None
        self.etag = "etag-1"
        self.write_calls = 0

    async def read_text(self, container_name: str, blob_name: str):
        if self.payload is None:
            raise RuntimeError("404 not found")
        return self.payload, self.etag

    async def write_text(
        self, container_name: str, blob_name: str, payload: str, *, etag: str | None = None
    ):
        self.write_calls += 1
        if self.write_calls == 1 and etag == "etag-1":
            raise RuntimeError("412 precondition failed")
        self.payload = payload
        self.etag = "etag-2"


@pytest.mark.asyncio()
async def test_status_service_etag_conflict_retries() -> None:
    blob_client = _BlobClient()
    service = StatusService(
        blob_client=blob_client, container_name="coordination", blob_name="bot_status.json"
    )
    seed = await service.ensure_seeded()
    assert seed.status.guild_count == 0

    blob_client.payload = seed.model_dump_json()
    updated = await service.update_status(
        {"latency_ms": 15, "guild_count": 7, "updated_at": "2026-07-18T00:00:00Z"}
    )
    assert updated.guild_count == 7
    assert json.loads(blob_client.payload)["status"]["guild_count"] == 7
