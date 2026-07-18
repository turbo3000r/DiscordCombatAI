from __future__ import annotations

from datetime import timedelta
from typing import Any

from shared.azure._helpers import utc_now
from shared.azure.clients.table import TableClient
from shared.models import NodeMetricsEntity


class MetricsService:
    def __init__(self, *, table_client: TableClient, table_name: str) -> None:
        self.table_client = table_client
        self.table_name = table_name

    @staticmethod
    def row_key_for_sampled_at(sampled_at_iso: str, sequence: int) -> str:
        stamp = (
            sampled_at_iso.replace("-", "").replace(":", "").replace("T", "").replace("Z", "")[:14]
        )
        return f"{stamp}_{sequence:04d}"

    async def batch_upsert(self, entities: list[dict[str, Any]]) -> list[NodeMetricsEntity]:
        saved: list[NodeMetricsEntity] = []
        for entity in entities:
            model = NodeMetricsEntity.model_validate(entity)
            result = await self.table_client.upsert_entity(
                self.table_name, model.model_dump(mode="python")
            )
            saved.append(NodeMetricsEntity.model_validate(result))
        return saved

    async def query_history(
        self, node_id: str, *, start_row_key: str, end_row_key: str
    ) -> list[NodeMetricsEntity]:
        filter_ = (
            f"PartitionKey eq '{node_id}' and RowKey ge '{start_row_key}' "
            f"and RowKey le '{end_row_key}'"
        )
        rows = await self.table_client.query_entities(self.table_name, filter_)
        return [NodeMetricsEntity.model_validate(row) for row in rows]

    async def cleanup_retention(self, *, days: int = 30) -> int:
        cutoff = utc_now() - timedelta(days=days)
        row_cutoff = cutoff.strftime("%Y%m%d%H%M%S")
        rows = await self.table_client.query_entities(
            self.table_name, f"RowKey lt '{row_cutoff}_0000'"
        )
        removed = 0
        for row in rows:
            await self.table_client.delete_entity(
                self.table_name, row["PartitionKey"], row["RowKey"]
            )
            removed += 1
        return removed
