import asyncio
import datetime
from threading import Timer
import typing

import discord

from modules.LocalizationHandler import lstr
from modules.LocalizationHandler import LocalizationHandler
from modules.LoggerHandler import get_logger
from modules.guild import Guild
from modules.utils import BattleMetadata, ProcessCommand, save_battle_result, editMessage, sendMessage

from modules.PromptHandler import Prompt, PromptHandler, Prompts, SystemPrompt, random_string, SETTINGS

logger = get_logger()



class Fighter:
    def __init__(self, name: str, description: str, player: discord.Member):
        self.name = name
        self.description = description
        self.strategy = None
        self.player = player
    
    def __str__(self):
        return f"{self.name} - {self.description}\nStrategy: {self.strategy}"


class FighterPrompt(SystemPrompt):
    def __init__(self):
        super().__init__("prompts/elements/fighters.txt")
    
    def fill(self, fighter: dict[discord.Member, Fighter]) -> Prompt:
        content = self.content
        for member, fighter in fighter.items():
            content += "\n"
            content += f"### [{member.name}]:\nNAME: {fighter.name}\nDESCRIPTION: {fighter.description}\nSTRATEGY: {fighter.strategy if fighter.strategy else 'N/A'}"
        return Prompt(content)

class StrategyCreator:
    def __init__(self, channel: discord.TextChannel, participants: dict[discord.Member, Fighter], owner: discord.Member = None):
        self.channel = channel
        self.participants = participants
        self.strategy = None
        self.guild = Guild(channel.guild)
        self.submissions = {}
        self.message = None
        self.completed = asyncio.Event()
        self.owner = owner
        self.aborted = False
    
    async def _button_callback(self, interaction: discord.Interaction):
        """Handle button click to open modal."""
        if interaction.user not in self.participants:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.strategy.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.submissions:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.strategy.already_submitted"),
                ephemeral=True
            )
            return
        
        modal = StrategyModal(self)
        await interaction.response.send_modal(modal)
    
    async def get_strategy(self) -> dict[discord.Member, Fighter]:
        """Start the strategy collection process and wait for all submissions."""
        # Create initial embed
        remaining = list(self.participants.keys())
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.strategy.title"),
            description=self.guild.localization.t("commands.quick-battle.strategy.description"),
            color=discord.Color.blue()
        )
        embed.add_field(
            name=self.guild.localization.t("commands.quick-battle.strategy.remaining"),
            value="\n".join([p.mention for p in remaining]) if remaining else self.guild.localization.t("commands.quick-battle.strategy.none_remaining"),
            inline=False
        )
        
        # Create view with button
        self.view = discord.ui.View()
        button = discord.ui.Button(
            label=self.guild.localization.t("commands.quick-battle.strategy.button_label"),
            style=discord.ButtonStyle.primary
        )
        button.callback = self._button_callback
        self.view.add_item(button)
        # Add abort button if owner is set
        if self.owner:
            self.view.add_item(CreatorAbortButton(self, self.owner, self.guild.localization.t("commands.quick-battle.communication.abort_button")))
        
        # Send message
        self.message = await sendMessage(self.channel, self.guild, embed=embed, view=self.view)
        if self.message is None:
            # Failed to send message, abort
            self.aborted = True
            self.completed.set()
            return None
        
        # Wait for all submissions or abort
        await self.completed.wait()
        
        # Check if aborted
        if self.aborted:
            return None
        
        # Disable button
        self.view.clear_items()
        await editMessage(self.message, view=self.view)
        
        return self.participants
    
    async def _abort(self):
        """Abort the strategy collection process."""
        self.aborted = True
        # Update message to show aborted status
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.strategy.title"),
            description=self.guild.localization.t("commands.quick-battle.communication.battle_aborted_message"),
            color=discord.Color.red()
        )
        self.view.clear_items()
        await editMessage(self.message, embed=embed, view=self.view)
        self.completed.set()


