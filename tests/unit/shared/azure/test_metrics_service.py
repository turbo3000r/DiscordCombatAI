from __future__ import annotations

import pytest

from shared.azure.services.metrics import MetricsService


class _TableClient:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.queries: list[str] = []
        self.deletes: list[tuple[str, str]] = []

    async def upsert_entity(self, table_name: str, entity: dict):
        self.upserts.append(entity)
        return entity

    async def query_entities(self, table_name: str, query_filter: str):
        self.queries.append(query_filter)
        return [
            {
                "PartitionKey": "n1",
                "RowKey": "20260718125630_0001",
                "schema_version": 1,
                "node_id": "n1",
                "leadership_term": "t1",
                "sampled_at": "2026-07-18T12:56:30Z",
                "cpu_percent": 1.0,
                "memory_mb": 2.0,
                "memory_percent": 3.0,
                "latency_ms": None,
                "guild_count": None,
                "errors_in_window": 0,
                "uptime_sec": 1,
                "batch_interval_sec": 60,
            }
        ]

    async def delete_entity(self, table_name: str, partition_key: str, row_key: str):
        self.deletes.append((partition_key, row_key))


@pytest.mark.asyncio()
async def test_metrics_service_row_key_and_history() -> None:
    table = _TableClient()
    service = MetricsService(table_client=table, table_name="NodeMetrics")

    assert service.row_key_for_sampled_at("2026-07-18T12:56:30Z", 7) == "20260718125630_0007"

    saved = await service.batch_upsert(
        [
            {
                "PartitionKey": "n1",
                "RowKey": "20260718125630_0001",
                "node_id": "n1",
                "leadership_term": "t1",
                "sampled_at": "2026-07-18T12:56:30Z",
                "cpu_percent": 1.0,
                "memory_mb": 2.0,
                "memory_percent": 3.0,
                "latency_ms": None,
                "guild_count": None,
                "errors_in_window": 0,
                "uptime_sec": 1,
                "batch_interval_sec": 60,
            }
        ]
    )
    assert saved[0].PartitionKey == "n1"

    history = await service.query_history(
        "n1", start_row_key="20260718120000_0000", end_row_key="20260718130000_9999"
    )
    assert history[0].node_id == "n1"
