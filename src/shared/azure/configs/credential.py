from __future__ import annotations

from functools import cache
from typing import Any

from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource


class FallbackClientSecretCredential:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@cache
def _build_credential(tenant_id: str, client_id: str, client_secret: str) -> Any:
    credential: Any
    try:
        from azure.identity.aio import ClientSecretCredential

        credential = ClientSecretCredential(
            tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
        )
    except Exception:
        credential = FallbackClientSecretCredential(tenant_id, client_id, client_secret)
    register_resource(credential)
    return credential


def get_credential(service: AzureServiceName, settings: AzureSettings | None = None) -> Any:
    resolved = settings or load_azure_settings(service)
    if resolved.service != service:
        raise ValueError(
            f"settings service {resolved.service!r} does not match requested {service!r}"
        )
    client_id, client_secret = resolved.service_principal(service)
    return _build_credential(resolved.azure_tenant_id, client_id, client_secret)
