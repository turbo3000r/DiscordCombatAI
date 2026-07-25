"""Guild join/update/remove handlers — Phase 2 persistence only."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from bot.modules.services.guild_guard import is_managed_guild
from shared.runtime.settings import RuntimeMode

logger = logging.getLogger(__name__)


class GuildService(Protocol):
    async def ensure_active_guild(
        self,
        *,
        guild_id: str,
        name: str,
        icon_url: str | None,
        member_count: int,
        owner_id: str,
    ) -> Any: ...

    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> Any: ...

    async def mark_left(self, guild_id: str) -> Any: ...


class GuildHealthTracker(Protocol):
    def set_cosmos_ok(self, ok: bool) -> None: ...


class GuildEventHandler:
    def __init__(
        self,
        *,
        guilds: GuildService,
        health: GuildHealthTracker | None = None,
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
        development_guild_id: str | None = None,
    ) -> None:
        self._guilds = guilds
        self._health = health
        self._runtime_mode = RuntimeMode(runtime_mode)
        self._development_guild_id = development_guild_id or ""

    def _managed(self, guild_id: str) -> bool:
        if not self._development_guild_id:
            return True
        return is_managed_guild(
            mode=self._runtime_mode,
            guild_id=guild_id,
            development_guild_id=self._development_guild_id,
        )

    async def on_guild_join(self, guild: Any) -> None:
        guild_id = str(guild.id)
        if not self._managed(guild_id):
            logger.info("ignoring unmanaged guild join %s", guild_id)
            return
        try:
            icon = getattr(guild, "icon", None)
            icon_url = str(icon.url) if icon is not None else None
            await self._guilds.ensure_active_guild(
                guild_id=guild_id,
                name=str(guild.name),
                icon_url=icon_url,
                member_count=int(getattr(guild, "member_count", 0) or 0),
                owner_id=str(getattr(guild, "owner_id", "")),
            )
            if self._health is not None:
                self._health.set_cosmos_ok(True)
        except Exception:
            logger.exception("guild join sync failed")
            if self._health is not None:
                self._health.set_cosmos_ok(False)

    async def on_guild_update(self, before: Any, after: Any) -> None:
        guild_id = str(after.id)
        if not self._managed(guild_id):
            return
        patch: dict[str, Any] = {}
        if getattr(before, "name", None) != getattr(after, "name", None):
            patch["name"] = str(after.name)
        before_icon = getattr(getattr(before, "icon", None), "url", None)
        after_icon = getattr(getattr(after, "icon", None), "url", None)
        if before_icon != after_icon:
            patch["icon_url"] = str(after_icon) if after_icon else None
        if getattr(before, "owner_id", None) != getattr(after, "owner_id", None):
            patch["owner_id"] = str(after.owner_id)
        if not patch:
            return
        try:
            await self._guilds.patch_metadata(guild_id, patch)
            if self._health is not None:
                self._health.set_cosmos_ok(True)
        except Exception:
            logger.exception("guild update sync failed")
            if self._health is not None:
                self._health.set_cosmos_ok(False)

    async def on_guild_remove(self, guild: Any) -> None:
        guild_id = str(guild.id)
        if not self._managed(guild_id):
            return
        try:
            await self._guilds.mark_left(guild_id)
            if self._health is not None:
                self._health.set_cosmos_ok(True)
        except Exception:
            logger.exception("guild remove sync failed")
            if self._health is not None:
                self._health.set_cosmos_ok(False)


__all__ = ["GuildEventHandler", "GuildHealthTracker", "GuildService"]
