"""Suggestion catalog validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    types: tuple[tuple[str, str], ...]
    categories: tuple[tuple[str, str], ...]


class CatalogError(ValueError):
    pass


def parse_suggestion_catalog(raw: Any) -> CatalogSnapshot:
    if raw is None:
        raise CatalogError("missing catalog")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="python")
    if not isinstance(raw, dict):
        raise CatalogError("malformed catalog")
    types_raw = raw.get("types") or raw.get("suggestion_types")
    cats_raw = raw.get("categories") or raw.get("suggestion_categories")
    if not isinstance(types_raw, list) or not isinstance(cats_raw, list):
        catalog = raw.get("suggestion_catalog")
        if isinstance(catalog, dict):
            types_raw = catalog.get("types")
            cats_raw = catalog.get("categories")
    if not isinstance(types_raw, list) or not isinstance(cats_raw, list):
        raise CatalogError("malformed catalog")
    if not types_raw or not cats_raw:
        raise CatalogError("empty catalog")

    def _entries(items: list[Any]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in items:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="python")
            if not isinstance(item, dict):
                raise CatalogError("malformed entry")
            value = str(item.get("value") or "").strip()
            label = str(item.get("label") or "").strip()
            if not value or not label:
                raise CatalogError("malformed entry")
            if value in seen:
                raise CatalogError("duplicate value")
            seen.add(value)
            out.append((value, label))
        if len(out) > 25:
            raise CatalogError("too many options")
        return out

    return CatalogSnapshot(types=tuple(_entries(types_raw)), categories=tuple(_entries(cats_raw)))


__all__ = ["CatalogError", "CatalogSnapshot", "parse_suggestion_catalog"]
