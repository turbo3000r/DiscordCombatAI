"""Battle story chunking, winner rendering, and best-effort archive."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

MAX_CHUNK = 1900
MAX_MULTI_TOTAL = 5700

ArchiveWriter = Callable[..., Awaitable[Any]]


def paragraph_chunks(story: str, limit: int = MAX_CHUNK) -> list[str]:
    text = (story or "").strip()
    if not text:
        return [""]
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = (
            [paragraph]
            if len(paragraph) <= limit
            else [paragraph[offset : offset + limit] for offset in range(0, len(paragraph), limit)]
        )
        for piece in pieces:
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def split_story(story: str) -> tuple[list[str], bytes | None]:
    chunks = paragraph_chunks(story)
    total = sum(len(chunk) for chunk in chunks)
    if total <= MAX_MULTI_TOTAL and len(chunks) <= 3:
        return chunks, None
    preview = paragraph_chunks(story, MAX_CHUNK)[:1]
    return preview, story.encode("utf-8")


def validated_winners(raw: object, roster_ids: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    winners: list[str] = []
    for item in raw:
        value = str(item)
        if value in roster_ids and value not in winners:
            winners.append(value)
    return winners


def escape_display_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace("@", "@\u200b")


async def archive_story(
    writer: ArchiveWriter | None,
    *,
    guild_id: str,
    task_id: str,
    created_at: str,
    story_text: str,
    winners: list[str],
) -> None:
    if writer is None:
        return
    try:
        submit = getattr(writer, "write", writer)
        await submit(
            guild_id=guild_id,
            task_id=task_id,
            created_at=created_at,
            story_text=story_text,
            winners=winners,
        )
    except Exception:
        return


__all__ = [
    "MAX_CHUNK",
    "MAX_MULTI_TOTAL",
    "archive_story",
    "escape_display_name",
    "paragraph_chunks",
    "split_story",
    "validated_winners",
]
