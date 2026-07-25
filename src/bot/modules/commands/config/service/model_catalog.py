"""Google model catalog listing and Apply probe (off event loop)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ProviderFailureKind(StrEnum):
    invalid_key = "invalid_key"
    unavailable_model = "unavailable_model"
    rate_limit = "rate_limit"
    transient_service = "transient_service"


@dataclass(frozen=True, slots=True)
class ModelListResult:
    options: list[tuple[str, str]]
    total: int
    truncated: bool


class ProviderError(Exception):
    def __init__(self, kind: ProviderFailureKind, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


def _model_id(model: Any) -> str:
    return str(getattr(model, "name", None) or getattr(model, "id", None) or model)


def _supports_generate_content(model: Any) -> bool:
    actions = getattr(model, "supported_actions", None) or getattr(
        model, "supportedActions", None
    )
    if actions is None:
        return "generatecontent" in _model_id(model).lower()
    return any("generatecontent" in str(a).lower() for a in actions)


def _classify_provider_error(exc: BaseException) -> ProviderFailureKind:
    text = f"{type(exc).__name__} {exc}".lower()
    if "401" in text or "403" in text or "api key" in text or "invalid" in text and "key" in text:
        return ProviderFailureKind.invalid_key
    if "429" in text or "rate" in text:
        return ProviderFailureKind.rate_limit
    if "404" in text or "not found" in text or "unavailable" in text:
        return ProviderFailureKind.unavailable_model
    return ProviderFailureKind.transient_service


def list_gemini_models(api_key: str) -> ModelListResult:
    """Blocking Google list — call only via asyncio.to_thread."""
    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        try:
            pager = client.models.list()
            models = list(pager)
        except Exception as exc:  # noqa: BLE001
            kind = _classify_provider_error(exc)
            logger.warning("model list failed kind=%s", kind.value)
            raise ProviderError(kind=kind, message=type(exc).__name__) from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    filtered: dict[str, str] = {}
    for model in models:
        model_id = _model_id(model)
        short = model_id.split("/")[-1]
        if "gemini" not in short.lower():
            continue
        if not _supports_generate_content(model):
            continue
        filtered[short] = short
    ordered = sorted(filtered.items(), key=lambda item: item[0])
    total = len(ordered)
    capped = ordered[:25]
    return ModelListResult(options=capped, total=total, truncated=total > 25)


def probe_model(api_key: str, model: str) -> None:
    """Minimal generation probe — call only via asyncio.to_thread."""
    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        try:
            client.models.generate_content(model=model, contents="ping")
        except Exception as exc:  # noqa: BLE001
            kind = _classify_provider_error(exc)
            logger.warning("model probe failed kind=%s", kind.value)
            raise ProviderError(kind=kind, message=type(exc).__name__) from exc
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


__all__ = [
    "ModelListResult",
    "ProviderError",
    "ProviderFailureKind",
    "list_gemini_models",
    "probe_model",
]
