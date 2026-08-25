from __future__ import annotations

import logging

from bot.logging_config import CanonicalLogFormatter, public_command_params
from shared.models.log_archive import parse_log_line


def test_public_command_params_omits_secrets_and_bounds_strings() -> None:
    params = public_command_params(
        {
            "ctx": object(),
            "setting": "unpredictable-funny",
            "timeout": 60,
            "custom_environment": 0,
            "api_key": "AIzaShouldNeverAppear",
            "bot_token": "secret-token",
            "bio": "x" * 120,
        }
    )
    assert params["setting"] == "unpredictable-funny"
    assert params["timeout"] == 60
    assert params["custom_environment"] == 0
    assert "api_key" not in params
    assert "bot_token" not in params
    assert "ctx" not in params
    assert params["bio"].endswith("...")
    assert "AIzaShouldNeverAppear" not in str(params)


def test_canonical_formatter_tags_and_redacts() -> None:
    record = logging.LogRecord(
        name="bot.modules.commands.process_command",
        level=logging.INFO,
        pathname="process_command.py",
        lineno=1,
        msg="ai_task published api_key=%s",
        args=("AIzaLeak",),
        exc_info=None,
    )
    record.guild_id = "111"
    record.user_id = "222"
    record.command = "quick-battle"
    record.interaction_id = "333"
    record.decision = "allowed"
    line = CanonicalLogFormatter().format(record)
    parsed = parse_log_line(line)
    assert parsed.service == "bot"
    assert parsed.level == "INFO"
    assert "guild_id=111" in parsed.tags
    assert "command=quick-battle" in parsed.tags
    assert "AIzaLeak" not in line
    assert "api_key=[REDACTED]" in parsed.message
