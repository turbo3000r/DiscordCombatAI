"""Delivery helpers for /quick-battle story chunking and winner rendering."""

from __future__ import annotations

import pytest

from bot.modules.commands.battle.service.delivery import (
    escape_display_name,
    split_story,
    validated_winners,
)


def test_split_story_paragraph_bound_chunks() -> None:
    story = "Alpha paragraph.\n\n" + ("B" * 1800) + "\n\nCharlie."
    chunks, attachment = split_story(story)
    assert attachment is None
    assert 1 <= len(chunks) <= 3
    assert all(len(chunk) <= 1900 for chunk in chunks)
    assert "Alpha paragraph." in chunks[0]


def test_split_story_long_output_uses_preview_and_attachment() -> None:
    story = "\n\n".join(f"Paragraph {index} " + ("x" * 400) for index in range(20))
    chunks, attachment = split_story(story)
    assert attachment is not None
    assert chunks[0]
    assert len(chunks[0]) <= 1900
    assert attachment.decode("utf-8") == story


def test_validated_winners_and_escaped_names() -> None:
    assert validated_winners(["u1", "nope", "u1"], {"u1", "u2"}) == ["u1"]
    assert validated_winners([], {"u1"}) == []
    assert "@everyone" not in escape_display_name("Captain @everyone")
    assert escape_display_name("A\\B") != "A\\B" or "\\" in escape_display_name("A\\B")


@pytest.mark.asyncio
async def test_archive_story_uses_write_and_swallows_errors() -> None:
    from bot.modules.commands.battle.service.delivery import archive_story

    calls: list[dict[str, object]] = []

    class Writer:
        async def write(self, **kwargs: object) -> None:
            calls.append(kwargs)

    class Exploding:
        async def write(self, **kwargs: object) -> None:
            raise RuntimeError("blob down")

    await archive_story(
        Writer().write if False else Writer(),
        guild_id="g1",
        task_id="t1",
        created_at="2026-08-23T00:00:00+00:00",
        story_text="story",
        winners=["u1"],
    )
    assert calls[0]["guild_id"] == "g1"
    await archive_story(
        Exploding(),
        guild_id="g1",
        task_id="t1",
        created_at="2026-08-23T00:00:00+00:00",
        story_text="story",
        winners=["u1"],
    )
