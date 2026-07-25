"""Shared Components V2 staged-settings shell."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import ui

logger = logging.getLogger(__name__)

OwnerCheck = Callable[[discord.Interaction], Awaitable[bool]]
TerminalReason = str  # "timeout" | "cancel" | "apply" | "success"


class StagedSettingsView(ui.LayoutView):
    """Ownership-aware LayoutView shell with serialized mutations and terminal rebuild."""

    def __init__(
        self,
        *,
        owner_id: int,
        timeout: float,
        locale_key: str,
        t: Callable[..., str],
        guard_check: OwnerCheck | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.locale_key = locale_key
        self._t = t
        self._guard_check = guard_check
        self._lock = asyncio.Lock()
        self.terminal: TerminalReason | None = None
        self.busy = False
        self.inline_error: str | None = None
        self.status_text: str | None = None

    def t(self, key: str, **variables: Any) -> str:
        return self._t(key, locale=self.locale_key, **variables)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                self.t("errors.foreign_user"),
                ephemeral=True,
            )
            return False
        if self._guard_check is not None:
            try:
                allowed = await self._guard_check(interaction)
            except Exception:  # noqa: BLE001
                logger.exception("staged settings guard failed")
                allowed = False
            if not allowed:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        self.t("errors.denied_permission"),
                        ephemeral=True,
                    )
                return False
        return True

    async def on_timeout(self) -> None:
        if self.terminal is not None:
            return
        async with self._lock:
            self.terminal = "timeout"
            self.busy = False
            self.status_text = self.timeout_message()
            self.rebuild(disabled=True)

    def timeout_message(self) -> str:
        return self.t("commands.config.timeout")

    def cancel_message(self) -> str:
        return self.t("commands.config.cancelled")

    async def mark_terminal(self, reason: TerminalReason, *, status: str | None = None) -> None:
        async with self._lock:
            self.terminal = reason
            self.busy = False
            if status is not None:
                self.status_text = status
            elif reason == "cancel":
                self.status_text = self.cancel_message()
            elif reason == "timeout":
                self.status_text = self.timeout_message()
            self.rebuild(disabled=True)

    async def run_exclusive(self, work: Callable[[], Awaitable[None]]) -> bool:
        """Serialize button/select handlers; return False if already busy/terminal."""
        if self.terminal is not None:
            return False
        if self._lock.locked() or self.busy:
            return False
        async with self._lock:
            if self.terminal is not None or self.busy:
                return False
            self.busy = True
            try:
                await work()
            finally:
                if self.terminal is None:
                    self.busy = False
        return True

    def clear_layout(self) -> None:
        for child in list(self.children):
            self.remove_item(child)

    def rebuild(self, *, disabled: bool = False) -> None:
        """Rebuild the component tree from immutable current state."""
        self.clear_layout()
        items = self.build_items(disabled=disabled or self.terminal is not None)
        self.add_item(ui.Container(*items))

    def build_items(self, *, disabled: bool) -> list[ui.Item[Any]]:
        raise NotImplementedError


__all__ = ["StagedSettingsView", "TerminalReason"]
