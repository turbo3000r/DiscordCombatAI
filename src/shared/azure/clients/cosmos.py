from __future__ import annotations

from typing import Any

from shared.azure._helpers import collect_async_items, maybe_await
from shared.azure.configs.credential import get_credential
from shared.azure.configs.settings import AzureServiceName, AzureSettings, load_azure_settings
from shared.azure.lifecycle import register_resource
from shared.utils.retry import RetryCategory, retry_async


class CosmosClient:
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
                from azure.cosmos.aio import CosmosClient as CosmosSdkClient
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("azure-cosmos is not available") from exc
            self._service_client = CosmosSdkClient(
                self.settings.azure_cosmos_endpoint, credential=self.credential, retry_total=0
            )
            register_resource(self._service_client)
        return self._service_client

    def _container(self, container_name: str) -> Any:
        database = self._client().get_database_client(self.settings.azure_cosmos_database)
        return database.get_container_client(container_name)

    async def point_read(
        self, container_name: str, item_id: str, partition_key: str
    ) -> dict[str, Any]:
        async def _read() -> dict[str, Any]:
            container = self._container(container_name)
            result = container.read_item(item=item_id, partition_key=partition_key)
            return await maybe_await(result)

        return await retry_async(_read, category=RetryCategory.SAFE_READ)

    async def patch_if_match(
        self,
        container_name: str,
        item_id: str,
        partition_key: str,
        operations: list[dict[str, Any]],
        etag: str,
    ) -> dict[str, Any]:
        async def _patch() -> dict[str, Any]:
            container = self._container(container_name)
            try:
                from azure.core import MatchConditions

                match_condition: Any = MatchConditions.IfNotModified
            except Exception:
                match_condition = "IfNotModified"
            result = container.patch_item(
                item=item_id,
                partition_key=partition_key,
                patch_operations=operations,
                etag=etag,
                match_condition=match_condition,
            )
            return await maybe_await(result)

        return await _patch()

    async def create_item(self, container_name: str, document: dict[str, Any]) -> dict[str, Any]:
        async def _create() -> dict[str, Any]:
            container = self._container(container_name)
            try:
                result = container.create_item(document)
                return await maybe_await(result)
            except Exception as exc:
                from shared.azure.errors import classify_azure_error

                classified = classify_azure_error(exc, operation="cosmos.create_item")
                status = classified.status_code
                text = str(classified).lower()
                if status == 409 or "conflict" in text or "already exists" in text:
                    from shared.azure.errors import AzurePermanentError

                    raise AzurePermanentError(
                        "document already exists",
                        operation="cosmos.create_item",
                        status_code=409,
                    ) from exc
                raise classified from exc

        return await _create()

    async def upsert(self, container_name: str, document: dict[str, Any]) -> dict[str, Any]:
        async def _upsert() -> dict[str, Any]:
            container = self._container(container_name)
            result = container.upsert_item(document)
            return await maybe_await(result)

        return await retry_async(_upsert, category=RetryCategory.ETag_RMW)

    async def query(
        self, container_name: str, query: str, parameters: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        async def _query() -> list[dict[str, Any]]:
            container = self._container(container_name)
            result = container.query_items(
                query=query, parameters=parameters or [], enable_cross_partition_query=True
            )
            return await collect_async_items(result)

        return await retry_async(_query, category=RetryCategory.SAFE_READ)

    async def close(self) -> None:
        if self._service_client is None:
            return
        close = getattr(self._service_client, "aclose", None) or getattr(
            self._service_client, "close", None
        )
        if close is not None:
            result = close()
            await maybe_await(result)
