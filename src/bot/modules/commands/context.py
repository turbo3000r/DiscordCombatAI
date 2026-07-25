"""Typed command context for ProcessCommand-wrapped callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from bot.localization.handler import LocalizationHandler
from shared.models.guild_config import GuildConfigDocument
from shared.runtime.settings import RuntimeMode
from shared.storage.protocols import GuildRepository, StatusRepository, SuggestionRepository


@dataclass(frozen=True, slots=True)
class CommandContext:
    interaction: discord.Interaction
    runtime_mode: RuntimeMode
    locale_key: str
    guild_id: str | None
    user_id: str
    command: str
    interaction_id: str
    draining: bool
    l10n: LocalizationHandler
    guild_config: GuildConfigDocument | None = None
    guild_repository: GuildRepository | None = None
    suggestion_repository: SuggestionRepository | None = None
    status_repository: StatusRepository | None = None

    def t(self, key: str, **variables: Any) -> str:
        return self.l10n.t(key, locale=self.locale_key, **variables)

    def __repr__(self) -> str:
        return (
            "CommandContext("
            f"runtime_mode={self.runtime_mode.value!r}, "
            f"locale_key={self.locale_key!r}, "
            f"guild_id={self.guild_id!r}, "
            f"user_id={self.user_id!r}, "
            f"command={self.command!r}, "
            f"interaction_id={self.interaction_id!r}, "
            f"draining={self.draining!r}, "
            f"has_guild_config={self.guild_config is not None}"
            ")"
        )


__all__ = ["CommandContext"]
