import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional

import discord
from discord.ext import commands

from modules.BattleHandler import BattleHandler
from modules.guild import Guild
from modules.utils import (
    load_guilds,
    read_bot_config,
    setup_guild,
    ProcessCommand,
    update_bot_config,
    append_suggestion_record,
)
from modules.ConfigurationHandler import register_setup, ALLOWED_LANGS
from modules.LoggerHandler import get_logger
from modules.LocalizationHandler import LocalizationHandler, DiscordTranslator, lstr

logger = get_logger()

suggestion_types = [
    {"value": "minor_issue", "label": "minor issue"},
    {"value": "major_issue", "label": "major issue"},
    {"value": "request", "label": "request"},
    {"value": "improvement", "label": "improvement"},
    {"value": "feedback", "label": "feedback"},
]

suggestion_categories = [
    {"value": "category_1", "label": "category 1"},
    {"value": "category_2", "label": "category 2"},
    {"value": "category_3", "label": "category 3"},
]

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
        
        selected_locale = self.values[0]
        l10n = LocalizationHandler()
        
        
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
        

class WelcomeView(discord.ui.View):
    """View containing the locale selector for welcome message."""
    
    def __init__(self, bot: commands.Bot, guild_id: int, current_locale: str = "en"):
        super().__init__(timeout=None)  # No timeout so the message stays interactive
        self.bot = bot
        self.guild_id = guild_id
        
        # Add locale selector
        self.locale_select = WelcomeLocaleSelect(bot, guild_id, current_locale)
        self.add_item(self.locale_select)


def _simplify_locale(locale: Optional[discord.Locale]) -> Optional[str]:
    if not locale:
        return None
    value = str(locale.value)
    return value.split("-")[0].lower()


def _translate(localization: LocalizationHandler, key: str, fallback: str, locale: Optional[str] = None) -> str:
    if not localization:
        return fallback
    translated = localization.t(key, locale=locale)
    if not translated or translated == key:
        return fallback
    return translated


class SuggestionTypeSelect(discord.ui.Select):
    def __init__(self, parent_view: "SuggestionView"):
        self.parent_view = parent_view
        options = []
        for suggestion_type in parent_view.suggestion_types:
            label = suggestion_type["label"]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=suggestion_type["value"],
                )
            )
            parent_view.type_label_map[suggestion_type["value"]] = label
        localization = parent_view.localization
        locale = parent_view.locale_code
        placeholder = _translate(localization, "commands.suggest.placeholders.type", "Select suggestion type", locale)
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, custom_id="suggestion_type_select")

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_type = self.values[0]
        await interaction.response.defer()


class SuggestionCategorySelect(discord.ui.Select):
    def __init__(self, parent_view: "SuggestionView", categories: List[dict]):
        self.parent_view = parent_view
        options = []
        for category in categories:
            label = category["label"]
            options.append(discord.SelectOption(label=label, value=category["value"]))
            parent_view.category_label_map[category["value"]] = label
        localization = parent_view.localization
        locale = parent_view.locale_code
        placeholder = _translate(localization, "commands.suggest.placeholders.category", "Select categories", locale)
        max_values = max(1, len(options))
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=max_values,
            options=options,
            custom_id="suggestion_category_select",
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_categories = list(self.values)
        await interaction.response.defer()


