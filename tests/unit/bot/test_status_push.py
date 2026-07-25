from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bot.modules.services.heartbeat import BotHeartbeatService


class FakeHealth:
    rabbitmq_connected = False
    cosmos_ok = False
    azure_queue_ok = False
    status_blob_ok = False


class FakeGateway:
    gateway_connected = False

    def guild_count(self) -> int:
        return 0

    def latency_ms(self) -> int | None:
        return None


class FakeStatus:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def update_status(self, status: dict[str, Any]) -> Any:
        self.updates.append(status)
        return status


@pytest.mark.asyncio
async def test_status_push_updates_status_section_only() -> None:
    status = FakeStatus()
    health = FakeHealth()
    service = BotHeartbeatService(
        node_id="node-local",
        application_version="v0.1.0",
        heartbeat_interval_sec=30,
        status_push_interval_sec=60,
        mqtt=None,
        status_service=status,
        health=health,
        gateway=FakeGateway(),
        utcnow=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    section = await service.push_status_once()
    assert section is not None
    assert status.updates
    assert set(status.updates[0].keys()) <= {"latency_ms", "guild_count", "updated_at"}
    assert health.status_blob_ok is True
