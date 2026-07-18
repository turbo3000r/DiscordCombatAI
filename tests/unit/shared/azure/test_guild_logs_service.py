from __future__ import annotations

import pytest

from shared.azure.services.guild_logs import GuildLogArchiveService


class _BlobClient:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    async def write_text(
        self, container_name: str, blob_name: str, payload: str, *, etag: str | None = None
    ) -> None:
        self.writes.append((container_name, blob_name, payload))


@pytest.mark.asyncio()
async def test_guild_log_paths_and_write() -> None:
    blob = _BlobClient()
    service = GuildLogArchiveService(blob_client=blob, container_name="battle-results")

    story_path, meta_path = await service.write(
        guild_id="123456789012345678",
        task_id="550e8400-e29b-41d4-a716-446655440001",
        created_at="2026-07-18T12:56:30Z",
        story_text="story",
        winners=["user-1"],
    )
    assert story_path == "123456789012345678/2026/07/550e8400-e29b-41d4-a716-446655440001.txt"
    assert meta_path == "123456789012345678/2026/07/550e8400-e29b-41d4-a716-446655440001.meta.json"
    assert len(blob.writes) == 2
