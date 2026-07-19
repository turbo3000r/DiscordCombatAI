from __future__ import annotations

import json
from typing import cast

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
from head.mqtt import MqttManager
from head.pubsub import ClusterPubSub
from head.update import DrainObserver, UpdateOrchestrator
from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import UpdateAcceptedResponse, UpdateAvailableMessage, UpdateReason


class FakeObserver:
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


class FakeLauncher:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.calls: list[tuple[str, UpdateReason]] = []
        self.fail = False

    async def request_update(
        self, target_version: str, *, reason: UpdateReason
    ) -> UpdateAcceptedResponse:
        self.journal.append("launcher.update")
        self.calls.append((target_version, reason))
        if self.fail:
            raise ConnectionError("Launcher unavailable")
        return UpdateAcceptedResponse(
            request_id="11111111-1111-4111-8111-111111111111",
            operation_id="22222222-2222-4222-8222-222222222222",
            state="accepted",
            target_version=target_version,
        )


async def _setup(
    *, promote: bool, drained: bool
) -> tuple[
    ElectionMachine,
    UpdateOrchestrator,
    FakeMqttTransport,
    FakeBlobClient,
    FakeLauncher,
    list[str],
]:
    journal: list[str] = []
    settings = make_settings()
    clock = FakeClock()
    mqtt_transport = FakeMqttTransport(journal)
    blob = FakeBlobClient(journal)
    cluster_transport = FakeClusterTransport(journal)
    cluster = ClusterPubSub(cluster_transport, group=settings.pubsub_cluster_group)
    election = ElectionMachine(
        settings=settings,
        clock=clock,
        mqtt=MqttManager(mqtt_transport),
        lease=LeaseCoordinator(
            blob,
            container=settings.lease_blob_container,
            blob_name=settings.lease_blob_name,
            duration_sec=settings.lease_duration_sec,
        ),
        pubsub=cluster,
    )
    await election.start()
    if promote:
        clock.advance(91)
        await election.tick()
    launcher = FakeLauncher(journal)
    orchestrator = UpdateOrchestrator(
        election=election,
        cluster=cluster,
        launcher=cast(LauncherClient, launcher),
        observer=cast(DrainObserver, FakeObserver(journal, drained=drained)),
    )
    journal.clear()
    mqtt_transport.published.clear()
    return election, orchestrator, mqtt_transport, blob, launcher, journal


@pytest.mark.asyncio
async def test_s07_leader_drains_stops_releases_then_calls_launcher() -> None:
    election, orchestrator, mqtt, blob, launcher, journal = await _setup(
        promote=True, drained=True
    )

    result = await orchestrator.handle(UpdateAvailableMessage(target_version="v1.1.0"))

    desired_payloads = [
        json.loads(payload)
        for topic, payload, _, _ in mqtt.published
        if topic == MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
    ]
    assert [payload["state"] for payload in desired_payloads] == ["draining", "stopped"]
    assert journal.index("observer.stopped") < journal.index("lease.release")
    assert journal.index("lease.release") < journal.index("launcher.update")
    assert blob.releases == 1
    assert launcher.calls == [("v1.1.0", UpdateReason.auto_detected)]
    assert result is not None
    assert election.state == ElectionState.UPDATING


@pytest.mark.asyncio
async def test_s08_drain_timeout_still_hard_stops_before_release() -> None:
    _, orchestrator, mqtt, blob, _, journal = await _setup(promote=True, drained=False)

    await orchestrator.handle(UpdateAvailableMessage(target_version="v1.1.0"))

    stopped = [
        json.loads(payload)
        for topic, payload, _, _ in mqtt.published
        if topic == MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
        and json.loads(payload)["state"] == "stopped"
    ]
    assert stopped[0]["reason"] == "planned_update_drain_timeout"
    assert journal.index("observer.drain") < journal.index("observer.stopped")
    assert journal.index("observer.stopped") < journal.index("lease.release")
    assert blob.releases == 1


@pytest.mark.asyncio
async def test_s07_follower_calls_launcher_without_drain_or_lease_release() -> None:
    election, orchestrator, mqtt, blob, launcher, journal = await _setup(
        promote=False, drained=True
    )

    await orchestrator.handle(UpdateAvailableMessage(target_version="v1.1.0"))

    assert election.state == ElectionState.FOLLOWER
    assert mqtt.published == []
    assert blob.releases == 0
    assert journal == ["launcher.update"]
    assert len(launcher.calls) == 1


@pytest.mark.asyncio
async def test_update_broadcast_is_deduplicated_by_target_version() -> None:
    _, orchestrator, _, _, launcher, _ = await _setup(promote=False, drained=True)
    message = UpdateAvailableMessage(target_version="v1.1.0")

    await orchestrator.handle(message)
    duplicate = await orchestrator.handle(message)

    assert duplicate is None
    assert len(launcher.calls) == 1


@pytest.mark.asyncio
async def test_failed_launcher_submission_can_be_retried() -> None:
    _, orchestrator, _, _, launcher, _ = await _setup(promote=False, drained=True)
    message = UpdateAvailableMessage(target_version="v1.1.0")
    launcher.fail = True

    with pytest.raises(ConnectionError):
        await orchestrator.handle(message)

    launcher.fail = False
    result = await orchestrator.handle(message)
    assert result is not None
    assert len(launcher.calls) == 2
