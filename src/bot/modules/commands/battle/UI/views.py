"""Command-specific quick-battle views and modals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import discord
from discord import ui

from bot.modules.commands.battle.service.session import QuickBattleService


class _GuardedView(ui.LayoutView):
    def __init__(
        self,
        *,
        service: QuickBattleService,
        session_id: str,
        translate: Callable[[str], str],
        timeout: float | None = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.session_id = session_id
        self.translate = translate

    async def _guard(self, interaction: discord.Interaction) -> bool:
        guard = getattr(interaction.client, "guard", None)
        if guard is None:
            return True
        try:
            await guard.evaluate(
                interaction,
                required_guild=True,
                required_guild_enabled=True,
                allowed_permissions=None,
                blocked_during_drain=True,
            )
            return True
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    self.translate("commands.quick-battle.session_expired"),
                    ephemeral=True,
                )
            return False


class LobbyView(_GuardedView):
    def __init__(
        self, *, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(service=service, session_id=session_id, translate=translate, timeout=600)
        join: Any = ui.Button(
            label=translate("commands.quick-battle.join"), style=discord.ButtonStyle.primary
        )
        leave: Any = ui.Button(
            label=translate("commands.quick-battle.leave"), style=discord.ButtonStyle.secondary
        )
        start: Any = ui.Button(
            label=translate("commands.quick-battle.start"), style=discord.ButtonStyle.success
        )
        abort: Any = ui.Button(
            label=translate("commands.quick-battle.abort"), style=discord.ButtonStyle.danger
        )
        join.callback = self.on_join
        leave.callback = self.on_leave
        start.callback = self.on_start
        abort.callback = self.on_abort
        self.add_item(ui.TextDisplay(content=translate("commands.quick-battle.lobby_title")))
        self.add_item(ui.ActionRow(join, leave))
        self.add_item(ui.ActionRow(start, abort))

    async def on_join(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        self.service.join(
            self.session_id,
            str(interaction.user.id),
            getattr(interaction.user, "display_name", str(interaction.user)),
        )
        await interaction.response.defer()

    async def on_leave(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        self.service.leave(self.session_id, str(interaction.user.id))
        await interaction.response.defer()

    async def on_start(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.start_from_lobby(self.session_id, str(interaction.user.id))
        await interaction.response.defer()

    async def on_abort(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.abort(self.session_id, str(interaction.user.id))
        await interaction.response.defer()


class SequentialCollectorView(_GuardedView):
    def __init__(
        self,
        *,
        service: QuickBattleService,
        session_id: str,
        translate: Callable[[str], str],
        kind: str,
        timeout: float,
    ) -> None:
        super().__init__(
            service=service, session_id=session_id, translate=translate, timeout=timeout
        )
        self.kind = kind
        button: Any = ui.Button(
            label=translate("commands.quick-battle.submit"), style=discord.ButtonStyle.primary
        )
        abort: Any = ui.Button(
            label=translate("commands.quick-battle.abort"), style=discord.ButtonStyle.danger
        )
        button.callback = self.on_submit
        abort.callback = self.on_abort
        self.add_item(ui.TextDisplay(content=translate("commands.quick-battle.submit")))
        self.add_item(ui.ActionRow(button, abort))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        if self.kind == "environment":
            await interaction.response.send_modal(
                EnvironmentModal(self.service, self.session_id, self.translate)
            )
            return
        await interaction.response.send_modal(
            FighterModal(self.service, self.session_id, self.translate)
        )

    async def on_abort(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.abort(self.session_id, str(interaction.user.id))
        await interaction.response.defer()


class EnvironmentApprovalView(_GuardedView):
    def __init__(
        self, *, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(service=service, session_id=session_id, translate=translate, timeout=120)
        approve: Any = ui.Button(
            label=translate("commands.quick-battle.approve"), style=discord.ButtonStyle.success
        )
        decline: Any = ui.Button(
            label=translate("commands.quick-battle.decline"), style=discord.ButtonStyle.danger
        )
        abort: Any = ui.Button(
            label=translate("commands.quick-battle.abort"), style=discord.ButtonStyle.danger
        )
        approve.callback = self.on_approve
        decline.callback = self.on_decline
        abort.callback = self.on_abort
        self.add_item(ui.TextDisplay(content=translate("commands.quick-battle.approve")))
        self.add_item(ui.ActionRow(approve, decline, abort))

    async def on_approve(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.vote(self.session_id, str(interaction.user.id), approve=True)
        await interaction.response.defer()

    async def on_decline(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(
            DeclineReasonModal(self.service, self.session_id, self.translate)
        )

    async def on_abort(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.abort(self.session_id, str(interaction.user.id))
        await interaction.response.defer()


class TaskProgressContainer(_GuardedView):
    def __init__(
        self,
        *,
        service: QuickBattleService,
        session_id: str,
        translate: Callable[[str], str],
        timeout: float | None = None,
    ) -> None:
        super().__init__(
            service=service,
            session_id=session_id,
            translate=translate,
            timeout=timeout,
        )
        lines = [
            f"⬜ {translate('commands.quick-battle.phase_queued')}",
            f"⬜ {translate('commands.quick-battle.phase_launching')}",
            f"⬜ {translate('commands.quick-battle.phase_composing')}",
            f"⬜ {translate('commands.quick-battle.phase_refining')}",
            f"⬜ {translate('commands.quick-battle.phase_finishing')}",
        ]
        self.add_item(ui.TextDisplay(content="\n".join(lines)))
        abort: Any = ui.Button(
            label=translate("commands.quick-battle.abort"), style=discord.ButtonStyle.danger
        )
        abort.callback = self.on_abort
        self.add_item(ui.ActionRow(abort))

    async def on_abort(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await self.service.abort(self.session_id, str(interaction.user.id))
        await interaction.response.defer()


class EnvironmentModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.submit"))
        self.service = service
        self.session_id = session_id
        self.field: Any = ui.TextInput(
            label=translate("commands.quick-battle.submit"),
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.submit_environment(
            self.session_id, str(interaction.user.id), str(self.field.value)
        )
        await interaction.response.defer()


class DeclineReasonModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.decline"))
        self.service = service
        self.session_id = session_id
        self.field: Any = ui.TextInput(
            label=translate("commands.quick-battle.decline"),
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.vote(
            self.session_id, str(interaction.user.id), approve=False, comment=str(self.field.value)
        )
        await interaction.response.defer()


class FighterModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.submit"))
        self.service = service
        self.session_id = session_id
        self.fighter_name: Any = ui.TextInput(label="name", max_length=80)
        self.description: Any = ui.TextInput(
            label="description", style=discord.TextStyle.paragraph, max_length=1000
        )
        self.strategy: Any = ui.TextInput(
            label="strategy", style=discord.TextStyle.paragraph, required=False, max_length=500
        )
        self.add_item(self.fighter_name)
        self.add_item(self.description)
        self.add_item(self.strategy)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.service.submit_fighter(
            self.session_id,
            str(interaction.user.id),
            fighter_name=str(self.fighter_name.value),
            description=str(self.description.value),
            strategy=str(self.strategy.value) or None,
        )
        await interaction.response.defer()


__all__ = [
    "DeclineReasonModal",
    "EnvironmentApprovalView",
    "EnvironmentModal",
    "FighterModal",
    "LobbyView",
    "SequentialCollectorView",
    "TaskProgressContainer",
]
