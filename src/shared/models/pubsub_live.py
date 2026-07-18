from __future__ import annotations

from datetime import UTC, datetime

from .base import SchemaVersionedModel, UTCDateTime


class PubSubNegotiateResponse(SchemaVersionedModel):
    schema_version: int = 1
    url: str
    expires_at: UTCDateTime
    group: str


def build_negotiate_response(url: str, expires_at: datetime, group: str) -> PubSubNegotiateResponse:
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ValueError("expires_at must be timezone-aware")
    return PubSubNegotiateResponse(url=url, expires_at=expires_at.astimezone(UTC), group=group)


__all__ = ["PubSubNegotiateResponse", "build_negotiate_response"]
