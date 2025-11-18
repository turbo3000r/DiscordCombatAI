from functools import wraps
import inspect
import json
import os
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence
from datetime import datetime
import discord
from discord.flags import flag_value
from modules.guild import Guild
from modules.LoggerHandler import get_logger

logger = get_logger()

BOT_CONFIG_FILE = "bot.json"
GENERIC_DIR = "generic"
SUGGESTIONS_FILE = os.path.join(GENERIC_DIR, "suggestions.json")
_suggestions_lock = threading.Lock()

class BattleMetadata:
    def __init__(self, guild: Guild, date: datetime, **kwargs):
        self.date = date
        self.guild = guild
        self.kwargs = kwargs
    @classmethod
    def deserialize(cls, string: str) -> "BattleMetadata":
        data = json.loads(string)
        return cls(Guild(data["guild"]), datetime.fromisoformat(data["date"]), **data["kwargs"])
    
    def serialize(self) -> str:
        return json.dumps({
            "date": self.date.isoformat(),
            "guild": self.guild.guild_id,
            "kwargs": self.kwargs
        })

    def __getitem__(self, key: str):
        return self.kwargs[key]
    def __setitem__(self, key: str, value: Any):
        self.kwargs[key] = value
    def __delitem__(self, key: str):
        del self.kwargs[key]
    def __iter__(self):
        return iter(self.kwargs)
    def __len__(self):
        return len(self.kwargs)
    def __contains__(self, key: str):
        return key in self.kwargs
    def __repr__(self):
        return self.__str__()
    def __str__(self):
        return f"BattleMetadata(date={self.date}, guild={self.guild}, kwargs={self.kwargs})"


def setup_guild(guild_id: int):
    if not os.path.exists(f"guilds/{guild_id}"):
        os.makedirs(f"guilds/{guild_id}")
        with open(f"guilds/{guild_id}/config.json", "w") as f:
            json.dump({"enabled": False, "language": "en","AIEnabled":False, "api_key": "", "model": "gemini-2.5-flash", "webhook_url": ""}, f)


def load_guilds(bot: discord.ext.commands.Bot) -> dict:
    guilds = {}
    for id in os.listdir("guilds"):
        g = Guild(bot.get_guild(int(id)))
        guilds[int(id)] = g
    return guilds


def read_bot_config() -> Dict[str, Any]:
    """Read bot configuration from bot.json."""
    if not os.path.exists(BOT_CONFIG_FILE):
        # Create default config if it doesn't exist
        default_config = {
            "name": "Discord Combat AI Bot",
            "description": "AI-powered combat bot for Discord",
            "id": "",
            "invite_link": "",
            "version": "1.0.0"
        }
        write_bot_config(default_config)
        return default_config
    
    try:
        with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read bot config: {e}", extra={"guild": "Core"})
        return {
            "name": "Discord Combat AI Bot",
            "description": "AI-powered combat bot for Discord",
            "id": "",
            "invite_link": "",
            "version": "1.0.0"
        }

def save_battle_result(guild: Guild, metadata: BattleMetadata, result: str, folder: str = "generic") -> None:
    """Save a battle result to a file."""
    file_dir = os.path.join("guilds", str(guild.guild_id), folder)
    os.makedirs(file_dir, exist_ok=True)
    file_path = os.path.join(file_dir, f"battle-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(metadata.serialize()+"\n")
        f.write(result)
    logger.info(f"Battle result saved to {file_path}", extra={"guild": f"{guild.name}({guild.guild_id})"})


def write_bot_config(config: Dict[str, Any]) -> None:
    """Write bot configuration to bot.json."""
    try:
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to write bot config: {e}", extra={"guild": "Core"})


def _ensure_suggestions_storage() -> None:
    os.makedirs(GENERIC_DIR, exist_ok=True)
    if not os.path.exists(SUGGESTIONS_FILE):
        with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2, ensure_ascii=False)


def _read_suggestions_unlocked() -> List[Dict[str, Any]]:
    _ensure_suggestions_storage()
    try:
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            logger.warning("Suggestions file did not contain a list, resetting to empty list", extra={"guild": "Core"})
            return []
    except json.JSONDecodeError:
        logger.error("Failed to decode suggestions.json; returning empty list", extra={"guild": "Core"})
        return []
    except Exception as e:
        logger.error(f"Failed to read suggestions: {e}", extra={"guild": "Core"})
        return []


def _write_suggestions_unlocked(records: List[Dict[str, Any]]) -> None:
    _ensure_suggestions_storage()
    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def load_suggestions() -> List[Dict[str, Any]]:
    """Return a copy of all stored suggestions."""
    with _suggestions_lock:
        return list(_read_suggestions_unlocked())


