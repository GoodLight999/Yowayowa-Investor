"""Machine-discovered screening candidates (P4-D) models.

The screening pipeline turns free sources (EDINET daily filing lists, weekly
credit margin balances, the Yahoo Finance screener) into observation
candidates for the operator. Every candidate carries an explicit source,
signal classification, concrete numeric evidence and full provenance.

Financial-correctness invariants:

- Missing data is never zero-filled. Every field of
  :class:`ScreeningCandidate` is required; when the pipeline cannot produce a
  value it produces no candidate (the row is skipped and the skip is counted),
  it never fabricates or substitutes a number.
- Candidates from different sources are deliberately NOT deduplicated: the
  same code appearing under multiple independent signals is information
  (corroboration), and different signals are different candidates.
- Published dates are as published; nothing is interpolated across missing
  weeks or days.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance

__all__ = [
    "ScreeningCandidate",
    "ScreeningRunResult",
    "ScreeningSignal",
    "ScreeningSource",
]


class ScreeningSource(StrEnum):
    EDINET_FILING = "edinet_filing"
    CREDIT_MARGIN_WEEKLY = "credit_margin_weekly"
    MARKET_SCREENER = "market_screener"


class ScreeningSignal(StrEnum):
    FILING_FORECAST_REVISION = "filing_forecast_revision"
    FILING_BUYBACK = "filing_buyback"
    FILING_DISPOSAL = "filing_disposal"
    FILING_CANCELLATION = "filing_cancellation"
    CREDIT_SHORT_SURGE = "credit_short_surge"
    CREDIT_LONG_SURGE = "credit_long_surge"
    CREDIT_SHORT_DROP = "credit_short_drop"
    SCREENER_LOW_PE = "screener_low_pe"
    SCREENER_LOW_PBR = "screener_low_pbr"
    SCREENER_VOLUME_SPIKE = "screener_volume_spike"


class ScreeningCandidate(BaseModel):
    """One machine-discovered observation candidate.

    ``code`` follows the repo-wide JPX local code grammar: a bare 4-digit
    weekly-source spelling or (for EDINET issuers) the 5-digit security code
    EDINET publishes; value decoration is never repaired. ``value`` holds the
    concrete numbers that triggered the signal — absent numbers are omitted,
    never replaced with zeros.
    """

    code: str
    source: ScreeningSource
    signal: ScreeningSignal
    company_name: str | None = None
    value: dict[str, Any]
    reason: str
    provenance: Provenance
    detected_at: datetime


class ScreeningRunResult(BaseModel):
    """One screening run over the configured free sources."""

    run_date: date
    candidates: list[ScreeningCandidate] = Field(default_factory=list)
    per_source_counts: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
