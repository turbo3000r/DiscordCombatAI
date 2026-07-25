"""Phase 3 integration helpers — packaging and auth isolation smoke checks."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_phase3_web_absent_from_base_compose() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "web" not in compose["services"]


def test_phase3_dev_web_is_loopback_only() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    assert web["ports"] == ["127.0.0.1:8088:8080"]
    assert web["environment"]["DCA_RUNTIME_MODE"] == "development"
