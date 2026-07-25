from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bot.modules.events.guild_events import GuildEventHandler
from bot.modules.services.guild_sync import GuildSyncService
from shared.runtime.settings import RuntimeMode


class FakeGuildService:
    def __init__(self) -> None:
        self.ensured: list[dict[str, Any]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.left: list[str] = []

    async def ensure_active_guild(self, **kwargs: Any) -> None:
        self.ensured.append(kwargs)

    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> None:
        self.patches.append((guild_id, patch))

    async def mark_left(self, guild_id: str) -> None:
        self.left.append(guild_id)


@pytest.mark.asyncio
async def test_development_ignores_foreign_guild_events() -> None:
    service = FakeGuildService()
    handler = GuildEventHandler(
        guilds=service,
        runtime_mode=RuntimeMode.development,
        development_guild_id="111111111111111111",
    )
    foreign = SimpleNamespace(
        id=222222222222222222,
        name="Other",
        icon=None,
        member_count=1,
        owner_id=1,
    )
    await handler.on_guild_join(foreign)
    await handler.on_guild_remove(foreign)
    assert service.ensured == []
    assert service.left == []


@pytest.mark.asyncio
async def test_production_ignores_reserved_guild_events() -> None:
    service = FakeGuildService()
    handler = GuildEventHandler(
        guilds=service,
        runtime_mode=RuntimeMode.production,
        development_guild_id="111111111111111111",
    )
    reserved = SimpleNamespace(
        id=111111111111111111,
        name="Dev",
        icon=None,
        member_count=1,
        owner_id=1,
    )
    await handler.on_guild_join(reserved)
    assert service.ensured == []


@pytest.mark.asyncio
async def test_guild_sync_filters_unmanaged() -> None:
    service = FakeGuildService()
    sync = GuildSyncService(
        guilds=service,
        interval_sec=3600,
        runtime_mode=RuntimeMode.development,
        development_guild_id="111111111111111111",
    )
    bot = SimpleNamespace(
        guilds=[
            SimpleNamespace(
                id=111111111111111111,
                name="Dev",
                icon=None,
                member_count=2,
                owner_id=9,
            ),
            SimpleNamespace(
                id=222222222222222222,
                name="Other",
                icon=None,
                member_count=3,
                owner_id=8,
            ),
        ]
    )
    await sync.reconcile_once(bot)
    assert len(service.patches) == 1
    assert service.patches[0][0] == "111111111111111111"
