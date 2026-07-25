"""Periodic guild metadata reconciliation for currently joined guilds."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Protocol

from bot.modules.services.guild_guard import is_managed_guild
from shared.runtime.settings import RuntimeMode

logger = logging.getLogger(__name__)


class GuildService(Protocol):
    async def patch_metadata(self, guild_id: str, patch: dict[str, Any]) -> Any: ...


class GuildSyncService:
    """Iterate bot.guilds only — offline absent-guild sweep is deferred (P1.3)."""

    def __init__(
        self,
        *,
        guilds: GuildService,
        interval_sec: float,
        runtime_mode: RuntimeMode | str = RuntimeMode.production,
        development_guild_id: str | None = None,
    ) -> None:
        self._guilds = guilds
        self._interval_sec = interval_sec
        self._runtime_mode = RuntimeMode(runtime_mode)
        self._development_guild_id = development_guild_id or ""
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self, bot: Any) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(bot), name="bot-guild-sync")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _managed(self, guild_id: str) -> bool:
        if not self._development_guild_id:
            return True
        return is_managed_guild(
            mode=self._runtime_mode,
            guild_id=guild_id,
            development_guild_id=self._development_guild_id,
        )

    async def reconcile_once(self, bot: Any) -> None:
        for guild in list(getattr(bot, "guilds", [])):
            guild_id = str(guild.id)
            if not self._managed(guild_id):
                continue
            try:
                icon = getattr(guild, "icon", None)
                await self._guilds.patch_metadata(
                    guild_id,
                    {
                        "name": str(guild.name),
                        "icon_url": str(icon.url) if icon is not None else None,
                        "member_count": int(getattr(guild, "member_count", 0) or 0),
                        "owner_id": str(getattr(guild, "owner_id", "")),
                    },
                )
            except Exception:
                logger.exception("guild sync failed for %s", getattr(guild, "id", "?"))

    async def _loop(self, bot: Any) -> None:
        while not self._stop.is_set():
            await self.reconcile_once(bot)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_sec)
            except TimeoutError:
                continue


__all__ = ["GuildSyncService"]
