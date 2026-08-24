"""Slash command registration for Phase 3 commands."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import app_commands

from bot.modules.commands.battle.models import SETTINGS
from bot.modules.commands.context import CommandContext
from bot.modules.commands.process_command import (
    CommandCallback,
    CommandGuardService,
    ProcessCommand,
)

logger = logging.getLogger(__name__)

ConfigHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]
SuggestHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]
QuickBattleHandler = Callable[..., Awaitable[None]]


async def _stub_config(interaction: discord.Interaction, ctx: CommandContext) -> None:
    if not interaction.response.is_done():
        await interaction.response.send_message(
            ctx.t("errors.error_unexpected"),
            ephemeral=True,
        )


async def _stub_suggest(interaction: discord.Interaction, ctx: CommandContext) -> None:
    if not interaction.response.is_done():
        await interaction.response.send_message(
            ctx.t("errors.error_unexpected"),
            ephemeral=True,
        )


async def _stub_quick_battle(
    interaction: discord.Interaction,
    ctx: CommandContext,
    custom_environment: int,
    timeout: int = 60,
    setting: str = "unpredictable-funny",
) -> None:
    if not interaction.response.is_done():
        await interaction.response.send_message(
            ctx.t("errors.error_unexpected"),
            ephemeral=True,
        )


def register_phase3_commands(
    tree: app_commands.CommandTree[Any],
    guard: CommandGuardService,
    *,
    config_handler: ConfigHandler | None = None,
    suggest_handler: SuggestHandler | None = None,
    quick_battle_handler: QuickBattleHandler | None = None,
) -> None:
    """Register `/config`, `/suggest`, and `/quick-battle`."""
    on_config = config_handler or _stub_config
    on_suggest = suggest_handler or _stub_suggest
    on_quick_battle = quick_battle_handler or _stub_quick_battle

    async def config_body(interaction: discord.Interaction, ctx: CommandContext) -> None:
        await on_config(interaction, ctx)

    async def suggest_body(interaction: discord.Interaction, ctx: CommandContext) -> None:
        await on_suggest(interaction, ctx)

    config_cb: CommandCallback = ProcessCommand(
        guard,
        required_guild=True,
        required_guild_enabled=False,
        allowed_permissions=discord.Permissions(administrator=True),
        blocked_during_drain=False,
        load_guild_config=True,
    )(config_body)
    suggest_cb: CommandCallback = ProcessCommand(
        guard,
        required_guild=False,
        required_guild_enabled=False,
        allowed_permissions=None,
        blocked_during_drain=False,
        load_guild_config=True,
    )(suggest_body)

    tree.command(name="config", description="Configure the bot for this server")(config_cb)  # type: ignore[arg-type]
    tree.command(
        name="suggest",
        description="Suggest a feature or report a bug",
    )(suggest_cb)  # type: ignore[arg-type]

    async def quick_battle_body(
        interaction: discord.Interaction,
        ctx: CommandContext,
        custom_environment: app_commands.Range[int, 0, 1],
        timeout: app_commands.Range[int, 30, 600] = 60,
        setting: str = "unpredictable-funny",
    ) -> None:
        await on_quick_battle(interaction, ctx, custom_environment, timeout, setting)

    quick_battle_cb: CommandCallback = ProcessCommand(
        guard,
        required_guild=True,
        required_guild_enabled=True,
        allowed_permissions=None,
        blocked_during_drain=True,
        load_guild_config=True,
    )(quick_battle_body)
    quick_battle_cb = app_commands.describe(
        custom_environment="0 = generic arena, 1 = custom environment",
        timeout="Lobby countdown in seconds",
        setting="Battle setting",
    )(quick_battle_cb)
    quick_battle_cb = app_commands.choices(
        setting=[app_commands.Choice(name=value, value=value) for value in SETTINGS]
    )(quick_battle_cb)
    tree.command(name="quick-battle", description="Start a quick battle lobby")(quick_battle_cb)  # type: ignore[arg-type]
    logger.info("registered phase3 commands: config, suggest, quick-battle")


__all__ = ["register_phase3_commands"]
