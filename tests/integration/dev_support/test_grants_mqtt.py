"""Integration: GrantPublisher wire shape and MQTT publish against Compose brokers."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import ActivationGrant, ActivationGrantMode

pytestmark = pytest.mark.integration

GRANT_TOPIC = MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic


def _brokers_available() -> bool:
    host = os.environ.get("BOT_MOSQUITTO_HOST", "127.0.0.1")
    port = int(os.environ.get("BOT_MOSQUITTO_PORT", "1883"))
    try:
        import socket

        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _brokers_available(), reason="Mosquitto not reachable on loopback")
@pytest.mark.asyncio
async def test_grant_publisher_emits_valid_activation_grant() -> None:
    import paho.mqtt.client as mqtt

    from dev_support.grants import GrantPublisher

    received: asyncio.Queue[bytes] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"s14-grant-sub-{uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
    )

    def on_message(_c: object, _u: object, msg: object) -> None:
        payload = bytes(msg.payload)
        loop.call_soon_threadsafe(received.put_nowait, payload)

    client.on_message = on_message
    host = os.environ.get("BOT_MOSQUITTO_HOST", "127.0.0.1")
    port = int(os.environ.get("BOT_MOSQUITTO_PORT", "1883"))
    client.connect(host, port, 60)
    client.subscribe(GRANT_TOPIC, qos=1)
    client.loop_start()

    publisher = GrantPublisher(
        host=host,
        port=port,
        node_id="node-local",
        ttl_sec=45,
        renew_sec=15,
    )
    await publisher.start()
    try:
        payload = await asyncio.wait_for(received.get(), timeout=5)
        grant = ActivationGrant.parse_wire_json(payload.decode("utf-8"))
        assert grant.mode is ActivationGrantMode.active
        assert grant.node_id == "node-local"
        assert grant.ttl_sec == 45
        assert grant.command_seq >= 1
        assert grant.issued_at.tzinfo is not None
        _ = datetime.now(UTC)
    finally:
        await publisher.stop()
        client.loop_stop()
        client.disconnect()
