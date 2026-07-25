"""Minimal /config command wiring test."""

from __future__ import annotations

from bot.modules.commands.config import handle_config_command


def test_handler_exported() -> None:
    assert callable(handle_config_command)
