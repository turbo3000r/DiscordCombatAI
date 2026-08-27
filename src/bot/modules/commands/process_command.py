"""ProcessCommand wrapper and reusable interaction guards."""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

import discord
from discord import app_commands

from bot.localization.handler import LocalizationHandler
from bot.logging_config import public_command_params
from bot.modules.commands.context import CommandContext
from bot.modules.commands.errors import (
    CommandDenial,
    CommandError,
    CommandPermanentError,
    CommandTransientError,
    CommandValidationError,
)
from bot.modules.services.guild_guard import is_managed_guild
from shared.azure.errors import AzurePermanentError, AzureTransientError
from shared.models.guild_config import GuildConfigDocument
from shared.runtime.settings import RuntimeMode
from shared.storage.local_adapters import LocalHttpError
from shared.storage.protocols import GuildRepository, StatusRepository, SuggestionRepository

logger = logging.getLogger(__name__)

CommandCallback = Callable[..., Awaitable[Any]]


def _command_name(interaction: discord.Interaction) -> str:
    command = getattr(interaction, "command", None)
    name = getattr(command, "qualified_name", None) or getattr(command, "name", None)
    return str(name or "unknown")


def _interaction_locale(interaction: discord.Interaction) -> str | None:
    locale = getattr(interaction, "locale", None)
    if locale is None:
        return None
    return str(getattr(locale, "value", locale))


def _member_has_permissions(
    member: discord.abc.User,
    allowed: discord.Permissions | Mapping[Any, bool] | None,
) -> bool:
    if allowed is None:
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms is None:
        return False
    if isinstance(allowed, discord.Permissions):
        return bool(perms >= allowed)
    for key, required in allowed.items():
        if not required:
            continue
        if isinstance(key, str):
            if not bool(getattr(perms, key, False)):
                return False
            continue
        # flag_value / Permissions attribute
        name = getattr(key, "flag", None) or getattr(key, "name", None) or str(key)
        attr = str(name)
        if attr.startswith("Permissions."):
            attr = attr.split(".", 1)[1]
        flag = getattr(key, "flag", None)
        if isinstance(flag, int):
            if not (int(perms.value) & flag):
                return False
            continue
        if not bool(getattr(perms, attr, False)):
            return False
    return True


def _classify_repo_error(exc: BaseException) -> CommandError:
    if isinstance(exc, AzureTransientError):
        return CommandTransientError()
    if isinstance(exc, AzurePermanentError):
        return CommandPermanentError()
    if isinstance(exc, LocalHttpError):
        if exc.status_code is not None and 500 <= exc.status_code <= 599:
            return CommandTransientError()
        if exc.status_code in {408, 429}:
            return CommandTransientError()
        return CommandPermanentError()
    return CommandPermanentError()


