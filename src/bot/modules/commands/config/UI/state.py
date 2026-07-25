"""In-memory staged /config draft (UI-layer; business Apply lives in P3-04)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ConfigDraft:
    language: str = "en"
    staged_language: str | None = None
    api_key_configured: bool = False
    staged_api_key: str | None = None
    model: str = ""
    staged_model: str | None = None
    model_options: list[tuple[str, str]] = field(default_factory=list)  # (value, label)
    models_truncated: bool = False
    models_total: int = 0
    webhook_configured: bool = False
    staged_webhook: str | None = None
    enabled: bool = False
    applied_summary: str | None = None
    rejected_summary: str | None = None

    @property
    def effective_language(self) -> str:
        return self.staged_language or self.language

    @property
    def effective_model(self) -> str:
        return self.staged_model if self.staged_model is not None else self.model

    @property
    def has_usable_key(self) -> bool:
        return bool(self.staged_api_key) or self.api_key_configured

    @property
    def api_key_status(self) -> str:
        if self.staged_api_key is not None:
            return "staged"
        if self.api_key_configured:
            return "configured"
        return "missing"

    @property
    def webhook_status(self) -> str:
        if self.staged_webhook is not None:
            return "staged"
        if self.webhook_configured:
            return "configured"
        return "missing"


__all__ = ["ConfigDraft"]
