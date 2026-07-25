from __future__ import annotations

import pytest

from shared.runtime.guards import RuntimeConfigurationError, assert_service_runtime
from shared.runtime.settings import clear_runtime_settings_cache, load_runtime_settings


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> None:
    clear_runtime_settings_cache()
    yield
    clear_runtime_settings_cache()


def _prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "production")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    monkeypatch.delenv("DEV_SUPPORT_URL", raising=False)
    monkeypatch.delenv("DEV_COMPOSE_OVERLAY_ACTIVE", raising=False)
    monkeypatch.delenv("DISCORD_DEVELOPMENT_APPLICATION_ID", raising=False)
    monkeypatch.delenv("WEB_LOCAL_ADMIN_OID", raising=False)


def _dev(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_development_rejects_azure_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    _dev(monkeypatch)
    monkeypatch.setenv("BOT_AZURE_CLIENT_ID", "leak")
    runtime = load_runtime_settings()
    with pytest.raises(RuntimeConfigurationError):
        assert_service_runtime(runtime, "bot")


def test_development_requires_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    _dev(monkeypatch)
    monkeypatch.setenv("DEV_COMPOSE_OVERLAY_ACTIVE", "false")
    clear_runtime_settings_cache()
    runtime = load_runtime_settings()
    with pytest.raises(RuntimeConfigurationError):
        assert_service_runtime(runtime, "bot")


def test_production_rejects_dev_support_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _prod(monkeypatch)
    monkeypatch.setenv("DEV_SUPPORT_URL", "http://dev-support:8080")
    clear_runtime_settings_cache()
    runtime = load_runtime_settings()
    with pytest.raises(RuntimeConfigurationError):
        assert_service_runtime(runtime, "bot")


def test_development_bot_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _dev(monkeypatch)
    runtime = load_runtime_settings()
    assert_service_runtime(runtime, "bot")
