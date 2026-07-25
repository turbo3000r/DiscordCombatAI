"""Composition-root repository factory keyed by runtime mode."""

from __future__ import annotations

from typing import Literal

from shared.runtime.guards import RuntimeConfigurationError, assert_service_runtime
from shared.runtime.settings import RuntimeMode, RuntimeSettings

from .types import Repositories

FactoryService = Literal["bot", "web"]


def build_repositories(service: FactoryService, runtime: RuntimeSettings) -> Repositories:
    """Select Azure or local adapters. Development must not import Azure clients."""
    assert_service_runtime(runtime, service)
    if runtime.mode is RuntimeMode.development:
        return _build_local(runtime)
    return _build_azure(service)


def _build_local(runtime: RuntimeSettings) -> Repositories:
    if not runtime.dev_support_url:
        raise RuntimeConfigurationError("DEV_SUPPORT_URL is required for local repositories")
    # Local adapters import httpx only — never azure.*
    from .local_adapters import (
        LocalGuildRepository,
        LocalMetricsRepository,
        LocalStatusRepository,
        LocalSuggestionRepository,
    )

    base = runtime.dev_support_url
    return Repositories(
        guild=LocalGuildRepository(base),
        suggestion=LocalSuggestionRepository(base),
        status=LocalStatusRepository(base),
        metrics=LocalMetricsRepository(base),
    )


def _build_azure(service: FactoryService) -> Repositories:
    # Imported only on the production path so development never loads Azure SDKs here.
    from shared.azure.clients.blob import BlobClient
    from shared.azure.clients.cosmos import CosmosClient
    from shared.azure.clients.queue import QueueClient
    from shared.azure.clients.table import TableClient
    from shared.azure.configs.settings import load_azure_settings
    from shared.azure.services.guilds import GuildConfigService
    from shared.azure.services.metrics import MetricsService
    from shared.azure.services.status import StatusService
    from shared.azure.services.suggestions import SuggestionService

    from .azure_adapters import (
        AzureGuildRepository,
        AzureMetricsRepository,
        AzureStatusRepository,
        AzureSuggestionRepository,
    )

    settings = load_azure_settings(service)
    cosmos = CosmosClient(service=service, settings=settings)
    queue = QueueClient(service=service, settings=settings)
    blob = BlobClient(service=service, settings=settings)
    table = TableClient(service=service, settings=settings)

    guild_service = GuildConfigService(cosmos_client=cosmos, container_name="GuildConfigs")
    suggestion_service = SuggestionService(
        cosmos_client=cosmos,
        queue_client=queue,
        container_name="Suggestions",
        queue_name=settings.azure_queue_name,
    )
    status_service = StatusService(
        blob_client=blob,
        container_name=settings.azure_status_blob_container,
        blob_name=settings.azure_status_blob_name,
    )
    metrics_service = MetricsService(
        table_client=table, table_name=settings.azure_metrics_table
    )
    return Repositories(
        guild=AzureGuildRepository(guild_service),
        suggestion=AzureSuggestionRepository(suggestion_service),
        status=AzureStatusRepository(status_service),
        metrics=AzureMetricsRepository(metrics_service),
    )


__all__ = ["FactoryService", "build_repositories"]
