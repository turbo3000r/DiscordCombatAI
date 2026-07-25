"""Interaction guild admission helpers for slash commands and components."""

from __future__ import annotations

from typing import Any

from bot.modules.services.guild_guard import is_managed_guild
from shared.runtime.settings import RuntimeMode


def interaction_allowed(
    interaction: Any,
    *,
    mode: RuntimeMode | str,
    development_guild_id: str,
    allow_dm_in_production: bool = True,
) -> bool:
    guild_id = getattr(interaction, "guild_id", None)
    if guild_id is None:
        if RuntimeMode(mode) is RuntimeMode.development:
            return False
        return allow_dm_in_production
    return is_managed_guild(
        mode=mode,
        guild_id=str(guild_id),
        development_guild_id=development_guild_id,
    )


__all__ = ["interaction_allowed"]
