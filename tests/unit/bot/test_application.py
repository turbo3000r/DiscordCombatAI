from __future__ import annotations

from typing import Any

import pytest

from bot.application import BotApplication
from bot.settings import BotSettings


def _settings(monkeypatch: pytest.MonkeyPatch) -> BotSettings:
    monkeypatch.delenv("NODE_ID", raising=False)
    monkeypatch.setenv("APPLICATION_VERSION", "v0.1.0")
    monkeypatch.setenv("BOT_NODE_ID", "node-local")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token-value")
    monkeypatch.setenv("BOT_RABBITMQ_USER", "discordcombatai")
    monkeypatch.setenv("BOT_RABBITMQ_PASS", "change-me-in-env")
    return BotSettings()  # type: ignore[call-arg]


class FakeBot:
    def __init__(self) -> None:
        self.guilds: list[Any] = []
        self.started = False
        self.closed = False
        self._ready = False
        self.event_handlers: dict[str, Any] = {}

    def event(self, coro: Any) -> Any:
        self.event_handlers[coro.__name__] = coro
        return coro

    async def start(self, token: str) -> None:
        assert token == "discord-token-value"
        self.started = True
        self._ready = True
        await __import__("asyncio").Event().wait()

    async def close(self) -> None:
        self.closed = True
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready


@pytest.mark.asyncio
async def test_cold_start_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    created: list[FakeBot] = []

    def factory() -> FakeBot:
        bot = FakeBot()
        created.append(bot)
        return bot  # type: ignore[return-value]

    app = BotApplication(settings, bot_factory=factory)  # type: ignore[arg-type]
    await app.start()
    assert app.started is True
    assert app.gateway_authorized is False
    assert created == []
    assert "discord-token-value" not in repr(settings)


@pytest.mark.asyncio
async def test_fresh_client_per_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    created: list[FakeBot] = []

    def factory() -> FakeBot:
        bot = FakeBot()
        created.append(bot)
        return bot  # type: ignore[return-value]

    app = BotApplication(settings, bot_factory=factory)  # type: ignore[arg-type]
    await app.start()
    await app.authorize_gateway()
    await app.revoke_gateway()
    await app.authorize_gateway()
    await app.close()
    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True
