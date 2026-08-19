from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance

HoldingChangeStatus = Literal["new", "increased", "decreased", "exited", "unchanged"]


class InstitutionalHolding(BaseModel):
    issuer: str
    title_of_class: str | None = None
    cusip: str
    reported_value_thousands: int
    value_usd: int
    shares_or_principal: float
    amount_type: str | None = None
    put_call: str | None = None
    investment_discretion: str | None = None
    voting_sole: float | None = None
    voting_shared: float | None = None
    voting_none: float | None = None
    weight: float | None = None


class ThirteenFFiling(BaseModel):
    accession_number: str
    form: str
    filing_date: date
    report_date: date
    primary_document: str
    source_url: str
    holdings: list[InstitutionalHolding]
    total_value_usd: int
    provenance: Provenance


class ThirteenFHoldingChange(BaseModel):
    issuer: str
    title_of_class: str | None = None
    cusip: str
    put_call: str | None = None
    status: HoldingChangeStatus
    current_shares: float
    previous_shares: float
    share_change: float
    share_change_fraction: float | None = None
    current_value_usd: int
    previous_value_usd: int


class ThirteenFManagerReport(BaseModel):
    cik: str
    manager_name: str
    filings: list[ThirteenFFiling] = Field(default_factory=list)
    changes: list[ThirteenFHoldingChange] = Field(default_factory=list)
    unavailable_filings: list[str] = Field(default_factory=list)
    retrieved_at: datetime
    notes: list[str] = Field(default_factory=list)
