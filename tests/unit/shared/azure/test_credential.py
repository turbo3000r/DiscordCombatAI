from __future__ import annotations

import sys
import types

from shared.azure.configs.credential import _build_credential, get_credential
from shared.azure.configs.settings import AzureSettings


class _FakeCredential:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_credential_is_cached(monkeypatch) -> None:
    module = types.ModuleType("azure.identity.aio")
    module.ClientSecretCredential = _FakeCredential
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", types.ModuleType("azure.identity"))
    monkeypatch.setitem(sys.modules, "azure.identity.aio", module)
    _build_credential.cache_clear()

    settings = AzureSettings.model_validate(
        {
            "service": "head",
            "AZURE_TENANT_ID": "tenant-id",
            "azure_client_id": "head-client-id",
            "azure_client_secret": "head-client-secret",
            "AZURE_COSMOS_ENDPOINT": "https://cosmos.example.com",
            "AZURE_STORAGE_ACCOUNT_NAME": "storageacct",
            "AZURE_WEBPUBSUB_ENDPOINT": "https://pubsub.example.com",
        }
    )

    first = get_credential("head", settings)
    second = get_credential("head", settings)
    assert first is second
    assert first.client_id == "head-client-id"
