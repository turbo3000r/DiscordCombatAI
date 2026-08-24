"""Bot-packaged generic arena loader."""

from __future__ import annotations

from bot.modules.commands.battle.service.generic_arenas import (
    generic_arena_root,
    load_generic_arenas,
    pick_generic_arena,
)


def test_packaged_generic_arenas_are_bounded_utf8() -> None:
    arenas = load_generic_arenas()
    assert len(arenas) >= 2
    for stem, text in arenas:
        assert stem
        assert text
        assert len(text) <= 4000
    picked = pick_generic_arena(setting="unpredictable-funny", index=0)
    assert picked["setting"] == "unpredictable-funny"
    assert picked["tags"][0] == "generic"
    assert generic_arena_root().is_dir()
