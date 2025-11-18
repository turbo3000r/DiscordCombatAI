"""
Bridge utilities to let the FastAPI server interact with the Discord bot instance.
"""
import asyncio
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands

from modules.utils import (
    append_conversation_entry,
    find_suggestion_by_id,
    update_suggestion_record,
)

_bot_instance: Optional[commands.Bot] = None


def set_bot(bot: Optional[commands.Bot]) -> None:
    """Store a reference to the running bot for later use by the web server."""
    global _bot_instance
    _bot_instance = bot


def get_bot() -> Optional[commands.Bot]:
    """Return the stored bot instance, if any."""
    return _bot_instance


class SuggestionFollowupModal(discord.ui.Modal):
    """Modal shown to users when they click the follow-up button in DMs."""

    def __init__(self, suggestion_id: str, ticket_uid: str):
        super().__init__(title=f"Ticket {ticket_uid} follow-up")
        self.suggestion_id = suggestion_id
        self.ticket_uid = ticket_uid

        self.followup_input = discord.ui.TextInput(
            label="Additional details",
            placeholder="Share more context or updates for this ticket…",
            style=discord.TextStyle.long,
            max_length=2000,
            required=True,
        )
        self.add_item(self.followup_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        message = self.followup_input.value.strip()
        if not message:
            await interaction.response.send_message("Please provide some details.", ephemeral=True)
            return

        closed = {"value": False}
        timestamp = datetime.utcnow().isoformat()

        def _update(entry: dict):
            if entry.get("responded"):
                closed["value"] = True
                return
            append_conversation_entry(
                entry,
                author_role="user",
                direction="incoming",
                text=message,
                created_at=timestamp,
                metadata={"ticket_uid": self.ticket_uid, "via": "user_followup"},
                source="user_followup",
            )

        updated = update_suggestion_record(self.suggestion_id, _update)
        if updated is None:
            await interaction.response.send_message("We couldn't find that ticket anymore.", ephemeral=True)
            return
        if closed["value"]:
            await interaction.response.send_message(
                "This ticket has already been marked as done, so follow-ups are disabled.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Thanks! Your follow-up was sent to the team.", ephemeral=True)


class SuggestionFollowupView(discord.ui.View):
    """View attached to staff responses so users can send follow-up details."""

    def __init__(self, suggestion_id: str, ticket_uid: str):
        super().__init__(timeout=None)
        self.suggestion_id = suggestion_id
        self.ticket_uid = ticket_uid

    @discord.ui.button(label="Add more details", style=discord.ButtonStyle.secondary)
    async def open_followup_modal(self, interaction: discord.Interaction, _: discord.ui.Button):
        suggestion = find_suggestion_by_id(self.suggestion_id)
        if not suggestion:
            await interaction.response.send_message("This ticket is no longer available.", ephemeral=True)
            return
        if suggestion.get("responded"):
            await interaction.response.send_message(
                "This ticket has been marked as done, so follow-ups are disabled.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SuggestionFollowupModal(self.suggestion_id, self.ticket_uid))


async def send_user_dm(
    user_id: int,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> bool:
    """
    Send a DM to the given user by scheduling the coroutine on the bot's loop.

    Returns:
        bool: True if the message was sent, False otherwise.
    """
    if not content and not embed:
        return False

    bot = get_bot()
    if bot is None or bot.user is None:
        return False

    loop = bot.loop
    if loop is None or not loop.is_running():
        return False

    async def _send():
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                return False
        if user is None:
            return False
        try:
            await user.send(content=content, embed=embed, view=view)
            return True
        except Exception:
            return False

    thread_safe_future = asyncio.run_coroutine_threadsafe(_send(), loop)
    try:
        wrapped = asyncio.wrap_future(thread_safe_future)
        return await asyncio.wait_for(wrapped, timeout=10)
    except asyncio.TimeoutError:
        thread_safe_future.cancel()
        return False
    except Exception:
        return False


async def send_suggestion_response_dm(
    suggestion: dict,
    message_text: str,
    *,
    allow_followup: bool,
) -> bool:
    """
    Send a suggestion response DM with an optional follow-up button.
    """
    user_info = suggestion.get("user") or {}
    user_id = user_info.get("id")
    if not user_id:
        return False

    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return False

    view: Optional[discord.ui.View] = None
    if allow_followup:
        view = SuggestionFollowupView(
            suggestion_id=str(suggestion.get("id")),
            ticket_uid=str(suggestion.get("ticket_uid") or "N/A"),
        )

    title = suggestion.get("title") or "Suggestion update"
    ticket_uid = str(suggestion.get("ticket_uid") or "N/A")
    embed = discord.Embed(
        title=title,
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"Ticket {ticket_uid}")

    if message_text:
        chunk_size = 1024
        chunks = [message_text[i : i + chunk_size] for i in range(0, len(message_text), chunk_size)] or [""]
        for index, chunk in enumerate(chunks):
            field_name = "Response" if index == 0 else f"Response (cont. {index})"
            embed.add_field(name=field_name, value=chunk, inline=False)

    return await send_user_dm(numeric_user_id, content=None, embed=embed, view=view)

