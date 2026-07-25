"""Interaction guild admission for future slash commands."""

from __future__ import annotations

from typing import Any

from bot.modules.services.guild_guard import is_managed_guild
from shared.runtime.settings import RuntimeMode


def interaction_allowed(
    interaction: Any,
    *,
    mode: RuntimeMode | str,
    development_guild_id: str,
) -> bool:
    guild_id = getattr(interaction, "guild_id", None)
    if guild_id is None:
        # DMs / no-guild rejected in development; production may allow later.
        return RuntimeMode(mode) is RuntimeMode.production
    return is_managed_guild(
        mode=mode,
        guild_id=str(guild_id),
        development_guild_id=development_guild_id,
    )


__all__ = ["interaction_allowed"]
