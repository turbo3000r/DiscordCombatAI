from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


async def maybe_await(value: T | Awaitable[T]) -> T:
    if asyncio.iscoroutine(value):
        return cast(T, await value)
    if isinstance(value, Awaitable):
        return cast(T, await value)
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def json_loads(payload: str) -> Any:
    return json.loads(payload)


def make_storage_endpoint(account_name: str, service: str) -> str:
    return f"https://{account_name}.{service}.core.windows.net"
