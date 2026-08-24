"""Load Bot-packaged generic arenas for /quick-battle."""

from __future__ import annotations

from pathlib import Path

MAX_ARENA_CHARS = 4000


def generic_arena_root() -> Path:
    return Path(__file__).resolve().parents[4] / "resources" / "generic_environments"


def load_generic_arenas(root: Path | None = None) -> list[tuple[str, str]]:
    directory = root or generic_arena_root()
    arenas: list[tuple[str, str]] = []
    if not directory.is_dir():
        raise FileNotFoundError(f"generic arena directory missing: {directory}")
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        if not text or len(text) > MAX_ARENA_CHARS:
            raise ValueError(
                f"generic arena {path.name} is empty or exceeds {MAX_ARENA_CHARS} characters"
            )
        arenas.append((path.stem, text))
    if not arenas:
        raise ValueError("no generic arenas packaged")
    return arenas


def pick_generic_arena(
    *,
    setting: str,
    index: int | None = None,
    root: Path | None = None,
) -> dict[str, object]:
    arenas = load_generic_arenas(root)
    stem, description = arenas[(index or 0) % len(arenas)]
    return {
        "description": description,
        "tags": ["generic", stem],
        "setting": setting,
    }


__all__ = ["generic_arena_root", "load_generic_arenas", "pick_generic_arena"]
