"""/config Components V2 UI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord
from discord import ui

from bot.modules.commands.config.UI.state import ConfigDraft
from bot.modules.UI.views.staged_settings import StagedSettingsView

logger = logging.getLogger(__name__)

ApplyCallback = Callable[["ConfigSettingsView"], Awaitable[None]]
ListModelsCallback = Callable[["ConfigSettingsView", str], Awaitable[None]]


class ApiKeyModal(ui.Modal, title="API Key"):
    api_key: ui.TextInput[Any] = ui.TextInput(
        label="API key",
        style=discord.TextStyle.short,
        required=True,
        min_length=1,
        max_length=256,
    )

    def __init__(self, view: ConfigSettingsView) -> None:
        super().__init__()
        self._view = view
        self.title = view.t("commands.config.api_key_label")
        self.api_key.label = view.t("commands.config.api_key_label")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._view.handle_api_key_submit(interaction, str(self.api_key.value))


class WebhookModal(ui.Modal, title="Webhook"):
    webhook_url: ui.TextInput[Any] = ui.TextInput(
        label="Webhook URL",
        style=discord.TextStyle.short,
        required=True,
        min_length=1,
        max_length=512,
    )

    def __init__(self, view: ConfigSettingsView) -> None:
        super().__init__()
        self._view = view
        self.title = view.t("commands.config.webhook_label")
        self.webhook_url.label = view.t("commands.config.webhook_label")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._view.handle_webhook_submit(interaction, str(self.webhook_url.value))


class ConfigSettingsView(StagedSettingsView):
    """Command-specific staged settings panel for /config."""

    def __init__(
        self,
        *,
        owner_id: int,
        draft: ConfigDraft,
        locale_key: str,
        t: Callable[..., str],
        on_apply: ApplyCallback | None = None,
        on_list_models: ListModelsCallback | None = None,
        guard_check: Callable[[discord.Interaction], Awaitable[bool]] | None = None,
        timeout: float = 300.0,
    ) -> None:
        super().__init__(
            owner_id=owner_id,
            timeout=timeout,
            locale_key=locale_key,
            t=t,
            guard_check=guard_check,
        )
        self.draft = draft
        self.on_apply = on_apply
        self.on_list_models = on_list_models
        self.rebuild(disabled=False)

    def build_items(self, *, disabled: bool) -> list[ui.Item[Any]]:
        items: list[ui.Item[Any]] = [
            ui.TextDisplay(self._header_text()),
        ]
        if self.inline_error:
            items.append(ui.TextDisplay(f"**!** {self.inline_error}"))
        if self.status_text:
            items.append(ui.TextDisplay(self.status_text))
        if self.draft.applied_summary:
            items.append(ui.TextDisplay(self.draft.applied_summary))
        if self.draft.rejected_summary:
            items.append(ui.TextDisplay(self.draft.rejected_summary))

        language: ui.Select[Any] = ui.Select(
            placeholder=self.t("commands.config.language_label"),
            options=[
                discord.SelectOption(
                    label="English",
                    value="en",
                    default=self.draft.effective_language == "en",
                ),
                discord.SelectOption(
                    label="Español",
                    value="es",
                    default=self.draft.effective_language == "es",
                ),
                discord.SelectOption(
                    label="Українська",
                    value="ua",
                    default=self.draft.effective_language == "ua",
                ),
            ],
            disabled=disabled or self.busy,
            min_values=1,
            max_values=1,
        )
        language.callback = self._on_language  # type: ignore[method-assign]
        items.append(ui.ActionRow(language))

        model_disabled = (
            disabled
            or self.busy
            or not self.draft.model_options
            or not self.draft.has_usable_key
        )
        model_options = [
            discord.SelectOption(
                label=label[:100],
                value=value[:100],
                default=self.draft.effective_model == value,
            )
            for value, label in self.draft.model_options[:25]
        ] or [
            discord.SelectOption(
                label=self.t("commands.config.model_label"),
                value="__none__",
            )
        ]
        model: ui.Select[Any] = ui.Select(
            placeholder=self.t("commands.config.model_label"),
            options=model_options,
            disabled=model_disabled,
            min_values=1,
            max_values=1,
        )
        model.callback = self._on_model  # type: ignore[method-assign]
        items.append(ui.ActionRow(model))

        key_btn: ui.Button[Any] = ui.Button(
            label=self.t("commands.config.api_key_label"),
            style=discord.ButtonStyle.secondary,
            disabled=disabled or self.busy,
        )
        key_btn.callback = self._on_api_key_button  # type: ignore[method-assign]
        webhook_btn: ui.Button[Any] = ui.Button(
            label=self.t("commands.config.webhook_label"),
            style=discord.ButtonStyle.secondary,
            disabled=disabled or self.busy,
        )
        webhook_btn.callback = self._on_webhook_button  # type: ignore[method-assign]
        items.append(ui.ActionRow(key_btn, webhook_btn))

        apply_btn: ui.Button[Any] = ui.Button(
            label=self.t("commands.config.apply_button"),
            style=discord.ButtonStyle.success,
            disabled=disabled or self.busy,
        )
        apply_btn.callback = self._on_apply  # type: ignore[method-assign]
        cancel_btn: ui.Button[Any] = ui.Button(
            label=self.t("commands.config.cancel_button"),
            style=discord.ButtonStyle.danger,
            disabled=disabled or self.busy,
        )
        cancel_btn.callback = self._on_cancel  # type: ignore[method-assign]
        items.append(ui.ActionRow(apply_btn, cancel_btn))
        return items

    def _header_text(self) -> str:
        key_map = {
            "configured": self.t("commands.config.api_key_configured"),
            "staged": self.t("commands.config.api_key_staged"),
            "missing": self.t("commands.config.api_key_missing"),
        }
        webhook_map = {
            "configured": self.t("commands.config.webhook_configured"),
            "staged": self.t("commands.config.webhook_configured"),
            "missing": self.t("commands.config.webhook_missing"),
        }
        lines = [
            f"**{self.t('commands.config.language_label')}:** `{self.draft.effective_language}`",
            f"**{self.t('commands.config.api_key_label')}:** {key_map[self.draft.api_key_status]}",
            f"**{self.t('commands.config.model_label')}:** `{self.draft.effective_model or '—'}`",
            (
                f"**{self.t('commands.config.webhook_label')}:** "
                f"{webhook_map[self.draft.webhook_status]}"
            ),
            (
                f"**{self.t('commands.config.enabled_label')}:** "
                f"{'yes' if self.draft.enabled else 'no'}"
            ),
        ]
        if self.draft.models_truncated:
            lines.append(
                self.t(
                    "commands.config.truncation_notice",
                    shown=min(25, self.draft.models_total),
                    total=self.draft.models_total,
                )
            )
        # Never render API key or raw webhook URL.
        return "\n".join(lines)

    async def _on_language(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            values: list[str] = []
            data = interaction.data
            if isinstance(data, dict):
                raw_values = data.get("values") or []
                if isinstance(raw_values, list):
                    values = [str(v) for v in raw_values]
            if values:
                self.draft.staged_language = values[0]
            self.inline_error = None

        if await self.run_exclusive(_work):
            self.rebuild(disabled=False)
            await interaction.response.edit_message(view=self)

    async def _on_model(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            values: list[str] = []
            data = interaction.data
            if isinstance(data, dict):
                raw_values = data.get("values") or []
                if isinstance(raw_values, list):
                    values = [str(v) for v in raw_values]
            if values and values[0] != "__none__":
                self.draft.staged_model = values[0]
            self.inline_error = None

        if await self.run_exclusive(_work):
            self.rebuild(disabled=False)
            await interaction.response.edit_message(view=self)

    async def _on_api_key_button(self, interaction: discord.Interaction) -> None:
        if self.terminal is not None or self.busy:
            return
        await interaction.response.send_modal(ApiKeyModal(self))

    async def _on_webhook_button(self, interaction: discord.Interaction) -> None:
        if self.terminal is not None or self.busy:
            return
        await interaction.response.send_modal(WebhookModal(self))

    async def handle_api_key_submit(
        self, interaction: discord.Interaction, api_key: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            self.draft.staged_api_key = api_key
            self.inline_error = None
            if self.on_list_models is not None:
                self.busy = True
                try:
                    await self.on_list_models(self, api_key)
                finally:
                    self.busy = False
            self.rebuild(disabled=False)
            await interaction.edit_original_response(view=self)

    async def handle_webhook_submit(
        self, interaction: discord.Interaction, webhook_url: str
    ) -> None:
        # Staging validation (allowlist) is owned by P3-04; UI only stages opaque value.
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            self.draft.staged_webhook = webhook_url
            self.inline_error = None
            self.rebuild(disabled=False)
            await interaction.edit_original_response(view=self)

    async def _on_apply(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            await interaction.response.defer(ephemeral=True)
            if self.on_apply is not None:
                await self.on_apply(self)
            self.rebuild(disabled=self.terminal is not None)
            await interaction.edit_original_response(view=self)

        ok = await self.run_exclusive(_work)
        if not ok and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        async def _work() -> None:
            self.draft.staged_language = None
            self.draft.staged_api_key = None
            self.draft.staged_model = None
            self.draft.staged_webhook = None
            self.terminal = "cancel"
            self.status_text = self.cancel_message()

        if await self.run_exclusive(_work):
            self.rebuild(disabled=True)
            await interaction.response.edit_message(view=self)

    def set_model_options(
        self, options: list[tuple[str, str]], *, total: int | None = None
    ) -> None:
        capped = options[:25]
        self.draft.model_options = capped
        self.draft.models_total = total if total is not None else len(options)
        self.draft.models_truncated = self.draft.models_total > 25
        if self.draft.effective_model not in {value for value, _ in capped} and capped:
            self.draft.staged_model = capped[0][0]


__all__ = ["ApiKeyModal", "ConfigSettingsView", "WebhookModal"]
