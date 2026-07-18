from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import SchemaVersionedModel, UTCDateTime


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeMetricsEntity(SchemaVersionedModel):
    schema_version: int = 1
    PartitionKey: str
    RowKey: str
    node_id: str
    leadership_term: str
    sampled_at: UTCDateTime
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    latency_ms: int | None
    guild_count: int | None
    errors_in_window: int
    uptime_sec: int
    batch_interval_sec: int


class TelemetryMetrics(_StrictModel):
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    latency_ms: int | None
    guild_count: int | None
    errors_in_window: int
    uptime_sec: int


class TelemetryLiveLogLine(_StrictModel):
    time: UTCDateTime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    service: str
    module: str
    tags: str
    message: str


class TelemetryLivePayload(SchemaVersionedModel):
    schema_version: int = 1
    type: Literal["telemetry_live"] = "telemetry_live"
    seq: int
    node_id: str
    leadership_term: str
    sampled_at: UTCDateTime
    metrics: TelemetryMetrics
    logs: list[TelemetryLiveLogLine] = Field(default_factory=list)


def build_row_key(sampled_at: datetime, seq: int) -> str:
    if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
        raise ValueError("sampled_at must be timezone-aware")
    if seq < 0:
        raise ValueError("seq must be non-negative")
    return f"{sampled_at.astimezone(UTC):%Y%m%d%H%M%S}_{seq:04d}"


__all__ = [
    "NodeMetricsEntity",
    "TelemetryLiveLogLine",
    "TelemetryLivePayload",
    "TelemetryMetrics",
    "build_row_key",
]
