from __future__ import annotations

import pytest

from shared.azure.lifecycle import close_all_resources, register_resource


class _SyncResource:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _AsyncResource:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio()
async def test_lifecycle_closes_registered_resources() -> None:
    sync = _SyncResource()
    async_resource = _AsyncResource()
    register_resource(sync)
    register_resource(async_resource)

    await close_all_resources()

    assert sync.closed is True
    assert async_resource.closed is True