class SuggestionModal(discord.ui.Modal):
    def __init__(self, parent_view: "SuggestionView"):
        self.parent_view = parent_view
        localization = parent_view.localization
        locale = parent_view.locale_code
        title = _translate(localization, "commands.suggest.modal.title", "Share your suggestion", locale)
        super().__init__(title=title, custom_id="suggestion_modal")
        
        title_label = _translate(localization, "commands.suggest.modal.title_label", "Title", locale)
        title_placeholder = _translate(localization, "commands.suggest.modal.title_placeholder", "Brief summary...", locale)
        self.title_input = discord.ui.TextInput(
            label=title_label,
            style=discord.TextStyle.short,
            placeholder=title_placeholder,
            max_length=100,
            required=True,
        )
        self.add_item(self.title_input)
        
        label = _translate(localization, "commands.suggest.modal.label", "Suggestion details", locale)
        placeholder = _translate(localization, "commands.suggest.modal.placeholder", "Describe your idea or issue...", locale)
        self.body_input = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.long,
            placeholder=placeholder,
            max_length=2000,
            required=True,
        )
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        content = self.body_input.value.strip()
        localization = self.parent_view.localization
        locale = self.parent_view.locale_code
        if not title or not content:
            error_text = _translate(localization, "commands.suggest.errors.empty", "Title and details cannot be empty.", locale)
            await interaction.response.send_message(error_text, ephemeral=True)
            return

        payload = self.parent_view.build_payload(title, content, interaction)
        stored = append_suggestion_record(payload)

        logger.info(
            f"Stored suggestion {stored.get('id')} from user {stored['user'].get('id')}",
            extra={"guild": stored.get("guild", {}).get("name", "Suggestions")},
        )

        await self.parent_view.make_unavailable()

        success_text = _translate(localization, "commands.suggest.success", "Thank you! Your suggestion was recorded.", locale)
        await interaction.response.send_message(success_text, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Error submitting suggestion: {error}", exc_info=True, extra={"guild": "Suggestions"})
        localization = self.parent_view.localization
        locale = self.parent_view.locale_code
        error_text = _translate(localization, "commands.suggest.errors.generic", "Something went wrong. Please try again.", locale)
        if not interaction.response.is_done():
            await interaction.response.send_message(error_text, ephemeral=True)
        else:
            await interaction.followup.send(error_text, ephemeral=True)


class SuggestionView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        author: discord.abc.User,
        localization: LocalizationHandler,
        locale_code: Optional[str],
        configured_guild: Optional[Guild],
        discord_guild: Optional[discord.Guild],
        categories: Optional[List[dict]] = None,
    ):
        super().__init__(timeout=600)
        self.bot = bot
        self.author = author
        self.localization = localization
        self.locale_code = locale_code
        self.configured_guild = configured_guild
        self.discord_guild = discord_guild
        self.command_interaction: Optional[discord.Interaction] = None
        self.message: Optional[discord.Message] = None
        self.selected_type: Optional[str] = None
        self.selected_categories: List[str] = []
        self.type_label_map: Dict[str, str] = {}
        self.category_label_map: Dict[str, str] = {}
        self.suggestion_types = suggestion_types
        self.categories = categories or suggestion_categories

        self.type_select = SuggestionTypeSelect(self)
        self.category_select = SuggestionCategorySelect(self, self.categories)
        self.add_item(self.type_select)
        self.add_item(self.category_select)
        self.open_modal.label = self._button_label()

    def _button_label(self) -> str:
        return _translate(self.localization, "commands.suggest.button.open_modal", "Write suggestion", self.locale_code)

    async def _send_selection_warning(self, interaction: discord.Interaction):
        localization = self.localization
        locale = self.locale_code
        missing_text = _translate(localization, "commands.suggest.errors.selection", "Select a type and at least one category first.", locale)
        if not interaction.response.is_done():
            await interaction.response.send_message(missing_text, ephemeral=True)
        else:
            await interaction.followup.send(missing_text, ephemeral=True)

    @discord.ui.button(
        label="Write suggestion",
        style=discord.ButtonStyle.primary,
        custom_id="suggestion_open_modal",
    )
    async def open_modal(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not self.selected_type or not self.selected_categories:
            await self._send_selection_warning(interaction)
            return
        modal = SuggestionModal(self)
        modal.title = _translate(self.localization, "commands.suggest.modal.title", modal.title, self.locale_code)
        modal.body_input.label = _translate(self.localization, "commands.suggest.modal.label", modal.body_input.label, self.locale_code)
        modal.body_input.placeholder = _translate(
            self.localization, "commands.suggest.modal.placeholder", modal.body_input.placeholder, self.locale_code
        )
        await interaction.response.send_modal(modal)

    def translate(self, key: str, fallback: str) -> str:
        return _translate(self.localization, key, fallback, self.locale_code)

    async def make_unavailable(self):
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception as e:
                logger.warning(f"Failed to disable suggestion view message: {e}", exc_info=True, extra={"guild": "Suggestions"})

    async def on_timeout(self):
        await self.make_unavailable()

    def build_payload(self, title: str, message: str, interaction: discord.Interaction) -> dict:
        now = datetime.utcnow().isoformat()
        guild_obj = self.discord_guild or interaction.guild
        categories = [
            {
                "value": category_value,
                "label": self.category_label_map.get(category_value, category_value),
            }
            for category_value in self.selected_categories
        ]
        suggestion_type = {
            "value": self.selected_type,
            "label": self.type_label_map.get(self.selected_type, self.selected_type),
        }
        user = interaction.user
        locale_info = {
            "user": str(interaction.locale.value) if interaction.locale else None,
            "guild": str(interaction.guild_locale.value) if interaction.guild_locale else None,
            "stored": self.locale_code,
        }
        guild_payload = None
        if guild_obj:
            guild_payload = {
                "id": str(guild_obj.id),
                "name": guild_obj.name,
                "configured": str(self.configured_guild.guild_id) if isinstance(self.configured_guild, Guild) else None,
            }

        return {
            "title": title,
            "type": suggestion_type,
            "categories": categories,
            "message": message,
            "created_at": now,
            "user": {
                "id": str(user.id),
                "name": user.name,
                "display_name": getattr(user, "display_name", user.name),
                "global_name": getattr(user, "global_name", None),
                "discriminator": getattr(user, "discriminator", "0"),
            },
            "contact": {
                "method": "dm",
                "user_id": str(user.id),
            },
            "locale": locale_info,
            "guild": guild_payload,
            "context": {
                "interaction_id": str(interaction.id),
                "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                "in_guild": guild_obj is not None,
            },
            "responded": False,
            "response_given": False,
            "response_text": None,
            "response_type": None,
            "response_sent_at": None,
        }


class DiscordBot(commands.Bot):
    def __init__(self, dev: bool = False):

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guild_messages = True

        super().__init__(intents=intents, command_prefix="!")
        self.dev = dev
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
        @ProcessCommand(self, allowed_permissions={discord.Permissions.administrator: True})
        async def ping(interaction: discord.Interaction, guild: Guild):
            latency_ms = round(self.latency * 1000)
            await interaction.response.send_message(guild.localization.t("common.pong", ms=latency_ms), ephemeral=True)

        @self.tree.command(
            name="suggest",
            description=lstr("commands.suggest.description", default="Suggest a feature or report a bug")
        )
        @ProcessCommand(self, allowed_permissions={}, required_guild=False, required_guild_enabled=False)
        async def suggest(interaction: discord.Interaction, guild: Guild = None, executor: discord.Member = None):
            localization = guild.localization if isinstance(guild, Guild) else self.l10n
            locale_code = _simplify_locale(interaction.locale)
            view = SuggestionView(
                bot=self,
                author=executor or interaction.user,
                localization=localization,
                locale_code=locale_code,
                configured_guild=guild if isinstance(guild, Guild) else None,
                discord_guild=interaction.guild,
                categories=suggestion_categories,
            )

            content = _translate(
                localization,
                "commands.suggest.prompt",
                "Help us improve by selecting the type and categories, then press the button to describe your suggestion.",
                locale_code,
            )

            await interaction.response.send_message(content, view=view, ephemeral=True)
            view.command_interaction = interaction
            try:
                view.message = await interaction.original_response()
            except Exception:
                view.message = None


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
            
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                await guild.system_channel.send(welcome_text, view=view)
            elif guild.owner:
                await guild.owner.send(welcome_text, view=view)
            else:
                logger.warning(f"Could not send welcome message to guild {guild.id}: no system channel and owner is None", extra={"guild": f"{guild.name}({guild.id})"})
            
            logger.debug(f"Guild {guild.id} setup completed", extra={"guild": f"{guild.name}({guild.id})"})
        
        @self.event
        async def on_guild_remove(guild: discord.Guild):
            logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})", extra={"guild": f"{guild.name}({guild.id})"})
            if guild.id in self.guilds_data:
                del self.guilds_data[guild.id]
            if os.path.exists(f"guilds/{guild.id}"):
                shutil.rmtree(f"guilds/{guild.id}")
                logger.debug(f"Removed guild directory for {guild.id}", extra={"guild": f"{guild.name}({guild.id})"})
    