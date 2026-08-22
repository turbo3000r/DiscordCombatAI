"""Integration: GrantPublisher wire shape and MQTT publish against Compose brokers."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from shared.messaging.mqtt_topics import MQTT_TOPIC_POLICIES
from shared.models import ActivationGrant, ActivationGrantMode

pytestmark = pytest.mark.integration

GRANT_TOPIC = MQTT_TOPIC_POLICIES["control_bot_activation_grant"].topic
ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [
    str(ROOT / "docker-compose.yml"),
    str(ROOT / "docker-compose.dev.yml"),
]


def _docker_compose_args() -> list[str]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker unavailable on this machine")
    result = subprocess.run(
        [docker, "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("Docker Compose unavailable on this machine")
    return [docker, "compose", *sum((["-f", file_path] for file_path in COMPOSE_FILES), [])]


@pytest.fixture(scope="module")
def mosquitto_broker() -> Iterator[None]:
    args = _docker_compose_args()
    result = subprocess.run(
        [*args, "up", "-d", "--wait", "--wait-timeout", "120", "mosquitto"],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"unable to start Mosquitto: {result.stderr[-500:]}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 1883), timeout=1):
                break
        except OSError:
            time.sleep(1)
    else:
        pytest.skip("Mosquitto not reachable on loopback")
    yield


@pytest.mark.asyncio
async def test_grant_publisher_emits_valid_activation_grant(
    mosquitto_broker: None,
) -> None:
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