class StrategyModal(discord.ui.Modal):
    def __init__(self, creator: StrategyCreator):
        super().__init__(title=creator.guild.localization.t("commands.quick-battle.strategy.modal_title"))
        self.creator = creator
        
        self.strategy_input = discord.ui.TextInput(
            label=creator.guild.localization.t("commands.quick-battle.strategy.input_label"),
            placeholder=creator.guild.localization.t("commands.quick-battle.strategy.input_placeholder"),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.strategy_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user not in self.creator.participants:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.strategy.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.creator.submissions:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.strategy.already_submitted"),
                ephemeral=True
            )
            return
        
        strategy_text = self.strategy_input.value.strip()
        if not strategy_text:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.strategy.empty_input"),
                ephemeral=True
            )
            return
        
        self.creator.submissions[interaction.user] = strategy_text
        self.creator.participants[interaction.user].strategy = strategy_text
        
        await interaction.response.send_message(
            self.creator.guild.localization.t("commands.quick-battle.strategy.submitted"),
            ephemeral=True
        )
        
        # Update message with remaining participants
        remaining = [p for p in self.creator.participants.keys() if p not in self.creator.submissions]
        if remaining:
            embed = discord.Embed(
                title=self.creator.guild.localization.t("commands.quick-battle.strategy.title"),
                description=self.creator.guild.localization.t("commands.quick-battle.strategy.description"),
                color=discord.Color.blue()
            )
            embed.add_field(
                name=self.creator.guild.localization.t("commands.quick-battle.strategy.remaining"),
                value="\n".join([p.mention for p in remaining]) if remaining else self.creator.guild.localization.t("commands.quick-battle.strategy.none_remaining"),
                inline=False
            )
            await editMessage(self.creator.message, embed=embed, view=self.creator.view)
        else:
            # All submitted
            self.creator.completed.set()

class FighterCreator:
    def __init__(self, channel: discord.TextChannel, participants: list[discord.Member], owner: discord.Member = None):
        self.channel = channel
        self.participants = participants
        self.guild = Guild(channel.guild)
        self.submissions = {}
        self.fighters = {}
        self.message = None
        self.completed = asyncio.Event()
        self.owner = owner
        self.aborted = False

    async def _button_callback(self, interaction: discord.Interaction):
        """Handle button click to open modal."""
        if interaction.user not in self.participants:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.fighter.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.submissions:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.fighter.already_submitted"),
                ephemeral=True
            )
            return
        
        modal = FighterModal(self)
        await interaction.response.send_modal(modal)

    async def get_fighters(self) -> dict[discord.Member, Fighter]:
        """Start the fighter collection process and wait for all submissions."""
        # Create initial embed
        remaining = list(self.participants)
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.fighter.title"),
            description=self.guild.localization.t("commands.quick-battle.fighter.description"),
            color=discord.Color.blue()
        )
        embed.add_field(
            name=self.guild.localization.t("commands.quick-battle.fighter.remaining"),
            value="\n".join([p.mention for p in remaining]) if remaining else self.guild.localization.t("commands.quick-battle.fighter.none_remaining"),
            inline=False
        )
        
        # Create view with button
        view = discord.ui.View()
        button = discord.ui.Button(
            label=self.guild.localization.t("commands.quick-battle.fighter.button_label"),
            style=discord.ButtonStyle.primary
        )
        button.callback = self._button_callback
        view.add_item(button)
        # Add abort button if owner is set
        if self.owner:
            view.add_item(CreatorAbortButton(self, self.owner, self.guild.localization.t("commands.quick-battle.communication.abort_button")))
        self.view = view
        
        # Send message
        self.message = await sendMessage(self.channel, self.guild, embed=embed, view=view)
        if self.message is None:
            # Failed to send message, abort
            self.aborted = True
            self.completed.set()
            return None
        
        # Wait for all submissions or abort
        await self.completed.wait()
        
        # Check if aborted
        if self.aborted:
            return None
        
        # Disable button
        view.clear_items()
        await editMessage(self.message, view=view)
        
        return self.fighters
    
    async def _abort(self):
        """Abort the fighter collection process."""
        self.aborted = True
        # Update message to show aborted status
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.fighter.title"),
            description=self.guild.localization.t("commands.quick-battle.communication.battle_aborted_message"),
            color=discord.Color.red()
        )
        self.view.clear_items()
        await editMessage(self.message, embed=embed, view=self.view)
        self.completed.set()


