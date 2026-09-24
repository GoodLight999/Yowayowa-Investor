"""LLM research brief / ask response models (P5-A).

Financial-correctness invariants (same class of rules as the screening
models):

- Every citation carries provider, source URL, retrieved-at and as-of so any
  sentence in a brief can be traced back to its captured evidence.
- Coverage is explicit: an input that was not obtained is recorded as
  ``None`` / an explanatory string in ``coverage`` and rendered as 「未取得」
  in the answer — it is never zero-filled and never silently omitted.
- ``answer`` is model output grounded in the collected evidence packet; the
  service prompt forbids inventing numbers, and the structured fields
  (``coverage``, ``citations``, ``provenance``) stay deterministic.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from yowayowa.domain import Provenance

__all__ = [
    "BriefCitation",
    "ResearchAskResponse",
    "ResearchBrief",
]


class BriefCitation(BaseModel):
    """One evidence pointer backing (part of) an LLM answer."""

    provider: str
    source: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    as_of: str | None = None
    kind: str = Field(
        description=(
            "evidence family: edinet_filing / credit_margin / macro / screener / ask_evidence"
        )
    )
    code_or_series: str | None = Field(
        default=None,
        description="JPX/EDINET code or macro series_id this citation points at",
    )
    note: str | None = None


class ResearchBrief(BaseModel):
    """One generated morning research brief (P5-A A-1/A-2)."""

    run_date: date
    sections: list[str]
    answer: str
    citations: list[BriefCitation] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    generated_at: datetime
    provider: str
    model: str


class ResearchAskResponse(BaseModel):
    """One research_ask round: answer plus deterministic evidence trace."""

    question: str
    answer: str
    citations: list[BriefCitation] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str
    generated_at: datetime
