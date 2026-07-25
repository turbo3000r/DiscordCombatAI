"""Fail-closed runtime configuration errors and service-aware checks."""

from __future__ import annotations

import os
from typing import Literal

from .settings import RuntimeMode, RuntimeSettings

ServiceName = Literal["bot", "web", "dev_support"]


class RuntimeConfigurationError(SystemExit):
    """Raised/used as SystemExit for fail-closed startup refusal."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_BOT_AZURE_MARKERS = (
    "BOT_AZURE_CLIENT_ID",
    "BOT_AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "AZURE_COSMOS_ENDPOINT",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_WEBPUBSUB_ENDPOINT",
)

_WEB_AZURE_MARKERS = (
    "WEB_AZURE_CLIENT_ID",
    "WEB_AZURE_CLIENT_SECRET",
    "AZURE_TENANT_ID",
    "AZURE_COSMOS_ENDPOINT",
    "AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_WEBPUBSUB_ENDPOINT",
    "WEB_ENTRA_TENANT_ID",
    "WEB_ENTRA_CLIENT_ID",
    "WEB_ENTRA_API_AUDIENCE",
    "WEB_ENTRA_ADMIN_GROUP_ID",
)


def _nonempty(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _azure_markers_for(service: ServiceName) -> tuple[str, ...]:
    if service == "bot":
        return _BOT_AZURE_MARKERS
    if service == "web":
        return _WEB_AZURE_MARKERS
    return ()


def assert_service_runtime(runtime: RuntimeSettings, service: ServiceName) -> None:
    """Raise RuntimeConfigurationError if mode/service combination is unsafe."""
    if runtime.mode is RuntimeMode.development:
        if service in {"bot", "web"} and not runtime.dev_support_url:
            raise RuntimeConfigurationError(
                f"{service}: DEV_SUPPORT_URL is required when DCA_RUNTIME_MODE=development"
            )
        if service in {"bot", "web", "dev_support"} and not runtime.dev_compose_overlay_active:
            raise RuntimeConfigurationError(
                f"{service}: DEV_COMPOSE_OVERLAY_ACTIVE=true is required in development"
            )
        if service == "bot" and not runtime.discord_development_application_id:
            raise RuntimeConfigurationError(
                "bot: DISCORD_DEVELOPMENT_APPLICATION_ID is required in development"
            )
        for key in _azure_markers_for(service):
            if _nonempty(key):
                raise RuntimeConfigurationError(
                    f"{service}: {key} must be empty when DCA_RUNTIME_MODE=development"
                )
        return

    # production
    if runtime.dev_support_url:
        raise RuntimeConfigurationError(
            f"{service}: DEV_SUPPORT_URL is forbidden when DCA_RUNTIME_MODE=production"
        )
    if runtime.dev_compose_overlay_active:
        raise RuntimeConfigurationError(
            f"{service}: DEV_COMPOSE_OVERLAY_ACTIVE is forbidden in production"
        )
    if runtime.discord_development_application_id:
        raise RuntimeConfigurationError(
            f"{service}: DISCORD_DEVELOPMENT_APPLICATION_ID is forbidden in production"
        )
    if runtime.web_local_admin_oid:
        raise RuntimeConfigurationError(
            f"{service}: WEB_LOCAL_ADMIN_OID is forbidden in production"
        )


__all__ = ["RuntimeConfigurationError", "ServiceName", "assert_service_runtime"]
