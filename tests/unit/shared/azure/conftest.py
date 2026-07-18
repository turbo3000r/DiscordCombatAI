from __future__ import annotations

import pytest

from shared.azure.configs.settings import AzureSettings


@pytest.fixture()
def azure_settings() -> AzureSettings:
    return AzureSettings.model_validate(
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


@pytest.fixture()
def fake_credential() -> object:
    return object()
