import discord
from modules.LoggerHandler import get_logger
from modules.guild import Guild
from modules.utils import ProcessCommand
from modules.LocalizationHandler import LocalizationHandler, lstr

logger = get_logger()


ALLOWED_LANGS = {"en", "es", "ua"}
ALLOWED_MODELS = {
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-2.5-flash",
}


class SetupModal(discord.ui.Modal, title="Configure Bot"):
    api_key = discord.ui.TextInput(label="Google API Key", style=discord.TextStyle.short, required=True)
    language = discord.ui.TextInput(label="Language (en/es/ua)", style=discord.TextStyle.short, required=False, placeholder="en", default="en")
    model = discord.ui.TextInput(label="Model", style=discord.TextStyle.short, required=False, placeholder="gemini-2.5-flash", default="gemini-2.5-flash")

    def __init__(self, bot: discord.Client):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        lang_value = (str(self.language) or "en").strip().lower()
        if lang_value not in ALLOWED_LANGS:
            lang_value = "en"

        model_value = (str(self.model) or "gemini-2.5-flash").strip()
        if model_value not in ALLOWED_MODELS:
            model_value = "gemini-2.5-flash"
        guild_id = interaction.guild.id
        guild_name = interaction.guild.name
        guild_identifier = f"{guild_name}({guild_id})"
        logger.debug(f"Guild data for {guild_id}: {self.bot.guilds_data.get(guild_id)}", extra={"guild": guild_identifier})
        logger.debug(f"All guilds data: {self.bot.guilds_data}", extra={"guild": guild_identifier})
        g = self.bot.guilds_data.get(guild_id)
        if g is None:
            logger.warning(f"Guild data not initialized for guild {guild_id}", extra={"guild": guild_identifier})
            await interaction.response.send_message("Guild data not initialized. Try again in a few seconds.", ephemeral=True)
            return
        g["language"] = lang_value
        g["model"] = model_value
        g["api_key"] = str(self.api_key)
        g["enabled"] = True
        logger.info(f"Bot configured for guild {guild_id} - Language: {lang_value}, Model: {model_value}", extra={"guild": guild_identifier})

        await interaction.response.send_message("Setup completed successfully.", ephemeral=True)


async def is_configured(interaction: discord.Interaction, guilds_data: dict) -> bool:

    if interaction.guild is None:
        return True
    g = guilds_data.get(interaction.guild.id)
    if g is None:
        return False
    return bool(g.params.get("enabled"))


def register_setup(bot: discord.Client):
    @bot.tree.command(
        name="setup",
        description=lstr("commands.setup.description", default="Configure the bot for this server")
    )
    @ProcessCommand(allowed_permissions={discord.Permissions.administrator: True})
    async def setup_cmd(interaction: discord.Interaction, guild: Guild):
        await interaction.response.send_modal(SetupModal(bot))
    
    # Register interaction handler for global check
    # This replaces the non-existent tree.add_check() method
    @bot.event
    async def on_interaction(interaction: discord.Interaction):
        # Only check app commands, skip other interaction types (buttons, modals, etc.)
        if interaction.type == discord.InteractionType.application_command:
            # Skip the setup command itself - it should always be accessible
            if interaction.command and interaction.command.name == "setup":
                return
            
            # Safety check: ensure guilds_data is initialized
            if not hasattr(bot, 'guilds_data') or bot.guilds_data is None:
                return
            
            # Check if guild is configured
            if not await is_configured(interaction, bot.guilds_data):
                guild_id = interaction.guild.id if interaction.guild else None
                guild_name = interaction.guild.name if interaction.guild else "DM"
                guild_identifier = f"{guild_name}({guild_id})" if guild_id else "DM"
                logger.warning(f"Command {interaction.command.name if interaction.command else 'unknown'} blocked - guild not configured", extra={"guild": guild_identifier})
                await interaction.response.send_message(
                    "This bot is not configured for this server. Please use `/setup` to configure it first.",
                    ephemeral=True
                )
                return
    
    logger.debug("Setup gate check registered successfully", extra={"guild": "Core"})
