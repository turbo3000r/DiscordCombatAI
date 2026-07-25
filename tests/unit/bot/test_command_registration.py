"""Command registration assertions for Phase 3."""

from __future__ import annotations

from bot.modules.client import create_bot
from shared.runtime.settings import RuntimeMode


def test_phase3_registers_exact_command_names() -> None:
    bot = create_bot(
        runtime_mode=RuntimeMode.production,
        development_guild_id="555555555555555555",
    )
    names = sorted(cmd.name for cmd in bot.tree.get_commands())
    assert names == ["config", "suggest"]
    assert "quick-battle" not in names


def test_command_callbacks_expose_interaction_only() -> None:
    bot = create_bot(runtime_mode=RuntimeMode.production)
    for cmd in bot.tree.get_commands():
        params = list(cmd.params) if hasattr(cmd, "params") else []
        # discord.app_commands.Command stores parameters excluding interaction
        assert all(getattr(p, "name", None) != "ctx" for p in params)
