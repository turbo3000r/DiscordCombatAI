"""Command registration includes /quick-battle with documented guards."""

from __future__ import annotations

from bot.modules.client import create_bot
from shared.runtime.settings import RuntimeMode


def test_phase3_registers_config_suggest_and_quick_battle() -> None:
    bot = create_bot(
        runtime_mode=RuntimeMode.production,
        development_guild_id="555555555555555555",
    )
    names = sorted(cmd.name for cmd in bot.tree.get_commands())
    assert names == ["config", "quick-battle", "suggest"]
    quick = next(cmd for cmd in bot.tree.get_commands() if cmd.name == "quick-battle")
    option_names = [opt.name for opt in quick.parameters]
    assert option_names == ["custom_environment", "timeout", "setting"]


def test_command_callbacks_expose_interaction_only() -> None:
    bot = create_bot(runtime_mode=RuntimeMode.production)
    for cmd in bot.tree.get_commands():
        params = list(cmd.parameters) if hasattr(cmd, "parameters") else []
        assert all(getattr(p, "name", None) != "ctx" for p in params)


def test_quick_battle_registration_guards_and_development_isolation() -> None:
    from bot.modules.commands.battle.command import QUICK_BATTLE_GUARDS

    assert QUICK_BATTLE_GUARDS["required_guild"] is True
    assert QUICK_BATTLE_GUARDS["required_guild_enabled"] is True
    assert QUICK_BATTLE_GUARDS["allowed_permissions"] is None
    assert QUICK_BATTLE_GUARDS["blocked_during_drain"] is True

    production = create_bot(runtime_mode=RuntimeMode.production)
    development = create_bot(
        runtime_mode=RuntimeMode.development,
        development_guild_id="555555555555555555",
    )
    assert "quick-battle" in {cmd.name for cmd in production.tree.get_commands()}
    assert "quick-battle" in {cmd.name for cmd in development.tree.get_commands()}
