from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MOSQUITTO_CONF = ROOT / "infra" / "mosquitto" / "mosquitto.conf"


def test_mosquitto_config_contains_required_directives() -> None:
    lines = [
        line.strip()
        for line in MOSQUITTO_CONF.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert lines == [
        "listener 1883",
        "allow_anonymous true",
        "persistence false",
    ]
