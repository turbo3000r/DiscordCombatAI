"""Command-specific quick-battle views and modals."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

import discord
from discord import ui

from bot.modules.commands.battle.service.session import QuickBattleService
from bot.modules.commands.battle.v2 import localization_key_for_denial
from shared.models import TaskPhase

logger = logging.getLogger(__name__)

_PHASE_ORDER = (
    TaskPhase.queued,
    TaskPhase.launching,
    TaskPhase.composing,
    TaskPhase.refining,
    TaskPhase.finishing,
)
_PHASE_KEYS = {
    TaskPhase.queued: "commands.quick-battle.phase_queued",
    TaskPhase.launching: "commands.quick-battle.phase_launching",
    TaskPhase.composing: "commands.quick-battle.phase_composing",
    TaskPhase.refining: "commands.quick-battle.phase_refining",
    TaskPhase.finishing: "commands.quick-battle.phase_finishing",
}


def progress_checklist(translate: Callable[[str], str], current: TaskPhase) -> str:
    try:
        reached = _PHASE_ORDER.index(current)
    except ValueError:
        reached = 0
    lines = []
    for index, phase in enumerate(_PHASE_ORDER):
        mark = "✅" if index <= reached else "⬜"
        lines.append(f"{mark} {translate(_PHASE_KEYS[phase])}")
    return "\n".join(lines)


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

    def _log_extra(self, interaction: discord.Interaction, **more: Any) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
            "user_id": str(interaction.user.id),
            "command": "quick-battle",
            "interaction_id": str(interaction.id),
            "session_id": self.session_id,
            "trace_id": self.session_id,
        }
        extra.update(more)
        return extra

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

    async def _run_guarded(
        self, interaction: discord.Interaction, action: Callable[[], Any]
    ) -> None:
        if not await self._guard(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        logger.info(
            "quick-battle callback starting",
            extra=self._log_extra(interaction),
        )
        try:
            result = action()
            if inspect.isawaitable(result):
                await result
            logger.info(
                "quick-battle callback finished",
                extra=self._log_extra(interaction, decision="allowed"),
            )
        except PermissionError as exc:
            key = localization_key_for_denial(str(exc.args[0]) if exc.args else "")
            logger.info(
                "quick-battle callback denied key=%s",
                key,
                extra=self._log_extra(
                    interaction, decision=str(exc.args[0]) if exc.args else "denied"
                ),
            )
            await interaction.followup.send(self.translate(key), ephemeral=True)
        except discord.HTTPException:
            logger.exception("quick-battle interaction failed", extra=self._log_extra(interaction))
            await interaction.followup.send(
                self.translate("errors.error_unexpected"),
                ephemeral=True,
            )


async def _complete_modal(
    interaction: discord.Interaction,
    translate: Callable[[str], str],
    action: Callable[[], Any],
    *,
    session_id: str,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()
    extra = {
        "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
        "user_id": str(interaction.user.id),
        "command": "quick-battle",
        "interaction_id": str(interaction.id),
        "session_id": session_id,
        "trace_id": session_id,
    }
    logger.info("quick-battle modal starting", extra=extra)
    try:
        result = action()
        if inspect.isawaitable(result):
            await result
        logger.info("quick-battle modal finished", extra={**extra, "decision": "allowed"})
    except PermissionError as exc:
        key = localization_key_for_denial(str(exc.args[0]) if exc.args else "")
        logger.info(
            "quick-battle modal denied key=%s",
            key,
            extra={**extra, "decision": str(exc.args[0]) if exc.args else "denied"},
        )
        await interaction.followup.send(translate(key), ephemeral=True)
    except Exception:
        logger.exception("quick-battle modal failed", extra=extra)
        await interaction.followup.send(
            translate("errors.error_unexpected"),
            ephemeral=True,
        )


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
        await self._run_guarded(
            interaction,
            lambda: self.service.join(
                self.session_id,
                str(interaction.user.id),
                getattr(interaction.user, "display_name", str(interaction.user)),
            ),
        )

    async def on_leave(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.leave(self.session_id, str(interaction.user.id)),
        )

    async def on_start(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.start_from_lobby(self.session_id, str(interaction.user.id)),
        )

    async def on_abort(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.abort(self.session_id, str(interaction.user.id)),
        )


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
        await self._run_guarded(
            interaction,
            lambda: self.service.abort(self.session_id, str(interaction.user.id)),
        )


class EnvironmentApprovalView(_GuardedView):
    def __init__(
        self,
        *,
        service: QuickBattleService,
        session_id: str,
        translate: Callable[[str], str],
        description: str | None = None,
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
        body = description.strip() if description else translate("commands.quick-battle.approve")
        self.add_item(ui.TextDisplay(content=body))
        self.add_item(ui.ActionRow(approve, decline, abort))

    async def on_approve(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.vote(self.session_id, str(interaction.user.id), approve=True),
        )

    async def on_decline(self, interaction: discord.Interaction) -> None:
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(
            DeclineReasonModal(self.service, self.session_id, self.translate)
        )

    async def on_abort(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.abort(self.session_id, str(interaction.user.id)),
        )


class TaskProgressContainer(_GuardedView):
    def __init__(
        self,
        *,
        service: QuickBattleService,
        session_id: str,
        translate: Callable[[str], str],
        timeout: float | None = None,
        current_phase: TaskPhase = TaskPhase.queued,
    ) -> None:
        super().__init__(
            service=service,
            session_id=session_id,
            translate=translate,
            timeout=timeout,
        )
        self.add_item(ui.TextDisplay(content=progress_checklist(translate, current_phase)))
        abort: Any = ui.Button(
            label=translate("commands.quick-battle.abort"), style=discord.ButtonStyle.danger
        )
        abort.callback = self.on_abort
        self.add_item(ui.ActionRow(abort))

    async def on_abort(self, interaction: discord.Interaction) -> None:
        await self._run_guarded(
            interaction,
            lambda: self.service.abort(self.session_id, str(interaction.user.id)),
        )


class EnvironmentModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.submit"))
        self.service = service
        self.session_id = session_id
        self.translate = translate
        self.field: Any = ui.TextInput(
            label=translate("commands.quick-battle.submit"),
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _complete_modal(
            interaction,
            self.translate,
            lambda: self.service.submit_environment(
                self.session_id, str(interaction.user.id), str(self.field.value)
            ),
            session_id=self.session_id,
        )


class DeclineReasonModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.decline"))
        self.service = service
        self.session_id = session_id
        self.translate = translate
        self.field: Any = ui.TextInput(
            label=translate("commands.quick-battle.decline"),
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _complete_modal(
            interaction,
            self.translate,
            lambda: self.service.vote(
                self.session_id,
                str(interaction.user.id),
                approve=False,
                comment=str(self.field.value),
            ),
            session_id=self.session_id,
        )


class FighterModal(ui.Modal):
    def __init__(
        self, service: QuickBattleService, session_id: str, translate: Callable[[str], str]
    ) -> None:
        super().__init__(title=translate("commands.quick-battle.submit"))
        self.service = service
        self.session_id = session_id
        self.translate = translate
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
        await _complete_modal(
            interaction,
            self.translate,
            lambda: self.service.submit_fighter(
                self.session_id,
                str(interaction.user.id),
                fighter_name=str(self.fighter_name.value),
                description=str(self.description.value),
                strategy=str(self.strategy.value) or None,
            ),
            session_id=self.session_id,
        )


__all__ = [
    "DeclineReasonModal",
    "EnvironmentApprovalView",
    "EnvironmentModal",
    "FighterModal",
    "LobbyView",
    "SequentialCollectorView",
    "TaskProgressContainer",
    "progress_checklist",
]
