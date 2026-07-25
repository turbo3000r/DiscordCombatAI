"""Suggest catalog parser tests."""

from __future__ import annotations

import pytest

from bot.modules.commands.suggest.service.category_catalog import (
    CatalogError,
    parse_suggestion_catalog,
)


def test_parse_valid_catalog() -> None:
    snap = parse_suggestion_catalog(
        {
            "types": [{"value": "bug", "label": "Bug"}],
            "categories": [{"value": "ui", "label": "UI"}],
        }
    )
    assert snap.types[0] == ("bug", "Bug")


def test_parse_empty_fails() -> None:
    with pytest.raises(CatalogError):
        parse_suggestion_catalog({"types": [], "categories": [{"value": "a", "label": "A"}]})
