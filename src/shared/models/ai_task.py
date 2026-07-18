from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .base import SchemaVersionedModel, UnknownSchemaVersionError, UTCDateTime, UUIDString

FAILED_PSEUDO_NODES = {
    "invalid_input",
    "worker_terminated",
    "bot_stall_timeout",
    "bot_task_timeout",
}


class EnvironmentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    tags: list[str]
    setting: str


class FighterState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str
    player_nick: str
    fighter_name: str
    description: str
    strategy: str | None = None


class _AiTaskBase(SchemaVersionedModel):
    schema_version: int = 1
    task_id: UUIDString
    created_at: UTCDateTime
    setting: str
    language_locale: str
    trace_id: str
    guild_id: str
    api_key: str
    model: str


class EnvironmentAiTaskEnvelope(_AiTaskBase):
    graph: Literal["environment"] = "environment"
    input_type: Literal["initial", "revision"]
    raw_input: list[str]
    existing_environment: EnvironmentState | None = None
    max_enhancer_retries: int

    @model_validator(mode="after")
    def _validate_environment_shape(self) -> EnvironmentAiTaskEnvelope:
        if self.input_type == "initial" and self.existing_environment is not None:
            raise ValueError("existing_environment must be null for initial input")
        if self.input_type == "revision" and self.existing_environment is None:
            raise ValueError("existing_environment is required for revision input")
        return self


class BattleAiTaskEnvelope(_AiTaskBase):
    graph: Literal["battle"] = "battle"
    fighters: list[FighterState]
    environment: EnvironmentState
    random_winner_mode: bool
    max_modifier_retries: int


AiTaskEnvelope = Annotated[
    EnvironmentAiTaskEnvelope | BattleAiTaskEnvelope, Field(discriminator="graph")
]


class AiTaskResultSuccess(SchemaVersionedModel):
    schema_version: int = 1
    task_id: UUIDString
    graph: Literal["environment", "battle"]
    status: Literal["success"] = "success"
    result: dict[str, Any]
    completed_at: UTCDateTime


class AiTaskResultFailed(SchemaVersionedModel):
    schema_version: int = 1
    task_id: UUIDString
    graph: Literal["environment", "battle"]
    status: Literal["failed"] = "failed"
    node: str
    reason: str
    completed_at: UTCDateTime

    @model_validator(mode="after")
    def _validate_node(self) -> AiTaskResultFailed:
        if not self.node:
            raise ValueError("node is required")
        return self


AiTaskResult = Annotated[AiTaskResultSuccess | AiTaskResultFailed, Field(discriminator="status")]


def parse_ai_task_envelope(data: Any, *, reader_version: int | None = None) -> AiTaskEnvelope:
    adapter: TypeAdapter[AiTaskEnvelope] = TypeAdapter(AiTaskEnvelope)
    model = adapter.validate_python(data)
    if reader_version is None:
        if model.schema_version != EnvironmentAiTaskEnvelope.current_schema_version():
            raise UnknownSchemaVersionError(
                model.schema_version, EnvironmentAiTaskEnvelope.current_schema_version()
            )
    elif abs(model.schema_version - reader_version) > 1:
        raise UnknownSchemaVersionError(
            model.schema_version,
            EnvironmentAiTaskEnvelope.current_schema_version(),
            reader_version,
        )
    return model


def parse_ai_task_result(data: Any, *, reader_version: int | None = None) -> AiTaskResult:
    adapter: TypeAdapter[AiTaskResult] = TypeAdapter(AiTaskResult)
    model = adapter.validate_python(data)
    if reader_version is None:
        if model.schema_version != AiTaskResultSuccess.current_schema_version():
            raise UnknownSchemaVersionError(
                model.schema_version, AiTaskResultSuccess.current_schema_version()
            )
    elif abs(model.schema_version - reader_version) > 1:
        raise UnknownSchemaVersionError(
            model.schema_version, AiTaskResultSuccess.current_schema_version(), reader_version
        )
    return model


def build_task_id() -> str:
    return str(uuid4())


__all__ = [
    "AiTaskEnvelope",
    "AiTaskResult",
    "AiTaskResultFailed",
    "AiTaskResultSuccess",
    "BattleAiTaskEnvelope",
    "EnvironmentAiTaskEnvelope",
    "EnvironmentState",
    "FAILED_PSEUDO_NODES",
    "FighterState",
    "build_task_id",
    "parse_ai_task_envelope",
    "parse_ai_task_result",
]
