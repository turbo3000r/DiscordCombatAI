from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class BlobLeaseClient(Protocol):
    async def acquire_lease(
        self, container_name: str, blob_name: str, *, lease_duration: int = 15
    ) -> Any: ...

    async def renew_lease(
        self, container_name: str, blob_name: str, lease: Any
    ) -> Any: ...

    async def release_lease(
        self, container_name: str, blob_name: str, lease: Any
    ) -> Any: ...


class LeaseConflictError(RuntimeError):
    """The named lease is already held by another node."""


class LeaseLostError(RuntimeError):
    """Azure has conclusively rejected this node's lease identity."""


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class LeaseSnapshot:
    held: bool
    lease_id: str | None


class LeaseCoordinator:
    def __init__(
        self,
        client: BlobLeaseClient,
        *,
        container: str,
        blob_name: str,
        duration_sec: int,
    ) -> None:
        self._client = client
        self.container = container
        self.blob_name = blob_name
        self.duration_sec = duration_sec
        self._lease: Any | None = None
        self._ensured = False

    @property
    def held(self) -> bool:
        return self._lease is not None

    @property
    def snapshot(self) -> LeaseSnapshot:
        lease_id = getattr(self._lease, "id", None)
        return LeaseSnapshot(self.held, str(lease_id) if lease_id is not None else None)

    async def acquire(self) -> bool:
        if self._lease is not None:
            return True
        if not self._ensured:
            ensure_blob = getattr(self._client, "ensure_blob", None)
            if ensure_blob is not None:
                await ensure_blob(self.container, self.blob_name)
            self._ensured = True
        try:
            self._lease = await self._client.acquire_lease(
                self.container,
                self.blob_name,
                lease_duration=self.duration_sec,
            )
        except Exception as exc:
            if _status_code(exc) in {409, 412}:
                return False
            raise
        return True

    async def renew(self) -> None:
        if self._lease is None:
            raise LeaseLostError("cannot renew a lease that is not held")
        try:
            await self._client.renew_lease(self.container, self.blob_name, self._lease)
        except Exception as exc:
            if _status_code(exc) in {404, 409, 412}:
                self._lease = None
                raise LeaseLostError("lease ownership was lost") from exc
            raise

    async def release(self) -> None:
        lease, self._lease = self._lease, None
        if lease is None:
            return
        try:
            await self._client.release_lease(self.container, self.blob_name, lease)
        except Exception:
            self._lease = lease
            raise

    def abandon(self) -> None:
        """Stop renewing without releasing; Azure expires the lease naturally."""
        self._lease = None


__all__ = [
    "BlobLeaseClient",
    "LeaseConflictError",
    "LeaseCoordinator",
    "LeaseLostError",
    "LeaseSnapshot",
]
