"""Shared Azure client/configuration helpers."""

from .configs.credential import get_credential
from .configs.settings import AzureSettings, load_azure_settings
from .errors import AzurePermanentError, AzureTransientError, classify_azure_error
from .lifecycle import close_all_resources, register_resource

__all__ = [
    "AzurePermanentError",
    "AzureSettings",
    "AzureTransientError",
    "classify_azure_error",
    "close_all_resources",
    "get_credential",
    "load_azure_settings",
    "register_resource",
]
