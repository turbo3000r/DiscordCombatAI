"""Phase 2 integration fixtures — local brokers when Docker Compose is up."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Final
from urllib.parse import quote

import pytest

# Ensure AI Worker settings can load when tests import celery_app/tasks.
os.environ.setdefault("APPLICATION_VERSION", "v0.1.0")
os.environ.setdefault("AI_WORKER_NODE_ID", "node-local")
os.environ.setdefault("AI_WORKER_RABBITMQ_USER", "discordcombatai")
os.environ.setdefault("AI_WORKER_RABBITMQ_PASS", "change-me-in-env")
os.environ.setdefault("AI_WORKER_TRANSPORT_SHELL", "false")

ROOT: Final = Path(__file__).resolve().parents[3]
COMPOSE_FILES: Final = [
    str(ROOT / "docker-compose.yml"),
    str(ROOT / "docker-compose.dev.yml"),
]
RABBIT_USER: Final = os.getenv("RABBITMQ_DEFAULT_USER", "discordcombatai")
RABBIT_PASS: Final = os.getenv("RABBITMQ_DEFAULT_PASS", "change-me-in-env")
RABBIT_VHOST: Final = os.getenv("RABBITMQ_DEFAULT_VHOST", "/discordcombatai")


def docker_compose_args() -> list[str]:
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


def wait_for_tcp(host: str, port: int, timeout_sec: int = 60) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise AssertionError(f"{host}:{port} did not become reachable")


def broker_url() -> str:
    return (
        f"amqp://{RABBIT_USER}:{RABBIT_PASS}@127.0.0.1:5672/"
        f"{quote(RABBIT_VHOST, safe='')}"
    )


@pytest.fixture(scope="module")
def phase2_brokers() -> Iterator[None]:
    args = docker_compose_args()
    result = subprocess.run(
        [*args, "up", "-d", "--wait", "--wait-timeout", "120", "mosquitto", "rabbitmq"],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"unable to start Phase 2 brokers: {result.stderr[-500:]}")
    try:
        wait_for_tcp("127.0.0.1", 5672, timeout_sec=30)
        wait_for_tcp("127.0.0.1", 1883, timeout_sec=30)
    except AssertionError:
        pytest.skip("Phase 2 brokers not reachable")
    yield
