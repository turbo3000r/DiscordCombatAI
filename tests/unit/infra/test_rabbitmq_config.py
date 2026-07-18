from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RABBITMQ_DIR = ROOT / "infra" / "rabbitmq"


def test_enabled_plugins_enables_required_plugins() -> None:
    content = (RABBITMQ_DIR / "enabled_plugins").read_text(encoding="utf-8").strip()

    assert content == "[rabbitmq_event_exchange,rabbitmq_management]."


def test_rabbitmq_conf_loads_definitions_and_management_ui() -> None:
    content = (RABBITMQ_DIR / "rabbitmq.conf").read_text(encoding="utf-8")

    assert "management.tcp.ip = 0.0.0.0" in content
    assert "management.tcp.port = 15672" in content
    assert "management.load_definitions = /etc/rabbitmq/definitions.json" in content


def test_definitions_define_vhost_topology_without_secrets() -> None:
    raw = (RABBITMQ_DIR / "definitions.json").read_text(encoding="utf-8")
    definitions = json.loads(raw)

    assert "/discordcombatai" in {vhost["name"] for vhost in definitions["vhosts"]}
    assert {exchange["name"] for exchange in definitions["exchanges"]} == {
        "ai_tasks",
        "ai_tasks_results",
        "dlx",
    }
    assert {queue["name"] for queue in definitions["queues"]} == {
        "ai_tasks",
        "ai_tasks_results",
        "dead_letter",
    }

    ai_tasks = next(queue for queue in definitions["queues"] if queue["name"] == "ai_tasks")
    ai_tasks_results = next(
        queue for queue in definitions["queues"] if queue["name"] == "ai_tasks_results"
    )
    dead_letter = next(queue for queue in definitions["queues"] if queue["name"] == "dead_letter")

    assert ai_tasks["durable"] is True
    assert ai_tasks_results["durable"] is True
    assert dead_letter["durable"] is True
    assert ai_tasks["arguments"]["x-dead-letter-exchange"] == "dlx"
    assert ai_tasks_results["arguments"]["x-dead-letter-exchange"] == "dlx"
    assert dead_letter["arguments"] == {}

    bindings = definitions["bindings"]
    assert len(bindings) == 3
    assert any(
        binding["source"] == "dlx" and binding["destination"] == "dead_letter"
        for binding in bindings
    )
    assert "password" not in raw.lower()
    assert "password_hash" not in raw.lower()
    assert "users" not in definitions
