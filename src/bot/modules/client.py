"""discord.py Bot subclass for DiscordCombatAI."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from shared.runtime.settings import RuntimeMode

logger = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    return intents


class CombatBot(commands.Bot):
    """Phase 2 Bot: guild intent only, no user-facing commands."""

    def __init__(
        self,
        *,
        command_prefix: str = "!",
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
        development_guild_id: str | None = None,
        expected_application_id: str | None = None,
    ) -> None:
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(),
            help_command=None,
        )
        self.runtime_mode = RuntimeMode(runtime_mode)
        self.development_guild_id = development_guild_id
        self.expected_application_id = expected_application_id
        self._tree_synced = False

    async def setup_hook(self) -> None:
        if self.runtime_mode is RuntimeMode.development:
            if not self.expected_application_id:
                raise RuntimeError(
                    "DISCORD_DEVELOPMENT_APPLICATION_ID is required before sync in development"
                )
            # application_id is populated after login; setup_hook runs post-login.
            authenticated = str(self.application_id) if self.application_id is not None else None
            if authenticated != self.expected_application_id:
                raise RuntimeError(
                    "authenticated Discord application id "
                    f"{authenticated!r} does not match "
                    f"DISCORD_DEVELOPMENT_APPLICATION_ID={self.expected_application_id!r}"
                )
            if not self.development_guild_id:
                raise RuntimeError("DISCORD_DEVELOPMENT_GUILD_ID required for guild-scoped sync")
            guild = discord.Object(id=int(self.development_guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("guild-scoped command sync complete for %s", self.development_guild_id)
        else:
            await self.tree.sync()
            logger.info("global command sync complete")
        self._tree_synced = True

    async def on_ready(self) -> None:
        return None


def create_bot(
    *,
    runtime_mode: RuntimeMode | str = RuntimeMode.production,
    development_guild_id: str | None = None,
    expected_application_id: str | None = None,
) -> CombatBot:
    return CombatBot(
        runtime_mode=runtime_mode,
        development_guild_id=development_guild_id,
        expected_application_id=expected_application_id,
    )


__all__ = ["CombatBot", "build_intents", "create_bot"]
