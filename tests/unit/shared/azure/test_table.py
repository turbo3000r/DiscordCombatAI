from __future__ import annotations

import pytest

from shared.azure.clients.table import TableClient


class _Table:
    def __init__(self) -> None:
        self.entities: list[dict] = []
        self.deleted: list[tuple[str, str]] = []

    def upsert_entity(self, entity: dict):
        self.entities.append(entity)
        return {**entity, "saved": True}

    def query_entities(self, query_filter: str):
        assert "PartitionKey" in query_filter
        return [{"PartitionKey": "n1", "RowKey": "20260718125630_0001"}]

    def delete_entity(self, *, partition_key: str, row_key: str):
        self.deleted.append((partition_key, row_key))
        return None


class _AsyncEntities:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def __aiter__(self):
        async def _iter():
            for item in self._items:
                yield item

        return _iter()


class _AsyncTable(_Table):
    def query_entities(self, query_filter: str):
        return _AsyncEntities(
            [{"PartitionKey": "n2", "RowKey": "20260718125631_0001"}]
        )


@pytest.mark.asyncio()
async def test_table_client_query_collects_async_pages(azure_settings, fake_credential) -> None:
    class _AsyncService:
        def __init__(self) -> None:
            self.table = _AsyncTable()

        def get_table_client(self, table_name: str) -> _AsyncTable:
            return self.table

    client = TableClient(
        service="head",
        service_client=_AsyncService(),
        settings=azure_settings,
        credential=fake_credential,
    )
    rows = await client.query_entities("NodeMetrics", "PartitionKey eq 'n2'")
    assert rows == [{"PartitionKey": "n2", "RowKey": "20260718125631_0001"}]


class _Service:
    def __init__(self) -> None:
        self.table = _Table()

    def get_table_client(self, table_name: str) -> _Table:
        assert table_name == "NodeMetrics"
        return self.table


@pytest.mark.asyncio()
async def test_table_client_round_trips(azure_settings, fake_credential) -> None:
    service = _Service()
    client = TableClient(
        service="head",
        service_client=service,
        settings=azure_settings,
        credential=fake_credential,
    )

    saved = await client.upsert_entity("NodeMetrics", {"PartitionKey": "n1", "RowKey": "rk"})
    rows = await client.query_entities("NodeMetrics", "PartitionKey eq 'n1'")
    await client.delete_entity("NodeMetrics", "n1", "rk")

    assert saved["saved"] is True
    assert rows[0]["PartitionKey"] == "n1"
    assert service.table.deleted == [("n1", "rk")]
