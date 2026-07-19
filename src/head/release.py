from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import total_ordering
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from shared.models.launcher_ipc import RELEASE_TAG_PATTERN

from .clock import Clock

_RELEASE_TAG = re.compile(RELEASE_TAG_PATTERN)


@total_ordering
@dataclass(frozen=True, slots=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None = None

    @classmethod
    def parse(cls, value: str) -> SemanticVersion | None:
        normalized = value.strip()
        match = _RELEASE_TAG.fullmatch(normalized)
        if match is None:
            return None
        core, separator, prerelease = normalized[1:].partition("-")
        major, minor, patch = (int(part) for part in core.split("."))
        return cls(
            major,
            minor,
            patch,
            tuple(prerelease.split(".")) if separator else None,
        )

    @classmethod
    def parse_stable(cls, value: str) -> SemanticVersion | None:
        parsed = cls.parse(value)
        return parsed if parsed is not None and parsed.prerelease is None else None

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        for own, candidate in zip(self.prerelease, other.prerelease, strict=False):
            if own == candidate:
                continue
            own_numeric = own.isdigit()
            candidate_numeric = candidate.isdigit()
            if own_numeric and candidate_numeric:
                return int(own) < int(candidate)
            if own_numeric != candidate_numeric:
                return own_numeric
            return own < candidate
        return len(self.prerelease) < len(other.prerelease)


class GitHubRelease(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag_name: str
    draft: bool
    prerelease: bool


class ReleaseSource(Protocol):
    async def list_releases(self, repository: str) -> list[GitHubRelease]: ...

    async def close(self) -> None: ...


def select_newest_release(
    releases: list[GitHubRelease],
    *,
    current_version: str,
    include_prerelease: bool = False,
) -> GitHubRelease | None:
    current = SemanticVersion.parse(current_version)
    if current is None:
        raise ValueError("current application version must be valid semantic version")
    candidates: list[tuple[SemanticVersion, GitHubRelease]] = []
    for release in releases:
        version = SemanticVersion.parse(release.tag_name)
        if (
            version is not None
            and version > current
            and not release.draft
            and (include_prerelease or not release.prerelease)
            and (include_prerelease or version.prerelease is None)
        ):
            candidates.append((version, release))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def select_newest_stable(
    releases: list[GitHubRelease], *, current_version: str
) -> GitHubRelease | None:
    return select_newest_release(releases, current_version=current_version)


class ReleasePoller:
    def __init__(
        self,
        *,
        source: ReleaseSource,
        repository: str,
        current_version: str,
        is_leader: Callable[[], bool],
        on_release: Callable[[GitHubRelease], Awaitable[None]],
        clock: Clock,
        interval_sec: float,
        include_prerelease: bool = False,
    ) -> None:
        self._source = source
        self._repository = repository
        self._current_version = current_version
        self._is_leader = is_leader
        self._on_release = on_release
        self._clock = clock
        self._interval_sec = interval_sec
        self._include_prerelease = include_prerelease

    async def run_once(self) -> GitHubRelease | None:
        if not self._is_leader():
            return None
        release = select_newest_release(
            await self._source.list_releases(self._repository),
            current_version=self._current_version,
            include_prerelease=self._include_prerelease,
        )
        if release is not None:
            await self._on_release(release)
        return release

    async def run(self) -> None:
        while True:
            with suppress(Exception):
                await self.run_once()
            await self._clock.sleep(self._interval_sec)

    async def close(self) -> None:
        await self._source.close()


__all__ = [
    "GitHubRelease",
    "ReleasePoller",
    "ReleaseSource",
    "SemanticVersion",
    "select_newest_release",
    "select_newest_stable",
]
