from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _LifecycleRegistry:
    resources: list[Any] = field(default_factory=list)
    seen: set[int] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def register(self, resource: Any) -> None:
        resource_id = id(resource)
        if resource_id in self.seen:
            return
        self.seen.add(resource_id)
        self.resources.append(resource)

    async def close_all(self) -> None:
        async with self.lock:
            resources = list(reversed(self.resources))
            self.resources.clear()
            self.seen.clear()
        for resource in resources:
            close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                await result


_REGISTRY = _LifecycleRegistry()


def register_resource(resource: Any) -> Any:
    _REGISTRY.register(resource)
    return resource


async def close_all_resources() -> None:
    await _REGISTRY.close_all()
