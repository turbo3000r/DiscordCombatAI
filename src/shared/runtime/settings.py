"""Runtime mode settings — fail-closed loader."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .discord import require_snowflake


class RuntimeMode(StrEnum):
    production = "production"
    development = "development"


class StorageProvider(StrEnum):
    azure = "azure"
    local = "local"


class RuntimeSettings(BaseSettings):
    """Shared process runtime mode. STORAGE_PROVIDER is derived, never free-form."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    dca_runtime_mode: RuntimeMode = Field(alias="DCA_RUNTIME_MODE")
    discord_development_guild_id: str = Field(alias="DISCORD_DEVELOPMENT_GUILD_ID")
    discord_development_application_id: str | None = Field(
        default=None, alias="DISCORD_DEVELOPMENT_APPLICATION_ID"
    )
    dev_support_url: str | None = Field(default=None, alias="DEV_SUPPORT_URL")
    dev_compose_overlay_active: bool = Field(default=False, alias="DEV_COMPOSE_OVERLAY_ACTIVE")
    web_local_admin_oid: str | None = Field(default=None, alias="WEB_LOCAL_ADMIN_OID")

    @field_validator("discord_development_guild_id")
    @classmethod
    def _guild_snowflake(cls, value: str) -> str:
        return require_snowflake(value, field_name="DISCORD_DEVELOPMENT_GUILD_ID")

    @field_validator("discord_development_application_id")
    @classmethod
    def _app_snowflake(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return require_snowflake(value, field_name="DISCORD_DEVELOPMENT_APPLICATION_ID")

    @field_validator("dev_support_url")
    @classmethod
    def _empty_url_to_none(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return value.rstrip("/")

    @field_validator("web_local_admin_oid")
    @classmethod
    def _empty_oid_to_none(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return value

    @property
    def mode(self) -> RuntimeMode:
        return self.dca_runtime_mode

    @property
    def storage_provider(self) -> StorageProvider:
        if self.mode is RuntimeMode.development:
            return StorageProvider.local
        return StorageProvider.azure

    @property
    def is_development(self) -> bool:
        return self.mode is RuntimeMode.development

    @property
    def is_production(self) -> bool:
        return self.mode is RuntimeMode.production


@lru_cache(maxsize=1)
def load_runtime_settings() -> RuntimeSettings:
    try:
        return RuntimeSettings()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001 — fail closed with clear SystemExit
        raise SystemExit(f"invalid runtime settings: {exc}") from exc


def clear_runtime_settings_cache() -> None:
    load_runtime_settings.cache_clear()


__all__ = [
    "RuntimeMode",
    "RuntimeSettings",
    "StorageProvider",
    "clear_runtime_settings_cache",
    "load_runtime_settings",
]