class FighterModal(discord.ui.Modal):
    def __init__(self, creator: FighterCreator):
        super().__init__(title=creator.guild.localization.t("commands.quick-battle.fighter.modal_title"))
        self.creator = creator
        
        self.name_input = discord.ui.TextInput(
            label=creator.guild.localization.t("commands.quick-battle.fighter.name_label"),
            placeholder=creator.guild.localization.t("commands.quick-battle.fighter.name_placeholder"),
            style=discord.TextStyle.short,
            required=True,
            max_length=100
        )
        self.add_item(self.name_input)
        
        self.description_input = discord.ui.TextInput(
            label=creator.guild.localization.t("commands.quick-battle.fighter.description_label"),
            placeholder=creator.guild.localization.t("commands.quick-battle.fighter.description_placeholder"),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.add_item(self.description_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user not in self.creator.participants:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.fighter.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.creator.submissions:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.fighter.already_submitted"),
                ephemeral=True
            )
            return
        
        name = self.name_input.value.strip()
        description = self.description_input.value.strip()
        
        if not name or not description:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.fighter.empty_input"),
                ephemeral=True
            )
            return
        
        fighter = Fighter(name, description, interaction.user)
        self.creator.submissions[interaction.user] = fighter
        self.creator.fighters[interaction.user] = fighter
        
        await interaction.response.send_message(
            self.creator.guild.localization.t("commands.quick-battle.fighter.submitted"),
            ephemeral=True
        )
        
        # Update message with remaining participants
        remaining = [p for p in self.creator.participants if p not in self.creator.submissions]
        if remaining:
            embed = discord.Embed(
                title=self.creator.guild.localization.t("commands.quick-battle.fighter.title"),
                description=self.creator.guild.localization.t("commands.quick-battle.fighter.description"),
                color=discord.Color.blue()
            )
            embed.add_field(
                name=self.creator.guild.localization.t("commands.quick-battle.fighter.remaining"),
                value="\n".join([p.mention for p in remaining]) if remaining else self.creator.guild.localization.t("commands.quick-battle.fighter.none_remaining"),
                inline=False
            )
            await editMessage(self.creator.message, embed=embed, view=self.creator.view)
        else:
            # All submitted
            self.creator.completed.set()
    

