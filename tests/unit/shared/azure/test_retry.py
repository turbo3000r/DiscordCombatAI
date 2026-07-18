from __future__ import annotations

import pytest

from shared.azure.errors import AzurePermanentError
from shared.utils.retry import RetryCategory, retry_async


@pytest.mark.asyncio()
async def test_retry_async_retries_transient(monkeypatch) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    async def operation() -> int:
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("timeout")
        return 42

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    result = await retry_async(operation, category=RetryCategory.SAFE_READ, sleep=fake_sleep)
    assert result == 42
    assert len(attempts) == 3
    assert len(sleeps) == 2
    assert sleeps[1] >= sleeps[0]


@pytest.mark.asyncio()
async def test_retry_async_stops_on_permanent_error() -> None:
    async def operation() -> None:
        raise RuntimeError("401 unauthorized")

    with pytest.raises(AzurePermanentError):
        await retry_async(operation, category=RetryCategory.SAFE_READ)
