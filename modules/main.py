import os
import shutil
import discord
from discord.ext import commands

from modules.BattleHandler import BattleHandler
from modules.guild import Guild
from modules.utils import load_guilds, setup_guild, ProcessCommand
from modules.ConfigurationHandler import register_setup
from modules.LoggerHandler import get_logger
from modules.LocalizationHandler import LocalizationHandler, DiscordTranslator, lstr

logger = get_logger()


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
            logger.debug(f"Guild {guild.id} setup completed", extra={"guild": f"{guild.name}({guild.id})"})
        
        @self.event
        async def on_guild_remove(guild: discord.Guild):
            logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})", extra={"guild": f"{guild.name}({guild.id})"})
            if guild.id in self.guilds_data:
                del self.guilds_data[guild.id]
            if os.path.exists(f"guilds/{guild.id}"):
                shutil.rmtree(f"guilds/{guild.id}")
                logger.debug(f"Removed guild directory for {guild.id}", extra={"guild": f"{guild.name}({guild.id})"})
    