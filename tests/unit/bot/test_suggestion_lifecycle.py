"""Lifecycle wiring for suggestion queue delivery."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.application import BotApplication
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


class StubBot:
    def __init__(self) -> None:
        self.guilds: list[Any] = []
        self.closed = False

    def is_ready(self) -> bool:
        return True

    async def start(self, token: str) -> None:
        await AsyncMock()()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_development_suppresses_suggestion_queue(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)
    settings = BotSettings()  # type: ignore[call-arg]
    app = BotApplication(
        settings,
        bot_factory=StubBot,
        runtime_mode="development",
        enable_suggestion_queue=True,
        enable_mqtt=False,
        enable_transport=False,
    )
    assert app.enable_suggestion_queue is False
    await app.authorize_gateway()
    assert app.suggestion_queue_poller is None
    assert app.suggestion_sweep is None
    await app.revoke_gateway()


@pytest.mark.asyncio
async def test_production_starts_and_stops_suggestion_delivery(
    clear_node_id: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _base_env().items():
        monkeypatch.setenv(key, value)
    settings = BotSettings()  # type: ignore[call-arg]

    class FakeQueue:
        async def receive_messages(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

        async def delete_message(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            return None

    class FakeSuggestionRepo:
        async def get(self, guild_id: str, suggestion_id: str) -> Any:
            raise AssertionError("should not load")

        async def claim_pending(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def mark_sent(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("unused")

        async def mark_failed(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("unused")

        async def list_pending_for_sweep(self, **kwargs: Any) -> list[Any]:
            return []

        async def list_expired_claims(self, **kwargs: Any) -> list[Any]:
            return []

        async def reset_expired_claim(self, *args: Any, **kwargs: Any) -> None:
            return None

    class FakeL10n:
        def t(self, key: str, *, locale: str | None = None, **variables: Any) -> str:
            return key

    app = BotApplication(
        settings,
        bot_factory=StubBot,
        suggestion_service=FakeSuggestionRepo(),  # type: ignore[arg-type]
        l10n=FakeL10n(),  # type: ignore[arg-type]
        runtime_mode="production",
        enable_suggestion_queue=True,
        suggestion_queue_client=FakeQueue(),
        suggestion_queue_name="suggestions",
        enable_mqtt=False,
        enable_transport=False,
    )
    await app.authorize_gateway()
    assert app.accepting_suggestion_claims is True
    assert app.suggestion_queue_poller is not None
    assert app.suggestion_sweep is not None
    await app.lifecycle.on_soft_stop()
    assert app.accepting_suggestion_claims is False
    await app.lifecycle.on_hard_stop()
    await app.revoke_gateway()
