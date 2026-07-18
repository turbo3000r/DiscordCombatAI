from __future__ import annotations

import re

from shared.security.redact import redact_sensitive


class AzurePermanentError(Exception):
    """Non-retryable Azure failure."""

    def __init__(
        self, message: str, *, operation: str | None = None, status_code: int | None = None
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(redact_sensitive(message))


class AzureTransientError(Exception):
    """Retryable Azure failure."""

    def __init__(
        self, message: str, *, operation: str | None = None, status_code: int | None = None
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(redact_sensitive(message))


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _message(exc: BaseException) -> str:
    message = " ".join(str(part) for part in exc.args if part)
    if not message:
        message = exc.__class__.__name__
    return message


def _is_azure_sdk_error(exc: BaseException) -> bool:
    try:
        from azure.core.exceptions import AzureError
    except Exception:
        return False
    return isinstance(exc, AzureError)


def classify_azure_error(
    exc: BaseException, *, operation: str | None = None
) -> AzurePermanentError | AzureTransientError:
    message = _message(exc)
    status_code = _status_code(exc)
    text = message.lower()

    permanent_signals = (
        status_code in {401, 403},
        bool(re.search(r"\b401\b|\b403\b", text)),
        "aadsts" in text,
        "unauthorized" in text,
        "forbidden" in text,
        "permission" in text and "denied" in text,
        "authentication" in text and "failed" in text,
    )
    if any(permanent_signals):
        return AzurePermanentError(message, operation=operation, status_code=status_code)

    transient_signals = (
        status_code in {408, 412, 429},
        status_code is not None and 500 <= status_code <= 599,
        "timeout" in text,
        "timed out" in text,
        "temporarily unavailable" in text,
        "service unavailable" in text,
        "connection reset" in text,
        "network" in text,
        "throttl" in text,
        "too many requests" in text,
        _is_azure_sdk_error(exc),
    )
    if any(transient_signals):
        return AzureTransientError(message, operation=operation, status_code=status_code)

    return AzureTransientError(message, operation=operation, status_code=status_code)


__all__ = [
    "AzurePermanentError",
    "AzureTransientError",
    "classify_azure_error",
]
