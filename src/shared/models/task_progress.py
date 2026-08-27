from __future__ import annotations

from enum import StrEnum

from .base import SchemaVersionedModel, UTCDateTime, UUIDString


class TaskPhase(StrEnum):
    queued = "queued"
    launching = "launching"
    composing = "composing"
    refining = "refining"
    finishing = "finishing"


class TaskProgressMessage(SchemaVersionedModel):
    schema_version: int = 1
    task_id: UUIDString
    graph: str
    phase: TaskPhase
    timestamp: UTCDateTime
    attempt: int | None = None


def parse_task_progress_message(
    data: object, *, reader_version: int | None = None
) -> TaskProgressMessage:
    """Parse a Mosquitto progress tick from JSON text or an already-decoded mapping."""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return TaskProgressMessage.parse_wire_json(bytes(data), reader_version=reader_version)
    if isinstance(data, str):
        return TaskProgressMessage.parse_wire_json(data, reader_version=reader_version)
    return TaskProgressMessage.parse_wire(data, reader_version=reader_version)


__all__ = ["TaskPhase", "TaskProgressMessage", "parse_task_progress_message"]
