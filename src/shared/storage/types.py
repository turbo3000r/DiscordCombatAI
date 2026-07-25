"""Repository bundle returned by the composition-root factory."""

from __future__ import annotations

from dataclasses import dataclass

from .protocols import (
    GuildRepository,
    MetricsRepository,
    StatusRepository,
    SuggestionRepository,
)


@dataclass(frozen=True, slots=True)
class Repositories:
    guild: GuildRepository
    suggestion: SuggestionRepository
    status: StatusRepository
    metrics: MetricsRepository


__all__ = ["Repositories"]
