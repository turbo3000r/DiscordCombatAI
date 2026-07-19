from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from helpers import FakeClock

from head.http import HeadHttpApi, HeadStatus, HttpRequest, RequestAuthenticator
from head.logs import LogAggregator, RabbitBrokerEvent, RabbitMqLogBridge, RabbitSeverity
from head.release import GitHubRelease, select_newest_stable
from head.telemetry import HardwareSnapshot, TelemetryPipeline
from shared.models import NodeMetricsEntity, TelemetryLivePayload
from shared.security.ipc_auth import sign_request


class FakeArchive:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.flushes = 0

    def buffer_lines(self, lines: list[str]) -> None:
        self.lines.extend(lines)

    async def flush(self, *, node_id: str, timestamp_iso: str | None = None) -> str:
        self.flushes += 1
        return f"logs/{node_id}/archive.log"


class FakeSampler:
    def sample(self) -> HardwareSnapshot:
        return HardwareSnapshot(cpu_percent=25, memory_mb=512, memory_percent=50)


class FakeMetricsWriter:
    def __init__(self) -> None:
        self.entities: list[NodeMetricsEntity] = []
        self.fail = False

    async def write(self, entity: NodeMetricsEntity) -> None:
        if self.fail:
            raise ConnectionError("Table unavailable")
        self.entities.append(entity)


class FakeLiveSender:
    def __init__(self) -> None:
        self.payloads: list[tuple[str, TelemetryLivePayload]] = []

    async def send(self, group: str, payload: TelemetryLivePayload) -> None:
        self.payloads.append((group, payload))


def test_release_selector_ignores_drafts_and_prereleases() -> None:
    selected = select_newest_stable(
        [
            GitHubRelease(tag_name="v1.2.0", draft=False, prerelease=False),
            GitHubRelease(tag_name="v2.0.0-rc.1", draft=False, prerelease=False),
            GitHubRelease(tag_name="v3.0.0", draft=True, prerelease=False),
            GitHubRelease(tag_name="v1.3.0", draft=False, prerelease=True),
        ],
        current_version="v1.0.0",
    )

    assert selected is not None
    assert selected.tag_name == "v1.2.0"


def test_log_bridge_redacts_and_never_accepts_message_payload() -> None:
    archive = FakeArchive()
    aggregator = LogAggregator(archive)
    bridge = RabbitMqLogBridge(aggregator)

    bridge.handle(
        RabbitBrokerEvent(
            routing_key="queue.deleted",
            severity=RabbitSeverity.warning,
            occurred_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
    )
    aggregator.ingest(
        "[2026-07-19T00:00:00Z][ERROR][bot][worker]: [api_key=very-secret]"
    )

    assert "very-secret" not in "\n".join(archive.lines)
    assert aggregator.errors_in_window == 1


@pytest.mark.asyncio
async def test_telemetry_is_leader_only_and_always_sends_live_tick() -> None:
    clock = FakeClock()
    archive = FakeArchive()
    logs = LogAggregator(archive)
    writer = FakeMetricsWriter()
    sender = FakeLiveSender()
    pipeline = TelemetryPipeline(
        node_id="node-a",
        clock=clock,
        sampler=FakeSampler(),
        logs=logs,
        metrics_writer=writer,
        live_sender=sender,
        dashboard_group="dashboard-live",
        batch_interval_sec=60,
    )

    assert await pipeline.flush_persistent(is_leader=False, leadership_term=None) is None
    assert await pipeline.stream_live(is_leader=False, leadership_term=None) is None
    await pipeline.flush_persistent(is_leader=True, leadership_term=str(UUID(int=1)))
    await pipeline.stream_live(is_leader=True, leadership_term=str(UUID(int=1)))

    assert len(writer.entities) == 1
    assert len(sender.payloads) == 1
    assert sender.payloads[0][0] == "dashboard-live"


@pytest.mark.asyncio
async def test_telemetry_buffers_oldest_first_and_applies_staleness_and_live_caps() -> None:
    clock = FakeClock()
    archive = FakeArchive()
    logs = LogAggregator(archive)
    writer = FakeMetricsWriter()
    sender = FakeLiveSender()
    pipeline = TelemetryPipeline(
        node_id="node-a",
        clock=clock,
        sampler=FakeSampler(),
        logs=logs,
        metrics_writer=writer,
        live_sender=sender,
        dashboard_group="dashboard-live",
        batch_interval_sec=60,
        metrics_buffer_max_batches=2,
        live_max_logs=1,
        live_max_bytes=65_536,
    )
    pipeline.update_bot_sample(latency_ms=42, guild_count=3)
    logs.ingest("[2026-07-19T00:00:00Z][INFO][bot][one]: [first]")
    logs.ingest("[2026-07-19T00:00:01Z][INFO][bot][two]: [second]")

    writer.fail = True
    for _ in range(3):
        await pipeline.flush_persistent(is_leader=True, leadership_term=str(UUID(int=1)))
        clock.advance(60)
    assert pipeline.dropped_metric_windows == 1

    writer.fail = False
    await pipeline.flush_persistent(is_leader=True, leadership_term=str(UUID(int=1)))
    assert pipeline.dropped_metric_windows == 2
    assert len(writer.entities) == 2

    payload = await pipeline.stream_live(is_leader=True, leadership_term=str(UUID(int=1)))
    assert payload is not None
    assert payload.service_liveness.bot == "stale"
    assert payload.metrics.latency_ms is None
    assert len(payload.logs) == 1
    assert payload.logs_dropped == 1


def test_authenticated_health_is_liveness_only_and_replay_protected() -> None:
    clock = FakeClock()
    secret = b"x" * 32
    request_id = UUID("11111111-1111-4111-8111-111111111111")
    timestamp = int(clock.utcnow().timestamp())
    signature = sign_request(secret, "GET", "/v1/health", timestamp, request_id, b"")
    request = HttpRequest(
        method="GET",
        path_with_query="/v1/health",
        headers={
            "X-DCA-Timestamp": str(timestamp),
            "X-DCA-Request-ID": str(request_id),
            "X-DCA-Signature": signature,
        },
    )
    api = HeadHttpApi(
        authenticator=RequestAuthenticator(secret, clock),
        clock=clock,
        version="v1.0.0",
        instance_id="22222222-2222-4222-8222-222222222222",
        started_at=clock.utcnow(),
        status_provider=lambda: HeadStatus(
            election_state="FOLLOWER",
            is_leader=False,
            lease_held=False,
            mosquitto_connected=True,
            pubsub_connected=True,
        ),
    )

    initializing = api.handle(request)
    replay = api.handle(request)

    assert initializing.status == 503
    assert b"initializing" in initializing.body
    assert b"is_leader" not in initializing.body
    assert replay.status == 401
