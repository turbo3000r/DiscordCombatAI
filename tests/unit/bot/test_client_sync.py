from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from bot.modules.client import CombatBot
from shared.runtime.settings import RuntimeMode


@pytest.mark.asyncio
async def test_development_sync_is_guild_scoped() -> None:
    bot = CombatBot(
        runtime_mode=RuntimeMode.development,
        development_guild_id="123456789012345678",
        expected_application_id="987654321098765432",
    )
    type(bot).application_id = PropertyMock(return_value=987654321098765432)  # type: ignore[method-assign]

    synced: dict[str, Any] = {}

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        synced["guild"] = guild
        return []

    bot.tree.copy_global_to = MagicMock()  # type: ignore[method-assign]
    bot.tree.sync = fake_sync  # type: ignore[method-assign]

    await bot.setup_hook()
    assert synced["guild"] is not None
    assert int(synced["guild"].id) == 123456789012345678
    bot.tree.copy_global_to.assert_called_once()


@pytest.mark.asyncio
async def test_development_rejects_application_mismatch() -> None:
    bot = CombatBot(
        runtime_mode=RuntimeMode.development,
        development_guild_id="123456789012345678",
        expected_application_id="987654321098765432",
    )
    type(bot).application_id = PropertyMock(return_value=111111111111111111)  # type: ignore[method-assign]
    bot.tree.sync = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="does not match"):
        await bot.setup_hook()
    bot.tree.sync.assert_not_called()


@pytest.mark.asyncio
async def test_production_uses_global_sync() -> None:
    bot = CombatBot(runtime_mode=RuntimeMode.production)
    synced: dict[str, Any] = {}

    async def fake_sync(*, guild: Any = None) -> list[Any]:
        synced["guild"] = guild
        return []

    bot.tree.sync = fake_sync  # type: ignore[method-assign]
    await bot.setup_hook()
    assert synced["guild"] is None
