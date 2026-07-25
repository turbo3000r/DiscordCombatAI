"""Canonical Bot UI localization loader and resolver."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "es", "ua")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Phase 3 required keys — exact copy remains P2; presence and placeholder parity are required.
REQUIRED_PHASE3_KEYS: tuple[str, ...] = (
    "commands.config.description",
    "commands.suggest.description",
    "errors.denied_permission",
    "errors.denied_guild",
    "errors.denied_drain",
    "errors.denied_runtime_filter",
    "errors.denied_enabled",
    "errors.denied_development_dm",
    "errors.error_transient",
    "errors.error_permanent",
    "errors.error_unexpected",
    "errors.foreign_user",
    "commands.config.permission_denied",
    "commands.config.cosmos_transient",
    "commands.config.cosmos_permanent",
    "commands.config.listing_failure",
    "commands.config.truncation_notice",
    "commands.config.apply_applied",
    "commands.config.apply_rejected",
    "commands.config.invalid_webhook",
    "commands.config.invalid_key",
    "commands.config.unavailable_model",
    "commands.config.rate_limit",
    "commands.config.transient_service",
    "commands.config.enabled_gate_warning",
    "commands.config.timeout",
    "commands.config.cancelled",
    "commands.config.apply_button",
    "commands.config.cancel_button",
    "commands.suggest.catalog_unavailable",
    "commands.suggest.validation_error",
    "commands.suggest.write_transient",
    "commands.suggest.write_permanent",
    "commands.suggest.success",
    "commands.suggest.development_dm_rejected",
    "commands.suggest.timeout",
    "commands.suggest.open_modal",
    "commands.suggest.notification_dm",
)


class LocalizationError(RuntimeError):
    """Fatal localization startup failure."""


def _default_lang_dir() -> Path:
    return Path(__file__).resolve().parent / "lang"


def _lookup(data: dict[str, Any], key_path: str) -> Any | None:
    node: Any = data
    for part in key_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


class LocalizationHandler:
    def __init__(
        self,
        *,
        locales: dict[str, dict[str, Any]],
        default_locale: str = DEFAULT_LOCALE,
    ) -> None:
        self._locales = locales
        self.default_locale = default_locale

    def resolve_locale_key(
        self,
        *,
        guild_language: str | None = None,
        interaction_locale: str | None = None,
    ) -> str:
        """Resolve UI locale: guild language first, else interaction locale chain → en."""
        if guild_language:
            return self._normalize_locale(guild_language)
        if interaction_locale:
            return self._normalize_locale(interaction_locale)
        return self.default_locale

    def _normalize_locale(self, value: str) -> str:
        candidate = value.strip().replace("_", "-")
        if not candidate:
            return self.default_locale
        if candidate in self._locales:
            return candidate
        primary = candidate.split("-", 1)[0]
        if primary == "uk" and "ua" in self._locales:
            return "ua"
        if primary in self._locales:
            return primary
        return self.default_locale

    def t(self, key: str, *, locale: str | None = None, **variables: Any) -> str:
        locale_key = self._normalize_locale(locale or self.default_locale)
        raw = _lookup(self._locales.get(locale_key, {}), key)
        if raw is None and locale_key != self.default_locale:
            raw = _lookup(self._locales.get(self.default_locale, {}), key)
        if raw is None:
            logger.error("missing localization key marker=%s locale=%s", key, locale_key)
            return f"[{key}]"
        if not isinstance(raw, str):
            logger.error("non-string localization key marker=%s locale=%s", key, locale_key)
            return f"[{key}]"
        if not variables:
            return raw
        expected = _placeholders(raw)
        provided = set(variables)
        missing = expected - provided
        extra = provided - expected
        if missing or extra:
            raise LocalizationError(
                f"placeholder mismatch for {key!r}: missing={sorted(missing)} extra={sorted(extra)}"
            )
        return raw.format(**variables)


def load_localization(lang_dir: Path | None = None) -> LocalizationHandler:
    directory = lang_dir or _default_lang_dir()
    if not directory.is_dir():
        raise LocalizationError(f"localization directory missing: {directory}")

    locales: dict[str, dict[str, Any]] = {}
    for code in SUPPORTED_LOCALES:
        path = directory / f"{code}.json"
        if not path.is_file():
            raise LocalizationError(f"required locale file missing: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalizationError(f"locale file is not valid JSON: {path}") from exc
        if not isinstance(data, dict):
            raise LocalizationError(f"locale root must be an object: {path}")
        locales[code] = data

    english = locales[DEFAULT_LOCALE]
    for key in REQUIRED_PHASE3_KEYS:
        en_value = _lookup(english, key)
        if not isinstance(en_value, str) or not en_value.strip():
            raise LocalizationError(f"English locale missing required key: {key}")
        en_placeholders = _placeholders(en_value)
        for code in ("es", "ua"):
            other = _lookup(locales[code], key)
            if other is None:
                # Non-English may fall back at runtime; presence preferred but not fatal
                # beyond English completeness. Still require placeholder parity when present.
                continue
            if not isinstance(other, str):
                raise LocalizationError(f"{code} locale non-string for key: {key}")
            if _placeholders(other) != en_placeholders:
                raise LocalizationError(
                    f"placeholder parity failure for {key!r} between en and {code}"
                )

    return LocalizationHandler(locales=locales)


__all__ = [
    "DEFAULT_LOCALE",
    "LocalizationError",
    "LocalizationHandler",
    "REQUIRED_PHASE3_KEYS",
    "SUPPORTED_LOCALES",
    "load_localization",
]
