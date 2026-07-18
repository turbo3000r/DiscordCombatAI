from __future__ import annotations

from pathlib import Path

from shared.models.localization import LanguageCode, resolve_ui_lang_file, to_ai_language_locale


def test_language_mapping() -> None:
    assert to_ai_language_locale(LanguageCode.en) == "en"
    assert to_ai_language_locale(LanguageCode.es) == "es"
    assert to_ai_language_locale(LanguageCode.ua) == "uk-UA"


def test_ui_lang_resolution() -> None:
    available = ["es.json", "en.json"]
    assert resolve_ui_lang_file("es", available) == Path("es.json")
    assert resolve_ui_lang_file("de-DE", ["de.json", "en.json"]) == Path("de.json")
    assert resolve_ui_lang_file("zz", available) == Path("en.json")
