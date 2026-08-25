from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Operation, Provenance


class ResearchSection(StrEnum):
    PROFILE = "profile"
    ANALYST = "analyst"
    OWNERSHIP = "ownership"
    INSIDERS = "insiders"
    ESG = "esg"
    ACTIONS = "actions"
    FILINGS = "filings"
    FUND = "fund"


class CompanyResearch(BaseModel):
    symbol: str
    sections: dict[str, Any]
    errors: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance


class OptionChainSnapshot(BaseModel):
    symbol: str
    expirations: list[str]
    expiration: str | None = None
    underlying: dict[str, Any] = Field(default_factory=dict)
    calls: list[dict[str, Any]] = Field(default_factory=list)
    puts: list[dict[str, Any]] = Field(default_factory=list)
    provenance: Provenance


MarketScreenOperator = Literal["eq", "is-in", "btwn", "gt", "lt", "gte", "lte"]


class MarketScreenFilter(BaseModel):
    field: str = Field(min_length=1, max_length=120)
    operator: MarketScreenOperator
    value: str | float | int | list[str] | list[float] | list[int]


class MarketScreenRequest(BaseModel):
    filters: list[MarketScreenFilter] = Field(default_factory=list, max_length=24)
    predefined: str | None = Field(default=None, max_length=80)
    sort_field: str | None = Field(default=None, max_length=120)
    sort_ascending: bool = False
    offset: int = Field(default=0, ge=0, le=10000)
    size: int = Field(default=100, ge=1, le=250)


class MarketScreenResponse(BaseModel):
    quotes: list[dict[str, Any]]
    total: int | None = None
    offset: int
    size: int
    query: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance


class AIProviderConfig(BaseModel):
    provider: Literal["openai_compatible", "anthropic"] = "openai_compatible"
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=1000, repr=False)
    base_url: str | None = Field(default=None, max_length=1000)


class AIMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50000)


class AIChatRequest(BaseModel):
    messages: list[AIMessage] = Field(min_length=1, max_length=40)
    provider: AIProviderConfig | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    max_tool_rounds: int = Field(default=6, ge=1, le=10)
    allow_mutations: bool = False


class AIToolTrace(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result_preview: str
    mutating: bool = False


class AIChatResponse(BaseModel):
    answer: str
    provider: str
    model: str
    tool_trace: list[AIToolTrace] = Field(default_factory=list)
    proposed_operations: list[Operation] = Field(default_factory=list)
