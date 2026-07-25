from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from ai_worker.settings import AiWorkerSettings


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "APPLICATION_VERSION": "v0.1.0",
        "AI_WORKER_NODE_ID": "node-local",
        "AI_WORKER_RABBITMQ_USER": "discordcombatai",
        "AI_WORKER_RABBITMQ_PASS": "change-me-in-env",
    }
    env.update(overrides)
    return env


@pytest.fixture()
def clear_node_id(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("NODE_ID", raising=False)
    yield


def test_ai_worker_settings_defaults(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)

    settings = AiWorkerSettings()  # type: ignore[call-arg]

    assert settings.node_id == "node-local"
    assert settings.application_version == "v0.1.0"
    assert settings.rabbitmq_host == "rabbitmq"
    assert settings.rabbitmq_port == 5672
    assert settings.mosquitto_host == "mosquitto"
    assert settings.mosquitto_port == 1883
    assert settings.celery_concurrency == 1
    assert settings.progress_heartbeat_sec == 30
    assert settings.heartbeat_interval_sec == 30
    assert settings.transport_shell is False
    assert settings.broker_url() == (
        "amqp://discordcombatai:change-me-in-env@rabbitmq:5672/%2Fdiscordcombatai"
    )
    assert "change-me-in-env" not in repr(settings)
    assert settings.model_dump()["rabbitmq_pass"] == "***"


def test_ai_worker_settings_reject_node_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env(AI_WORKER_NODE_ID="worker-a").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("NODE_ID", "node-local")
    with pytest.raises(ValidationError, match="must equal host NODE_ID"):
        AiWorkerSettings()  # type: ignore[call-arg]


def test_ai_worker_settings_reject_empty_credentials(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env(AI_WORKER_RABBITMQ_PASS="").items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        AiWorkerSettings()  # type: ignore[call-arg]


def test_ai_worker_transport_shell_can_be_enabled(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env(AI_WORKER_TRANSPORT_SHELL="true").items():
        monkeypatch.setenv(key, value)
    settings = AiWorkerSettings()  # type: ignore[call-arg]
    assert settings.transport_shell is True
