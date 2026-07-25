"""/config slash command handler."""

from __future__ import annotations

import asyncio
import logging

import discord

from bot.modules.commands.config.models import (
    build_apply_plan,
    commit_probe_success,
    reject_key_model,
)
from bot.modules.commands.config.service.model_catalog import (
    ProviderError,
    ProviderFailureKind,
    list_gemini_models,
    probe_model,
)
from bot.modules.commands.config.UI import ConfigDraft, ConfigSettingsView
from bot.modules.commands.context import CommandContext
from bot.modules.commands.errors import CommandPermanentError, CommandTransientError
from shared.azure.errors import AzurePermanentError, AzureTransientError
from shared.storage.local_adapters import LocalHttpError

logger = logging.getLogger(__name__)

_KIND_TO_KEY = {
    ProviderFailureKind.invalid_key: "commands.config.invalid_key",
    ProviderFailureKind.unavailable_model: "commands.config.unavailable_model",
    ProviderFailureKind.rate_limit: "commands.config.rate_limit",
    ProviderFailureKind.transient_service: "commands.config.transient_service",
}


def _guild_icon_url(guild: discord.Guild) -> str | None:
    icon = getattr(guild, "icon", None)
    if icon is None:
        return None
    return str(getattr(icon, "url", None) or icon)


async def handle_config_command(interaction: discord.Interaction, ctx: CommandContext) -> None:
    if ctx.guild_repository is None or interaction.guild is None or ctx.guild_id is None:
        raise CommandPermanentError("commands.config.cosmos_permanent")

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    try:
        document = await ctx.guild_repository.ensure_active_guild(
            guild_id=ctx.guild_id,
            name=guild.name,
            icon_url=_guild_icon_url(guild),
            member_count=getattr(guild, "member_count", 0) or 0,
            owner_id=str(guild.owner_id or 0),
        )
    except (AzureTransientError, LocalHttpError) as exc:
        raise CommandTransientError("commands.config.cosmos_transient") from exc
    except AzurePermanentError as exc:
        raise CommandPermanentError("commands.config.cosmos_permanent") from exc

    language = document.language
    language_value = language.value if hasattr(language, "value") else str(language)
    draft = ConfigDraft(
        language=language_value,
        api_key_configured=bool(document.api_key),
        model=document.model or "",
        webhook_configured=bool(document.webhook_url),
        enabled=bool(document.enabled),
    )

    async def guard_check(inter: discord.Interaction) -> bool:
        assert interaction.client.guard  # type: ignore[attr-defined]
        try:
            await interaction.client.guard.evaluate(  # type: ignore[attr-defined]
                inter,
                required_guild=True,
                required_guild_enabled=False,
                allowed_permissions=discord.Permissions(administrator=True),
                blocked_during_drain=False,
                load_guild_config=False,
            )
            return True
        except Exception:
            return False

    view = ConfigSettingsView(
        owner_id=interaction.user.id,
        draft=draft,
        locale_key=ctx.locale_key,
        t=ctx.l10n.t,
        guard_check=guard_check,
        on_apply=lambda v: _on_apply(v, ctx, document_api_key=document.api_key),
        on_list_models=_on_list_models,
    )

    if document.api_key:
        try:
            result = await asyncio.to_thread(list_gemini_models, document.api_key)
            view.set_model_options(result.options, total=result.total)
            view.rebuild(disabled=False)
        except ProviderError as exc:
            view.inline_error = ctx.t(_KIND_TO_KEY[exc.kind])
            view.rebuild(disabled=False)

    await interaction.followup.send(view=view, ephemeral=True)


async def _on_list_models(view: ConfigSettingsView, api_key: str) -> None:
    try:
        result = await asyncio.to_thread(list_gemini_models, api_key)
        view.set_model_options(result.options, total=result.total)
        view.inline_error = None
    except ProviderError as exc:
        view.draft.model_options = []
        view.inline_error = view.t(_KIND_TO_KEY[exc.kind])


async def _on_apply(
    view: ConfigSettingsView, ctx: CommandContext, *, document_api_key: str
) -> None:
    if ctx.guild_repository is None or ctx.guild_id is None:
        view.inline_error = ctx.t("commands.config.cosmos_permanent")
        return
    # Reload current
    try:
        current = await ctx.guild_repository.get(ctx.guild_id)
    except (AzureTransientError, LocalHttpError):
        view.inline_error = ctx.t("commands.config.cosmos_transient")
        return
    except AzurePermanentError:
        view.inline_error = ctx.t("commands.config.cosmos_permanent")
        return

    plan = build_apply_plan(
        current_api_key=current.api_key,
        current_model=current.model,
        current_language=str(
            current.language.value if hasattr(current.language, "value") else current.language
        ),
        current_webhook=current.webhook_url,
        current_enabled=current.enabled,
        draft=view.draft,
    )

    if plan.needs_probe:
        key = plan.probe_key or ""
        model = plan.probe_model or ""
        if not key or not model:
            reject_key_model(plan)
        else:
            try:
                await asyncio.to_thread(probe_model, key, model)
                commit_probe_success(plan, view.draft, current.enabled)
            except ProviderError as exc:
                reject_key_model(plan)
                view.inline_error = ctx.t(_KIND_TO_KEY[exc.kind])

    if not plan.patch:
        view.status_text = None
        view.draft.applied_summary = None
        view.draft.rejected_summary = (
            ctx.t("commands.config.apply_rejected", fields=", ".join(plan.rejected))
            if plan.rejected
            else None
        )
        return

    try:
        saved = await ctx.guild_repository.patch_admin_config(ctx.guild_id, plan.patch)
    except AzureTransientError as exc:
        if getattr(exc, "status_code", None) == 412:
            view.inline_error = ctx.t("commands.config.cosmos_transient")
            return
        view.inline_error = ctx.t("commands.config.cosmos_transient")
        return
    except (AzurePermanentError, LocalHttpError):
        view.inline_error = ctx.t("commands.config.cosmos_permanent")
        return

    # Clear only successfully committed staged fields
    if "language" in plan.applied:
        view.draft.language = str(
            saved.language.value if hasattr(saved.language, "value") else saved.language
        )
        view.draft.staged_language = None
    if "api_key" in plan.applied:
        view.draft.api_key_configured = bool(saved.api_key)
        view.draft.staged_api_key = None
    if "model" in plan.applied:
        view.draft.model = saved.model
        view.draft.staged_model = None
    if "webhook_url" in plan.applied:
        view.draft.webhook_configured = bool(saved.webhook_url)
        view.draft.staged_webhook = None
    view.draft.enabled = saved.enabled
    view.draft.applied_summary = (
        ctx.t("commands.config.apply_applied", fields=", ".join(plan.applied))
        if plan.applied
        else None
    )
    view.draft.rejected_summary = (
        ctx.t("commands.config.apply_rejected", fields=", ".join(plan.rejected))
        if plan.rejected
        else None
    )
    view.inline_error = None
    logger.info(
        "config applied",
        extra={
            "guild_id": ctx.guild_id,
            "user_id": ctx.user_id,
            "command": "config",
            "interaction_id": ctx.interaction_id,
            "decision": "allowed",
            "applied": plan.applied,
        },
    )


__all__ = ["handle_config_command"]
