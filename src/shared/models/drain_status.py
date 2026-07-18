from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .base import SchemaVersionedModel, UTCDateTime, UUIDString


class DrainProgress(SchemaVersionedModel):
    schema_version: int = 1
    node_id: str
    leadership_term: UUIDString
    in_flight_workflows: int
    observed_at: UTCDateTime


class PauseAck(SchemaVersionedModel):
    schema_version: int = 1
    node_id: str
    paused_at: UTCDateTime


class AiWorkerDesiredStateValue(StrEnum):
    running = "running"
    paused = "paused"


class AiWorkerDesiredState(SchemaVersionedModel):
    schema_version: int = 1
    state: AiWorkerDesiredStateValue


class UpdateAvailableMessage(SchemaVersionedModel):
    schema_version: int = 1
    type: Literal["update_available"] = "update_available"
    target_version: str


__all__ = [
    "AiWorkerDesiredState",
    "AiWorkerDesiredStateValue",
    "DrainProgress",
    "PauseAck",
    "UpdateAvailableMessage",
]
