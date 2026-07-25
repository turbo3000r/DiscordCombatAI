"""Guild admission predicate for production vs development isolation."""

from __future__ import annotations

from shared.runtime.settings import RuntimeMode


def is_managed_guild(
    *,
    mode: RuntimeMode | str,
    guild_id: str,
    development_guild_id: str,
) -> bool:
    """Return whether the Bot may process lifecycle/interactions for this guild."""
    resolved = RuntimeMode(mode) if not isinstance(mode, RuntimeMode) else mode
    if resolved is RuntimeMode.development:
        return guild_id == development_guild_id
    # production: reject the reserved development guild
    return guild_id != development_guild_id


__all__ = ["is_managed_guild"]
