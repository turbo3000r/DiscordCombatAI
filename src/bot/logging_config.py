"""Stdout logging for the Bot container.

Canonical line shape is `contracts/log_archive.md`. MQTT archive publish is
out of this slice (Head is absent in the development overlay).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shared.models.log_archive import LogLine, format_log_line
from shared.security.redact import redact_sensitive

_TAG_KEYS = (
    "trace_id",
    "guild_id",
    "user_id",
    "command",
    "task_id",
    "session_id",
    "interaction_id",
    "decision",
    "graph",
    "phase",
    "participant_count",
    "approval_round",
    "approve_ratio",
)

_SECRET_FRAGMENTS = ("token", "api_key", "password", "secret", "webhook")
_SKIP_PARAM_KEYS = frozenset({"ctx", "interaction", "self"})
_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


class CanonicalLogFormatter(logging.Formatter):
    """Render LogRecord as a single-line archive string."""

    def __init__(self, *, service: str = "bot") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname if record.levelname in _LEVELS else "ERROR"
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        tags = []
        for key in _TAG_KEYS:
            value = getattr(record, key, None)
            if value is None or value == "":
                continue
            tags.append(f"{key}={value}")
        line = format_log_line(
            LogLine(
                time=datetime.fromtimestamp(record.created, tz=UTC),
                level=level,  # type: ignore[arg-type]
                service=self._service,
                module=record.name,
                tags=redact_sensitive(", ".join(tags)),
                message=redact_sensitive(message),
            )
        )
        return line


def public_command_params(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Slash/callback kwargs safe for INFO logs (no secrets, bounded strings)."""
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        lowered = key.lower()
        if key in _SKIP_PARAM_KEYS or any(fragment in lowered for fragment in _SECRET_FRAGMENTS):
            continue
        if value is None or isinstance(value, (bool, int, float)):
            out[key] = value
            continue
        if isinstance(value, str):
            out[key] = value if len(value) <= 80 else f"{value[:77]}..."
            continue
        out[key] = type(value).__name__
    return out


def configure_bot_logging(*, level: int = logging.INFO) -> None:
    """Attach a canonical formatter to the root logger for container stdout."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = CanonicalLogFormatter()
    stream_handlers = [
        handler for handler in root.handlers if isinstance(handler, logging.StreamHandler)
    ]
    if stream_handlers:
        for handler in stream_handlers:
            handler.setFormatter(formatter)
            handler.setLevel(level)
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        root.addHandler(handler)
    logging.getLogger("bot").setLevel(level)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("amqp").setLevel(logging.WARNING)
    logging.getLogger("kombu").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.WARNING)


__all__ = [
    "CanonicalLogFormatter",
    "configure_bot_logging",
    "public_command_params",
]
