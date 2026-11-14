"""Immutable investment-hypothesis records with explicit evidence provenance."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HypothesisEvidenceLink(BaseModel):
    """A source pointer captured with the decision, not fetched at read time."""

    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(min_length=1, max_length=2048)
    provider: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=500)
    retrieved_at: datetime | None = None
    as_of: str | None = Field(default=None, max_length=100)

    @field_validator("source_url")
    @classmethod
    def require_http_url_without_credentials(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("source_url must not have surrounding whitespace")
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError("source_url must be an absolute HTTP or HTTPS URL")
        if parts.username is not None or parts.password is not None:
            raise ValueError("source_url must not contain credentials")
        return value


class HypothesisCreate(BaseModel):
    """Required decision content; record time is server-assigned and immutable."""

    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=1, max_length=4000)
    falsification_criteria: list[str] = Field(min_length=1, max_length=20)
    evidence_links: list[HypothesisEvidenceLink] = Field(min_length=1, max_length=50)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("hypothesis")
    @classmethod
    def require_nonblank_hypothesis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hypothesis must not be blank")
        return value

    @field_validator("falsification_criteria")
    @classmethod
    def require_nonblank_criteria(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("falsification criteria must not be blank")
        return values

    @field_validator("symbol")
    @classmethod
    def require_nonblank_symbol(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("symbol must not be blank")
        return value


class HypothesisProvenance(BaseModel):
    """When the decision was captured and the links available at that time."""

    created_at: datetime
    evidence_links: list[HypothesisEvidenceLink] = Field(min_length=1)


class InvestmentHypothesis(BaseModel):
    """Persisted hypothesis and falsification criteria, without execution state."""

    id: int
    hypothesis: str
    falsification_criteria: list[str]
    symbol: str | None = None
    provenance: HypothesisProvenance
