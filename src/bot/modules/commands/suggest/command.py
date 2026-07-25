"""/suggest slash command handler."""

from __future__ import annotations

import logging
import time

import discord

from bot.modules.commands.context import CommandContext
from bot.modules.commands.errors import CommandPermanentError, CommandTransientError
from bot.modules.commands.suggest.service.category_catalog import (
    CatalogError,
    CatalogSnapshot,
    parse_suggestion_catalog,
)
from bot.modules.commands.suggest.service.suggestion_service import (
    build_suggestion_document,
    create_suggestion,
)
from bot.modules.commands.suggest.UI import SuggestionDraft, SuggestionView
from shared.azure.errors import AzurePermanentError, AzureTransientError
from shared.models.suggestion import GuildSnapshot, LocaleInfo, SubmitterSnapshot
from shared.storage.local_adapters import LocalHttpError

logger = logging.getLogger(__name__)


class _InvocationCatalog:
    def __init__(self, snapshot: CatalogSnapshot, *, ttl_sec: float = 600.0) -> None:
        self.snapshot = snapshot
        self.expires_at = time.monotonic() + ttl_sec

    @property
    def alive(self) -> bool:
        return time.monotonic() < self.expires_at


async def handle_suggest_command(interaction: discord.Interaction, ctx: CommandContext) -> None:
    if ctx.status_repository is None or ctx.suggestion_repository is None:
        raise CommandPermanentError("commands.suggest.write_permanent")

    await interaction.response.defer(ephemeral=True)
    try:
        raw = await ctx.status_repository.get_suggestion_catalog()
        snapshot = parse_suggestion_catalog(raw)
    except CatalogError as exc:
        raise CommandTransientError("commands.suggest.catalog_unavailable") from exc
    except (AzureTransientError, LocalHttpError) as exc:
        raise CommandTransientError("commands.suggest.catalog_unavailable") from exc
    except AzurePermanentError as exc:
        raise CommandTransientError("commands.suggest.catalog_unavailable") from exc

    cache = _InvocationCatalog(snapshot)
    draft = SuggestionDraft(
        type_options=list(snapshot.types),
        category_options=list(snapshot.categories),
    )

    async def guard_check(inter: discord.Interaction) -> bool:
        try:
            await interaction.client.guard.evaluate(  # type: ignore[attr-defined]
                inter,
                required_guild=False,
                required_guild_enabled=False,
                allowed_permissions=None,
                blocked_during_drain=False,
                load_guild_config=False,
            )
            return True
        except Exception:
            return False

    async def on_submit(view: SuggestionView, title: str, details: str) -> None:
        await _submit(view, ctx, interaction, cache, title, details)

    view = SuggestionView(
        owner_id=interaction.user.id,
        draft=draft,
        locale_key=ctx.locale_key,
        t=ctx.l10n.t,
        on_submit=on_submit,
        guard_check=guard_check,
    )
    await interaction.followup.send(view=view, ephemeral=True)


async def _submit(
    view: SuggestionView,
    ctx: CommandContext,
    interaction: discord.Interaction,
    cache: _InvocationCatalog,
    title: str,
    details: str,
) -> None:
    assert ctx.suggestion_repository is not None
    assert ctx.status_repository is not None

    if not title.strip() or not details.strip():
        view.inline_error = ctx.t("commands.suggest.validation_error")
        return

    snapshot = cache.snapshot
    if not cache.alive:
        try:
            raw = await ctx.status_repository.get_suggestion_catalog()
            snapshot = parse_suggestion_catalog(raw)
        except Exception:
            view.inline_error = ctx.t("commands.suggest.catalog_unavailable")
            return

    type_values = {value for value, _ in snapshot.types}
    cat_values = {value for value, _ in snapshot.categories}
    if view.draft.type_value not in type_values:
        view.inline_error = ctx.t("commands.suggest.validation_error")
        return
    if not view.draft.category_values or any(
        c not in cat_values for c in view.draft.category_values
    ):
        view.inline_error = ctx.t("commands.suggest.validation_error")
        return

    user = interaction.user
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    submitter = SubmitterSnapshot(
        id=str(user.id),
        name=str(getattr(user, "name", user)),
        display_name=str(
            getattr(member, "display_name", None) or getattr(user, "display_name", user.name)
        ),
        global_name=getattr(user, "global_name", None),
        discriminator=str(getattr(user, "discriminator", "0") or "0"),
    )
    guild_id = ctx.guild_id or "dm"
    in_guild = ctx.guild_id is not None
    guild_language = None
    if ctx.guild_config is not None:
        lang = ctx.guild_config.language
        guild_language = lang.value if hasattr(lang, "value") else str(lang)
    stored = guild_language or ctx.locale_key
    locale = LocaleInfo(
        user=str(getattr(getattr(interaction, "locale", None), "value", "en")),
        guild=guild_language,
        stored=stored,
    )
    guild_snapshot = None
    if interaction.guild is not None:
        guild_snapshot = GuildSnapshot(id=str(interaction.guild.id), name=interaction.guild.name)

    channel_id = str(interaction.channel_id) if interaction.channel_id else None
    payload = build_suggestion_document(
        interaction_id=str(interaction.id),
        guild_id=guild_id,
        title=title.strip(),
        details=details.strip(),
        type_value=str(view.draft.type_value),
        categories=list(view.draft.category_values),
        submitter=submitter,
        locale=locale,
        guild_snapshot=guild_snapshot,
        channel_id=channel_id,
        in_guild=in_guild,
    )
    try:
        saved = await create_suggestion(ctx.suggestion_repository, payload)
    except (AzureTransientError, LocalHttpError):
        view.inline_error = ctx.t("commands.suggest.write_transient")
        return
    except AzurePermanentError:
        view.inline_error = ctx.t("commands.suggest.write_permanent")
        return

    view.terminal = "success"
    view.status_text = ctx.t("commands.suggest.success", ticket_uid=saved.ticket_uid)
    logger.info(
        "suggestion created",
        extra={
            "guild_id": guild_id,
            "user_id": ctx.user_id,
            "command": "suggest",
            "interaction_id": ctx.interaction_id,
            "decision": "allowed",
            "type": saved.type,
            "categories": saved.categories,
        },
    )


__all__ = ["handle_suggest_command"]
