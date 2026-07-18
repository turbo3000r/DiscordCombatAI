from __future__ import annotations

from shared.azure.errors import AzurePermanentError, AzureTransientError, classify_azure_error


def test_classify_permanent_error_redacts_secrets() -> None:
    error = classify_azure_error(
        RuntimeError("HTTP 401 secret=abc123 https://host/path?access_token=abc")
    )
    assert isinstance(error, AzurePermanentError)
    assert "abc123" not in str(error)
    assert "access_token" in str(error) or "<redacted" in str(error)


def test_classify_transient_error() -> None:
    error = classify_azure_error(RuntimeError("service unavailable"))
    assert isinstance(error, AzureTransientError)
