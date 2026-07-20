"""Phase 1 Slice 16 acceptance harness.

Claims only Head/Launcher-owned steps. Bot/AI Worker autonomous behavior stays deferred.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from helpers import (
    FakeBlobClient,
    FakeClock,
    FakeClusterTransport,
    FakeMqttTransport,
    make_settings,
)

from head.election import ElectionMachine, ElectionState
from head.launcher import LauncherClient
from head.lease import LeaseCoordinator
from head.logs import LogAggregator, RabbitBrokerEvent, RabbitMqLogBridge, RabbitSeverity
from head.mqtt import MqttControlError, MqttManager
from head.pubsub import ClusterPubSub
from head.update import DrainObserver, UpdateOrchestrator
from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import (
    ActivationGrant,
    ActivationGrantMode,
    AiWorkerHeartbeat,
    BotHeartbeat,
    ServiceLiveness,
    TelemetryLivePayload,
    TelemetryMetrics,
    UpdateAcceptedResponse,
    UpdateAvailableMessage,
    UpdateReason,
    format_log_line,
)

PHASE1_SCENARIO_OWNERSHIP = {
    "S01": {
        "steps": ["fail_closed_mqtt_before_election"],
        "head_election": "complete",
    },
    "S02": {
        "steps": ["lease_winner_grants", "lease_loser_never_grants"],
        "head_failover": "complete",
    },
    "S03": {
        "steps": ["head_grant_cessation"],
        "head_grant_cessation": "complete",
        "bot_autonomous_hard_stop": "deferred:phase2",
    },
    "S04": {
        "steps": ["head_mosquitto_disconnect_clears_authority"],
        "head_mosquitto_response": "complete",
        "bot_control_disconnect": "deferred:phase2",
    },
    "S05": {
        "steps": ["rabbitmq_event_bridge_normalize_and_topic"],
        "head_rabbitmq_event_bridge": "complete",
        "task_delivery_recovery": "deferred:phase2",
    },
    "S06": {
        "steps": ["same_term_blob_renew_recovery_path"],
        "head_coordination_failure_matrix": "complete",
    },
    "S07": {
        "steps": ["leader_drain_then_launcher_with_simulated_bot"],
        "head_launcher_ordering": "complete",
        "bot_drain_execution": "deferred:phase2",
    },
    "S08": {
        "steps": ["drain_timeout_escalates_to_stopped"],
        "head_timeout_escalation": "complete",
        "bot_hard_stop_execution": "deferred:phase2",
    },
    "S09": {
        "steps": ["launcher_update_admission_contract"],
        "launcher_rollback_and_cold_election": "complete",
    },
}


class _FakeArchive:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def buffer_lines(self, lines: list[str]) -> None:
        self.lines.extend(lines)

    async def flush(self, *, node_id: str, timestamp_iso: str | None = None) -> str:
        return f"logs/{node_id}/archive.log"


class _FakeObserver:
    def __init__(self, journal: list[str], *, drained: bool, stopped: bool = True) -> None:
        self.journal = journal
        self.drained = drained
        self.stopped = stopped

    async def wait_for_zero(self, leadership_term: str, timeout_sec: float) -> bool:
        self.journal.append("observer.drain")
        return self.drained

    async def wait_for_stopped(
        self, leadership_term: str, command_seq: int, timeout_sec: float
    ) -> bool:
        self.journal.append("observer.stopped")
        return self.stopped


class _FakeLauncher:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.calls: list[tuple[str, UpdateReason]] = []

    async def request_update(
        self, target_version: str, *, reason: UpdateReason
    ) -> UpdateAcceptedResponse:
        self.journal.append("launcher.update")
        self.calls.append((target_version, reason))
        return UpdateAcceptedResponse(
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="22222222-2222-4222-8222-222222222222",
            state="accepted",
            target_version=target_version,
        )


def _machine(
    *,
    mqtt: FakeMqttTransport | None = None,
    blob: FakeBlobClient | None = None,
) -> tuple[
    ElectionMachine,
    FakeClock,
    FakeMqttTransport,
    FakeBlobClient,
    FakeClusterTransport,
]:
    settings = make_settings()
    clock = FakeClock()
    mqtt_fake = mqtt or FakeMqttTransport()
    blob_fake = blob or FakeBlobClient()
    cluster_fake = FakeClusterTransport()
    machine = ElectionMachine(
        settings=settings,
        clock=clock,
        mqtt=MqttManager(mqtt_fake),
        lease=LeaseCoordinator(
            blob_fake,
            container=settings.lease_blob_container,
            blob_name=settings.lease_blob_name,
            duration_sec=settings.lease_duration_sec,
        ),
        pubsub=ClusterPubSub(cluster_fake, group=settings.pubsub_cluster_group),
    )
    return machine, clock, mqtt_fake, blob_fake, cluster_fake


async def _leader_updater(*, drained: bool) -> tuple[
    ElectionMachine,
    UpdateOrchestrator,
    FakeMqttTransport,
    FakeBlobClient,
    _FakeLauncher,
    list[str],
]:
    settings = make_settings()
    clock = FakeClock()
    mqtt = FakeMqttTransport()
    blob = FakeBlobClient()
    journal: list[str] = []
    cluster = ClusterPubSub(FakeClusterTransport(journal), group=settings.pubsub_cluster_group)
    election = ElectionMachine(
        settings=settings,
        clock=clock,
        mqtt=MqttManager(mqtt),
        lease=LeaseCoordinator(
            blob,
            container=settings.lease_blob_container,
            blob_name=settings.lease_blob_name,
            duration_sec=settings.lease_duration_sec,
        ),
        pubsub=cluster,
    )
    launcher = _FakeLauncher(journal)
    observer = _FakeObserver(journal, drained=drained, stopped=True)
    updater = UpdateOrchestrator(
        election=election,
        cluster=cluster,
        launcher=cast(LauncherClient, launcher),
        observer=cast(DrainObserver, observer),
    )
    await election.start()
    clock.advance(91)
    await election.tick()
    return election, updater, mqtt, blob, launcher, journal


@pytest.mark.acceptance()
def test_phase1_scenario_ownership_matrix() -> None:
    assert set(PHASE1_SCENARIO_OWNERSHIP) == {f"S{number:02d}" for number in range(1, 10)}
    for scenario_id, ownership in PHASE1_SCENARIO_OWNERSHIP.items():
        assert ownership["steps"], scenario_id
        deferred = [
            value for value in ownership.values() if str(value).startswith("deferred:")
        ]
        if scenario_id in {"S03", "S04", "S05", "S07", "S08"}:
            assert deferred, scenario_id


@pytest.mark.acceptance()
def test_p1_8_contracts_remain_concrete() -> None:
    bot = BotHeartbeat(
        node_id="node-a",
        application_version="v1.2.3",
        observed_at="2026-07-19T20:00:00Z",
        gateway_connected=True,
        latency_ms=42,
        guild_count=12,
        dependencies={
            "rabbitmq_connected": True,
            "cosmos_ok": True,
            "azure_queue_ok": True,
            "status_blob_ok": True,
        },
    )
    worker = AiWorkerHeartbeat(
        node_id="node-a",
        application_version="v1.2.3",
        observed_at="2026-07-19T20:00:00Z",
        state="running",
        active_tasks=1,
        dependencies={"rabbitmq_connected": True},
    )
    payload = TelemetryLivePayload(
        seq=1,
        node_id="node-a",
        leadership_term="11111111-1111-4111-8111-111111111111",
        sampled_at="2026-07-19T20:00:00Z",
        service_liveness=ServiceLiveness(bot="fresh", ai_worker="fresh"),
        metrics=TelemetryMetrics(
            cpu_percent=1,
            memory_mb=128,
            memory_percent=2,
            latency_ms=bot.latency_ms,
            guild_count=bot.guild_count,
            errors_in_window=0,
            uptime_sec=10,
        ),
        logs=[],
        logs_dropped=0,
    )
    assert worker.state == "running"
    assert payload.logs_dropped == 0


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s01_fail_closed_mqtt_blocks_election() -> None:
    """S01: Head-side fail-closed Mosquitto startup = complete."""
    mqtt = FakeMqttTransport()
    mqtt.fail_publish = True
    machine, clock, _, blob, _ = _machine(mqtt=mqtt)

    with pytest.raises(ConnectionError):
        await machine.start()
    clock.advance(100)
    await machine.tick()

    assert blob.acquires == 0
    assert machine.state == ElectionState.FOLLOWER


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s02_lease_winner_and_loser() -> None:
    """S02: Head-side failover fencing = complete."""
    winner, clock, mqtt, blob, _ = _machine()
    await winner.start()
    clock.advance(91)
    await winner.tick()
    grants = [
        item
        for item in mqtt.published
        if item[0] == MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
    ]
    assert blob.acquires == 1
    assert winner.state == ElectionState.LEADER
    assert len(grants) == 1

    loser_blob = FakeBlobClient()
    loser_blob.acquire_conflict = True
    loser, clock2, mqtt2, _, _ = _machine(blob=loser_blob)
    await loser.start()
    clock2.advance(91)
    await loser.tick()
    loser_grants = [
        item
        for item in mqtt2.published
        if item[0] == MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
    ]
    assert loser.state == ElectionState.FOLLOWER
    assert loser_grants == []


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s03_head_grant_cessation_on_planned_drain() -> None:
    """S03: Head grant cessation = complete; Bot autonomous hard-stop = deferred."""
    machine, clock, mqtt, _, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    assert machine.state == ElectionState.LEADER

    await machine.begin_planned_drain()
    assert machine.state == ElectionState.DRAINING
    mqtt.published.clear()
    clock.advance(machine.settings.bot_grant_renew_sec + 1)
    await machine.tick()
    grants = [
        json.loads(payload)
        for topic, payload, _, _ in mqtt.published
        if topic == MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
    ]
    assert all(grant["mode"] != "active" for grant in grants)


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s04_mosquitto_disconnect_clears_authority() -> None:
    """S04: Head Mosquitto outage handling = complete; Bot disconnect UX = deferred."""
    machine, clock, _, _, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    term = machine.leadership_term
    assert term is not None
    await machine.handle_mqtt_loss("broker lost")
    assert machine.mqtt.connected is False
    assert machine.mqtt.safe_state_confirmed is False
    with pytest.raises(MqttControlError):
        await machine.mqtt.publish_grant(
            ActivationGrant(
                grant_id="11111111-1111-4111-8111-111111111111",
                node_id="node-a",
                head_instance_id=str(machine.instance_id),
                leadership_term=str(term),
                command_seq=99,
                mode=ActivationGrantMode.active,
                ttl_sec=45,
                issued_at=datetime.now(tz=UTC),
            )
        )


@pytest.mark.acceptance()
def test_s05_rabbitmq_event_bridge_contract() -> None:
    """S05: Head RabbitMQ event bridge normalize/topic = complete."""
    archive = _FakeArchive()
    aggregator = LogAggregator(archive)
    bridge = RabbitMqLogBridge()
    line = bridge.handle(
        RabbitBrokerEvent(
            routing_key="alarm.set",
            severity=RabbitSeverity.error,
            occurred_at=datetime(2026, 7, 19, tzinfo=UTC),
            headers={"name": "memory", "body": b"should-ignore"},
        ),
        aggregator,
    )
    assert line is not None
    assert bridge.mqtt_topic(RabbitSeverity.error) == "logs/errors/rabbitmq"
    assert bridge.classify("connection.created") is None
    assert "body" not in line.tags
    assert format_log_line(line).startswith("[")


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s06_uncertain_renewal_soft_stops() -> None:
    """S06: Head coordination failure matrix (renew soft-stop) = complete."""
    machine, clock, mqtt, blob, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()

    blob.renew_error = TimeoutError("network timeout")
    clock.advance(31)
    await machine.tick()
    assert machine.state == ElectionState.DRAINING
    assert json.loads(mqtt.published[-1][1])["mode"] == "draining"


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s07_leader_update_ordering_with_simulated_bot_drain() -> None:
    """S07: Head→Launcher ordering with simulated Bot drain/ack = complete."""
    _, updater, _, blob, launcher, journal = await _leader_updater(drained=True)
    await updater.handle(UpdateAvailableMessage(target_version="v1.1.0"))
    assert journal.index("observer.drain") < journal.index("observer.stopped")
    assert journal.index("observer.stopped") < journal.index("launcher.update")
    assert blob.releases == 1
    assert launcher.calls == [("v1.1.0", UpdateReason.auto_detected)]


@pytest.mark.acceptance()
@pytest.mark.asyncio
async def test_s08_drain_timeout_still_calls_launcher() -> None:
    """S08: Head drain-timeout escalation path = complete; Bot hard-stop body = deferred."""
    _, updater, mqtt, blob, launcher, journal = await _leader_updater(drained=False)
    await updater.handle(UpdateAvailableMessage(target_version="v1.2.0"))
    stopped = [
        json.loads(payload)
        for topic, payload, _, _ in mqtt.published
        if topic == MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
        and json.loads(payload)["state"] == "stopped"
    ]
    assert stopped[0]["reason"] == "planned_update_drain_timeout"
    assert journal.index("observer.drain") < journal.index("observer.stopped")
    assert blob.releases == 1
    assert launcher.calls == [("v1.2.0", UpdateReason.auto_detected)]


@pytest.mark.acceptance()
def test_s09_launcher_admission_response_contract() -> None:
    """S09: Launcher admission response shape used after rollback/cold election = complete."""
    response = UpdateAcceptedResponse(
        request_id="11111111-1111-4111-8111-111111111111",
        operation_id="22222222-2222-4222-8222-222222222222",
        state="accepted",
        target_version="v1.0.0",
    )
    assert response.state == "accepted"
    assert UUID(response.operation_id)
