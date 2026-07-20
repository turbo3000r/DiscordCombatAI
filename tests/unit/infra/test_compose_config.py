from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load_compose(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_base_compose_has_expected_broker_topology() -> None:
    compose = _load_compose(ROOT / "docker-compose.yml")
    services = compose["services"]  # type: ignore[index]

    network = compose["networks"]["dca-internal"]  # type: ignore[index]
    assert network is None or network.get("internal") is not True  # type: ignore[union-attr]
    assert "rabbitmq-data" in compose["volumes"]  # type: ignore[index]

    mosquitto = services["mosquitto"]  # type: ignore[index]
    rabbitmq = services["rabbitmq"]  # type: ignore[index]
    head = services["head"]  # type: ignore[index]
    bot = services["bot"]  # type: ignore[index]
    ai_worker = services["ai_worker"]  # type: ignore[index]

    assert "ports" not in mosquitto
    assert "ports" not in rabbitmq
    assert mosquitto["healthcheck"]  # type: ignore[index]
    assert rabbitmq["healthcheck"]  # type: ignore[index]
    assert rabbitmq["volumes"][0].startswith("rabbitmq-data:")  # type: ignore[index]

    assert "profiles" not in head
    for service in (bot, ai_worker):
        assert service["profiles"] == ["application"]  # type: ignore[index]
    for service in (head, bot, ai_worker):
        assert service["restart"] == "unless-stopped"  # type: ignore[index]
        assert "APPLICATION_VERSION" in service["environment"]  # type: ignore[index]

    assert head["extra_hosts"] == ["host.docker.internal:host-gateway"]  # type: ignore[index]
    assert head["ports"] == ["127.0.0.1:9800:9800"]  # type: ignore[index]
    assert any(
        str(mount).endswith(":/run/secrets/launcher_ipc_secret:ro")
        for mount in head["volumes"]  # type: ignore[index]
    )
    assert "condition" in head["depends_on"]["mosquitto"]  # type: ignore[index]
    assert "condition" in head["depends_on"]["rabbitmq"]  # type: ignore[index]


def test_dev_compose_adds_local_ports_and_source_mounts() -> None:
    compose = _load_compose(ROOT / "docker-compose.dev.yml")
    services = compose["services"]  # type: ignore[index]

    assert services["mosquitto"]["ports"] == ["127.0.0.1:1883:1883"]  # type: ignore[index]
    assert services["rabbitmq"]["ports"] == [  # type: ignore[index]
        "127.0.0.1:5672:5672",
        "127.0.0.1:15672:15672",
    ]

    for service_name in ("head", "bot", "ai_worker"):
        mounts = services[service_name]["volumes"]  # type: ignore[index]
        assert "./src:/app/src" in mounts

    assert "./prompts:/app/prompts:ro" in services["ai_worker"]["volumes"]  # type: ignore[index]
