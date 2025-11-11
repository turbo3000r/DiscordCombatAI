from functools import wraps
import inspect
import json
import os
from typing import Any, Callable

import discord
from discord.flags import flag_value
from modules.guild import Guild
from modules.LoggerHandler import get_logger

logger = get_logger()

def setup_guild(guild_id: int):
    if not os.path.exists(f"guilds/{guild_id}"):
        os.makedirs(f"guilds/{guild_id}")
        with open(f"guilds/{guild_id}/config.json", "w") as f:
            json.dump({"enabled": False, "language": "en", "api_key": "", "model": "gemini-2.5-flash"}, f)


def load_guilds(bot: discord.ext.commands.Bot) -> dict:
    guilds = {}
    for id in os.listdir("guilds"):
        g = Guild(bot.get_guild(int(id)))
        guilds[int(id)] = g
    return guilds



def ProcessCommand(allowed_permissions: list = None):
    """
    Decorator to process Discord slash commands with logging and permission checking.
    
    Args:
        allowed_permissions: List of discord.Permissions flags that are required to run the command.
                           If None or empty, no permission check is performed.
    
    Usage:
        # Simple usage - function only needs interaction parameter
        @bot.tree.command(name="command")
        @ProcessCommand()
        async def command(interaction: discord.Interaction):
            guild = Guild(interaction.guild)  # Create Guild wrapper if needed
            ...
        
        # With permissions
        @bot.tree.command(name="admin_command")
        @ProcessCommand(allowed_permissions=[discord.Permissions.administrator])
        async def admin_command(interaction: discord.Interaction):
            ...
    
    Note: Discord.py doesn't support custom type annotations in slash command signatures.
    Functions should only have `interaction: discord.Interaction` in their signature.
    The decorator will inject guild and member at runtime if needed, but access them
    through the interaction object or create Guild wrapper manually.
    """
    if allowed_permissions is None:
        allowed_permissions = []
    
    def decorator(func: Callable[..., Any]):
        # Create a wrapper with clean signature for Discord.py
        # This ensures Discord.py only sees interaction: discord.Interaction
        signature = inspect.signature(func)
        default_args = [param.name for param in signature.parameters.values() if param.name in ["guild", "executor"]]
        async def discord_wrapper(interaction: discord.Interaction, *args, **kwargs):
            # This is what Discord.py sees and registers
            # Now call our actual processing implementation
            await _process_command_impl(
                func, interaction, allowed_permissions, default_args, *args, **kwargs
            )
        
        # Copy function metadata for Discord.py
        discord_wrapper.__name__ = func.__name__
        discord_wrapper.__qualname__ = func.__qualname__
        discord_wrapper.__doc__ = func.__doc__
        
        
        discord_wrapper.__signature__ = signature.replace(parameters=[param for param in signature.parameters.values() if param.name not in default_args])
        return discord_wrapper
    
    return decorator


async def _process_command_impl(func: Callable[..., Any], interaction: discord.Interaction, allowed_permissions: dict[flag_value, bool], default_args: list[inspect.Parameter], *args, **kwargs):
    """Internal implementation of command processing."""
    # Check if interaction is in a guild
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Create Guild wrapper
    guild = Guild(interaction.guild)
    member = interaction.user
    
    # Check permissions if required
    if allowed_permissions:
        member_permissions = member.guild_permissions
        missing = []
        for perm_flag in allowed_permissions:
            try:
                # perm_flag should be a flag_value, e.g. discord.Permissions.administrator
                perm_name = None
                for name in dir(discord.Permissions):
                    # Only properties starting with no underscore
                    if not name.startswith("_") and getattr(discord.Permissions, name) is perm_flag:
                        perm_name = name
                        break
                # Must resolve the permission name to bool, e.g. member_permissions.administrator
                has_permission = getattr(member_permissions, perm_name) if perm_name else False
                if not has_permission:
                    missing.append(perm_name or str(perm_flag))
            except Exception:
                missing.append(str(perm_flag))
        if missing:
            try:
                await interaction.response.send_message(
                    guild.localization.t("errors.member_has_no_permissions"),
                    ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    guild.localization.t("errors.member_has_no_permissions"),
                    ephemeral=True
                )
            logger.warning(
                f"Permission denied for command {func.__name__} - user {member.name} (ID: {member.id}) in guild {guild.name} (missing: {missing})",
                extra={"guild": guild.name}
            )
            return
    
    # Extract command arguments for logging
    command_args = {}
    if hasattr(interaction, 'data') and interaction.data:
        # For slash commands, extract options
        if hasattr(interaction.data, 'options') and interaction.data.options:
            for option in interaction.data.options:
                # Handle both AppCommandOptionData and dict-like structures
                if hasattr(option, 'name') and hasattr(option, 'value'):
                    command_args[option.name] = option.value
                elif isinstance(option, dict):
                    command_args[option.get('name', 'unknown')] = option.get('value', 'N/A')
                else:
                    command_args[str(option)] = str(option)
    
    # Get command name for logging
    command_name = getattr(interaction.command, 'name', None) if hasattr(interaction, 'command') and interaction.command else func.__name__
    
    # Log command invocation with all details
    logger.info(
        f"Command invoked: {command_name} by {member.name} (ID: {member.id}) in guild {guild.name} (ID: {guild.id})",
        extra={"guild": guild.name}
    )
    if command_args:
        logger.debug(f"Command arguments: {command_args}", extra={"guild": guild.name})
    
    try:
        # Call the original function with all passed arguments and keyword arguments
        call_args = {
            "guild": guild,
            "executor": member,
        }
        for arg in default_args:
            kwargs[arg] = call_args[arg]
        signature = inspect.signature(func)

        await func(interaction, *args, **kwargs)
    except Exception as e:
        logger.error(
            f"Error processing command {func.__name__} for guild {guild.name} and member {member.name}: {str(e)}",
            extra={"guild": guild.name},
            exc_info=True
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    guild.localization.t("errors.error"),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    guild.localization.t("errors.error"),
                    ephemeral=True
                )
        except Exception as send_error:
            logger.error(
                f"Failed to send error message: {str(send_error)}",
                extra={"guild": guild.name},
                exc_info=True
            )