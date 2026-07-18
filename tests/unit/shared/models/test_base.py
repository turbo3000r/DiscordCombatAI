from __future__ import annotations

from datetime import UTC, datetime

from shared.models.base import ensure_utc, parse_utc_datetime, web_can_read


def test_datetime_helpers() -> None:
    dt = parse_utc_datetime("2026-07-15T17:00:00Z")
    assert dt.tzinfo == UTC
    assert ensure_utc(datetime(2026, 7, 15, 17, 0, tzinfo=UTC)) == dt


def test_web_can_read_window() -> None:
    assert web_can_read(1, 1)
    assert web_can_read(2, 1)
    assert web_can_read(0, 1)
    assert not web_can_read(3, 1)
