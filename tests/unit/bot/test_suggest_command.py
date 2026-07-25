"""Minimal /suggest command wiring test."""

from __future__ import annotations

from bot.modules.commands.suggest import handle_suggest_command


def test_handler_exported() -> None:
    assert callable(handle_suggest_command)
