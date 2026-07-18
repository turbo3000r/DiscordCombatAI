from __future__ import annotations

from datetime import UTC, datetime

from .base import SchemaVersionedModel, SnowflakeString, UTCDateTime, UUIDString


class BattleArchiveMetadata(SchemaVersionedModel):
    schema_version: int = 1
    task_id: UUIDString
    guild_id: SnowflakeString
    graph: str
    winners: list[str]
    created_at: UTCDateTime
    content_type: str = "text/plain; charset=utf-8"


def build_battle_archive_paths(
    guild_id: str, created_at: datetime, task_id: str
) -> tuple[str, str]:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    stamp = created_at.astimezone(UTC)
    prefix = f"{guild_id}/{stamp:%Y/%m}/{task_id}"
    return f"{prefix}.txt", f"{prefix}.meta.json"


__all__ = ["BattleArchiveMetadata", "build_battle_archive_paths"]