class EnvironmentCreator:
    def __init__(self, channel: discord.TextChannel, participants: list[discord.Member], owner: discord.Member, setting: SystemPrompt):
        self.channel = channel
        self.participants = participants
        self.environments = []
        self.environment = None
        self.guild = Guild(channel.guild)
        self.submissions = {}
        self.message = None
        self.completed = asyncio.Event()
        self.owner = owner
        self.aborted = False
        self.setting = setting
    
    async def combine_environment(self, environtments: list[str]) -> str:
        prompt = "\n---\n".join(environtments)
        prompts_list = [
            Prompts.Core.EnvironmentCombiner,
            self.setting,
            Prompts.Elements.Language.fill(locale=self.guild.localization.full_localization_name(self.guild.params.get("language")))
        ]
    
        return await PromptHandler.from_guild(self.guild).evaluateMultiple(prompts_list, prompt)
    
    async def _button_callback(self, interaction: discord.Interaction):
        """Handle button click to open modal."""
        if interaction.user not in self.participants:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.environment.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.submissions:
            await interaction.response.send_message(
                self.guild.localization.t("commands.quick-battle.environment.already_submitted"),
                ephemeral=True
            )
            return
        
        modal = EnvironmentModal(self)
        await interaction.response.send_modal(modal)
    
    async def get_environment(self) -> str:
        """Start the environment collection process and wait for all submissions."""
        # Create initial embed
        remaining = list(self.participants)
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.environment.title"),
            description=self.guild.localization.t("commands.quick-battle.environment.description"),
            color=discord.Color.blue()
        )
        embed.add_field(
            name=self.guild.localization.t("commands.quick-battle.environment.remaining"),
            value="\n".join([p.mention for p in remaining]) if remaining else self.guild.localization.t("commands.quick-battle.environment.none_remaining"),
            inline=False
        )
        
        # Create view with button
        view = discord.ui.View()
        button = discord.ui.Button(
            label=self.guild.localization.t("commands.quick-battle.environment.button_label"),
            style=discord.ButtonStyle.primary
        )
        button.callback = self._button_callback
        view.add_item(button)
        # Add abort button if owner is set
        if self.owner:
            view.add_item(CreatorAbortButton(self, self.owner, self.guild.localization.t("commands.quick-battle.communication.abort_button")))
        self.view = view
        
        # Send message
        self.message = await sendMessage(self.channel, self.guild, embed=embed, view=view)
        if self.message is None:
            # Failed to send message, abort
            self.aborted = True
            self.completed.set()
            return None
        
        # Wait for all submissions or abort
        await self.completed.wait()
        
        # Check if aborted
        if self.aborted:
            return None
        
        # Disable button
        view.clear_items()
        await editMessage(self.message, view=view)
        
        # Combine environments
        environment_list = list(self.submissions.values())
        self.environment = await self.combine_environment(environment_list)
        
        return self.environment
    
    async def _abort(self):
        """Abort the environment collection process."""
        self.aborted = True
        # Update message to show aborted status
        embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.environment.title"),
            description=self.guild.localization.t("commands.quick-battle.communication.battle_aborted_message"),
            color=discord.Color.red()
        )
        self.view.clear_items()
        await editMessage(self.message, embed=embed, view=self.view)
        self.completed.set()


class EnvironmentModal(discord.ui.Modal):
    def __init__(self, creator: EnvironmentCreator):
        super().__init__(title=creator.guild.localization.t("commands.quick-battle.environment.modal_title"))
        self.creator = creator
        
        self.environment_input = discord.ui.TextInput(
            label=creator.guild.localization.t("commands.quick-battle.environment.input_label"),
            placeholder=creator.guild.localization.t("commands.quick-battle.environment.input_placeholder"),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.environment_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user not in self.creator.participants:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.environment.not_participant"),
                ephemeral=True
            )
            return
        
        if interaction.user in self.creator.submissions:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.environment.already_submitted"),
                ephemeral=True
            )
            return
        
        environment_text = self.environment_input.value.strip()
        if not environment_text:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.environment.empty_input"),
                ephemeral=True
            )
            return
        
        self.creator.submissions[interaction.user] = environment_text
        self.creator.environments.append(environment_text)
        
        await interaction.response.send_message(
            self.creator.guild.localization.t("commands.quick-battle.environment.submitted"),
            ephemeral=True
        )
        
        # Update message with remaining participants
        remaining = [p for p in self.creator.participants if p not in self.creator.submissions]
        if remaining:
            embed = discord.Embed(
                title=self.creator.guild.localization.t("commands.quick-battle.environment.title"),
                description=self.creator.guild.localization.t("commands.quick-battle.environment.description"),
                color=discord.Color.blue()
            )
            embed.add_field(
                name=self.creator.guild.localization.t("commands.quick-battle.environment.remaining"),
                value="\n".join([p.mention for p in remaining]) if remaining else self.creator.guild.localization.t("commands.quick-battle.environment.none_remaining"),
                inline=False
            )
            await editMessage(self.creator.message, embed=embed, view=self.creator.view)
        else:
            # All submitted
            self.creator.completed.set()

class JoinButton(discord.ui.Button):
    def __init__(self, request: 'QuickBattleRequest', label: str):
        super().__init__(label=label, style=discord.ButtonStyle.green, custom_id="join_button")
        self.request = request
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user not in self.request.participants:
            self.request.participants.append(interaction.user)
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.joined_message", user=interaction.user.mention),
                ephemeral=True
            )
            await self.request.__update_async__()
        else:
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.already_joined"),
                ephemeral=True
            )


