"""Tests for Bot localization handler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.localization.handler import (
    REQUIRED_PHASE3_KEYS,
    REQUIRED_PHASE5_KEYS,
    LocalizationError,
    load_localization,
)


def test_load_localization_validates_required_keys() -> None:
    handler = load_localization()
    for key in (*REQUIRED_PHASE3_KEYS, *REQUIRED_PHASE5_KEYS):
        assert handler.t(key, locale="en")
        assert not handler.t(key, locale="en").startswith("[")


def test_fallback_chain_and_uk_alias() -> None:
    handler = load_localization()
    assert handler.resolve_locale_key(interaction_locale="de-DE") == "en"
    assert handler.resolve_locale_key(interaction_locale="es-ES") == "es"
    assert handler.resolve_locale_key(interaction_locale="uk") == "ua"
    assert handler.resolve_locale_key(guild_language="ua") == "ua"
    assert handler.resolve_locale_key(guild_language="zz") == "en"


def test_missing_non_english_falls_back_to_english(tmp_path: Path) -> None:
    lang_dir = tmp_path / "lang"
    lang_dir.mkdir()
    source_dir = Path(__file__).resolve().parents[3] / "src/bot/localization/lang"
    for code in ("en", "es", "ua"):
        data = json.loads((source_dir / f"{code}.json").read_text(encoding="utf-8"))
        if code != "en":
            data["errors"].pop("denied_permission", None)
        (lang_dir / f"{code}.json").write_text(json.dumps(data), encoding="utf-8")

    handler = load_localization(lang_dir)
    text = handler.t("errors.denied_permission", locale="es")
    assert "permission" in text.lower()


def test_missing_english_key_returns_marker(caplog: pytest.LogCaptureFixture) -> None:
    handler = load_localization()
    with caplog.at_level("ERROR"):
        text = handler.t("commands.config.does_not_exist")
    assert text == "[commands.config.does_not_exist]"
    assert any("missing localization key" in r.message for r in caplog.records)


def test_placeholder_parity_enforced(tmp_path: Path) -> None:
    lang_dir = tmp_path / "lang"
    lang_dir.mkdir()
    for code in ("en", "es", "ua"):
        source_dir = (
            Path(__file__).resolve().parents[3] / "src/bot/localization/lang"
        )
        original = source_dir / f"{code}.json"
        data = json.loads(original.read_text(encoding="utf-8"))
        if code == "es":
            data["commands"]["suggest"]["success"] = "Gracias {wrong}"
        (lang_dir / f"{code}.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LocalizationError, match="placeholder parity"):
        load_localization(lang_dir)


def test_format_requires_exact_placeholders() -> None:
    handler = load_localization()
    with pytest.raises(LocalizationError, match="placeholder mismatch"):
        handler.t("commands.suggest.success", locale="en", ticket_uid="ABC", extra="x")
    text = handler.t("commands.suggest.success", locale="en", ticket_uid="ABC")
    assert "ABC" in text
    # Without variables, return the template unchanged (startup validates parity).
    assert "{ticket_uid}" in handler.t("commands.suggest.success", locale="en")
