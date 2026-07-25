"""Partial S14 spine acceptance — foundation isolation without commands/Web."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.modules.services.guild_guard import is_managed_guild
from dev_support.store import connect
from dev_support.store.repositories import DevStore
from shared.runtime.guards import RuntimeConfigurationError, assert_service_runtime
from shared.runtime.settings import RuntimeMode, clear_runtime_settings_cache, load_runtime_settings
from shared.storage.factory import build_repositories
from shared.storage.local_adapters import LocalGuildRepository

pytestmark = pytest.mark.acceptance

DEV_GUILD = "123456789012345678"
DEV_APP = "987654321098765432"


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> None:
    clear_runtime_settings_cache()
    yield
    clear_runtime_settings_cache()


def test_s14_spine_mode_guard_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "development")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", DEV_GUILD)
    monkeypatch.setenv("DISCORD_DEVELOPMENT_APPLICATION_ID", DEV_APP)
    monkeypatch.setenv("DEV_SUPPORT_URL", "http://dev-support:8080")
    monkeypatch.setenv("DEV_COMPOSE_OVERLAY_ACTIVE", "true")
    monkeypatch.setenv("BOT_AZURE_CLIENT_ID", "should-not-be-set")
    for key in (
        "BOT_AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_COSMOS_ENDPOINT",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_WEBPUBSUB_ENDPOINT",
    ):
        monkeypatch.setenv(key, "")
    runtime = load_runtime_settings()
    with pytest.raises(RuntimeConfigurationError):
        assert_service_runtime(runtime, "bot")


def test_s14_spine_local_provider_and_guild_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DCA_RUNTIME_MODE", "development")
    monkeypatch.setenv("DISCORD_DEVELOPMENT_GUILD_ID", DEV_GUILD)
    monkeypatch.setenv("DISCORD_DEVELOPMENT_APPLICATION_ID", DEV_APP)
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
    assert_service_runtime(runtime, "bot")
    repos = build_repositories("bot", runtime)
    assert isinstance(repos.guild, LocalGuildRepository)

    assert is_managed_guild(
        mode=RuntimeMode.development, guild_id=DEV_GUILD, development_guild_id=DEV_GUILD
    )
    assert not is_managed_guild(
        mode=RuntimeMode.development,
        guild_id="111111111111111111",
        development_guild_id=DEV_GUILD,
    )
    assert not is_managed_guild(
        mode=RuntimeMode.production, guild_id=DEV_GUILD, development_guild_id=DEV_GUILD
    )

    conn = connect(str(tmp_path / "s14.db"))
    store = DevStore(conn)
    store.ensure_status_seeded()
    store.ensure_active_guild(
        guild_id=DEV_GUILD,
        name="Dev",
        icon_url=None,
        member_count=1,
        owner_id="111111111111111111",
    )
    conn.close()
    conn2 = connect(str(tmp_path / "s14.db"))
    assert DevStore(conn2).get_guild(DEV_GUILD).name == "Dev"
    conn2.close()
