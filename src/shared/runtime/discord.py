"""Discord snowflake helpers for runtime validation."""

from __future__ import annotations

import re

_SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")


def is_valid_snowflake(value: str) -> bool:
    return bool(_SNOWFLAKE_RE.fullmatch(value))


def require_snowflake(value: str, *, field_name: str) -> str:
    if not is_valid_snowflake(value):
        raise ValueError(f"{field_name} must be a Discord snowflake (17-20 digits)")
    return value


__all__ = ["is_valid_snowflake", "require_snowflake"]
