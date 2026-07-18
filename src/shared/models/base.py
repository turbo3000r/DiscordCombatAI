from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Self, cast
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict

CURRENT = 1


class UnknownSchemaVersionError(ValueError):
    def __init__(
        self,
        schema_version: int,
        expected_version: int,
        reader_version: int | None = None,
    ) -> None:
        self.schema_version = schema_version
        self.expected_version = expected_version
        self.reader_version = reader_version
        message = f"Unknown schema_version {schema_version}; expected {expected_version}"
        if reader_version is not None:
            message += f" for reader {reader_version}"
        super().__init__(message)


def parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=UTC)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        dt = datetime.fromisoformat(candidate)
    else:
        raise TypeError(f"Unsupported datetime value: {type(value)!r}")

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Datetime must be timezone-aware UTC")
    return dt.astimezone(UTC)


def ensure_utc(value: datetime) -> datetime:
    return parse_utc_datetime(value)


def _validate_uuid_string(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        raise TypeError("UUID value must be a string")
    return str(UUID(value))


def _validate_snowflake_string(value: Any) -> str:
    if not isinstance(value, str):
        value = str(value)
    candidate = value.strip()
    if not candidate.isdigit():
        raise ValueError("Snowflake must contain only digits")
    snowflake = int(candidate)
    if snowflake < 0 or snowflake >= 2**64:
        raise ValueError("Snowflake is out of range")
    return cast(str, candidate)


def _validate_utc_datetime(value: Any) -> datetime:
    return ensure_utc(parse_utc_datetime(value))


UUIDString = Annotated[str, BeforeValidator(_validate_uuid_string)]
SnowflakeString = Annotated[str, BeforeValidator(_validate_snowflake_string)]
UTCDateTime = Annotated[datetime, BeforeValidator(_validate_utc_datetime)]


def web_can_read(document_version: int, reader_version: int) -> bool:
    return abs(document_version - reader_version) <= 1


class SchemaVersionedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_encoders={
            datetime: lambda value: value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        },
    )

    schema_version: int
    CURRENT_SCHEMA_VERSION: ClassVar[int] = CURRENT

    @classmethod
    def current_schema_version(cls) -> int:
        return cls.CURRENT_SCHEMA_VERSION

    @classmethod
    def _check_version(cls, model: SchemaVersionedModel, reader_version: int | None) -> None:
        version = model.schema_version
        expected_version = cls.current_schema_version()
        if reader_version is None:
            if version != expected_version:
                raise UnknownSchemaVersionError(version, expected_version)
        elif not web_can_read(version, reader_version):
            raise UnknownSchemaVersionError(version, expected_version, reader_version)

    @classmethod
    def _relaxed_model(cls) -> type[SchemaVersionedModel]:
        return cast(
            type[SchemaVersionedModel],
            type(f"{cls.__name__}AllowExtra", (cls,), {"model_config": ConfigDict(extra="allow")}),
        )

    @classmethod
    def parse_wire(
        cls,
        data: Any,
        *,
        allow_extra: bool = False,
        reader_version: int | None = None,
    ) -> Self:
        target = cls._relaxed_model() if allow_extra else cls
        model = cast(Self, target.model_validate(data))
        cls._check_version(model, reader_version)
        return model

    @classmethod
    def parse_wire_json(
        cls,
        data: str | bytes,
        *,
        allow_extra: bool = False,
        reader_version: int | None = None,
    ) -> Self:
        target = cls._relaxed_model() if allow_extra else cls
        model = cast(Self, target.model_validate_json(data))
        cls._check_version(model, reader_version)
        return model


__all__ = [
    "CURRENT",
    "SchemaVersionedModel",
    "SnowflakeString",
    "UTCDateTime",
    "UUIDString",
    "UnknownSchemaVersionError",
    "ensure_utc",
    "parse_utc_datetime",
    "web_can_read",
]
