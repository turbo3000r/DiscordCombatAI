from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from typing import Final

import pytest
import yaml

pytestmark = pytest.mark.integration

ROOT: Final = Path(__file__).resolve().parents[3]
COMPOSE_FILES: Final = [
    str(ROOT / "docker-compose.yml"),
    str(ROOT / "docker-compose.dev.yml"),
]
RABBIT_USER: Final = os.getenv("RABBITMQ_DEFAULT_USER", "discordcombatai")
RABBIT_PASS: Final = os.getenv("RABBITMQ_DEFAULT_PASS", "change-me-in-env")
RABBIT_VHOST: Final = os.getenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")


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


def _wait_for_tcp(host: str, port: int, timeout_sec: int = 60) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise AssertionError(f"{host}:{port} did not become reachable")


def _http_json(url: str) -> dict[str, object]:
    token = base64.b64encode(f"{RABBIT_USER}:{RABBIT_PASS}".encode()).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_http_json(url: str, timeout_sec: int = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _http_json(url)
        except (
            OSError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"{url} did not become ready: {last_error}")


@pytest.fixture(scope="module")
def broker_stack() -> Iterator[None]:
    args = _docker_compose_args()
    # --wait blocks until Compose healthchecks pass (TCP alone is not enough).
    subprocess.run(
        args + ["up", "-d", "--wait", "--wait-timeout", "120", "mosquitto", "rabbitmq"],
        check=True,
        cwd=ROOT,
    )
    try:
        _wait_for_tcp("127.0.0.1", 1883)
        _wait_for_tcp("127.0.0.1", 15672)
        _wait_for_http_json("http://127.0.0.1:15672/api/overview")
        yield
    finally:
        subprocess.run(args + ["down", "-v", "--remove-orphans"], check=False, cwd=ROOT)


def test_compose_cli_accepts_phase_zero_files() -> None:
    args = _docker_compose_args()
    result = subprocess.run(
        args + ["config"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert "eclipse-mosquitto:2.0.20" in result.stdout
    assert "rabbitmq:3.13-management" in result.stdout

    # `docker compose config` resolves the active project; it does not echo raw
    # `profiles:` keys. Default resolution must include brokers/head and omit
    # application-profile services until --profile application is set.
    services = yaml.safe_load(result.stdout)["services"]
    assert {"mosquitto", "rabbitmq", "head"} <= set(services)
    assert "bot" not in services
    assert "ai_worker" not in services

    profiled = subprocess.run(
        args + ["--profile", "application", "config"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    profiled_services = yaml.safe_load(profiled.stdout)["services"]
    assert "bot" in profiled_services
    assert "ai_worker" in profiled_services


def test_mosquitto_round_trip_publish_subscribe(broker_stack: None) -> None:
    mqtt = pytest.importorskip("paho.mqtt.client")

    topic = f"discordcombatai/test/{int(time.time() * 1000)}"
    payload = "phase0-broker-smoke"
    connected = Event()
    received = Event()
    received_payload: list[str] = []

    client = mqtt.Client()

    def on_connect(
        _client: object, _userdata: object, _flags: object, _reason_code: object
    ) -> None:
        connected.set()
        client.subscribe(topic)

    def on_message(_client: object, _userdata: object, message: object) -> None:
        received_payload.append(message.payload.decode("utf-8"))
        received.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("127.0.0.1", 1883, keepalive=30)
    client.loop_start()
    try:
        assert connected.wait(timeout=10) is True
        publish_result = client.publish(topic, payload=payload, qos=0, retain=False)
        assert publish_result.rc == mqtt.MQTT_ERR_SUCCESS
        assert received.wait(timeout=10) is True
        assert received_payload == [payload]
    finally:
        client.loop_stop()
        client.disconnect()


def test_rabbitmq_definitions_loaded_via_management_api(broker_stack: None) -> None:
    vhost = urllib.parse.quote(RABBIT_VHOST, safe="")
    tasks_queue = _http_json(f"http://127.0.0.1:15672/api/queues/{vhost}/ai_tasks")
    results_queue = _http_json(f"http://127.0.0.1:15672/api/queues/{vhost}/ai_tasks_results")
    dead_letter_queue = _http_json(f"http://127.0.0.1:15672/api/queues/{vhost}/dead_letter")
    dlx_exchange = _http_json(f"http://127.0.0.1:15672/api/exchanges/{vhost}/dlx")
    plugins = _http_json("http://127.0.0.1:15672/api/nodes")

    assert tasks_queue["durable"] is True
    assert results_queue["durable"] is True
    assert dead_letter_queue["durable"] is True
    assert tasks_queue["arguments"]["x-dead-letter-exchange"] == "dlx"  # type: ignore[index]
    assert results_queue["arguments"]["x-dead-letter-exchange"] == "dlx"  # type: ignore[index]
    assert dlx_exchange["type"] == "topic"

    overview = _http_json("http://127.0.0.1:15672/api/overview")
    enabled = overview.get("enable_queue_totals")  # presence check that API is alive
    assert enabled is not None or "rabbitmq_version" in overview
    assert isinstance(plugins, list)
    # Event exchange must be enabled for Head's bridge; management API lists enabled plugins
    # on each node under `enabled_plugins` when available.
    if plugins and isinstance(plugins[0], dict) and "enabled_plugins" in plugins[0]:
        assert "rabbitmq_event_exchange" in plugins[0]["enabled_plugins"]
