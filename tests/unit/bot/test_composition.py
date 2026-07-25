from __future__ import annotations

import pytest

from bot.composition import build_bot_application
from shared.runtime.guards import RuntimeConfigurationError
from shared.runtime.settings import RuntimeMode, clear_runtime_settings_cache


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> None:
    clear_runtime_settings_cache()
    yield
    clear_runtime_settings_cache()


def _bot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NODE_ID", raising=False)
    monkeypatch.setenv("APPLICATION_VERSION", "v0.1.0")
    monkeypatch.setenv("BOT_NODE_ID", "node-local")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-token-value")
    monkeypatch.setenv("BOT_RABBITMQ_USER", "discordcombatai")
    monkeypatch.setenv("BOT_RABBITMQ_PASS", "change-me-in-env")


def test_development_composition_refuses_azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _bot_env(monkeypatch)
    monkeypatch.setenv("DCA_RUNTIME_MODE", "development")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_APPLICATION_ID", "987654321098765432")
    monkeypatch.setenv("DEV_SUPPORT_URL", "http://dev-support:8080")
    monkeypatch.setenv("DEV_COMPOSE_OVERLAY_ACTIVE", "true")
    monkeypatch.setenv("BOT_AZURE_CLIENT_ID", "leak")
    for key in (
        "BOT_AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_COSMOS_ENDPOINT",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_WEBPUBSUB_ENDPOINT",
    ):
        monkeypatch.setenv(key, "")
    with pytest.raises(RuntimeConfigurationError):
        build_bot_application(enable_mqtt=False, enable_transport=False)


def test_development_composition_wires_local_and_suppresses_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bot_env(monkeypatch)
    monkeypatch.setenv("DCA_RUNTIME_MODE", "development")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_APPLICATION_ID", "987654321098765432")
    monkeypatch.setenv("DEV_SUPPORT_URL", "http://dev-support:8080")
    monkeypatch.setenv("DEV_COMPOSE_OVERLAY_ACTIVE", "true")
    for key in (
        "BOT_AZURE_CLIENT_ID",
        "BOT_AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_COSMOS_ENDPOINT",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_WEBPUBSUB_ENDPOINT",
    ):
        monkeypatch.setenv(key, "")
    app = build_bot_application(enable_mqtt=False, enable_transport=False)
    assert app.runtime_mode is RuntimeMode.development
    assert app.enable_suggestion_queue is False
    assert app.development_guild_id == "123456789012345678"
    assert app.expected_application_id == "987654321098765432"
    assert app._guild_service is not None
    assert app._status_service is not None
