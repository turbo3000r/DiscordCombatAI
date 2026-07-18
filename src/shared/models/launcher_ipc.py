from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import TypeAdapter

from .base import SchemaVersionedModel, UnknownSchemaVersionError, UTCDateTime, UUIDString


class UpdateReason(StrEnum):
    auto_detected = "auto_detected"
    manual = "manual"
    rollback = "rollback"
    reconcile = "reconcile"


class LauncherErrorBody(SchemaVersionedModel):
    schema_version: int = 1
    code: str
    message: str


class UpdateRequest(SchemaVersionedModel):
    schema_version: int = 1
    request_id: UUIDString
    target_version: str
    reason: UpdateReason
    requested_at: UTCDateTime
    source_node_id: str


class UpdateAcceptedResponse(SchemaVersionedModel):
    schema_version: int = 1
    request_id: UUIDString
    operation_id: UUIDString
    state: Literal["accepted"]
    target_version: str


class UpdateAlreadyCurrentResponse(SchemaVersionedModel):
    schema_version: int = 1
    request_id: UUIDString
    operation_id: UUIDString
    state: Literal["already_current"]
    target_version: str


class UpdateBusyResponse(SchemaVersionedModel):
    schema_version: int = 1
    request_id: UUIDString
    error: LauncherErrorBody
    operation_id: UUIDString
    target_version: str


class LauncherStatusResponse(SchemaVersionedModel):
    schema_version: int = 1
    state: str
    current_version: str
    previous_version: str | None
    operation_id: UUIDString | None
    target_version: str | None


class HealthStatus(StrEnum):
    alive = "alive"
    initializing = "initializing"


class HealthResponse(SchemaVersionedModel):
    schema_version: int = 1
    status: HealthStatus
    version: str
    instance_id: UUIDString
    started_at: UTCDateTime


LauncherUpdateResponse = UpdateAcceptedResponse | UpdateAlreadyCurrentResponse | UpdateBusyResponse


def parse_update_request(data: Any, *, reader_version: int | None = None) -> UpdateRequest:
    return UpdateRequest.parse_wire(data, reader_version=reader_version)


def parse_launcher_update_response(
    data: Any, *, reader_version: int | None = None
) -> LauncherUpdateResponse:
    adapter: TypeAdapter[LauncherUpdateResponse] = TypeAdapter(LauncherUpdateResponse)
    model = adapter.validate_python(data)
    if reader_version is None:
        if model.schema_version != UpdateRequest.current_schema_version():
            raise UnknownSchemaVersionError(
                model.schema_version, UpdateRequest.current_schema_version()
            )
    elif abs(model.schema_version - reader_version) > 1:
        raise UnknownSchemaVersionError(
            model.schema_version, UpdateRequest.current_schema_version(), reader_version
        )
    return model


__all__ = [
    "HealthResponse",
    "HealthStatus",
    "LauncherErrorBody",
    "LauncherStatusResponse",
    "LauncherUpdateResponse",
    "UpdateAcceptedResponse",
    "UpdateAlreadyCurrentResponse",
    "UpdateBusyResponse",
    "UpdateReason",
    "UpdateRequest",
    "parse_launcher_update_response",
    "parse_update_request",
]
