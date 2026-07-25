"""Tests for ProcessCommand guards and acknowledgement ownership."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.localization.handler import load_localization
from bot.modules.commands.context import CommandContext
from bot.modules.commands.errors import CommandDenial
from bot.modules.commands.process_command import CommandGuardService
from shared.azure.errors import AzurePermanentError, AzureTransientError
from shared.models.guild_config import GuildConfigDocument
from shared.runtime.settings import RuntimeMode


def _guild_doc(**overrides: Any) -> GuildConfigDocument:
    payload = {
        "id": "111111111111111111",
        "guild_id": "111111111111111111",
        "schema_version": 1,
        "name": "Test",
        "owner_id": "222222222222222222",
        "member_count": 3,
        "icon_url": None,
        "enabled": True,
        "language": "en",
        "api_key": "secret-key",
        "model": "gemini-2.0-flash",
        "webhook_url": "",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "left_at": None,
    }
    payload.update(overrides)
    if "id" in overrides and "guild_id" not in overrides:
        payload["guild_id"] = overrides["id"]
    return GuildConfigDocument.model_validate(payload)


class _Response:
    def __init__(self) -> None:
        self._done = False
        self.sent: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self._done = True
        self.sent.append({"content": content, "ephemeral": ephemeral, "via": "response"})


class _Followup:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.sent.append({"content": content, "ephemeral": ephemeral, "via": "followup"})


def _interaction(
    *,
    guild_id: int | None = 111111111111111111,
    user_id: int = 333333333333333333,
    administrator: bool = True,
    locale: str = "en-US",
    command_name: str = "config",
) -> MagicMock:
    interaction = MagicMock()
    interaction.id = 999999999999999999
    interaction.guild_id = guild_id
    interaction.locale = SimpleNamespace(value=locale)
    interaction.command = SimpleNamespace(name=command_name, qualified_name=command_name)
    perms = discord.Permissions(administrator=administrator)
    interaction.user = SimpleNamespace(id=user_id, guild_permissions=perms)
    interaction.response = _Response()
    interaction.followup = _Followup()
    return interaction


@pytest.fixture
def l10n():
    return load_localization()


@pytest.fixture
def guild_repo():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=_guild_doc())
    return repo


def _guard(
    l10n,
    guild_repo,
    *,
    mode: RuntimeMode = RuntimeMode.production,
    draining: bool = False,
    development_guild_id: str = "555555555555555555",
) -> CommandGuardService:
    return CommandGuardService(
        runtime_mode=mode,
        development_guild_id=development_guild_id,
        l10n=l10n,
        guild_repository=guild_repo,
        draining_provider=lambda: draining,
    )


@pytest.mark.asyncio
async def test_production_rejects_development_guild(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo, development_guild_id="111111111111111111")
    interaction = _interaction(guild_id=111111111111111111)

    @guard.process_command(required_guild=True)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    result = await cmd(interaction)
    assert result is None
    assert interaction.response.sent[0]["ephemeral"] is True
    assert "not available" in interaction.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_development_rejects_foreign_guild_and_dm(l10n, guild_repo) -> None:
    guard = _guard(
        l10n,
        guild_repo,
        mode=RuntimeMode.development,
        development_guild_id="111111111111111111",
    )

    foreign = _interaction(guild_id=222222222222222222)
    dm = _interaction(guild_id=None)

    @guard.process_command(required_guild=False)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(foreign) is None
    assert await cmd(dm) is None
    assert foreign.response.sent
    assert "direct messages" in dm.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_required_guild_denies_dm_in_production(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(guild_id=None)

    @guard.process_command(required_guild=True)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(interaction) is None
    assert "server" in interaction.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_suggest_allows_dm_in_production(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(guild_id=None, command_name="suggest")

    @guard.process_command(required_guild=False)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        assert ctx.guild_id is None
        return "ok"

    assert await cmd(interaction) == "ok"
    assert not interaction.response.sent


@pytest.mark.asyncio
async def test_permission_denial(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(administrator=False)

    @guard.process_command(
        required_guild=True,
        allowed_permissions=discord.Permissions(administrator=True),
    )
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(interaction) is None
    assert "permission" in interaction.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_drain_block_and_allow(l10n, guild_repo) -> None:
    blocked = _guard(l10n, guild_repo, draining=True)
    open_guard = _guard(l10n, guild_repo, draining=True)
    interaction = _interaction()

    @blocked.process_command(required_guild=True, blocked_during_drain=True)
    async def blocked_cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    @open_guard.process_command(required_guild=True, blocked_during_drain=False)
    async def open_cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await blocked_cmd(interaction) is None
    assert "update" in interaction.response.sent[0]["content"].lower()

    interaction2 = _interaction()
    assert await open_cmd(interaction2) == "ok"


@pytest.mark.asyncio
async def test_enabled_gate(l10n, guild_repo) -> None:
    guild_repo.get = AsyncMock(
        return_value=_guild_doc(enabled=False, api_key="", model="")
    )
    guard = _guard(l10n, guild_repo)
    interaction = _interaction()

    @guard.process_command(required_guild=True, required_guild_enabled=True)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(interaction) is None
    assert "not enabled" in interaction.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_transient_and_permanent_repo_errors(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction()
    guild_repo.get = AsyncMock(side_effect=AzureTransientError("timeout"))

    @guard.process_command(required_guild=True)
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(interaction) is None
    assert "temporary" in interaction.response.sent[0]["content"].lower()

    interaction2 = _interaction()
    guild_repo.get = AsyncMock(side_effect=AzurePermanentError("forbidden", status_code=403))
    assert await cmd(interaction2) is None
    assert "operator" in interaction2.response.sent[0]["content"].lower()


@pytest.mark.asyncio
async def test_followup_used_when_already_acknowledged(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(administrator=False)
    interaction.response._done = True

    @guard.process_command(
        required_guild=True,
        allowed_permissions=discord.Permissions(administrator=True),
    )
    async def cmd(interaction: discord.Interaction, ctx: CommandContext) -> str:
        return "ok"

    assert await cmd(interaction) is None
    assert interaction.followup.sent
    assert interaction.followup.sent[0]["ephemeral"] is True


@pytest.mark.asyncio
async def test_context_repr_excludes_api_key(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction()
    ctx = await guard.build_context(
        interaction,
        required_guild=True,
        required_guild_enabled=False,
        allowed_permissions=None,
        blocked_during_drain=False,
        load_guild_config=True,
    )
    text = repr(ctx)
    assert "secret-key" not in text
    assert "api_key" not in text


@pytest.mark.asyncio
async def test_evaluate_reruns_guards_for_stale_permissions(l10n, guild_repo) -> None:
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(administrator=False)
    with pytest.raises(CommandDenial) as exc:
        await guard.evaluate(
            interaction,
            required_guild=True,
            allowed_permissions=discord.Permissions(administrator=True),
        )
    assert exc.value.decision == "denied_permission"


@pytest.mark.asyncio
async def test_guild_language_preferred_over_interaction_locale(l10n, guild_repo) -> None:
    guild_repo.get = AsyncMock(return_value=_guild_doc(language="es"))
    guard = _guard(l10n, guild_repo)
    interaction = _interaction(locale="en-US")
    ctx = await guard.build_context(
        interaction,
        required_guild=True,
        required_guild_enabled=False,
        allowed_permissions=None,
        blocked_during_drain=False,
        load_guild_config=True,
    )
    assert ctx.locale_key == "es"
