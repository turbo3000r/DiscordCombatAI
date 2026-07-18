from __future__ import annotations

from shared.azure.clients.blob import BlobClient
from shared.models import BattleArchiveMetadata, build_battle_archive_paths, parse_utc_datetime


class GuildLogArchiveService:
    def __init__(self, *, blob_client: BlobClient, container_name: str) -> None:
        self.blob_client = blob_client
        self.container_name = container_name

    @staticmethod
    def paths(*, guild_id: str, created_at: str, task_id: str) -> tuple[str, str]:
        return build_battle_archive_paths(guild_id, parse_utc_datetime(created_at), task_id)

    async def write(
        self, *, guild_id: str, task_id: str, created_at: str, story_text: str, winners: list[str]
    ) -> tuple[str, str]:
        story_path, meta_path = self.paths(
            guild_id=guild_id, created_at=created_at, task_id=task_id
        )
        created_dt = parse_utc_datetime(created_at)
        metadata = BattleArchiveMetadata(
            task_id=task_id,
            guild_id=guild_id,
            graph="battle",
            winners=winners,
            created_at=created_dt,
        )
        await self.blob_client.write_text(self.container_name, story_path, story_text)
        await self.blob_client.write_text(
            self.container_name, meta_path, metadata.model_dump_json()
        )
        return story_path, meta_path
