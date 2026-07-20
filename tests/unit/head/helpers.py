from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from head.settings import HeadSettings


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.origin = datetime(2026, 7, 19, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def utcnow(self) -> datetime:
        return self.origin + timedelta(seconds=self.elapsed)

    async def sleep(self, seconds: float) -> None:
        self.elapsed += seconds
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds


class FakeMqttTransport:
    def __init__(self, journal: list[str] | None = None) -> None:
        self.journal = journal if journal is not None else []
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.fail_publish = False
        self.closed = 0
        self.message_handler: Any | None = None
        self.disconnect_handler: Any | None = None
        self.connect_handler: Any | None = None

    def set_message_handler(self, handler: Any) -> None:
        self.message_handler = handler

    def set_disconnect_handler(self, handler: Any) -> None:
        self.disconnect_handler = handler

    def set_connect_handler(self, handler: Any) -> None:
        self.connect_handler = handler

    async def connect(self) -> None:
        self.journal.append("mqtt.connect")

    async def publish(
        self, topic: str, payload: bytes, *, qos: int, retain: bool
    ) -> None:
        if self.fail_publish:
            raise ConnectionError("publish confirmation failed")
        self.published.append((topic, payload, qos, retain))
        self.journal.append(f"mqtt.publish:{topic}")

    async def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    async def close(self) -> None:
        self.closed += 1

    async def simulate_reconnect(self) -> None:
        assert self.connect_handler is not None
        await self.connect_handler()


class _Lease:
    id = "lease-id"


class FakeBlobClient:
    def __init__(self, journal: list[str] | None = None) -> None:
        self.journal = journal if journal is not None else []
        self.acquire_conflict = False
        self.renew_error: BaseException | None = None
        self.acquires = 0
        self.renews = 0
        self.releases = 0

    async def acquire_lease(
        self, container_name: str, blob_name: str, *, lease_duration: int = 15
    ) -> Any:
        self.acquires += 1
        self.journal.append("lease.acquire")
        if self.acquire_conflict:
            error = RuntimeError("lease conflict")
            error.status_code = 409  # type: ignore[attr-defined]
            raise error
        return _Lease()

    async def renew_lease(
        self, container_name: str, blob_name: str, lease: Any
    ) -> None:
        self.renews += 1
        self.journal.append("lease.renew")
        if self.renew_error is not None:
            raise self.renew_error

    async def release_lease(
        self, container_name: str, blob_name: str, lease: Any
    ) -> None:
        self.releases += 1
        self.journal.append("lease.release")


class FakeClusterTransport:
    def __init__(self, journal: list[str] | None = None) -> None:
        self.journal = journal if journal is not None else []
        self.sent: list[tuple[str, bytes]] = []
        self.joined: list[str] = []
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = 0
        self.state_handler: Any | None = None

    def set_state_handler(self, handler: Any) -> None:
        self.state_handler = handler

    async def connect(self) -> None:
        self.journal.append("pubsub.connect")

    async def join_group(self, group: str) -> None:
        self.joined.append(group)
        self.journal.append(f"pubsub.join:{group}")

    async def send_group(self, group: str, payload: bytes) -> None:
        self.sent.append((group, payload))
        self.journal.append(f"pubsub.send:{group}")

    async def receive(self) -> bytes:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed += 1

    async def simulate_connection_state(self, connected: bool) -> None:
        assert self.state_handler is not None
        await self.state_handler(connected)


def make_settings() -> HeadSettings:
    return HeadSettings(
        node_id="node-a",
        application_version="v1.0.0",
        github_repo="owner/repo",
        rabbitmq_user="discordcombatai",
        rabbitmq_pass="change-me-in-env",
    )
