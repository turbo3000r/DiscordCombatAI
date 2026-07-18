from __future__ import annotations

import pytest

from shared.azure.services.logging import LogArchiveService


class _BlobClient:
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str]] = []

    async def append_text(self, container_name: str, blob_name: str, payload: str) -> None:
        self.appended.append((container_name, blob_name, payload))


@pytest.mark.asyncio()
async def test_logging_buffer_drops_oldest_and_redacts() -> None:
    blob = _BlobClient()
    service = LogArchiveService(blob_client=blob, container_name="service-logs")

    for i in range(10_005):
        service.buffer_lines([f"line-{i}"])

    assert service.buffer.lines[0] == "line-5"
    service.buffer_lines(["api_key=super-secret webhook_url=https://discord.com/api/webhooks/1/2"])
    assert "super-secret" not in service.buffer.lines[-1]
    assert "[REDACTED]" in service.buffer.lines[-1]

    path = await service.flush(node_id="node-a", timestamp_iso="2026-07-18T12:56:30Z")
    assert path == "logs/node-a/2026/07/18.log"
    assert blob.appended[0][1] == path
    assert "super-secret" not in blob.appended[0][2]