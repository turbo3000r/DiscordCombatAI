"""Enabled-guild gate requires left_at is None."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.localization.handler import load_localization
from bot.modules.commands.errors import CommandDenial
from bot.modules.commands.process_command import CommandGuardService
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
    return GuildConfigDocument.model_validate(payload)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = 111111111111111111
    interaction.locale = SimpleNamespace(value="en-US")
    interaction.command = SimpleNamespace(name="quick-battle", qualified_name="quick-battle")
    interaction.user = SimpleNamespace(id=333, guild_permissions=discord.Permissions(administrator=True))
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_left_at_blocks_enabled_command() -> None:
    l10n = load_localization()
    repo = SimpleNamespace(get=AsyncMock(return_value=_guild_doc(left_at="2026-08-01T00:00:00Z")))
    guard = CommandGuardService(
        runtime_mode=RuntimeMode.production,
        development_guild_id="",
        l10n=l10n,
        guild_repository=repo,
    )
    with pytest.raises(CommandDenial):
        await guard.evaluate(
            _interaction(),
            required_guild=True,
            required_guild_enabled=True,
            allowed_permissions=None,
            blocked_during_drain=True,
        )
