from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from shared.models import LogLine, TelemetryLiveLogLine, format_log_line, parse_log_line
from shared.security.redact import redact_sensitive


class LogArchive(Protocol):
    def buffer_lines(self, lines: list[str]) -> None: ...

    async def flush(self, *, node_id: str, timestamp_iso: str | None = None) -> str: ...


class LogAggregator:
    def __init__(self, archive: LogArchive) -> None:
        self._archive = archive
        self._recent: deque[LogLine] = deque()
        self._recent_bytes = 0
        self._errors_in_window = 0

    @property
    def errors_in_window(self) -> int:
        return self._errors_in_window

    @property
    def recent_count(self) -> int:
        return len(self._recent)

    def reset_error_window(self) -> None:
        self._errors_in_window = 0

    def ingest(self, wire_line: str) -> LogLine:
        parsed = parse_log_line(wire_line)
        safe = parsed.model_copy(
            update={
                "tags": redact_sensitive(parsed.tags),
                "message": redact_sensitive(parsed.message),
            }
        )
        canonical = format_log_line(safe)
        self._archive.buffer_lines([canonical])
        self._recent.append(safe)
        self._recent_bytes += len(canonical.encode("utf-8")) + 1
        while len(self._recent) > 10_000 or self._recent_bytes > 8 * 1024 * 1024:
            removed = self._recent.popleft()
            self._recent_bytes -= len(format_log_line(removed).encode("utf-8")) + 1
        if safe.level == "ERROR":
            self._errors_in_window += 1
        return safe

    def live_tail(self, limit: int) -> list[TelemetryLiveLogLine]:
        if limit < 0:
            raise ValueError("live log limit must be non-negative")
        return [
            TelemetryLiveLogLine(
                time=line.time,
                level=line.level,
                service=line.service,
                module=line.module,
                tags=line.tags,
                message=line.message,
            )
            for line in list(self._recent)[-limit:]
        ]

    async def flush_if_leader(
        self, *, is_leader: bool, node_id: str, timestamp: datetime
    ) -> str | None:
        if not is_leader:
            return None
        path = await self._archive.flush(node_id=node_id, timestamp_iso=timestamp.isoformat())
        self._errors_in_window = 0
        return path


class RabbitSeverity(StrEnum):
    warning = "WARNING"
    error = "ERROR"


@dataclass(frozen=True, slots=True)
class RabbitBrokerEvent:
    routing_key: str
    severity: RabbitSeverity
    occurred_at: datetime


class RabbitMqLogBridge:
    """Converts broker metadata to logs without accepting message bodies."""

    def __init__(self, aggregator: LogAggregator) -> None:
        self._aggregator = aggregator

    def handle(self, event: RabbitBrokerEvent) -> LogLine:
        line = LogLine(
            time=event.occurred_at,
            level=event.severity.value,
            service="rabbitmq",
            module="event_exchange",
            tags=f"routing_key={event.routing_key}",
            message="RabbitMQ broker event",
        )
        return self._aggregator.ingest(format_log_line(line))


__all__ = [
    "LogAggregator",
    "LogArchive",
    "RabbitBrokerEvent",
    "RabbitMqLogBridge",
    "RabbitSeverity",
]