def append_suggestion_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Append a suggestion record to storage.

    Args:
        record: Suggestion data. If no `id` is provided, one will be generated.

    Returns:
        The stored suggestion (with generated id, if applicable).
    """
    with _suggestions_lock:
        suggestions = _read_suggestions_unlocked()
        stored_record = record.copy()
        stored_record.setdefault("id", str(uuid.uuid4()))
        suggestions.append(stored_record)
        _write_suggestions_unlocked(suggestions)
        return stored_record


def find_suggestion_by_id(suggestion_id: str) -> Optional[Dict[str, Any]]:
    """Find a suggestion by id."""
    with _suggestions_lock:
        suggestions = _read_suggestions_unlocked()
        for suggestion in suggestions:
            if str(suggestion.get("id")) == str(suggestion_id):
                return suggestion
    return None


def update_suggestion_record(suggestion_id: str, updater: Callable[[Dict[str, Any]], None]) -> Optional[Dict[str, Any]]:
    """
    Update a suggestion record in-place.

    Args:
        suggestion_id: ID of the suggestion to update.
        updater: A callable that receives the mutable suggestion dict.

    Returns:
        The updated suggestion dict, or None if not found.
    """
    with _suggestions_lock:
        suggestions = _read_suggestions_unlocked()
        updated = None
        for index, suggestion in enumerate(suggestions):
            if str(suggestion.get("id")) == str(suggestion_id):
                updater(suggestion)
                updated = suggestion
                suggestions[index] = suggestion
                break
        if updated is not None:
            _write_suggestions_unlocked(suggestions)
        return updated

def _split_content(content: str, max_length: int = 2000) -> list[str]:
    """Split content into chunks that are at most max_length characters."""
    if len(content) <= max_length:
        return [content]
    
    chunks = []
    current_pos = 0
    
    while current_pos < len(content):
        # Calculate chunk end position
        chunk_end = current_pos + max_length
        
        # If we're at the end, just take the rest
        if chunk_end >= len(content):
            chunks.append(content[current_pos:])
            break
        
        # Try to split at a newline if possible (prefer natural breaks)
        last_newline = content.rfind('\n', current_pos, chunk_end)
        if last_newline > current_pos:
            # Split at newline
            chunks.append(content[current_pos:last_newline + 1])
            current_pos = last_newline + 1
        else:
            # No newline found, split at max_length
            chunks.append(content[current_pos:chunk_end])
            current_pos = chunk_end
    
    return chunks


async def sendMessage(channel: discord.TextChannel, guild: Guild, *args, **kwargs):
    """Safely send a message to a channel, handling permission errors and message length limits."""
    try:
        # Check if bot has permission to send messages
        if not channel.permissions_for(channel.guild.me).send_messages:
            logger.warning(f"Bot lacks permission to send messages in channel {channel.id}", extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
            return None
        
        # Check content length - Discord limit is 2000 characters
        content = kwargs.get('content', None) or (args[0] if args and isinstance(args[0], str) else None)
        
        if content and len(content) > 2000:
            # Content is too long, split into multiple messages
            chunks = _split_content(content, max_length=2000)
            logger.info(f"Splitting long message ({len(content)} chars) into {len(chunks)} parts", extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
            
            # Prepare base kwargs (without content) for first message
            first_kwargs = kwargs.copy()
            first_kwargs.pop('content', None)
            
            # Prepare base args (without first string arg if it exists)
            first_args = list(args)
            if first_args and isinstance(first_args[0], str):
                first_args[0] = chunks[0]
            else:
                first_kwargs['content'] = chunks[0]
            
            # Send first message with original kwargs (embeds, files, etc.)
            first_message = await channel.send(*first_args, **first_kwargs)
            
            # Send remaining chunks as simple text messages
            for chunk in chunks[1:]:
                await channel.send(content=chunk)
            
            return first_message
        
        # Normal message sending
        return await channel.send(*args, **kwargs)
    except discord.Forbidden as e:
        logger.error(f"Forbidden error sending message to channel {channel.id}: {e}", extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
        return None
    except discord.HTTPException as e:
        # Handle HTTP exceptions (like message too long) - try splitting as fallback
        if e.code == 50035:  # Invalid Form Body - content too long
            content = kwargs.get('content', None) or (args[0] if args and isinstance(args[0], str) else None)
            if content:
                try:
                    chunks = _split_content(content, max_length=2000)
                    logger.info(f"Splitting long message ({len(content)} chars) into {len(chunks)} parts (fallback)", extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
                    
                    first_kwargs = kwargs.copy()
                    first_kwargs.pop('content', None)
                    first_args = list(args)
                    if first_args and isinstance(first_args[0], str):
                        first_args[0] = chunks[0]
                    else:
                        first_kwargs['content'] = chunks[0]
                    
                    first_message = await channel.send(*first_args, **first_kwargs)
                    for chunk in chunks[1:]:
                        await channel.send(content=chunk)
                    return first_message
                except Exception as split_error:
                    logger.error(f"Failed to split long message: {split_error}", exc_info=True, extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
        logger.error(f"HTTP error sending message to channel {channel.id}: {e}", exc_info=True, extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
        return None
    except Exception as e:
        logger.error(f"Error sending message to channel {channel.id}: {e}", exc_info=True, extra={"guild": f"{channel.guild.name}({channel.guild.id})"})
        return None


async def editMessage(message: discord.Message, *args, **kwargs) -> bool:
    """Safely edit a message, handling permission errors. Returns True if successful, False otherwise."""
    try:
        # Check if bot has permission to send messages (required to edit own messages)
        # Note: Editing your own messages only requires send_messages, not manage_messages
        perms = message.channel.permissions_for(message.guild.me)
        if not perms.send_messages:
            logger.warning(f"Bot lacks permission to send messages in channel {message.channel.id}", extra={"guild": f"{message.guild.name}({message.guild.id})"})
            return False
        await message.edit(*args, **kwargs)
        return True
    except discord.Forbidden as e:
        logger.error(f"Forbidden error editing message {message.id}: {e}", extra={"guild": f"{message.guild.name}({message.guild.id})"})
        return False
    except Exception as e:
        logger.error(f"Error editing message {message.id}: {e}", exc_info=True, extra={"guild": f"{message.guild.name}({message.guild.id})"})
        return False

def update_bot_config(bot: discord.Client) -> None:
    """Update bot.json with current bot information."""
    config = read_bot_config()
    
    if bot.user:
        config["id"] = str(bot.user.id)
        # Generate invite link with appropriate permissions
        # Permissions: Send Messages, Embed Links, Attach Files, Read Message History, Use Slash Commands
        permissions = discord.Permissions(
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            use_application_commands=True
        )
        config["invite_link"] = f"https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions={permissions.value}&scope=bot%20applications.commands"
    
    write_bot_config(config)
    logger.info("Bot configuration updated", extra={"guild": "Core"})

class PseudoGuild:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
    def __getattr__(self, name):
        return None
    def __setattr__(self, name, value):
        pass
    def __delattr__(self, name):
        pass


def _get_env_int(var_name: str) -> Optional[int]:
    """Safely read an integer environment variable."""
    value = os.getenv(var_name)
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"Environment variable {var_name} is not a valid integer.", extra={"guild": "Core"})
        return None


async def _send_ephemeral_message(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral response, falling back to followup if needed."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as send_error:
        logger.error(f"Failed to send ephemeral message: {send_error}", exc_info=True, extra={"guild": getattr(interaction.guild, 'name', 'DM')})


def _resolve_permission_name(perm_flag: flag_value) -> Optional[str]:
    """Resolve a discord permission flag to its attribute name."""
    for name in dir(discord.Permissions):
        if name.startswith("_"):
            continue
        if getattr(discord.Permissions, name) is perm_flag:
            return name
    return None


async def _enforce_dev_mode(bot: discord.ext.commands.Bot, interaction: discord.Interaction) -> bool:
    """Return True if the command should continue processing."""
    guild = interaction.guild
    dev_mode = getattr(bot, "dev", False)
    dev_guild_id = _get_env_int("DEV_GUILD_ID")

    if dev_mode:
        if not dev_guild_id:
            logger.warning("DEV_GUILD_ID is not configured; blocking command during dev mode.", extra={"guild": getattr(guild, "name", "DM")})
            return False
        if not guild or (dev_guild_id and guild.id != dev_guild_id):
            logger.debug("Command blocked: dev mode restriction", extra={"guild": getattr(guild, "name", "DM")})
            return False
    else:
        if guild and dev_guild_id and guild.id == dev_guild_id:
            logger.debug("Command blocked: production mode restriction", extra={"guild": guild.name})
            return False
    return True

def ProcessCommand(bot: discord.ext.commands.Bot, allowed_permissions: Optional[Sequence[flag_value]] = None, required_guild: bool = True, required_guild_enabled: bool = True):
    """
    Decorator to process Discord slash commands with logging and permission checking.
    
    Args:
        allowed_permissions: List of discord.Permissions flags that are required to run the command.
                           If None or empty, no permission check is performed.
        required_guild: If True, the command must be used in a guild.
        required_guild_enabled: If True, the guild must be enabled.
    
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
                func, interaction, bot, allowed_permissions, default_args, required_guild, required_guild_enabled, *args, **kwargs
            )
        
        # Copy function metadata for Discord.py
        discord_wrapper.__name__ = func.__name__
        discord_wrapper.__qualname__ = func.__qualname__
        discord_wrapper.__doc__ = func.__doc__
        
        
        discord_wrapper.__signature__ = signature.replace(parameters=[param for param in signature.parameters.values() if param.name not in default_args])
        return discord_wrapper
    
    return decorator


