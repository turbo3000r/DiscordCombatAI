from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
