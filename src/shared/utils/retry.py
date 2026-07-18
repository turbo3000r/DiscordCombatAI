from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

from shared.azure.errors import AzurePermanentError, classify_azure_error

T = TypeVar("T")


class RetryCategory(StrEnum):
    SAFE_READ = "safe_read"
    ETag_RMW = "etag_rmw"
    UNCERTAIN_SEND = "uncertain_send"
    APPEND_BLOB = "append_blob"
    LEASE_FAIL_FAST = "lease_fail_fast"


CATEGORY_ATTEMPTS: dict[RetryCategory, int] = {
    RetryCategory.SAFE_READ: 3,
    RetryCategory.ETag_RMW: 5,
    RetryCategory.UNCERTAIN_SEND: 3,
    RetryCategory.APPEND_BLOB: 3,
    RetryCategory.LEASE_FAIL_FAST: 1,
}


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    category: RetryCategory,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    base_delay: float = 0.5,
    max_delay: float = 4.0,
    jitter_ratio: float = 0.2,
) -> T:
    attempts = CATEGORY_ATTEMPTS[category]
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except AzurePermanentError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify Azure failures centrally
            classified = classify_azure_error(exc)
            last_error = classified
            if isinstance(classified, AzurePermanentError) or attempt >= attempts:
                raise classified from exc
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jitter = delay * jitter_ratio * random.random()
            await sleep(delay + jitter)
    assert last_error is not None
    raise last_error
