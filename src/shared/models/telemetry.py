from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import SchemaVersionedModel, UTCDateTime
from .launcher_ipc import RELEASE_TAG_PATTERN


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


class BotHeartbeatDependencies(_StrictModel):
    rabbitmq_connected: bool
    cosmos_ok: bool
    azure_queue_ok: bool
    status_blob_ok: bool


class BotHeartbeat(SchemaVersionedModel):
    schema_version: int = 1
    node_id: str = Field(min_length=1)
    application_version: str = Field(pattern=RELEASE_TAG_PATTERN, max_length=128)
    observed_at: UTCDateTime
    gateway_connected: bool
    latency_ms: int | None = Field(ge=0)
    guild_count: int = Field(ge=0)
    dependencies: BotHeartbeatDependencies


class AiWorkerHeartbeatState(StrEnum):
    running = "running"
    paused = "paused"


class AiWorkerHeartbeatDependencies(_StrictModel):
    rabbitmq_connected: bool


class AiWorkerHeartbeat(SchemaVersionedModel):
    schema_version: int = 1
    node_id: str = Field(min_length=1)
    application_version: str = Field(pattern=RELEASE_TAG_PATTERN, max_length=128)
    observed_at: UTCDateTime
    state: AiWorkerHeartbeatState
    active_tasks: int = Field(ge=0)
    dependencies: AiWorkerHeartbeatDependencies


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


class ServiceLivenessValue(StrEnum):
    fresh = "fresh"
    stale = "stale"


class ServiceLiveness(_StrictModel):
    bot: ServiceLivenessValue
    ai_worker: ServiceLivenessValue


class TelemetryLivePayload(SchemaVersionedModel):
    schema_version: int = 1
    type: Literal["telemetry_live"] = "telemetry_live"
    seq: int
    node_id: str
    leadership_term: str
    sampled_at: UTCDateTime
    service_liveness: ServiceLiveness
    metrics: TelemetryMetrics
    logs: list[TelemetryLiveLogLine] = Field(default_factory=list)
    logs_dropped: int = Field(ge=0)


def build_row_key(sampled_at: datetime, seq: int) -> str:
    if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
        raise ValueError("sampled_at must be timezone-aware")
    if seq < 0:
        raise ValueError("seq must be non-negative")
    return f"{sampled_at.astimezone(UTC):%Y%m%d%H%M%S}_{seq:04d}"


__all__ = [
    "AiWorkerHeartbeat",
    "AiWorkerHeartbeatDependencies",
    "AiWorkerHeartbeatState",
    "BotHeartbeat",
    "BotHeartbeatDependencies",
    "NodeMetricsEntity",
    "ServiceLiveness",
    "ServiceLivenessValue",
    "TelemetryLiveLogLine",
    "TelemetryLivePayload",
    "TelemetryMetrics",
    "build_row_key",
]