class LeaveButton(discord.ui.Button):
    def __init__(self, request: 'QuickBattleRequest', label: str):
        super().__init__(label=label, style=discord.ButtonStyle.red, custom_id="leave_button")
        self.request = request
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user in self.request.participants:
            self.request.participants.remove(interaction.user)
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.left_message", user=interaction.user.mention),
                ephemeral=True
            )
            await self.request.__update_async__()
        else:
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.not_joined"),
                ephemeral=True
            )


class StartButton(discord.ui.Button):
    def __init__(self, request: 'QuickBattleRequest', label: str):
        super().__init__(label=label, style=discord.ButtonStyle.green, custom_id="start_button")
        self.request = request
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.request.owner:
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.only_owner_can_start"),
                ephemeral=True
            )
            return
        
        await interaction.response.send_message(
            self.request.guild.localization.t("commands.quick-battle.communication.battle_starting"),
            ephemeral=True
        )
        # Start the battle immediately
        await self.request._start_battle()


class AbortButton(discord.ui.Button):
    def __init__(self, request: 'QuickBattleRequest', label: str):
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id="abort_button")
        self.request = request
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.request.owner:
            await interaction.response.send_message(
                self.request.guild.localization.t("commands.quick-battle.communication.only_owner_can_abort"),
                ephemeral=True
            )
            return
        
        await interaction.response.send_message(
            self.request.guild.localization.t("commands.quick-battle.communication.battle_aborted"),
            ephemeral=True
        )
        # Abort the battle
        await self.request._abort_battle()


class CreatorAbortButton(discord.ui.Button):
    """Abort button for Environment/Fighter/Strategy creators."""
    def __init__(self, creator, owner: discord.Member, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.danger, custom_id="creator_abort_button")
        self.creator = creator
        self.owner = owner
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.owner:
            await interaction.response.send_message(
                self.creator.guild.localization.t("commands.quick-battle.communication.only_owner_can_abort"),
                ephemeral=True
            )
            return
        
        await interaction.response.send_message(
            self.creator.guild.localization.t("commands.quick-battle.communication.battle_aborted"),
            ephemeral=True
        )
        # Abort the collection process
        await self.creator._abort()



