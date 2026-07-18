from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .base import SchemaVersionedModel, UTCDateTime, UUIDString


class BotDesiredStateValue(StrEnum):
    inactive = "inactive"
    draining = "draining"
    stopped = "stopped"


class BotDesiredState(SchemaVersionedModel):
    schema_version: int = 1
    state: BotDesiredStateValue
    head_instance_id: UUIDString
    leadership_term: UUIDString | None
    command_seq: int
    reason: str
    issued_at: UTCDateTime

    @model_validator(mode="after")
    def _validate_command_seq(self) -> BotDesiredState:
        if self.command_seq < 0:
            raise ValueError("command_seq must be non-negative")
        return self


class ActivationGrantMode(StrEnum):
    active = "active"
    draining = "draining"


class ActivationGrant(SchemaVersionedModel):
    schema_version: int = 1
    grant_id: UUIDString
    node_id: str
    head_instance_id: UUIDString
    leadership_term: UUIDString
    command_seq: int
    mode: ActivationGrantMode
    ttl_sec: int
    issued_at: UTCDateTime


class ControlAck(SchemaVersionedModel):
    schema_version: int = 1
    node_id: str
    head_instance_id: UUIDString
    leadership_term: UUIDString
    command_seq: int
    state: BotDesiredStateValue
    gateway_connected: bool
    observed_at: UTCDateTime


class LeaseBlobRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: str
    name: str


class LeaderHeartbeat(SchemaVersionedModel):
    schema_version: int = 1
    type: Literal["leader_heartbeat"] = "leader_heartbeat"
    node_id: str
    head_instance_id: UUIDString
    leadership_term: UUIDString
    lease_blob: LeaseBlobRef
    issued_at: UTCDateTime
    lease_expires_at: UTCDateTime
    application_version: str


__all__ = [
    "ActivationGrant",
    "ActivationGrantMode",
    "BotDesiredState",
    "BotDesiredStateValue",
    "ControlAck",
    "LeaderHeartbeat",
    "LeaseBlobRef",
]
