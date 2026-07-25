from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from bot.settings import BotSettings


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "APPLICATION_VERSION": "v0.1.0",
        "BOT_NODE_ID": "node-local",
        "DISCORD_BOT_TOKEN": "discord-token-value",
        "BOT_RABBITMQ_USER": "discordcombatai",
        "BOT_RABBITMQ_PASS": "change-me-in-env",
    }
    env.update(overrides)
    return env


@pytest.fixture()
def clear_node_id(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("NODE_ID", raising=False)
    yield


def test_bot_settings_defaults(clear_node_id: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)

    settings = BotSettings()  # type: ignore[call-arg]

    assert settings.node_id == "node-local"
    assert settings.application_version == "v0.1.0"
    assert settings.mosquitto_host == "mosquitto"
    assert settings.mosquitto_port == 1883
    assert settings.rabbitmq_host == "rabbitmq"
    assert settings.rabbitmq_port == 5672
    assert settings.rabbitmq_vhost == "/discordcombatai"
    assert settings.drain_progress_interval_sec == 5
    assert settings.ai_task_stall_timeout_sec == 120
    assert settings.ai_task_timeout_sec == 900
    assert settings.guild_sync_interval_sec == 3600
    assert settings.heartbeat_interval_sec == 30
    assert settings.status_push_interval_sec == 60
    assert settings.shutdown_grace_sec == 30
    assert settings.control_drain_timeout_sec == 45
    assert settings.activation_grant_max_ttl_sec == 60
    assert settings.broker_url().endswith("/%2Fdiscordcombatai")
    assert "change-me-in-env" in settings.broker_url()
    assert "discord-token-value" not in repr(settings)
    assert settings.model_dump()["discord_bot_token"] == "***"
    assert settings.model_dump()["rabbitmq_pass"] == "***"


def test_bot_settings_reject_invalid_version(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env(APPLICATION_VERSION="0.1.0").items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        BotSettings()  # type: ignore[call-arg]


def test_bot_settings_reject_node_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env(BOT_NODE_ID="node-a").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("NODE_ID", "node-b")
    with pytest.raises(ValidationError, match="must equal host NODE_ID"):
        BotSettings()  # type: ignore[call-arg]


def test_bot_settings_reject_empty_token(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env(DISCORD_BOT_TOKEN="").items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        BotSettings()  # type: ignore[call-arg]


def test_bot_settings_accept_matching_host_node_id(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("NODE_ID", "node-local")
    settings = BotSettings()  # type: ignore[call-arg]
    assert settings.node_id == "node-local"
