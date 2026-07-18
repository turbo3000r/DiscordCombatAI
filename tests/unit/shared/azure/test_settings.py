from __future__ import annotations

import os

from shared.azure.configs.settings import clear_azure_settings_cache, load_azure_settings


def test_settings_derives_endpoints(azure_settings) -> None:
    assert azure_settings.blob_endpoint == "https://storageacct.blob.core.windows.net"
    assert azure_settings.queue_endpoint == "https://storageacct.queue.core.windows.net"
    assert azure_settings.table_endpoint == "https://storageacct.table.core.windows.net"
    assert azure_settings.service_principal("head") == ("head-client-id", "head-client-secret")


def test_load_azure_settings_only_requires_calling_service_secret(monkeypatch) -> None:
    clear_azure_settings_cache()
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv("BOT_AZURE_CLIENT_ID", "bot-client-id")
    monkeypatch.setenv("BOT_AZURE_CLIENT_SECRET", "bot-client-secret")
    monkeypatch.setenv("AZURE_COSMOS_ENDPOINT", "https://cosmos.example.com")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "storageacct")
    monkeypatch.setenv("AZURE_WEBPUBSUB_ENDPOINT", "https://pubsub.example.com")
    for key in (
        "HEAD_AZURE_CLIENT_ID",
        "HEAD_AZURE_CLIENT_SECRET",
        "WEB_AZURE_CLIENT_ID",
        "WEB_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_azure_settings("bot")
    assert settings.service == "bot"
    assert settings.service_principal() == ("bot-client-id", "bot-client-secret")
    assert "HEAD_AZURE_CLIENT_SECRET" not in os.environ
