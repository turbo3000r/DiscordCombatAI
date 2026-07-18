from __future__ import annotations

from shared.models.log_archive import LogLine, format_log_line, parse_log_line, redact_sensitive


def test_log_escape_roundtrip() -> None:
    log = LogLine(
        time="2026-07-15T17:02:09.123Z",
        level="INFO",
        service="ai_worker",
        module="graphs/battle/nodes/modifier",
        tags="guild_id=1]x,api_key=secret%value",
        message="line 1\nline 2",
    )
    formatted = format_log_line(log)
    parsed = parse_log_line(formatted)

    assert parsed == log


def test_sensitive_redaction() -> None:
    text = "api_key=secret webhook_url=https://discord.com/api/webhooks/1/2 hmac_secret=abc"
    redacted = redact_sensitive(text)

    assert "[REDACTED]" in redacted
    assert "api_key=secret" not in redacted
    assert "webhook_url=https://discord.com/api/webhooks/1/2" not in redacted
    assert "hmac_secret=abc" not in redacted
