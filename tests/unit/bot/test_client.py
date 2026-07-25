from __future__ import annotations

from bot.modules.client import build_intents, create_bot


def test_bot_intents_are_guild_only() -> None:
    intents = build_intents()
    assert intents.guilds is True
    assert intents.message_content is False
    assert intents.members is False
    assert intents.presences is False


def test_create_bot_has_no_help_command() -> None:
    bot = create_bot()
    assert bot.help_command is None
    assert bot.intents.guilds is True
