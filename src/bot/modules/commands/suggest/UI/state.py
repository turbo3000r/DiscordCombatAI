"""In-memory /suggest draft."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SuggestionDraft:
    type_value: str | None = None
    category_values: list[str] = field(default_factory=list)
    type_options: list[tuple[str, str]] = field(default_factory=list)  # value, label
    category_options: list[tuple[str, str]] = field(default_factory=list)
    title: str | None = None
    details: str | None = None

    @property
    def can_open_modal(self) -> bool:
        return bool(self.type_value) and bool(self.category_values)


__all__ = ["SuggestionDraft"]
