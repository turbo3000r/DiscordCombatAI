"""Bot slash-command package."""

from bot.modules.commands.context import CommandContext
from bot.modules.commands.errors import (
    CommandDenial,
    CommandError,
    CommandPermanentError,
    CommandTransientError,
    CommandValidationError,
)
from bot.modules.commands.process_command import CommandGuardService, ProcessCommand
from bot.modules.commands.registration import register_phase3_commands

__all__ = [
    "CommandContext",
    "CommandDenial",
    "CommandError",
    "CommandGuardService",
    "CommandPermanentError",
    "CommandTransientError",
    "CommandValidationError",
    "ProcessCommand",
    "register_phase3_commands",
]
