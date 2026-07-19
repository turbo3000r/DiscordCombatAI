from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


__all__ = ["Clock", "SystemClock"]
