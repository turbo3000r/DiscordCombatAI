"""Command-layer exception taxonomy for ProcessCommand."""

from __future__ import annotations


class CommandError(Exception):
    """Base command-layer error."""


class CommandDenial(CommandError):
    """Expected access denial with a localization key."""

    def __init__(self, decision: str, message_key: str) -> None:
        self.decision = decision
        self.message_key = message_key
        super().__init__(decision)


class CommandValidationError(CommandError):
    """Expected validation failure with a localization key."""

    def __init__(self, message_key: str, *, decision: str = "denied_validation") -> None:
        self.decision = decision
        self.message_key = message_key
        super().__init__(message_key)


class CommandTransientError(CommandError):
    """Retryable repository/network failure."""

    def __init__(self, message_key: str = "errors.error_transient") -> None:
        self.decision = "error_transient"
        self.message_key = message_key
        super().__init__(message_key)


class CommandPermanentError(CommandError):
    """Non-retryable repository/auth failure."""

    def __init__(self, message_key: str = "errors.error_permanent") -> None:
        self.decision = "error_permanent"
        self.message_key = message_key
        super().__init__(message_key)


__all__ = [
    "CommandDenial",
    "CommandError",
    "CommandPermanentError",
    "CommandTransientError",
    "CommandValidationError",
]