class CommandGuardService:
    """Shared guild/runtime/permission/drain gates for commands and callbacks."""

    def __init__(
        self,
        *,
        runtime_mode: RuntimeMode | str,
        development_guild_id: str,
        l10n: LocalizationHandler,
        guild_repository: GuildRepository | None = None,
        suggestion_repository: SuggestionRepository | None = None,
        status_repository: StatusRepository | None = None,
        draining_provider: Callable[[], bool] | None = None,
    ) -> None:
        self.runtime_mode = RuntimeMode(runtime_mode)
        self.development_guild_id = development_guild_id
        self.l10n = l10n
        self.guild_repository = guild_repository
        self.suggestion_repository = suggestion_repository
        self.status_repository = status_repository
        self._draining_provider = draining_provider or (lambda: False)

    @property
    def draining(self) -> bool:
        return bool(self._draining_provider())

    def process_command(
        self,
        *,
        required_guild: bool = True,
        required_guild_enabled: bool = False,
        allowed_permissions: discord.Permissions | Mapping[Any, bool] | None = None,
        blocked_during_drain: bool = False,
        load_guild_config: bool = True,
    ) -> Callable[[CommandCallback], CommandCallback]:
        def decorator(func: CommandCallback) -> CommandCallback:
            wants_ctx = "ctx" in inspect.signature(func).parameters

            @functools.wraps(func)
            async def wrapper(interaction: discord.Interaction, *args: Any, **kwargs: Any) -> Any:
                try:
                    ctx = await self.build_context(
                        interaction,
                        required_guild=required_guild,
                        required_guild_enabled=required_guild_enabled,
                        allowed_permissions=allowed_permissions,
                        blocked_during_drain=blocked_during_drain,
                        load_guild_config=load_guild_config,
                    )
                except CommandDenial as denial:
                    await self._deny(interaction, denial)
                    return None
                except CommandTransientError as err:
                    await self._fail(interaction, err, level=logging.WARNING)
                    return None
                except CommandPermanentError as err:
                    await self._fail(interaction, err, level=logging.ERROR)
                    return None
                except Exception:  # noqa: BLE001
                    await self._unexpected(interaction)
                    return None

                self._log_command(
                    ctx,
                    "command started params=%s",
                    public_command_params(kwargs),
                    decision="allowed",
                )
                try:
                    call_kwargs = dict(kwargs)
                    if wants_ctx:
                        call_kwargs["ctx"] = ctx
                    result = await func(interaction, *args, **call_kwargs)
                except CommandDenial as denial:
                    await self._deny(interaction, denial, ctx=ctx)
                    return None
                except CommandValidationError as err:
                    await self._fail(interaction, err, level=logging.INFO, ctx=ctx)
                    return None
                except CommandTransientError as err:
                    await self._fail(interaction, err, level=logging.WARNING, ctx=ctx)
                    return None
                except CommandPermanentError as err:
                    await self._fail(interaction, err, level=logging.ERROR, ctx=ctx)
                    return None
                except (AzureTransientError, AzurePermanentError, LocalHttpError) as exc:
                    classified = _classify_repo_error(exc)
                    level = (
                        logging.WARNING
                        if isinstance(classified, CommandTransientError)
                        else logging.ERROR
                    )
                    await self._fail(interaction, classified, level=level, ctx=ctx)
                    return None
                except Exception:  # noqa: BLE001
                    await self._unexpected(interaction, ctx=ctx)
                    return None
                self._log_command(ctx, "command finished", decision="allowed")
                return result

            # discord.py must only see interaction (+ slash options), never ctx.
            original = inspect.signature(func)
            visible = [param for name, param in original.parameters.items() if name != "ctx"]
            wrapper.__signature__ = original.replace(parameters=visible)  # type: ignore[attr-defined]
            return cast(CommandCallback, wrapper)

        return decorator

    async def evaluate(
        self,
        interaction: discord.Interaction,
        *,
        required_guild: bool = True,
        required_guild_enabled: bool = False,
        allowed_permissions: discord.Permissions | Mapping[Any, bool] | None = None,
        blocked_during_drain: bool = False,
        load_guild_config: bool = True,
    ) -> CommandContext:
        """Re-run guards for component/modal callbacks; raises CommandDenial/errors."""
        return await self.build_context(
            interaction,
            required_guild=required_guild,
            required_guild_enabled=required_guild_enabled,
            allowed_permissions=allowed_permissions,
            blocked_during_drain=blocked_during_drain,
            load_guild_config=load_guild_config,
        )

    async def build_context(
        self,
        interaction: discord.Interaction,
        *,
        required_guild: bool,
        required_guild_enabled: bool,
        allowed_permissions: discord.Permissions | Mapping[Any, bool] | None,
        blocked_during_drain: bool,
        load_guild_config: bool,
    ) -> CommandContext:
        guild_id = str(interaction.guild_id) if interaction.guild_id is not None else None
        user = interaction.user
        user_id = str(user.id)
        command = _command_name(interaction)
        interaction_id = str(interaction.id)
        draining = self.draining

        # 1) Runtime guild filter first
        if guild_id is None:
            if self.runtime_mode is RuntimeMode.development:
                raise CommandDenial("denied_runtime_filter", "errors.denied_development_dm")
            if required_guild:
                raise CommandDenial("denied_guild", "errors.denied_guild")
        else:
            if not is_managed_guild(
                mode=self.runtime_mode,
                guild_id=guild_id,
                development_guild_id=self.development_guild_id,
            ):
                raise CommandDenial("denied_runtime_filter", "errors.denied_runtime_filter")

        # 2) Guild requirement (already handled for DMs above when required)
        if required_guild and guild_id is None:
            raise CommandDenial("denied_guild", "errors.denied_guild")

        # 3) Member permissions
        if allowed_permissions is not None and not _member_has_permissions(
            user, allowed_permissions
        ):
            raise CommandDenial("denied_permission", "errors.denied_permission")

        # 4) Drain gate
        if blocked_during_drain and draining:
            raise CommandDenial("denied_drain", "errors.denied_drain")

        guild_config: GuildConfigDocument | None = None
        if guild_id is not None and (load_guild_config or required_guild_enabled):
            if self.guild_repository is None:
                if required_guild_enabled:
                    raise CommandPermanentError("errors.error_permanent")
            else:
                try:
                    guild_config = await self.guild_repository.get(guild_id)
                except LocalHttpError as exc:
                    if exc.status_code == 404:
                        guild_config = None
                    else:
                        raise _classify_repo_error(exc) from exc
                except AzurePermanentError as exc:
                    if getattr(exc, "status_code", None) == 404:
                        guild_config = None
                    else:
                        raise CommandPermanentError() from exc
                except AzureTransientError as exc:
                    raise CommandTransientError() from exc
                except Exception as exc:  # noqa: BLE001
                    if "not found" in str(exc).lower():
                        guild_config = None
                    else:
                        raise _classify_repo_error(exc) from exc

        if required_guild_enabled and (
            guild_config is None or not self._guild_ai_enabled(guild_config)
        ):
            raise CommandDenial("denied_enabled", "errors.denied_enabled")

        locale_key = self.l10n.resolve_locale_key(
            guild_language=guild_config.language if guild_config is not None else None,
            interaction_locale=_interaction_locale(interaction),
        )
        return CommandContext(
            interaction=interaction,
            runtime_mode=self.runtime_mode,
            locale_key=locale_key,
            guild_id=guild_id,
            user_id=user_id,
            command=command,
            interaction_id=interaction_id,
            draining=draining,
            l10n=self.l10n,
            guild_config=guild_config,
            guild_repository=self.guild_repository,
            suggestion_repository=self.suggestion_repository,
            status_repository=self.status_repository,
        )

    @staticmethod
    def _guild_ai_enabled(document: GuildConfigDocument) -> bool:
        key = (document.api_key or "").strip()
        model = (document.model or "").strip()
        return bool(document.enabled and key and model and document.left_at is None)

    async def deny_ephemeral(
        self, interaction: discord.Interaction, message: str, *, ephemeral: bool = True
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)

    async def _deny(
        self,
        interaction: discord.Interaction,
        denial: CommandDenial,
        *,
        ctx: CommandContext | None = None,
    ) -> None:
        locale = ctx.locale_key if ctx is not None else self._fallback_locale(interaction)
        message = self.l10n.t(denial.message_key, locale=locale)
        self._log_decision(
            ctx or self._minimal_log_ctx(interaction, locale),
            denial.decision,
            level=logging.INFO,
        )
        await self.deny_ephemeral(interaction, message)

    async def _fail(
        self,
        interaction: discord.Interaction,
        err: CommandError,
        *,
        level: int,
        ctx: CommandContext | None = None,
    ) -> None:
        locale = ctx.locale_key if ctx is not None else self._fallback_locale(interaction)
        key = getattr(err, "message_key", "errors.error_unexpected")
        decision = getattr(err, "decision", "error_unexpected")
        message = self.l10n.t(key, locale=locale)
        self._log_decision(
            ctx or self._minimal_log_ctx(interaction, locale),
            decision,
            level=level,
        )
        await self.deny_ephemeral(interaction, message)

    async def _unexpected(
        self, interaction: discord.Interaction, *, ctx: CommandContext | None = None
    ) -> None:
        locale = ctx.locale_key if ctx is not None else self._fallback_locale(interaction)
        logger.exception(
            "unexpected command error",
            extra={
                "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
                "user_id": str(interaction.user.id),
                "command": _command_name(interaction),
                "interaction_id": str(interaction.id),
                "decision": "error_unexpected",
            },
        )
        message = self.l10n.t("errors.error_unexpected", locale=locale)
        try:
            await self.deny_ephemeral(interaction, message)
        except Exception:  # noqa: BLE001
            logger.exception("failed to send unexpected-error ephemeral")

    def _fallback_locale(self, interaction: discord.Interaction) -> str:
        return self.l10n.resolve_locale_key(interaction_locale=_interaction_locale(interaction))

    def _minimal_log_ctx(self, interaction: discord.Interaction, locale: str) -> CommandContext:
        return CommandContext(
            interaction=interaction,
            runtime_mode=self.runtime_mode,
            locale_key=locale,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            user_id=str(interaction.user.id),
            command=_command_name(interaction),
            interaction_id=str(interaction.id),
            draining=self.draining,
            l10n=self.l10n,
        )

    def _log_decision(
        self, ctx: CommandContext, decision: str, *, level: int = logging.INFO
    ) -> None:
        self._log_command(
            ctx,
            "process_command decision=%s",
            decision,
            decision=decision,
            level=level,
        )

    def _log_command(
        self,
        ctx: CommandContext,
        message: str,
        *args: Any,
        decision: str,
        level: int = logging.INFO,
    ) -> None:
        logger.log(
            level,
            message,
            *args,
            extra={
                "guild_id": ctx.guild_id,
                "user_id": ctx.user_id,
                "command": ctx.command,
                "interaction_id": ctx.interaction_id,
                "decision": decision,
            },
        )


def ProcessCommand(
    guard: CommandGuardService,
    *,
    required_guild: bool = True,
    required_guild_enabled: bool = False,
    allowed_permissions: discord.Permissions | Mapping[Any, bool] | None = None,
    blocked_during_drain: bool = False,
    load_guild_config: bool = True,
) -> Callable[[CommandCallback], CommandCallback]:
    """Decorator factory matching discord_bot.md §6.4."""
    return guard.process_command(
        required_guild=required_guild,
        required_guild_enabled=required_guild_enabled,
        allowed_permissions=allowed_permissions,
        blocked_during_drain=blocked_during_drain,
        load_guild_config=load_guild_config,
    )


async def autocomplete_fail_closed(
    _interaction: discord.Interaction, _current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete must fail closed with an empty result and no blocking work."""
    return []


__all__ = [
    "CommandCallback",
    "CommandGuardService",
    "ProcessCommand",
    "autocomplete_fail_closed",
]
