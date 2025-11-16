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


# Temporary storage for pending changes (guild_id -> changes dict)
_pending_changes = {}


class AIConfigModal(discord.ui.Modal, title="AI Configuration"):
    api_key = discord.ui.TextInput(
        label="Google API Key",
        style=discord.TextStyle.short,
        required=True,
        placeholder="Enter your Google API key"
    )
    model = discord.ui.TextInput(
        label="Model",
        style=discord.TextStyle.short,
        required=False,
        placeholder="gemini-2.5-flash",
        default="gemini-2.5-flash"
    )

    def __init__(self, bot: discord.Client, config_view):
        super().__init__()
        self.bot = bot
        self.config_view = config_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        model_value = (str(self.model) or "gemini-2.5-flash").strip()
        if model_value not in ALLOWED_MODELS:
            model_value = "gemini-2.5-flash"

        # Store pending changes
        if guild_id not in _pending_changes:
            _pending_changes[guild_id] = {}
        _pending_changes[guild_id]["api_key"] = str(self.api_key)
        _pending_changes[guild_id]["model"] = model_value

        # Update the config view and edit the message
        await self.config_view.update_embed(interaction)
        
        guild_identifier = f"{interaction.guild.name}({guild_id})"
        logger.info(f"AI config updated (pending) for guild {guild_id}", extra={"guild": guild_identifier})


class WebhookConfigModal(discord.ui.Modal, title="Webhook Configuration"):
    webhook_url = discord.ui.TextInput(
        label="Webhook URL",
        style=discord.TextStyle.short,
        required=False,
        placeholder="https://discord.com/api/webhooks/..."
    )

    def __init__(self, bot: discord.Client, config_view):
        super().__init__()
        self.bot = bot
        self.config_view = config_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        webhook_url_value = str(self.webhook_url).strip()

        # Store pending changes
        if guild_id not in _pending_changes:
            _pending_changes[guild_id] = {}
        _pending_changes[guild_id]["webhook_url"] = webhook_url_value

        # Update the config view and edit the message
        await self.config_view.update_embed(interaction)
        
        guild_identifier = f"{interaction.guild.name}({guild_id})"
        logger.info(f"Webhook config updated (pending) for guild {guild_id}", extra={"guild": guild_identifier})


class LanguageSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, current_language: str, config_view):
        self.bot = bot
        self.config_view = config_view
        
        # Get full language names
        l10n = LocalizationHandler()
        options = []
        for lang in ALLOWED_LANGS:
            full_name = l10n.full_localization_name(lang)
            options.append(
                discord.SelectOption(
                    label=full_name,
                    value=lang,
                    default=(lang == current_language)
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

        guild_id = interaction.guild.id
        selected_language = self.values[0]

        # Store pending changes
        if guild_id not in _pending_changes:
            _pending_changes[guild_id] = {}
        _pending_changes[guild_id]["language"] = selected_language

        # Update the config view and edit the message
        await self.config_view.update_embed(interaction)
        
        guild_identifier = f"{interaction.guild.name}({guild_id})"
        logger.info(f"Language changed to {selected_language} (pending) for guild {guild_id}", extra={"guild": guild_identifier})


class ConfigView(discord.ui.View):
    def __init__(self, bot: discord.Client, guild_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.bot = bot
        self.guild_id = guild_id
        
        # Get current guild data
        g = self.bot.guilds_data.get(guild_id)
        current_language = g.params.get("language", "en") if g else "en"
        
        # Add language selector
        self.language_select = LanguageSelect(bot, current_language, self)
        self.add_item(self.language_select)
        
        # Buttons are automatically added by the @discord.ui.button decorator
        # No need to manually add them

    def get_current_config(self):
        """Get current configuration with pending changes applied."""
        g = self.bot.guilds_data.get(self.guild_id)
        if not g:
            return None
        
        config = {
            "language": g.params.get("language", "en"),
            "api_key": g.params.get("api_key", ""),
            "model": g.params.get("model", "gemini-2.5-flash"),
            "webhook_url": g.params.get("webhook_url", ""),
            "enabled": g.params.get("enabled", False)
        }
        
        # Apply pending changes
        if self.guild_id in _pending_changes:
            config.update(_pending_changes[self.guild_id])
        
        return config

    def create_embed(self):
        """Create the configuration embed."""
        config = self.get_current_config()
        if not config:
            return None
        
        # Get localization handler
        g = self.bot.guilds_data.get(self.guild_id)
        l10n = LocalizationHandler(default_locale=config["language"])
        
        embed = discord.Embed(
            title=l10n.t("config.embed.title", locale=config["language"]),
            color=discord.Color.blue()
        )
        
        # AI Active field
        ai_active = bool(config.get("enabled"))
        ai_status = "✅ Yes" if ai_active else "❌ No"
        embed.add_field(
            name=l10n.t("config.embed.fields.ai_active", locale=config["language"]),
            value=ai_status,
            inline=True
        )
        
        # Language field
        lang_name = l10n.full_localization_name(config["language"])
        embed.add_field(
            name=l10n.t("config.embed.fields.language", locale=config["language"]),
            value=lang_name,
            inline=True
        )
        
        # Webhook configured field
        webhook_configured = bool(config["webhook_url"])
        webhook_status = "✅ Yes" if webhook_configured else "❌ No"
        embed.add_field(
            name=l10n.t("config.embed.fields.webhook_configured", locale=config["language"]),
            value=webhook_status,
            inline=True
        )
        
        # Show if there are pending changes
        if self.guild_id in _pending_changes and _pending_changes[self.guild_id]:
            embed.set_footer(text=l10n.t("config.messages.changes_pending", locale=config["language"]))
        
        return embed

    async def update_embed(self, interaction: discord.Interaction):
        """Update the embed after changes."""
        embed = self.create_embed()
        if embed:
            # Update language selector default
            config = self.get_current_config()
            self.remove_item(self.language_select)
            self.language_select = LanguageSelect(self.bot, config["language"], self)
            self.add_item(self.language_select)
            
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.success, row=1)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        guild_identifier = f"{interaction.guild.name}({guild_id})"
        
        g = self.bot.guilds_data.get(guild_id)
        if g is None:
            await interaction.response.send_message("Guild data not initialized.", ephemeral=True)
            return

        # Apply pending changes
        if guild_id in _pending_changes and _pending_changes[guild_id]:
            pending = _pending_changes[guild_id]
            for key, value in pending.items():
                g[key] = value
            
            # Enable flag mirrors whether a working API key is stored
            if "api_key" in pending:
                g["api_key"] = pending["api_key"]
                g["model"] = pending["model"]
                g["enabled"] = g.enableAI()
                if not g["enabled"]:
                    logger.warning(
                        "AI initialization failed; disabling bot for this guild",
                        extra={"guild": guild_identifier}
                    )
            
            # Clear pending changes
            _pending_changes[guild_id] = {}

            # Verify AI readiness when enabled
            ai_should_be_enabled = bool(g.params.get("enabled"))
            ai_ready = False
            if ai_should_be_enabled and g.params.get("api_key"):
                ai_ready = g.enableAI()
            if ai_should_be_enabled and not ai_ready:
                g["enabled"] = False
                logger.warning(
                    "AI initialization failed; disabling bot for this guild",
                    extra={"guild": guild_identifier}
                )
            
            logger.info(f"Configuration applied for guild {guild_id}", extra={"guild": guild_identifier})
            
            # Update embed
            embed = self.create_embed()
            l10n = LocalizationHandler(default_locale=g.params.get("language", "en"))
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(
                l10n.t("config.messages.applied", locale=g.params.get("language", "en")),
                ephemeral=True
            )
        else:
            l10n = LocalizationHandler(default_locale=g.params.get("language", "en"))
            await interaction.response.send_message(
                l10n.t("config.messages.no_changes", locale=g.params.get("language", "en")),
                ephemeral=True
            )

    @discord.ui.button(label="AI Config", style=discord.ButtonStyle.primary, row=1)
    async def ai_config_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AIConfigModal(self.bot, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Webhook Change", style=discord.ButtonStyle.secondary, row=1)
    async def webhook_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WebhookConfigModal(self.bot, self)
        await interaction.response.send_modal(modal)


async def is_configured(interaction: discord.Interaction, guilds_data: dict) -> bool:

    if interaction.guild is None:
        return True
    g = guilds_data.get(interaction.guild.id)
    if g is None:
        return False
    return bool(g.params.get("enabled"))


def register_setup(bot: discord.Client):
    @bot.tree.command(
        name="config",
        description=lstr("commands.config.description", default="Configure the bot for this server")
    )
    @ProcessCommand(allowed_permissions={discord.Permissions.administrator: True}, required_guild=True, required_guild_enabled=False)
    async def config_cmd(interaction: discord.Interaction, guild: Guild):
        # Create and send the config embed with view
        view = ConfigView(bot, interaction.guild.id)
        embed = view.create_embed()
        
        if embed is None:
            await interaction.response.send_message("Guild data not initialized.", ephemeral=True)
            return
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    logger.debug("Config gate check registered successfully", extra={"guild": "Core"})
