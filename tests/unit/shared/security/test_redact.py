from __future__ import annotations

from shared.security.redact import redact_sensitive


def test_redact_sensitive_covers_azure_and_log_secrets() -> None:
    text = (
        "api_key=topsecret webhook_url=https://discord.com/api/webhooks/1/2 "
        "hmac_secret=abc client_secret=xyz https://host/path?sig=token-value"
    )
    redacted = redact_sensitive(text)
    assert "topsecret" not in redacted
    assert "xyz" not in redacted
    assert "token-value" not in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "client_secret=<redacted>" in redacted
    assert "sig=<redacted>" in redacted or "<redacted-url>" in redacted
