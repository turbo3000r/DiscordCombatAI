from __future__ import annotations

import pytest

from shared.runtime.settings import clear_runtime_settings_cache, load_runtime_settings
from shared.storage.factory import build_repositories
from shared.storage.local_adapters import LocalGuildRepository


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> None:
    clear_runtime_settings_cache()
    yield
    clear_runtime_settings_cache()


def test_development_factory_selects_local(monkeypatch: pytest.MonkeyPatch) -> None:
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
    runtime = load_runtime_settings()
    repos = build_repositories("bot", runtime)
    assert isinstance(repos.guild, LocalGuildRepository)


def test_development_factory_does_not_import_azure_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    runtime = load_runtime_settings()
    import sys

    before = {name for name in sys.modules if name.startswith("shared.storage.azure")}
    build_repositories("bot", runtime)
    after = {name for name in sys.modules if name.startswith("shared.storage.azure")}
    assert after == before
