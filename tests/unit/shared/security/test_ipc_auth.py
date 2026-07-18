from __future__ import annotations

from datetime import UTC, datetime

from shared.security import build_canonical_string, sign_request, verify_request


def test_hmac_golden_vector() -> None:
    canonical = build_canonical_string(
        "post",
        "/v1/update",
        1721053200,
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        b"hello",
    )
    assert canonical == (
        "POST\n"
        "/v1/update\n"
        "1721053200\n"
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca\n"
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )

    signature = sign_request(
        b"secret",
        "POST",
        "/v1/update",
        1721053200,
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        b"hello",
    )
    assert signature == "2984b1dffdbb42554922746130b1e9f6fb285f86702437377ed06c9c74f39e8d"
    assert verify_request(
        b"secret",
        "POST",
        "/v1/update",
        1721053200,
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        b"hello",
        signature,
        now=datetime.fromtimestamp(1721053200, tz=UTC),
    )


def test_hmac_skew_rejected() -> None:
    signature = sign_request(
        b"secret",
        "GET",
        "/v1/status",
        1721053200,
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        b"",
    )
    assert not verify_request(
        b"secret",
        "GET",
        "/v1/status",
        1721053200,
        "02e75c7a-8de1-4e3b-883a-5c41ae8b98ca",
        b"",
        signature,
        now=datetime.fromtimestamp(1721053305, tz=UTC),
    )
