from __future__ import annotations

import json

import pytest
from helpers import (
    FakeBlobClient,
    FakeClock,
    FakeClusterTransport,
    FakeMqttTransport,
    make_settings,
)

from head.election import ElectionMachine, ElectionState
from head.lease import LeaseCoordinator
from head.mqtt import MqttManager
from head.pubsub import ClusterPubSub
from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES


def _machine(
    *,
    mqtt_transport: FakeMqttTransport | None = None,
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
    mqtt_fake = mqtt_transport or FakeMqttTransport()
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


@pytest.mark.asyncio
async def test_s01_publish_confirmation_failure_prevents_election() -> None:
    mqtt = FakeMqttTransport()
    mqtt.fail_publish = True
    machine, clock, _, blob, _ = _machine(mqtt_transport=mqtt)

    with pytest.raises(ConnectionError):
        await machine.start()
    clock.advance(100)
    await machine.tick()

    assert blob.acquires == 0
    assert machine.state == ElectionState.FOLLOWER


@pytest.mark.asyncio
async def test_s02_lease_winner_issues_first_non_retained_grant() -> None:
    machine, clock, mqtt, blob, cluster = _machine()
    await machine.start()

    startup = mqtt.published[0]
    assert startup[0] == MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
    assert startup[3] is True
    assert json.loads(startup[1])["state"] == "inactive"

    clock.advance(91)
    await machine.tick()

    grants = [
        item
        for item in mqtt.published
        if item[0] == MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
    ]
    assert blob.acquires == 1
    assert machine.state == ElectionState.LEADER
    assert len(grants) == 1
    assert grants[0][3] is False
    assert json.loads(grants[0][1])["command_seq"] == 1
    assert cluster.joined == ["cluster"]


@pytest.mark.asyncio
async def test_s02_lease_loser_never_issues_grant() -> None:
    blob = FakeBlobClient()
    blob.acquire_conflict = True
    machine, clock, mqtt, _, _ = _machine(blob=blob)
    await machine.start()

    clock.advance(91)
    await machine.tick()

    assert machine.state == ElectionState.FOLLOWER
    assert all(
        topic != MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
        for topic, _, _, _ in mqtt.published
    )


@pytest.mark.asyncio
async def test_s03_reconnect_republishes_safe_state_but_not_grant_history() -> None:
    machine, clock, mqtt, _, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    mqtt.published.clear()

    await machine.mqtt.reconnect()

    assert [item[0] for item in mqtt.published] == [
        MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
    ]


@pytest.mark.asyncio
async def test_transport_auto_reconnect_restores_safe_state_and_subscriptions() -> None:
    machine, _, mqtt, _, _ = _machine()
    await machine.start()
    mqtt.published.clear()
    mqtt.subscriptions.clear()
    await machine.mqtt.mark_disconnected("connection lost")

    await mqtt.simulate_reconnect()

    assert machine.mqtt.connected is True
    assert machine.mqtt.safe_state_confirmed is True
    assert [item[0] for item in mqtt.published] == [
        MQTT_TOPIC_POLICIES["control_bot_desired_state"].topic
    ]
    assert {topic for topic, _ in mqtt.subscriptions} == {
        MQTT_TOPIC_POLICIES[name].topic
        for name in (
            "status_bot_control_ack",
            "status_bot_drain_progress",
            "status_ai_worker_pause_ack",
            "status_bot_heartbeat",
            "status_ai_worker_heartbeat",
            "logs_all",
        )
    }


@pytest.mark.asyncio
async def test_recovered_mqtt_control_restores_authority_in_same_term() -> None:
    machine, clock, mqtt, _, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    term = machine.leadership_term
    await machine.mqtt.mark_disconnected("connection lost")
    clock.advance(16)
    await machine.tick()
    assert machine.state == ElectionState.DRAINING

    await mqtt.simulate_reconnect()
    assert machine.can_restore_same_term is True
    await machine.restore_same_term()

    assert machine.state == ElectionState.LEADER
    assert machine.leadership_term == term
    assert json.loads(mqtt.published[-1][1])["mode"] == "active"


@pytest.mark.asyncio
async def test_recovered_pubsub_and_lease_restore_authority_in_same_term() -> None:
    machine, clock, _, _, cluster = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    term = machine.leadership_term

    await cluster.simulate_connection_state(False)
    await machine.handle_pubsub_loss()
    assert machine.state == ElectionState.DRAINING
    assert machine.can_restore_same_term is False

    await cluster.simulate_connection_state(True)
    clock.advance(31)
    await machine.tick()
    assert machine.can_restore_same_term is True
    await machine.restore_same_term()

    assert machine.state == ElectionState.LEADER
    assert machine.leadership_term == term


@pytest.mark.asyncio
async def test_s06_uncertain_renewal_soft_stops_but_confirmed_loss_hard_stops() -> None:
    machine, clock, mqtt, blob, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()

    blob.renew_error = TimeoutError("network timeout")
    clock.advance(31)
    await machine.tick()
    assert machine.state == ElectionState.DRAINING
    assert json.loads(mqtt.published[-2][1])["state"] == "draining"
    assert json.loads(mqtt.published[-1][1])["mode"] == "draining"

    lost = RuntimeError("lease mismatch")
    lost.status_code = 412  # type: ignore[attr-defined]
    blob.renew_error = lost
    clock.advance(31)
    await machine.tick()

    assert machine.state == ElectionState.FOLLOWER
    assert json.loads(mqtt.published[-1][1])["state"] == "stopped"
    assert all(
        item[0] != MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
        for item in mqtt.published[-1:]
    )


@pytest.mark.asyncio
async def test_s06_pubsub_only_loss_soft_stops_but_combined_loss_hard_stops() -> None:
    machine, clock, mqtt, blob, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()

    await machine.handle_pubsub_loss()
    assert machine.state == ElectionState.DRAINING
    assert json.loads(mqtt.published[-1][1])["mode"] == "draining"

    blob.renew_error = TimeoutError("Blob coordination unavailable")
    clock.advance(31)
    await machine.tick()

    assert machine.state == ElectionState.FOLLOWER
    assert machine.lease.held is False
    assert json.loads(mqtt.published[-1][1])["state"] == "stopped"


@pytest.mark.asyncio
async def test_hard_stop_abandons_lease_if_stopped_publish_cannot_be_confirmed() -> None:
    machine, clock, mqtt, blob, _ = _machine()
    await machine.start()
    clock.advance(91)
    await machine.tick()
    mqtt.fail_publish = True

    stopped_seq = await machine.hard_stop("mosquitto_unavailable")

    assert stopped_seq is None
    assert machine.state == ElectionState.FOLLOWER
    assert machine.lease.held is False
    assert blob.releases == 0
