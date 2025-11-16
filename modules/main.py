import os
import shutil
import discord
from discord.ext import commands

from modules.BattleHandler import BattleHandler
from modules.guild import Guild
from modules.utils import load_guilds, read_bot_config, setup_guild, ProcessCommand, update_bot_config
from modules.ConfigurationHandler import register_setup, ALLOWED_LANGS
from modules.LoggerHandler import get_logger
from modules.LocalizationHandler import LocalizationHandler, DiscordTranslator, lstr

logger = get_logger()


class WelcomeLocaleSelect(discord.ui.Select):
    """Select dropdown for choosing locale in welcome message."""
    
    def __init__(self, bot: commands.Bot, guild_id: int, current_locale: str = "en"):
        self.bot = bot
        self.guild_id = guild_id
        
        # Get full language names
        l10n = LocalizationHandler()
        options = []
        for lang in ALLOWED_LANGS:
            full_name = l10n.full_localization_name(lang)
            options.append(
                discord.SelectOption(
                    label=full_name,
                    value=lang,
                    default=(lang == current_locale)
                )
            )
        
        super().__init__(
            placeholder="Select Language",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        
        selected_locale = self.values[0]
        l10n = LocalizationHandler()
        
        # Update the guild's language setting
        if self.guild_id in self.bot.guilds_data:
            guild_obj = self.bot.guilds_data[self.guild_id]
            guild_obj["language"] = selected_locale
        
        # Get translated welcome message
        welcome_text = l10n.t(
            "common.welcome",
            locale=selected_locale,
            name=self.bot.user.name,
            version=read_bot_config()["version"]
        )
        
        # Create a new view with the updated locale selection
        view = WelcomeView(self.bot, self.guild_id, selected_locale)
        
        # Update the message with translated text
        await interaction.response.edit_message(content=welcome_text, view=view)
        
        guild_identifier = f"{interaction.guild.name}({self.guild_id})"
        logger.info(f"Language changed to {selected_locale} for guild {self.guild_id}", extra={"guild": guild_identifier})


class WelcomeView(discord.ui.View):
    """View containing the locale selector for welcome message."""
    
    def __init__(self, bot: commands.Bot, guild_id: int, current_locale: str = "en"):
        super().__init__(timeout=None)  # No timeout so the message stays interactive
        self.bot = bot
        self.guild_id = guild_id
        
        # Add locale selector
        self.locale_select = WelcomeLocaleSelect(bot, guild_id, current_locale)
        self.add_item(self.locale_select)


class DiscordBot(commands.Bot):
    def __init__(self):

        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(intents=intents, command_prefix="!")
        
        # Initialize localization handler and translator
        self.l10n = LocalizationHandler()
        translator = DiscordTranslator(self.l10n)
        
        
        register_setup(self)
        self.battle_handler = BattleHandler(self)

        @self.event
        async def on_ready():
            logger.info(f"Bot logged in as {self.user}", extra={"guild": "Core"})
            
            # Update bot configuration file with bot ID and invite link
            update_bot_config(self)
            
            # Ensure slash commands are synced on startup
            try:
                await self.tree.set_translator(translator)
                await self.tree.sync()
                logger.info("Slash commands synced successfully", extra={"guild": "Core"})
            except Exception as e:
                # If syncing fails, we still want the bot to run
                logger.error(f"Failed to sync slash commands: {e}", exc_info=True, extra={"guild": "Core"})
            self.guilds_data = load_guilds(self)
            logger.debug(f"Loaded guilds data: {self.guilds_data}", extra={"guild": "Core"})

        @self.event
        async def on_message(message: discord.Message):
            if message.author.bot:
                return
            await self.process_commands(message)

        # Example slash command: /ping
        @self.tree.command(
            name="ping",
            description=lstr("commands.ping.description", default="Check bot latency")
        )
        @ProcessCommand(allowed_permissions={discord.Permissions.administrator: True})
        async def ping(interaction: discord.Interaction, guild: Guild):
            latency_ms = round(self.latency * 1000)
            
            await interaction.response.send_message(guild.localization.t("common.pong", ms=latency_ms), ephemeral=True)

        # Setup command and gating are registered via ConfigurationHandler

        @self.event
        async def on_guild_join(guild: discord.Guild):
            logger.info(f"Bot joined guild: {guild.name} (ID: {guild.id})", extra={"guild": f"{guild.name}({guild.id})"})
            setup_guild(guild.id)
            guild_obj = Guild(guild)
            guild_obj.__save__()
            self.guilds_data[guild.id] = guild_obj
            
            # Get current locale (default to "en")
            current_locale = guild_obj.params.get("language", "en")
            
            # Create welcome message with locale selector
            welcome_text = self.l10n.t("common.welcome", locale=current_locale, name=self.user.name, version=read_bot_config()["version"])
            view = WelcomeView(self, guild.id, current_locale)
            
            if guild.system_channel:
                await guild.system_channel.send(welcome_text, view=view)
            else:
                await guild.owner.send(welcome_text, view=view)
            
            logger.debug(f"Guild {guild.id} setup completed", extra={"guild": f"{guild.name}({guild.id})"})
        
        @self.event
        async def on_guild_remove(guild: discord.Guild):
            logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})", extra={"guild": f"{guild.name}({guild.id})"})
            if guild.id in self.guilds_data:
                del self.guilds_data[guild.id]
            if os.path.exists(f"guilds/{guild.id}"):
                shutil.rmtree(f"guilds/{guild.id}")
                logger.debug(f"Removed guild directory for {guild.id}", extra={"guild": f"{guild.name}({guild.id})"})
    