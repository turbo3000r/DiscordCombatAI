"""Shared runtime mode selection and fail-closed guards."""

from .discord import is_valid_snowflake, require_snowflake
from .guards import RuntimeConfigurationError, assert_service_runtime
from .settings import RuntimeMode, RuntimeSettings, StorageProvider, load_runtime_settings

__all__ = [
    "RuntimeConfigurationError",
    "RuntimeMode",
    "RuntimeSettings",
    "StorageProvider",
    "assert_service_runtime",
    "is_valid_snowflake",
    "load_runtime_settings",
    "require_snowflake",
]