class QuickBattleRequest:
    def __init__(self, message: discord.Message, 
                custom_environment: bool, timeout: int, 
                owner: discord.Member, guild: Guild, setting: SystemPrompt ):
        self.message = message
        self.custom_environment = custom_environment
        self.timeout = timeout
        self.timeelapsed = 0
        self.owner = owner
        self.guild = guild
        self.participants = [owner]
        self.embed = None
        self.ui = None
        self.timer = None
        self._timer_running = True
        self.setting = setting
        # Initialize UI in async context
        client = self.message.channel._state._get_client()
        if client:
            loop = client.loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self._async_initialize(), loop)
        # Start the timer
        self.timer = Timer(1, self.__tick__)
        self.timer.start()
    
    async def _async_initialize(self):
        """Initialize the message with embed and view in async context."""
        self.__update_embed()
        self.__update_ui()
        await editMessage(self.message, embed=self.embed, view=self.ui)

    def __tick__(self):
        if not self._timer_running:
            return
        self.timeelapsed += 1
        if self.timeelapsed >= self.timeout:
            self.__timeout__()
            return
        # Schedule next tick
        if self._timer_running:
            self.timer = Timer(1, self.__tick__)
            self.timer.start()
            self.__update__()

    def __update__(self):
        self.__update_embed()
        # Schedule async edit on event loop (View will be created in async context)
        client = self.message.channel._state._get_client()
        if client:
            loop = client.loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self._async_update_message(), loop)
    
    async def _async_update_message(self):
        """Update the message asynchronously, creating the View in the async context."""
        self.__update_ui()
        await editMessage(self.message, embed=self.embed, view=self.ui)
    
    async def __update_async__(self):
        """Async version of __update__ for use in async contexts."""
        self.__update_embed()
        self.__update_ui()
        await editMessage(self.message, embed=self.embed, view=self.ui)
    
    def __update_embed(self):
        if self.embed is not None:
            self.embed.clear_fields()
            self.embed.set_footer(text=None)
        remaining_time = max(0, self.timeout - self.timeelapsed)
        self.embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.communication.request_embed_title"), 
            description=self.guild.localization.t("commands.quick-battle.communication.request_embed_description", owner=self.owner.mention), 
            color=discord.Color.blue())
        participants_text = "\n".join([participant.mention for participant in self.participants]) if self.participants else "None"
        self.embed.add_field(name=self.guild.localization.t("commands.quick-battle.communication.request_embed_participants"), value=participants_text, inline=False)
        self.embed.add_field(name=self.guild.localization.t("commands.quick-battle.communication.environment_field"), value=self.guild.localization.t(f"commands.quick-battle.choices.custom_environment.{'custom' if self.custom_environment else 'generic'}"), inline=False)
        self.embed.set_footer(text=self.guild.localization.t("commands.quick-battle.communication.timeout_field", timeout=remaining_time))

    def __update_ui(self):
        """Create or update the UI View. Must be called from async context."""
        if self.ui is not None:
            self.ui.clear_items()
        self.ui = discord.ui.View()
        self.ui.add_item(JoinButton(self, self.guild.localization.t("commands.quick-battle.communication.join_button")))
        self.ui.add_item(LeaveButton(self, self.guild.localization.t("commands.quick-battle.communication.leave_button")))
        # Start button only for owner
        self.ui.add_item(StartButton(self, self.guild.localization.t("commands.quick-battle.communication.start_button")))
        # Abort button for everyone
        self.ui.add_item(AbortButton(self, self.guild.localization.t("commands.quick-battle.communication.abort_button")))

    def __timeout__(self):
        self._timer_running = False
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        # Update embed to show timeout
        self.__update_embed()
        # Schedule async edit and timeout processing on the event loop
        client = self.message.channel._state._get_client()
        if client:
            loop = client.loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self._async_timeout_complete(), loop)
    
    async def _async_timeout_complete(self):
        """Complete the timeout process asynchronously."""
        try:
            # Clear UI (create empty view in async context)
            self.ui = discord.ui.View()  # Empty view
            await editMessage(self.message, embed=self.embed, view=self.ui)
            await self._async_timeout()
        except Exception as e:
            logger.error(f"Error in timeout completion: {e}", exc_info=True, extra={"guild": f"{self.guild.guild.name}({self.guild.guild.id})" if self.guild and self.guild.guild else "Unknown"})
    
    async def _start_battle(self):
        """Start the battle immediately (before timeout)."""
        # Stop the timer
        self._timer_running = False
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        
        # Clear UI and update message
        self.__update_embed()
        self.ui = discord.ui.View()  # Empty view
        await editMessage(self.message, embed=self.embed, view=self.ui)
        
        # Start the battle process
        try:
            await self._async_timeout()
        except Exception as e:
            logger.error(f"Error in quick-battle starting: {e}", exc_info=True, extra={"guild": f"{self.guild.name}({self.guild.id})"})
            await sendMessage(self.message.channel, self.guild, self.guild.localization.t("errors.quick-battle_error"))
            return
    async def _abort_battle(self):
        """Abort the battle starting process."""
        # Stop the timer
        self._timer_running = False
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        
        # Update embed to show aborted status
        if self.embed is not None:
            self.embed.clear_fields()
            self.embed.set_footer(text=None)
        self.embed = discord.Embed(
            title=self.guild.localization.t("commands.quick-battle.communication.request_embed_title"),
            description=self.guild.localization.t("commands.quick-battle.communication.battle_aborted_message"),
            color=discord.Color.red()
        )
        
        # Clear UI
        self.ui = discord.ui.View()  # Empty view
        await editMessage(self.message, embed=self.embed, view=self.ui)
    
    async def _async_timeout(self):
        """Async part of timeout that creates environments, fighters, and strategies."""
        if self.custom_environment:
            environment = await EnvironmentCreator(self.message.channel, self.participants, self.owner, self.setting).get_environment()
            if environment is None:  # Aborted
                return
            await sendMessage(self.message.channel, self.guild, environment)
            environment = Prompts.Elements.CustomEnvironment.fill(env=environment)
        else:
            environment = Prompts.Core.GenericEnvironment
        logger.debug(f"Environment: {environment}", extra={"guild": f"{self.guild.name}({self.guild.id})"})
        fighters = await FighterCreator(self.message.channel, self.participants, self.owner).get_fighters()
        if fighters is None:  # Aborted
            return
        logger.debug(f"Fighters: {fighters}", extra={"guild": f"{self.guild.name}({self.guild.id})"})
        fighters = await StrategyCreator(self.message.channel, fighters, self.owner).get_strategy()
        if fighters is None:  # Aborted
            return
        logger.debug(f"Fighters with strategies: {fighters}", extra={"guild": f"{self.guild.name}({self.guild.id})"})
        fightersPrompt = FighterPrompt().fill(fighters)
        metadata = BattleMetadata(
            self.guild, 
            datetime.datetime.now(), 
            environment="custom" if self.custom_environment else "generic", 
            fighters=[(member.id, fighter.name, fighter.description) for member, fighter in fighters.items()], 
            setting=self.setting, 
            participants=[(participant.id, participant.name) for participant in self.participants]
        )
        prompts_list = [
            Prompts.Core.SimpleBattle,
            environment,
            fightersPrompt, 
            self.setting,
            Prompts.Elements.Language.fill(locale=self.guild.localization.full_localization_name(self.guild.params.get("language")))
        ]
        await sendMessage(self.message.channel, self.guild, self.guild.localization.t("commands.quick-battle.communication.evaluating_prompts"))
        FightResult = await PromptHandler.from_guild(self.guild).evaluateMultiple(prompts_list, prompt=f"Start the battle {random_string(128)}")
        save_battle_result(self.guild, metadata, FightResult, folder="quick-battle")
        await sendMessage(self.message.channel, self.guild, FightResult)
    
