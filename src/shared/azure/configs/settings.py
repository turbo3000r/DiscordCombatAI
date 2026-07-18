from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AzureServiceName = Literal["head", "bot", "web"]

_SERVICE_ALIASES: dict[AzureServiceName, tuple[str, str]] = {
    "head": ("HEAD_AZURE_CLIENT_ID", "HEAD_AZURE_CLIENT_SECRET"),
    "bot": ("BOT_AZURE_CLIENT_ID", "BOT_AZURE_CLIENT_SECRET"),
    "web": ("WEB_AZURE_CLIENT_ID", "WEB_AZURE_CLIENT_SECRET"),
}


class AzureSettings(BaseSettings):
    """Per-process Azure settings: only the calling service's SP secrets are required."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    service: AzureServiceName
    azure_tenant_id: str = Field(alias="AZURE_TENANT_ID")
    azure_client_id: str
    azure_client_secret: str

    azure_cosmos_endpoint: str = Field(alias="AZURE_COSMOS_ENDPOINT")
    azure_cosmos_database: str = Field(default="DiscordCombatAI", alias="AZURE_COSMOS_DATABASE")
    azure_storage_account_name: str = Field(alias="AZURE_STORAGE_ACCOUNT_NAME")
    azure_queue_name: str = Field(default="suggestions", alias="AZURE_QUEUE_NAME")
    azure_metrics_table: str = Field(default="NodeMetrics", alias="AZURE_METRICS_TABLE")
    azure_status_blob_container: str = Field(
        default="coordination", alias="AZURE_STATUS_BLOB_CONTAINER"
    )
    azure_status_blob_name: str = Field(default="bot_status.json", alias="AZURE_STATUS_BLOB_NAME")
    azure_battle_archive_container: str = Field(
        default="battle-results", alias="AZURE_BATTLE_ARCHIVE_CONTAINER"
    )
    azure_log_archive_container: str = Field(
        default="service-logs", alias="AZURE_LOG_ARCHIVE_CONTAINER"
    )
    azure_webpubsub_endpoint: str = Field(alias="AZURE_WEBPUBSUB_ENDPOINT")
    azure_webpubsub_hub_name: str = Field(
        default="discordcombatai", alias="AZURE_WEBPUBSUB_HUB_NAME"
    )

    @property
    def blob_endpoint(self) -> str:
        return f"https://{self.azure_storage_account_name}.blob.core.windows.net"

    @property
    def queue_endpoint(self) -> str:
        return f"https://{self.azure_storage_account_name}.queue.core.windows.net"

    @property
    def table_endpoint(self) -> str:
        return f"https://{self.azure_storage_account_name}.table.core.windows.net"

    def service_principal(self, service: AzureServiceName | None = None) -> tuple[str, str]:
        requested = service or self.service
        if requested != self.service:
            raise ValueError(
                f"settings were loaded for {self.service!r}; cannot use principal for {requested!r}"
            )
        return self.azure_client_id, self.azure_client_secret


@lru_cache(maxsize=3)
def load_azure_settings(service: AzureServiceName) -> AzureSettings:
    import os

    client_id_key, client_secret_key = _SERVICE_ALIASES[service]
    return AzureSettings(  # type: ignore[call-arg]
        service=service,
        azure_client_id=os.environ[client_id_key],
        azure_client_secret=os.environ[client_secret_key],
    )


def clear_azure_settings_cache() -> None:
    load_azure_settings.cache_clear()
