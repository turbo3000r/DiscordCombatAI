"""Slash command registration for Phase 3 commands."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import app_commands

from bot.modules.commands.context import CommandContext
from bot.modules.commands.process_command import (
    CommandCallback,
    CommandGuardService,
    ProcessCommand,
)

logger = logging.getLogger(__name__)

ConfigHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]
SuggestHandler = Callable[[discord.Interaction, CommandContext], Awaitable[None]]


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


def register_phase3_commands(
    tree: app_commands.CommandTree[Any],
    guard: CommandGuardService,
    *,
    config_handler: ConfigHandler | None = None,
    suggest_handler: SuggestHandler | None = None,
) -> None:
    """Register exactly `/config` and `/suggest` (no `/quick-battle`)."""
    on_config = config_handler or _stub_config
    on_suggest = suggest_handler or _stub_suggest

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
    logger.info("registered phase3 commands: config, suggest")


__all__ = ["register_phase3_commands"]
