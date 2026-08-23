"""Shared, graph-internal contracts for Phase 4 LangGraph nodes."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


class ModificationRequest(BaseModel):
    """A player request or a Validator-authored correction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    instruction: str = Field(min_length=1, max_length=1000)
    origin: Literal["player", "validator"]
    reason: str | None = Field(default=None, max_length=240)

    @field_validator("instruction", "reason")
    @classmethod
    def normalise_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalise_text(value)

    @model_validator(mode="after")
    def validate_reason(self) -> ModificationRequest:
        if self.origin == "player" and self.reason is not None:
            raise ValueError("player ModificationRequest must have reason=null")
        if self.origin == "validator" and self.reason is None:
            raise ValueError("validator ModificationRequest must include a reason")
        return self


class ValidatorVerdict(BaseModel):
    """The shared Validator output, with enforced valid/invalid invariants."""

    model_config = ConfigDict(extra="forbid", strict=True)

    is_valid: bool
    issues: list[str] = Field(max_length=8)
    fix_request: ModificationRequest | None = None

    @field_validator("issues")
    @classmethod
    def validate_issues(cls, value: list[str]) -> list[str]:
        result = [_normalise_text(issue) for issue in value]
        if any(not issue or len(issue) > 240 for issue in result):
            raise ValueError("issues must contain 1..240 character non-blank strings")
        return result

    @model_validator(mode="after")
    def validate_invariants(self) -> ValidatorVerdict:
        if self.is_valid:
            if self.issues or self.fix_request is not None:
                raise ValueError("valid verdict must have issues=[] and fix_request=null")
        elif not self.issues or self.fix_request is None:
            raise ValueError("invalid verdict must include issues and fix_request")
        elif self.fix_request.origin != "validator":
            raise ValueError("invalid verdict fix_request must originate from validator")
        return self


class AttemptRecord(BaseModel):
    """One candidate's pre- and post-validation lifecycle record."""

    model_config = ConfigDict(extra="forbid", strict=True)

    attempt_index: int = Field(ge=0, le=3)
    candidate: Any
    source: Literal["initial", "fixer"]
    validator_verdict: ValidatorVerdict | None = None


class DeciderSelection(BaseModel):
    """One bounded Decider choice."""

    model_config = ConfigDict(extra="forbid", strict=True)

    selected_attempt_index: int = Field(ge=0, le=3)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalise_reason(cls, value: str) -> str:
        return _normalise_text(value)


class GraphExecutionContext(BaseModel):
    """Per-task execution context; credentials never come from global configuration."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1)
    graph: Literal["environment", "battle"]
    trace_id: str = Field(min_length=1)
    guild_id: str = Field(min_length=1)
    api_key: SecretStr
    model: str = Field(min_length=1)
    executing_node: str = Field(min_length=1)
    attempt_index: int | None = Field(default=None, ge=0, le=3)


class NodeExecutionError(RuntimeError):
    """A graph failure attributed to the node that was executing."""

    def __init__(self, node: str, reason: str) -> None:
        self.node = node
        self.reason = reason
        super().__init__(f"{node}: {reason}")


def append_unvalidated_attempt(
    attempts: Sequence[AttemptRecord], *, candidate: Any, source: Literal["initial", "fixer"]
) -> list[AttemptRecord]:
    """Append a candidate in its required nullable-before-validation state."""
    if len(attempts) >= 4:
        raise ValueError("attempt pool cannot exceed four records")
    return [
        *attempts,
        AttemptRecord(
            attempt_index=len(attempts),
            candidate=candidate,
            source=source,
            validator_verdict=None,
        ),
    ]


def fill_attempt_verdict(
    attempts: Sequence[AttemptRecord], verdict: ValidatorVerdict
) -> list[AttemptRecord]:
    """Fill exactly the latest unvalidated record, once."""
    if not attempts or attempts[-1].validator_verdict is not None:
        raise ValueError("latest attempt must exist and be unvalidated")
    latest = attempts[-1].model_copy(update={"validator_verdict": verdict})
    return [*attempts[:-1], latest]


def validate_decider_pool(attempts: Sequence[AttemptRecord]) -> None:
    """Require the bounded, fully validated pool accepted by Decider."""
    if not 1 <= len(attempts) <= 4:
        raise ValueError("Decider requires one to four attempts")
    indexes = [attempt.attempt_index for attempt in attempts]
    if indexes != list(range(len(attempts))):
        raise ValueError("attempt indexes must be contiguous from zero")
    if any(attempt.validator_verdict is None for attempt in attempts):
        raise ValueError("Decider requires every attempt to have a validator verdict")


__all__ = [
    "AttemptRecord",
    "DeciderSelection",
    "GraphExecutionContext",
    "ModificationRequest",
    "NodeExecutionError",
    "ValidatorVerdict",
    "append_unvalidated_attempt",
    "fill_attempt_verdict",
    "validate_decider_pool",
]
