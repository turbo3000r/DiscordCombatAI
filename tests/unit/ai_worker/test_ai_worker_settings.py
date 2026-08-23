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
    assert (
        settings.environment_task_deadline_sec,
        settings.environment_max_input_tokens,
        settings.environment_max_output_tokens,
    ) == (600, 120_000, 30_000)
    assert (
        settings.battle_task_deadline_sec,
        settings.battle_max_input_tokens,
        settings.battle_max_output_tokens,
    ) == (840, 350_000, 90_000)
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


def test_graph_bounds_use_their_documented_environment_variables(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env(
        ENVIRONMENT_TASK_DEADLINE_SEC="601",
        ENVIRONMENT_MAX_INPUT_TOKENS="120001",
        ENVIRONMENT_MAX_OUTPUT_TOKENS="30001",
        BATTLE_TASK_DEADLINE_SEC="841",
        BATTLE_MAX_INPUT_TOKENS="350001",
        BATTLE_MAX_OUTPUT_TOKENS="90001",
    ).items():
        monkeypatch.setenv(key, value)

    settings = AiWorkerSettings()  # type: ignore[call-arg]

    assert settings.environment_task_deadline_sec == 601
    assert settings.environment_max_input_tokens == 120_001
    assert settings.environment_max_output_tokens == 30_001
    assert settings.battle_task_deadline_sec == 841
    assert settings.battle_max_input_tokens == 350_001
    assert settings.battle_max_output_tokens == 90_001


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENVIRONMENT_TASK_DEADLINE_SEC", "0"),
        ("ENVIRONMENT_MAX_INPUT_TOKENS", "0"),
        ("ENVIRONMENT_MAX_OUTPUT_TOKENS", "0"),
        ("BATTLE_TASK_DEADLINE_SEC", "0"),
        ("BATTLE_MAX_INPUT_TOKENS", "0"),
        ("BATTLE_MAX_OUTPUT_TOKENS", "0"),
    ],
)
def test_graph_bounds_reject_non_positive_values(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    for key, setting in _base_env(**{name: value}).items():
        monkeypatch.setenv(key, setting)
    with pytest.raises(ValidationError):
        AiWorkerSettings()  # type: ignore[call-arg]
