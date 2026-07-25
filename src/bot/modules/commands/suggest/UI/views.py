"""/suggest Components V2 UI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import ui

from bot.modules.commands.suggest.UI.state import SuggestionDraft
from bot.modules.UI.views.staged_settings import StagedSettingsView

logger = logging.getLogger(__name__)

SubmitCallback = Callable[["SuggestionView", str, str], Awaitable[None]]


class SuggestionModal(ui.Modal, title="Suggestion"):
    title_input: ui.TextInput[Any] = ui.TextInput(
        label="Title",
        style=discord.TextStyle.short,
        required=True,
        min_length=1,
        max_length=200,
    )
    details_input: ui.TextInput[Any] = ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=1,
        max_length=1800,
    )

    def __init__(self, view: SuggestionView) -> None:
        super().__init__()
        self._view = view
        self.title = view.t("commands.suggest.modal_title")
        self.title_input.label = view.t("commands.suggest.title_label")
        self.details_input.label = view.t("commands.suggest.details_label")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._view.handle_modal_submit(
            interaction,
            title=str(self.title_input.value),
            details=str(self.details_input.value),
        )


class SuggestionView(StagedSettingsView):
    def __init__(
        self,
        *,
        owner_id: int,
        draft: SuggestionDraft,
        locale_key: str,
        t: Callable[..., str],
        on_submit: SubmitCallback | None = None,
        guard_check: Callable[[discord.Interaction], Awaitable[bool]] | None = None,
        timeout: float = 600.0,
    ) -> None:
        super().__init__(
            owner_id=owner_id,
            timeout=timeout,
            locale_key=locale_key,
            t=t,
            guard_check=guard_check,
        )
        self.draft = draft
        self.on_submit = on_submit
        self.rebuild(disabled=False)

    def timeout_message(self) -> str:
        return self.t("commands.suggest.timeout")

    def cancel_message(self) -> str:
        return self.t("commands.suggest.timeout")

    def build_items(self, *, disabled: bool) -> list[ui.Item[Any]]:
        items: list[ui.Item[Any]] = [ui.TextDisplay(self.t("commands.suggest.prompt"))]
        if self.inline_error:
            items.append(ui.TextDisplay(f"**!** {self.inline_error}"))
        if self.status_text:
            items.append(ui.TextDisplay(self.status_text))

        type_options = [
            discord.SelectOption(
                label=label[:100],
                value=value[:100],
                default=self.draft.type_value == value,
            )
            for value, label in self.draft.type_options[:25]
        ] or [discord.SelectOption(label="—", value="__none__")]
        type_select: ui.Select[Any] = ui.Select(
            placeholder="Type",
            options=type_options,
            disabled=disabled or self.busy or not self.draft.type_options,
            min_values=1,
            max_values=1,
        )
        type_select.callback = self._on_type  # type: ignore[method-assign]
        items.append(ui.ActionRow(type_select))

        cat_options = [
            discord.SelectOption(
                label=label[:100],
                value=value[:100],
                default=value in self.draft.category_values,
            )
            for value, label in self.draft.category_options[:25]
        ] or [discord.SelectOption(label="—", value="__none__")]
        max_values = max(1, min(25, len(cat_options)))
        category_select: ui.Select[Any] = ui.Select(
            placeholder="Categories",
            options=cat_options,
            disabled=disabled or self.busy or not self.draft.category_options,
            min_values=1,
            max_values=max_values,
        )
        category_select.callback = self._on_categories  # type: ignore[method-assign]
        items.append(ui.ActionRow(category_select))

        open_modal: ui.Button[Any] = ui.Button(
            label=self.t("commands.suggest.open_modal"),
            style=discord.ButtonStyle.primary,
            disabled=disabled or self.busy or not self.draft.can_open_modal,
        )
        open_modal.callback = self._on_open_modal  # type: ignore[method-assign]
        items.append(ui.ActionRow(open_modal))
        return items

    def _values_from_interaction(self, interaction: discord.Interaction) -> list[str]:
        data = interaction.data
        if isinstance(data, dict):
            raw = data.get("values") or []
            if isinstance(raw, list):
                return [str(v) for v in raw]
        return []

    async def _on_type(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            values = self._values_from_interaction(interaction)
            if values and values[0] != "__none__":
                self.draft.type_value = values[0]
            self.inline_error = None

        if await self.run_exclusive(_work):
            self.rebuild(disabled=False)
            await interaction.response.edit_message(view=self)

    async def _on_categories(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            values = [v for v in self._values_from_interaction(interaction) if v != "__none__"]
            self.draft.category_values = values
            self.inline_error = None

        if await self.run_exclusive(_work):
            self.rebuild(disabled=False)
            await interaction.response.edit_message(view=self)

    async def _on_open_modal(self, interaction: discord.Interaction) -> None:
        if self.terminal is not None or self.busy or not self.draft.can_open_modal:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    self.t("commands.suggest.validation_error"),
                    ephemeral=True,
                )
            return
        await interaction.response.send_modal(SuggestionModal(self))

    async def handle_modal_submit(
        self, interaction: discord.Interaction, *, title: str, details: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if self.terminal is not None:
            await interaction.followup.send(self.t("commands.suggest.timeout"), ephemeral=True)
            return
        async with self._lock:
            if not self.draft.can_open_modal:
                self.inline_error = self.t("commands.suggest.validation_error")
                self.rebuild(disabled=False)
                await interaction.edit_original_response(view=self)
                return
            self.draft.title = title
            self.draft.details = details
            self.busy = True
            try:
                if self.on_submit is not None:
                    await self.on_submit(self, title, details)
                else:
                    self.terminal = "success"
                    self.status_text = self.t(
                        "commands.suggest.success", ticket_uid="TEST"
                    )
                self.rebuild(disabled=self.terminal is not None)
                await interaction.edit_original_response(view=self)
            finally:
                if self.terminal is None:
                    self.busy = False


__all__ = ["SuggestionModal", "SuggestionView"]
