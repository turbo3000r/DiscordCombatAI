from __future__ import annotations

import re
from datetime import UTC
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from .base import UTCDateTime, parse_utc_datetime

_LOG_RE = re.compile(
    r"^\[(?P<time>[^\]]+)\]\[(?P<level>DEBUG|INFO|WARNING|ERROR)\]\[(?P<service>[^\]]+)\]"
    r"\[(?P<module>[^\]]+)\](?P<tags><[^>]*>|): \[(?P<message>.*)\]$"
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LogLine(_StrictModel):
    time: UTCDateTime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    service: str
    module: str
    tags: str = ""
    message: str


def _escape_text(value: str) -> str:
    return value.replace("%", "%25").replace("]", "%5D").replace("\n", r"\n")


def _unescape_text(value: str) -> str:
    return value.replace(r"\n", "\n").replace("%5D", "]").replace("%25", "%")


def format_log_line(log: LogLine) -> str:
    tags = f"<{_escape_text(log.tags)}>" if log.tags else ""
    message = _escape_text(log.message)
    return (
        f"[{log.time.astimezone(UTC).isoformat().replace('+00:00', 'Z')}]"
        f"[{log.level}]"
        f"[{log.service}]"
        f"[{log.module}]"
        f"{tags}: "
        f"[{message}]"
    )


def parse_log_line(line: str) -> LogLine:
    match = _LOG_RE.fullmatch(line)
    if match is None:
        raise ValueError("Invalid log line")
    data = match.groupdict()
    tags = _unescape_text(data["tags"][1:-1]) if data["tags"] else ""
    level = cast(Literal["DEBUG", "INFO", "WARNING", "ERROR"], data["level"])
    return LogLine(
        time=parse_utc_datetime(data["time"]),
        level=level,
        service=data["service"],
        module=data["module"],
        tags=tags,
        message=_unescape_text(data["message"]),
    )


def redact_sensitive(text: str) -> str:
    from shared.security.redact import redact_sensitive as _redact_sensitive

    return _redact_sensitive(text)


__all__ = [
    "LogLine",
    "format_log_line",
    "parse_log_line",
    "redact_sensitive",
]
