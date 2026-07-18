from __future__ import annotations

from typing import Any

from shared.azure._helpers import collect_async_items, maybe_await
from shared.azure.configs.credential import get_credential
from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource
from shared.utils.retry import RetryCategory, retry_async


class TableClient:
    def __init__(
        self,
        *,
        service: AzureServiceName,
        service_client: Any | None = None,
        settings: AzureSettings | None = None,
        credential: Any | None = None,
    ) -> None:
        self.service = service
        self.settings = settings or load_azure_settings(service)
        self.credential = credential or get_credential(service, self.settings)
        self._service_client = service_client
        register_resource(self.credential)
        if self._service_client is not None:
            register_resource(self._service_client)

    def _client(self) -> Any:
        if self._service_client is None:
            try:
                from azure.data.tables.aio import TableServiceClient
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("azure-data-tables is not available") from exc
            self._service_client = TableServiceClient(
                endpoint=self.settings.table_endpoint, credential=self.credential, retry_total=0
            )
            register_resource(self._service_client)
        return self._service_client

    def _table(self, table_name: str) -> Any:
        return self._client().get_table_client(table_name)

    async def upsert_entity(self, table_name: str, entity: dict[str, Any]) -> dict[str, Any]:
        async def _upsert() -> dict[str, Any]:
            table = self._table(table_name)
            result = table.upsert_entity(entity)
            return await maybe_await(result)

        return await retry_async(_upsert, category=RetryCategory.ETag_RMW)

    async def query_entities(self, table_name: str, query_filter: str) -> list[dict[str, Any]]:
        async def _query() -> list[dict[str, Any]]:
            table = self._table(table_name)
            result = table.query_entities(query_filter)
            return await collect_async_items(result)

        return await retry_async(_query, category=RetryCategory.SAFE_READ)

    async def delete_entity(self, table_name: str, partition_key: str, row_key: str) -> None:
        async def _delete() -> None:
            table = self._table(table_name)
            result = table.delete_entity(partition_key=partition_key, row_key=row_key)
            await maybe_await(result)

        await retry_async(_delete, category=RetryCategory.ETag_RMW)

    async def close(self) -> None:
        if self._service_client is None:
            return
        close = getattr(self._service_client, "aclose", None) or getattr(
            self._service_client, "close", None
        )
        if close is not None:
            result = close()
            await maybe_await(result)
