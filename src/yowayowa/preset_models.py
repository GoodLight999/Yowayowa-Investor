from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PresetKind = Literal["chart", "screener", "compare"]


class ResearchPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: PresetKind
    payload: dict[str, Any]

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Preset name cannot be blank")
        return normalized


class ResearchPreset(BaseModel):
    id: int
    name: str
    kind: PresetKind
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime
