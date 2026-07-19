from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from shared.azure.services.metrics import MetricsService
from shared.models import (
    AiWorkerHeartbeat,
    BotHeartbeat,
    NodeMetricsEntity,
    ServiceLiveness,
    ServiceLivenessValue,
    TelemetryLivePayload,
    TelemetryMetrics,
    build_row_key,
)

from .clock import Clock
from .logs import LogAggregator
from .pubsub import DashboardTelemetrySender


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_percent: float
    memory_mb: float
    memory_percent: float


class ProcessSampler(Protocol):
    def sample(self) -> HardwareSnapshot: ...


class MetricsWriter(Protocol):
    async def write(self, entity: NodeMetricsEntity) -> None: ...


class MetricsServiceWriter:
    def __init__(self, service: MetricsService) -> None:
        self._service = service

    async def write(self, entity: NodeMetricsEntity) -> None:
        await self._service.batch_upsert([entity.model_dump(mode="python")])


class TelemetryPipeline:
    def __init__(
        self,
        *,
        node_id: str,
        clock: Clock,
        sampler: ProcessSampler,
        logs: LogAggregator,
        metrics_writer: MetricsWriter,
        live_sender: DashboardTelemetrySender,
        dashboard_group: str,
        batch_interval_sec: int,
        heartbeat_stale_sec: float = 90,
        metrics_buffer_max_batches: int = 60,
        live_max_logs: int = 50,
        live_max_bytes: int = 65_536,
    ) -> None:
        self._node_id = node_id
        self._clock = clock
        self._sampler = sampler
        self._logs = logs
        self._metrics_writer = metrics_writer
        self._live_sender = live_sender
        self._dashboard_group = dashboard_group
        self._batch_interval_sec = batch_interval_sec
        self._heartbeat_stale_sec = heartbeat_stale_sec
        self._metrics_buffer_max_batches = metrics_buffer_max_batches
        self._live_max_logs = live_max_logs
        self._live_max_bytes = live_max_bytes
        self._started = clock.monotonic()
        self._sequence = 0
        self._latest_hardware: HardwareSnapshot | None = None
        self._latency_ms: int | None = None
        self._guild_count: int | None = None
        self._bot_received_at: float | None = None
        self._worker_received_at: float | None = None
        self._pending_metrics: deque[NodeMetricsEntity] = deque()
        self.dropped_metric_windows = 0

    def sample_local(self) -> HardwareSnapshot:
        self._latest_hardware = self._sampler.sample()
        return self._latest_hardware

    def update_bot_sample(self, *, latency_ms: int | None, guild_count: int | None) -> None:
        if latency_ms is not None and latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if guild_count is not None and guild_count < 0:
            raise ValueError("guild_count must be non-negative")
        self._latency_ms = latency_ms
        self._guild_count = guild_count
        self._bot_received_at = self._clock.monotonic()

    def update_bot_heartbeat(self, heartbeat: BotHeartbeat) -> None:
        if heartbeat.node_id != self._node_id:
            return
        self.update_bot_sample(
            latency_ms=heartbeat.latency_ms,
            guild_count=heartbeat.guild_count,
        )

    def update_worker_heartbeat(self, heartbeat: AiWorkerHeartbeat) -> None:
        if heartbeat.node_id == self._node_id:
            self._worker_received_at = self._clock.monotonic()

    def _fresh(self, received_at: float | None) -> bool:
        return (
            received_at is not None
            and self._clock.monotonic() - received_at < self._heartbeat_stale_sec
        )

    def _service_liveness(self) -> ServiceLiveness:
        return ServiceLiveness(
            bot=(
                ServiceLivenessValue.fresh
                if self._fresh(self._bot_received_at)
                else ServiceLivenessValue.stale
            ),
            ai_worker=(
                ServiceLivenessValue.fresh
                if self._fresh(self._worker_received_at)
                else ServiceLivenessValue.stale
            ),
        )

    def _metrics(self) -> TelemetryMetrics:
        hardware = self._latest_hardware or self.sample_local()
        return TelemetryMetrics(
            cpu_percent=hardware.cpu_percent,
            memory_mb=hardware.memory_mb,
            memory_percent=hardware.memory_percent,
            latency_ms=self._latency_ms if self._fresh(self._bot_received_at) else None,
            guild_count=self._guild_count if self._fresh(self._bot_received_at) else None,
            errors_in_window=self._logs.errors_in_window,
            uptime_sec=max(0, int(self._clock.monotonic() - self._started)),
        )

    async def flush_persistent(
        self, *, is_leader: bool, leadership_term: str | None
    ) -> NodeMetricsEntity | None:
        if not is_leader:
            return None
        if leadership_term is None:
            raise ValueError("leader telemetry requires a leadership term")
        self._sequence += 1
        sampled_at = self._clock.utcnow()
        metrics = self._metrics()
        entity = NodeMetricsEntity(
            PartitionKey=self._node_id,
            RowKey=build_row_key(sampled_at, self._sequence),
            node_id=self._node_id,
            leadership_term=leadership_term,
            sampled_at=sampled_at,
            cpu_percent=metrics.cpu_percent,
            memory_mb=metrics.memory_mb,
            memory_percent=metrics.memory_percent,
            latency_ms=metrics.latency_ms,
            guild_count=metrics.guild_count,
            errors_in_window=metrics.errors_in_window,
            uptime_sec=metrics.uptime_sec,
            batch_interval_sec=self._batch_interval_sec,
        )
        self._pending_metrics.append(entity)
        if len(self._pending_metrics) > self._metrics_buffer_max_batches:
            self._pending_metrics.popleft()
            self.dropped_metric_windows += 1
        self._logs.reset_error_window()
        while self._pending_metrics:
            try:
                await self._metrics_writer.write(self._pending_metrics[0])
            except Exception:
                break
            self._pending_metrics.popleft()
        return entity

    async def stream_live(
        self, *, is_leader: bool, leadership_term: str | None
    ) -> TelemetryLivePayload | None:
        if not is_leader:
            return None
        if leadership_term is None:
            raise ValueError("leader live telemetry requires a leadership term")
        self._sequence += 1
        eligible_logs = self._logs.recent_count
        selected_logs = self._logs.live_tail(self._live_max_logs)
        logs_dropped = max(0, eligible_logs - len(selected_logs))
        payload = TelemetryLivePayload(
            seq=self._sequence,
            node_id=self._node_id,
            leadership_term=leadership_term,
            sampled_at=self._clock.utcnow(),
            service_liveness=self._service_liveness(),
            metrics=self._metrics(),
            logs=selected_logs,
            logs_dropped=logs_dropped,
        )
        while len(payload.model_dump_json().encode("utf-8")) > self._live_max_bytes:
            if not selected_logs:
                raise ValueError("live telemetry envelope exceeds configured byte cap")
            selected_logs.pop(0)
            logs_dropped += 1
            payload = payload.model_copy(
                update={"logs": list(selected_logs), "logs_dropped": logs_dropped}
            )
        await self._live_sender.send(self._dashboard_group, payload)
        return payload


__all__ = [
    "HardwareSnapshot",
    "MetricsServiceWriter",
    "MetricsWriter",
    "ProcessSampler",
    "TelemetryPipeline",
]
