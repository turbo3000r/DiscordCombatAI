from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID

MAX_CLOCK_SKEW_SEC = 30


def _normalize_request_id(request_id: str | UUID) -> str:
    if isinstance(request_id, UUID):
        return str(request_id)
    return str(UUID(request_id))


def _normalize_timestamp(timestamp: int | float | str | datetime) -> int:
    if isinstance(timestamp, datetime):
        dt = timestamp
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("Timestamp must be timezone-aware")
        return int(dt.astimezone(UTC).timestamp())
    if isinstance(timestamp, (int, float)):
        return int(timestamp)
    if isinstance(timestamp, str):
        return int(timestamp.strip())
    raise TypeError(f"Unsupported timestamp type: {type(timestamp)!r}")


def build_canonical_string(
    method: str,
    path_with_query: str,
    timestamp: int | float | str | datetime,
    request_id: str | UUID,
    body: bytes,
) -> str:
    normalized_method = method.upper()
    normalized_timestamp = _normalize_timestamp(timestamp)
    normalized_request_id = _normalize_request_id(request_id)
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join(
        [
            normalized_method,
            path_with_query,
            str(normalized_timestamp),
            normalized_request_id,
            body_hash,
        ]
    )


def sign_request(
    secret: bytes | str,
    method: str,
    path_with_query: str,
    timestamp: int | float | str | datetime,
    request_id: str | UUID,
    body: bytes = b"",
) -> str:
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    canonical = build_canonical_string(method, path_with_query, timestamp, request_id, body)
    return hmac.new(secret_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_request(
    secret: bytes | str,
    method: str,
    path_with_query: str,
    timestamp: int | float | str | datetime,
    request_id: str | UUID,
    body: bytes,
    signature: str,
    *,
    now: datetime | None = None,
) -> bool:
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    normalized_timestamp = _normalize_timestamp(timestamp)
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if abs(int(reference.timestamp()) - normalized_timestamp) > MAX_CLOCK_SKEW_SEC:
        return False
    expected = sign_request(
        secret_bytes, method, path_with_query, normalized_timestamp, request_id, body
    )
    return hmac.compare_digest(expected, signature.lower())


__all__ = [
    "MAX_CLOCK_SKEW_SEC",
    "build_canonical_string",
    "sign_request",
    "verify_request",
]
