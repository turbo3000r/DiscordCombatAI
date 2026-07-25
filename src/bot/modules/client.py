"""discord.py Bot subclass for DiscordCombatAI."""

from __future__ import annotations

import discord
from discord.ext import commands


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    return intents


class CombatBot(commands.Bot):
    """Phase 2 Bot: guild intent only, no user-facing commands."""

    def __init__(self, *, command_prefix: str = "!") -> None:
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(),
            help_command=None,
        )
        self._tree_synced = False

    async def setup_hook(self) -> None:
        # No slash commands in Phase 2; still sync an empty tree for readiness.
        await self.tree.sync()
        self._tree_synced = True

    async def on_ready(self) -> None:
        return None


def create_bot() -> CombatBot:
    return CombatBot()


__all__ = ["CombatBot", "build_intents", "create_bot"]