async def _process_command_impl(
    func: Callable[..., Any],
    interaction: discord.Interaction,
    bot: discord.ext.commands.Bot,
    allowed_permissions: Optional[Sequence[flag_value]],
    default_args: List[str],
    required_guild: bool,
    required_guild_enabled: bool,
    *args,
    **kwargs
):
    """Internal implementation of command processing."""
    guild = interaction.guild

    if not await _enforce_dev_mode(bot, interaction):
        return

    if required_guild and not guild:
        await _send_ephemeral_message(
            interaction,
            "This command can only be used inside a server."
        )
        return

    guild_wrapper = Guild(guild) if guild else PseudoGuild(0)
    member = interaction.user

    if required_guild_enabled and not getattr(guild_wrapper, "enabled", False):
        guild_name = getattr(guild, "name", "Unknown Guild")
        guild_id = getattr(guild, "id", "N/A")
        logger.warning(
            f"Command {interaction.command.name if interaction.command else 'unknown'} blocked - guild not configured",
            extra={"guild": f"{guild_name}({guild_id})"}
        )
        await _send_ephemeral_message(
            interaction,
            "This bot is not configured for this server. Please use `/config` to configure it first."
        )
        return

    # Check permissions if required
    if allowed_permissions:
        member_permissions = getattr(member, "guild_permissions", None)
        missing: List[str] = []
        if not member_permissions:
            missing = ["No guild permissions available"]
        else:
            for perm_flag in allowed_permissions:
                try:
                    perm_name = _resolve_permission_name(perm_flag)
                    has_permission = getattr(member_permissions, perm_name) if perm_name else False
                    if not has_permission:
                        missing.append(perm_name or str(perm_flag))
                except Exception:
                    missing.append(str(perm_flag))
        if missing:
            await _send_ephemeral_message(
                interaction,
                guild_wrapper.localization.t("errors.member_has_no_permissions") if getattr(guild_wrapper, "localization", None) else "You lack the required permissions to run this command."
            )
            logger.warning(
                f"Permission denied for command {func.__name__} - user {member.name} (ID: {member.id}) in guild {getattr(guild_wrapper, 'name', 'Unknown')} (missing: {missing})",
                extra={"guild": getattr(guild_wrapper, 'name', 'Unknown')}
            )
            return

    # Extract command arguments for logging
    command_args = {}
    data = getattr(interaction, "data", None)
    options = getattr(data, "options", None) if data else None
    if not options and isinstance(data, dict):
        options = data.get("options")
    if options:
        for option in options:
            if hasattr(option, "name") and hasattr(option, "value"):
                command_args[option.name] = option.value
            elif isinstance(option, dict):
                command_args[option.get("name", "unknown")] = option.get("value", "N/A")
            else:
                command_args[str(option)] = str(option)

    # Get command name for logging
    command_name = getattr(interaction.command, 'name', None) if hasattr(interaction, 'command') and interaction.command else func.__name__
    
    # Log command invocation with all details
    logger.info(
        f"Command invoked: {command_name} by {member.name} (ID: {member.id}) in guild {getattr(guild_wrapper, 'name', 'DM')} (ID: {getattr(guild_wrapper, 'guild_id', 'N/A')})",
        extra={"guild": getattr(guild_wrapper, 'name', 'Unknown')}
    )
    if command_args:
        logger.debug(f"Command arguments: {command_args}", extra={"guild": getattr(guild_wrapper, 'name', 'Unknown')})
    
    try:
        # Call the original function with all passed arguments and keyword arguments
        call_args = {
            "guild": guild_wrapper,
            "executor": member,
        }
        for arg in default_args:
            kwargs[arg] = call_args[arg]

        await func(interaction, *args, **kwargs)
    except Exception as e:
        logger.error(
            f"Error processing command {func.__name__} for guild {getattr(guild_wrapper, 'name', 'Unknown')} and member {member.name}: {str(e)}",
            extra={"guild": getattr(guild_wrapper, 'name', 'Unknown')},
            exc_info=True
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    guild_wrapper.localization.t("errors.error") if getattr(guild_wrapper, "localization", None) else "An unexpected error occurred.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    guild_wrapper.localization.t("errors.error") if getattr(guild_wrapper, "localization", None) else "An unexpected error occurred.",
                    ephemeral=True
                )
        except Exception as send_error:
            logger.error(
                f"Failed to send error message: {str(send_error)}",
                extra={"guild": getattr(guild_wrapper, 'name', 'Unknown')},
                exc_info=True
            )