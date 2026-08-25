"""Quick-battle session models and input normalization."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SETTINGS = (
    "realistic",
    "realistic-urban",
    "realistic-nature",
    "dreamcore",
    "unpredictable-realistic",
    "unpredictable-dreamcore",
    "unpredictable-funny",
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MENTION_EVERYONE = re.compile(r"@(?:everyone|here)\b", re.IGNORECASE)


class SessionPhase(StrEnum):
    lobby = "lobby"
    environment_collect = "environment_collect"
    environment_progress = "environment_progress"
    environment_ballot = "environment_ballot"
    fighter_collect = "fighter_collect"
    battle_progress = "battle_progress"
    delivered = "delivered"
    aborted = "aborted"
    timed_out = "timed_out"
    failed = "failed"
    dispatch_failed = "dispatch_failed"
    task_timeout = "task_timeout"
    hard_stopped = "hard_stopped"


class AdmissionError(StrEnum):
    guild_busy = "commands.quick-battle.denied_busy_guild"
    user_busy = "commands.quick-battle.denied_busy_user"
    invoker_cooldown = "commands.quick-battle.denied_invoker_cooldown"
    guild_cooldown = "commands.quick-battle.denied_guild_cooldown"
    ai_busy = "commands.quick-battle.denied_ai_busy"


@dataclass(frozen=True, slots=True)
class Participant:
    user_id: str
    display_name: str
    joined_at: float
    available: bool = True


@dataclass(frozen=True, slots=True)
class FighterSubmission:
    user_id: str
    fighter_name: str
    description: str
    strategy: str | None


@dataclass
class Session:
    session_id: str
    guild_id: str
    channel_id: str
    owner_id: str
    invoker_id: str
    locale: str
    setting: str
    custom_environment: bool
    lobby_timeout_sec: int
    workflow_id: str
    api_key: str = ""
    model: str = ""
    phase: SessionPhase = SessionPhase.lobby
    participants: dict[str, Participant] = field(default_factory=dict)
    frozen_roster: tuple[Participant, ...] | None = None
    environment_texts: dict[str, str] = field(default_factory=dict)
    fighter_submissions: dict[str, FighterSubmission] = field(default_factory=dict)
    approvals: dict[str, bool] = field(default_factory=dict)
    decline_comments: dict[str, str] = field(default_factory=dict)
    current_environment: dict[str, Any] | None = None
    expected_task_id: str | None = None
    expected_graph: str | None = None
    expected_revision: int = 0
    environment_attempts: int = 0
    holding_ai_slot: bool = False
    terminal: bool = False
    cleanup_done: bool = False
    lobby_message_id: str | None = None
    active_message_id: str | None = None
    progress_message_id: str | None = None
    created_at: float = 0.0
    phase_deadline: float | None = None
    last_story: str | None = None
    last_winners: list[str] = field(default_factory=list)
    revoke_on_cancel: bool = False

    def roster(self) -> tuple[Participant, ...]:
        if self.frozen_roster is not None:
            return self.frozen_roster
        return tuple(sorted(self.participants.values(), key=lambda item: item.joined_at))

    def available_members(self) -> tuple[Participant, ...]:
        return tuple(item for item in self.roster() if item.available)

    def roster_ids(self) -> tuple[str, ...]:
        return tuple(participant.user_id for participant in self.roster())

    def required_approvals(self) -> int:
        return max(1, math.ceil(0.70 * len(self.roster())))


def normalize_user_text(value: str, *, min_length: int, max_length: int) -> str:
    text = unicodedata.normalize("NFC", (value or "").strip())
    if _CONTROL.search(text) or _MENTION_EVERYONE.search(text):
        raise ValueError("invalid user text")
    if not (min_length <= len(text) <= max_length):
        raise ValueError("invalid user text length")
    return text


__all__ = [
    "AdmissionError",
    "FighterSubmission",
    "Participant",
    "SETTINGS",
    "Session",
    "SessionPhase",
    "normalize_user_text",
]
