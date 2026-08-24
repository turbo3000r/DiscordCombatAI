"""/quick-battle slash command handler."""

from __future__ import annotations

from typing import Any

import discord

from bot.modules.commands.battle.models import SETTINGS
from bot.modules.commands.battle.UI.views import LobbyView
from bot.modules.commands.context import CommandContext

QUICK_BATTLE_GUARDS = {
    "required_guild": True,
    "required_guild_enabled": True,
    "allowed_permissions": None,
    "blocked_during_drain": True,
}


class InteractionMessenger:
    def __init__(self, interaction: discord.Interaction) -> None:
        self._interaction = interaction
        self.mentionable: set[str] = set()

    def _mentions(self, mention_user_ids: list[str] | None) -> discord.AllowedMentions:
        if mention_user_ids:
            return discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(id=int(user_id)) for user_id in mention_user_ids],
                replied_user=False,
            )
        return discord.AllowedMentions.none()

    async def send(
        self,
        content: str,
        *,
        view: Any | None = None,
        mention_user_ids: list[str] | None = None,
    ) -> str:
        channel = self._interaction.channel
        sender = getattr(channel, "send", None)
        if sender is None:
            raise RuntimeError("channel missing")
        message = await sender(
            content,
            view=view,
            allowed_mentions=self._mentions(mention_user_ids),
        )
        return str(message.id)

    async def edit(
        self,
        message_id: str,
        content: str,
        *,
        view: Any | None = None,
        disable: bool = False,
        allowed_mentions: discord.AllowedMentions | None = None,
        mention_user_ids: list[str] | None = None,
    ) -> None:
        channel = self._interaction.channel
        getter = getattr(channel, "get_partial_message", None)
        if getter is None:
            return
        message = getter(int(message_id))
        if disable and view is not None:
            for child in getattr(view, "children", []):
                if hasattr(child, "disabled"):
                    child.disabled = True
        mentions = (
            allowed_mentions if allowed_mentions is not None else self._mentions(mention_user_ids)
        )
        await message.edit(content=content, view=view, allowed_mentions=mentions)

    async def send_file(self, *, filename: str, data: bytes, preview: str) -> None:
        sender = getattr(self._interaction.channel, "send", None)
        if sender is None:
            return
        await sender(
            preview,
            file=discord.File(fp=__import__("io").BytesIO(data), filename=filename),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def can_mention(self, user_id: str) -> bool:
        if user_id in self.mentionable:
            return True
        guild = getattr(self._interaction, "guild", None)
        getter = getattr(guild, "get_member", None) if guild is not None else None
        if getter is None:
            return False
        try:
            member = getter(int(user_id))
        except (TypeError, ValueError):
            return False
        return member is not None


async def handle_quick_battle_command(
    interaction: discord.Interaction,
    ctx: CommandContext,
    application: Any,
    custom_environment: int,
    timeout: int = 60,
    setting: str = "unpredictable-funny",
) -> None:
    service = application.quick_battle
    guild_config = ctx.guild_config
    if service is None or guild_config is None or ctx.guild_id is None:
        raise RuntimeError("quick-battle is unavailable")
    if setting not in SETTINGS:
        setting = "unpredictable-funny"
    messenger = InteractionMessenger(interaction)
    translate = ctx.t
    try:
        session = await service.start_lobby(
            guild_id=ctx.guild_id,
            channel_id=str(interaction.channel_id or ""),
            owner_id=ctx.user_id,
            owner_name=getattr(interaction.user, "display_name", str(interaction.user)),
            locale=ctx.locale_key,
            setting=setting,
            custom_environment=bool(custom_environment),
            lobby_timeout_sec=timeout,
            api_key=guild_config.api_key or "",
            model=guild_config.model or "",
            messenger=messenger,
            translate=translate,
        )
    except PermissionError as exc:
        key = str(exc.args[0]) if exc.args else "errors.error_unexpected"
        if not interaction.response.is_done():
            await interaction.response.send_message(translate(key), ephemeral=True)
        else:
            await interaction.followup.send(translate(key), ephemeral=True)
        return
    view = LobbyView(service=service, session_id=session.session_id, translate=translate)
    if not interaction.response.is_done():
        await interaction.response.send_message(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        original = await interaction.original_response()
        session.lobby_message_id = str(original.id)
        session.active_message_id = str(original.id)
    else:
        message_id = await messenger.send(translate("commands.quick-battle.lobby_title"), view=view)
        session.lobby_message_id = message_id
        session.active_message_id = message_id


__all__ = ["QUICK_BATTLE_GUARDS", "handle_quick_battle_command"]
