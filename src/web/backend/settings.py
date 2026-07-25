"""Web backend settings."""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.runtime.settings import RuntimeMode, RuntimeSettings, load_runtime_settings


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    web_entra_tenant_id: str | None = Field(default=None, alias="WEB_ENTRA_TENANT_ID")
    web_entra_client_id: str | None = Field(default=None, alias="WEB_ENTRA_CLIENT_ID")
    web_entra_api_audience: str | None = Field(default=None, alias="WEB_ENTRA_API_AUDIENCE")
    web_entra_admin_group_id: str | None = Field(default=None, alias="WEB_ENTRA_ADMIN_GROUP_ID")
    web_entra_authority: str | None = Field(default=None, alias="WEB_ENTRA_AUTHORITY")
    web_local_admin_oid: str | None = Field(default=None, alias="WEB_LOCAL_ADMIN_OID")

    @model_validator(mode="after")
    def _validate_mode_bounds(self) -> WebSettings:
        return self


def load_web_settings(runtime: RuntimeSettings | None = None) -> WebSettings:
    runtime = runtime or load_runtime_settings()
    settings = WebSettings()
    if runtime.mode is RuntimeMode.production:
        missing = [
            name
            for name, value in {
                "WEB_ENTRA_TENANT_ID": settings.web_entra_tenant_id,
                "WEB_ENTRA_CLIENT_ID": settings.web_entra_client_id,
                "WEB_ENTRA_API_AUDIENCE": settings.web_entra_api_audience,
                "WEB_ENTRA_ADMIN_GROUP_ID": settings.web_entra_admin_group_id,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"production Web missing required Entra settings: {missing}")
        if settings.web_local_admin_oid:
            raise RuntimeError("WEB_LOCAL_ADMIN_OID is forbidden in production")
    else:
        if not settings.web_local_admin_oid and not runtime.web_local_admin_oid:
            raise RuntimeError("WEB_LOCAL_ADMIN_OID is required in development")
        if not settings.web_local_admin_oid:
            settings.web_local_admin_oid = runtime.web_local_admin_oid
    return settings


__all__ = ["WebSettings", "load_web_settings"]
