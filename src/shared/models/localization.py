from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path


class LanguageCode(StrEnum):
    en = "en"
    es = "es"
    ua = "ua"


def to_ai_language_locale(language: LanguageCode | str) -> str:
    code = LanguageCode(language)
    if code is LanguageCode.ua:
        return "uk-UA"
    return code.value


def resolve_ui_lang_file(
    language: LanguageCode | str,
    available_files: Iterable[str] | None = None,
) -> Path:
    code = language.value if isinstance(language, LanguageCode) else str(language)
    candidates = [code]
    if "-" in code or "_" in code:
        candidates.append(code.split("-", 1)[0].split("_", 1)[0])
    candidates.append("en")

    if available_files is None:
        return Path(f"{candidates[0]}.json")

    available = {Path(name).stem: name for name in available_files}
    for candidate in candidates:
        if candidate in available:
            return Path(available[candidate])
    return Path(available.get("en", "en.json"))


__all__ = ["LanguageCode", "resolve_ui_lang_file", "to_ai_language_locale"]
