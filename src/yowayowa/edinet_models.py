from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance


class EdinetDocumentSummary(BaseModel):
    doc_id: str
    edinet_code: str | None = None
    security_code: str | None = None
    filer_name: str
    fund_code: str | None = None
    ordinance_code: str | None = None
    form_code: str | None = None
    doc_type_code: str | None = None
    description: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    submitted_at: datetime | None = None
    xbrl_available: bool = False
    csv_available: bool = False
    legal_status: str | None = None


class EdinetDocumentList(BaseModel):
    filing_date: date
    documents: list[EdinetDocumentSummary]
    matched_count: int
    total_count: int
    provenance: Provenance


class EdinetIndexFailure(BaseModel):
    filing_date: date
    error: str


class EdinetIndexSyncResult(BaseModel):
    start_date: date
    end_date: date
    days_requested: int
    days_synced: int
    documents_upserted: int
    failures: list[EdinetIndexFailure] = Field(default_factory=list)


class EdinetFilingHistory(BaseModel):
    start_date: date
    end_date: date
    security_code: str | None = None
    edinet_code: str | None = None
    documents: list[EdinetDocumentSummary]
    matched_count: int
    indexed_days: int
    expected_days: int
    coverage_complete: bool
    index_start: date | None = None
    index_end: date | None = None
    provenance: Provenance


class EdinetFact(BaseModel):
    source_file: str
    element_id: str
    label: str
    context_id: str
    relative_year: str | None = None
    consolidation: str | None = None
    period_type: str | None = None
    unit_id: str | None = None
    unit: str | None = None
    value: str


class EdinetMetricObservation(EdinetFact):
    numeric_value: Decimal | None = None


class EdinetFinancials(BaseModel):
    doc_id: str
    company_name: str | None = None
    edinet_code: str | None = None
    security_code: str | None = None
    accounting_standard: str | None = None
    document_type: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    metrics: dict[str, list[EdinetMetricObservation]]
    unavailable_metrics: list[str] = Field(default_factory=list)
    fact_count: int
    source_files: list[str]
    parse_warnings: list[str] = Field(default_factory=list)
    provenance: Provenance


class EdinetFactSearchResult(BaseModel):
    doc_id: str
    query: str | None = None
    facts: list[EdinetFact]
    matched_count: int
    total_count: int
    source_files: list[str]
    parse_warnings: list[str] = Field(default_factory=list)
    provenance: Provenance
