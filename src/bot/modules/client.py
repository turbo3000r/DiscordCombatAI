"""discord.py Bot subclass for DiscordCombatAI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import discord
from discord.ext import commands

from bot.localization.handler import LocalizationHandler, load_localization
from bot.modules.commands.context import CommandContext
from bot.modules.commands.process_command import CommandGuardService
from bot.modules.commands.registration import register_phase3_commands
from shared.runtime.settings import RuntimeMode
from shared.storage.protocols import GuildRepository, StatusRepository, SuggestionRepository

logger = logging.getLogger(__name__)

ConfigHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]
SuggestHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    return intents


class CombatBot(commands.Bot):
    """Phase 3 Bot: guild intent + /config and /suggest registration."""

    def __init__(
        self,
        *,
        command_prefix: str = "!",
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
        development_guild_id: str | None = None,
        expected_application_id: str | None = None,
        l10n: LocalizationHandler | None = None,
        guild_repository: GuildRepository | None = None,
        suggestion_repository: SuggestionRepository | None = None,
        status_repository: StatusRepository | None = None,
        draining_provider: Callable[[], bool] | None = None,
        config_handler: ConfigHandler | None = None,
        suggest_handler: SuggestHandler | None = None,
        register_commands: bool = True,
    ) -> None:
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(),
            help_command=None,
        )
        self.runtime_mode = RuntimeMode(runtime_mode)
        self.development_guild_id = development_guild_id
        self.expected_application_id = expected_application_id
        self.l10n = l10n or load_localization()
        self.guild_repository = guild_repository
        self.suggestion_repository = suggestion_repository
        self.status_repository = status_repository
        self._draining_provider = draining_provider or (lambda: False)
        self.guard = CommandGuardService(
            runtime_mode=self.runtime_mode,
            development_guild_id=development_guild_id or "",
            l10n=self.l10n,
            guild_repository=guild_repository,
            suggestion_repository=suggestion_repository,
            status_repository=status_repository,
            draining_provider=self._draining_provider,
        )
        self._tree_synced = False
        if register_commands:
            register_phase3_commands(
                self.tree,
                self.guard,
                config_handler=config_handler,
                suggest_handler=suggest_handler,
            )

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
    l10n: LocalizationHandler | None = None,
    guild_repository: GuildRepository | None = None,
    suggestion_repository: SuggestionRepository | None = None,
    status_repository: StatusRepository | None = None,
    draining_provider: Callable[[], bool] | None = None,
    config_handler: ConfigHandler | None = None,
    suggest_handler: SuggestHandler | None = None,
    register_commands: bool = True,
) -> CombatBot:
    return CombatBot(
        runtime_mode=runtime_mode,
        development_guild_id=development_guild_id,
        expected_application_id=expected_application_id,
        l10n=l10n,
        guild_repository=guild_repository,
        suggestion_repository=suggestion_repository,
        status_repository=status_repository,
        draining_provider=draining_provider,
        config_handler=config_handler,
        suggest_handler=suggest_handler,
        register_commands=register_commands,
    )


__all__ = ["CombatBot", "build_intents", "create_bot"]
