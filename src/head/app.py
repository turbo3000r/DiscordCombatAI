from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Protocol


class ManagedComponent(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...


class AsyncResource(Protocol):
    async def close(self) -> None: ...


LoopFactory = Callable[[], Coroutine[Any, Any, None]]


class HeadApplication:
    """Owns component startup, background tasks, and exactly-once closure."""

    def __init__(
        self,
        *,
        components: Sequence[ManagedComponent] = (),
        resources: Sequence[AsyncResource] = (),
        loops: Sequence[tuple[str, LoopFactory]] = (),
        on_initialized: Callable[[bool], None] | None = None,
    ) -> None:
        self._components = list(components)
        self._resources = list(resources)
        self._loops = list(loops)
        self._on_initialized = on_initialized
        self._tasks: list[asyncio.Task[None]] = []
        self._started_components: list[ManagedComponent] = []
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("application is closed")
        try:
            for component in self._components:
                self._started_components.append(component)
                await component.start()
            self._tasks = [
                asyncio.create_task(factory(), name=name) for name, factory in self._loops
            ]
            if self._on_initialized is not None:
                self._on_initialized(True)
        except BaseException:
            await self.close()
            raise

    async def run(self) -> None:
        await self.start()
        if self._tasks:
            await asyncio.gather(*self._tasks)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_initialized is not None:
            self._on_initialized(False)
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        errors: list[Exception] = []
        for component in reversed(self._started_components):
            try:
                await component.close()
            except Exception as exc:
                errors.append(exc)
        for resource in reversed(self._resources):
            try:
                await resource.close()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Head resource closure failed", errors)

    async def __aenter__(self) -> HeadApplication:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.close()


__all__ = [
    "AsyncResource",
    "HeadApplication",
    "LoopFactory",
    "ManagedComponent",
]
