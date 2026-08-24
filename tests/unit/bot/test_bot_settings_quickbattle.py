"""QUICKBATTLE_* settings aliases."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

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


def test_quickbattle_aliases(clear_node_id: None, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _base_env(
        QUICKBATTLE_MAX_PARTICIPANTS="8",
        QUICKBATTLE_AI_ADMISSION_TIMEOUT_SEC="45",
    ).items():
        monkeypatch.setenv(key, value)
    settings = BotSettings()  # type: ignore[call-arg]
    assert settings.quickbattle_max_participants == 8
    assert settings.quickbattle_ai_admission_timeout_sec == 45
