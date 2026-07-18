from __future__ import annotations

import re

_REDACTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api_key=)([^,\s]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(webhook_url=)([^,\s]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(hmac_secret=)([^,\s]+)"), r"\1[REDACTED]"),
    (
        re.compile(r"(?i)https://discord(?:app)?\.com/api/webhooks/\S+"),
        "[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(client_secret|accountkey|sharedaccesssignature|sig|token|"
            r"access_token|password|secret)=([^&\s]+)"
        ),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)(https?://[^\s?]+\?[^\s]+)"), "<redacted-url>"),
)


def redact_sensitive(text: str) -> str:
    """Redact secrets from logs, Azure errors, and persisted failure messages."""
    redacted = text
    for pattern, replacement in _REDACTION_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


__all__ = ["redact_sensitive"]
