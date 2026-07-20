from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from shared.models import LogLine, TelemetryLiveLogLine, format_log_line, parse_log_line
from shared.security.redact import redact_sensitive

_SAFE_HEADER_KEYS = frozenset(
    {
        "name",
        "queue",
        "vhost",
        "user",
        "connection",
        "channel",
        "reason",
        "pid",
        "peer_host",
        "peer_port",
        "node",
        "type",
        "protocol",
        "client_properties",
    }
)

_ERROR_ROUTING_KEYS = frozenset(
    {
        "alarm.set",
        "user.authentication.failure",
    }
)

_WARNING_ROUTING_KEYS = frozenset(
    {
        "alarm.cleared",
        "connection.closed",
        "channel.closed",
        "consumer.deleted",
        "queue.deleted",
        "exchange.deleted",
        "vhost.deleted",
        "permission.deleted",
        "binding.deleted",
        "topic.permission.deleted",
    }
)


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
    headers: Mapping[str, object] | None = None


class RabbitMqLogBridge:
    """Normalizes broker metadata to Mosquitto log lines without message bodies."""

    def classify(self, routing_key: str) -> RabbitSeverity | None:
        if routing_key in _ERROR_ROUTING_KEYS:
            return RabbitSeverity.error
        if routing_key in _WARNING_ROUTING_KEYS:
            return RabbitSeverity.warning
        return None

    def mqtt_topic(self, severity: RabbitSeverity) -> str:
        if severity is RabbitSeverity.error:
            return "logs/errors/rabbitmq"
        return "logs/warning/rabbitmq"

    def normalize(self, event: RabbitBrokerEvent) -> LogLine | None:
        classified = self.classify(event.routing_key)
        if classified is None:
            return None
        tags = self._safe_tags(event.routing_key, event.headers)
        return LogLine(
            time=event.occurred_at,
            level=classified.value,
            service="rabbitmq",
            module="event_exchange",
            tags=tags,
            message="RabbitMQ broker event",
        )

    def handle(self, event: RabbitBrokerEvent, aggregator: LogAggregator) -> LogLine | None:
        line = self.normalize(event)
        if line is None:
            return None
        return aggregator.ingest(format_log_line(line))

    def _safe_tags(self, routing_key: str, headers: Mapping[str, object] | None) -> str:
        parts = [f"routing_key={redact_sensitive(routing_key)}"]
        if not headers:
            return ",".join(parts)
        for key in sorted(_SAFE_HEADER_KEYS):
            if key not in headers:
                continue
            value = headers[key]
            if value is None or isinstance(value, (bytes, bytearray)):
                continue
            text = redact_sensitive(str(value))
            if not text:
                continue
            if len(text) > 128:
                text = text[:125] + "..."
            parts.append(f"{key}={text}")
        return ",".join(parts)


__all__ = [
    "LogAggregator",
    "LogArchive",
    "RabbitBrokerEvent",
    "RabbitMqLogBridge",
    "RabbitSeverity",
]
