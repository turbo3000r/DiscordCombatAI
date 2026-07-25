from __future__ import annotations

import pytest

from shared.runtime.discord import is_valid_snowflake
from shared.runtime.settings import (
    RuntimeMode,
    StorageProvider,
    clear_runtime_settings_cache,
    load_runtime_settings,
)


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> None:
    clear_runtime_settings_cache()
    yield
    clear_runtime_settings_cache()


def test_snowflake_validation() -> None:
    assert is_valid_snowflake("123456789012345678")
    assert not is_valid_snowflake("abc")
    assert not is_valid_snowflake("123")


def test_production_settings_derive_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "production")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    monkeypatch.delenv("DEV_SUPPORT_URL", raising=False)
    monkeypatch.delenv("DEV_COMPOSE_OVERLAY_ACTIVE", raising=False)
    settings = load_runtime_settings()
    assert settings.mode is RuntimeMode.production
    assert settings.storage_provider is StorageProvider.azure


def test_development_settings_derive_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "development")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_APPLICATION_ID", "987654321098765432")
    monkeypatch.setenv("DEV_SUPPORT_URL", "http://dev-support:8080")
    monkeypatch.setenv("DEV_COMPOSE_OVERLAY_ACTIVE", "true")
    settings = load_runtime_settings()
    assert settings.mode is RuntimeMode.development
    assert settings.storage_provider is StorageProvider.local
    assert settings.dev_compose_overlay_active is True


def test_missing_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCA_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "123456789012345678")
    with pytest.raises(SystemExit):
        load_runtime_settings()


def test_invalid_guild_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "production")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", "not-a-snowflake")
    with pytest.raises(SystemExit):
        load_runtime_settings()