class BattleHandler:
    def __init__(self, bot: discord.Client):
        self.bot = bot


        @self.bot.tree.command(
            name="quick-battle",
            description=lstr("commands.quick-battle.description", default="Start a quick battle")
        )
        @discord.app_commands.describe(
            custom_environment=lstr("commands.quick-battle.args.custom_environment", default="Generic or custom?"),
            timeout=lstr("commands.quick-battle.args.timeout", default="Timeout in seconds"),
            setting=lstr("commands.quick-battle.args.setting", default="Battle setting/style"),
        )
        @discord.app_commands.choices(
            custom_environment=[
                discord.app_commands.Choice(
                    name=lstr("commands.quick-battle.args.custom_environment.choices.generic", default="Generic"),
                    value=0
                ),
                discord.app_commands.Choice(
                    name=lstr("commands.quick-battle.args.custom_environment.choices.custom", default="Custom"),
                    value=1
                ),
            ],
            setting=[
                discord.app_commands.Choice(name=key, value=key)
                for key in SETTINGS.keys()
            ]
        )
        @ProcessCommand(self.bot, allowed_permissions=[])
        async def quick_battle(
            interaction: discord.Interaction,
            custom_environment: discord.app_commands.Choice[int],
            timeout: typing.Annotated[int, discord.app_commands.Range[int, 30, 600]] = 60,
            setting: discord.app_commands.Choice[str] = None,
            guild: Guild = None,
            executor: discord.Member = None,
        ):
            await interaction.response.send_message("@everyone")
            message = await interaction.original_response()
            setting_prompt = SETTINGS.get(setting.value) if setting and setting.value else SETTINGS.get("unpredictable-funny")
            if setting_prompt is None:
                setting_prompt = SETTINGS.get("unpredictable-funny")
            QuickBattleRequest(message, bool(custom_environment.value), timeout, executor, guild, setting_prompt)