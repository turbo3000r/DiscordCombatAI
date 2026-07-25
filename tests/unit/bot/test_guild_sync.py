from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bot.modules.events.guild_events import GuildEventHandler
from bot.modules.services.guild_sync import GuildSyncService


class FakeGuildService:
    def __init__(self) -> None:
        self.ensured: list[dict[str, Any]] = []
        self.patches: list[tuple[str, dict[str, Any]]] = []
        self.left: list[str] = []
        self.fail = False

    async def ensure_active_guild(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("cosmos down")
        self.ensured.append(kwargs)

    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("cosmos down")
        self.patches.append((guild_id, patch))

    async def mark_left(self, guild_id: str) -> None:
        if self.fail:
            raise RuntimeError("cosmos down")
        self.left.append(guild_id)


class FakeHealth:
    def __init__(self) -> None:
        self.cosmos_ok = True

    def set_cosmos_ok(self, ok: bool) -> None:
        self.cosmos_ok = ok


@pytest.mark.asyncio
async def test_guild_join_update_remove() -> None:
    service = FakeGuildService()
    health = FakeHealth()
    handler = GuildEventHandler(guilds=service, health=health)
    guild = SimpleNamespace(
        id=123,
        name="Guild",
        icon=None,
        member_count=10,
        owner_id=999,
    )
    await handler.on_guild_join(guild)
    assert service.ensured[0]["guild_id"] == "123"
    assert health.cosmos_ok is True

    before = SimpleNamespace(name="Guild", icon=None, owner_id=999)
    after = SimpleNamespace(id=123, name="Guild2", icon=None, owner_id=999)
    await handler.on_guild_update(before, after)
    assert service.patches[0] == ("123", {"name": "Guild2"})

    await handler.on_guild_remove(guild)
    assert service.left == ["123"]


@pytest.mark.asyncio
async def test_guild_join_failure_sets_health() -> None:
    service = FakeGuildService()
    service.fail = True
    health = FakeHealth()
    handler = GuildEventHandler(guilds=service, health=health)
    await handler.on_guild_join(
        SimpleNamespace(id=1, name="g", icon=None, member_count=0, owner_id=1)
    )
    assert health.cosmos_ok is False


@pytest.mark.asyncio
async def test_guild_sync_current_guilds_only() -> None:
    service = FakeGuildService()
    sync = GuildSyncService(guilds=service, interval_sec=3600)
    bot = SimpleNamespace(
        guilds=[
            SimpleNamespace(id=1, name="A", icon=None, member_count=2, owner_id=9),
            SimpleNamespace(id=2, name="B", icon=None, member_count=3, owner_id=8),
        ]
    )
    await sync.reconcile_once(bot)
    assert len(service.patches) == 2
    assert service.patches[0][0] == "1"
    assert "left_at" not in service.patches[0][1]
