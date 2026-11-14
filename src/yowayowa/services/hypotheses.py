"""Persistence service for append-only research hypotheses (no execution side effects)."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.db import HypothesisRecord, utcnow
from yowayowa.hypothesis_models import (
    HypothesisCreate,
    HypothesisEvidenceLink,
    HypothesisProvenance,
    InvestmentHypothesis,
)


def _to_model(row: HypothesisRecord) -> InvestmentHypothesis:
    created_at = row.created_at
    if created_at.tzinfo is None:
        # SQLite does not preserve timezone offsets for DateTime columns; all
        # timestamps in this table are assigned in UTC by this service.
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    return InvestmentHypothesis(
        id=row.id,
        hypothesis=row.hypothesis,
        falsification_criteria=list(row.falsification_criteria),
        symbol=row.symbol,
        provenance=HypothesisProvenance(
            created_at=created_at,
            evidence_links=[
                HypothesisEvidenceLink.model_validate(link) for link in row.evidence_links
            ],
        ),
    )


def create_hypothesis(session: Session, payload: HypothesisCreate) -> InvestmentHypothesis:
    """Append a hypothesis decision and its point-in-time evidence links."""

    row = HypothesisRecord(
        hypothesis=payload.hypothesis,
        falsification_criteria=list(payload.falsification_criteria),
        symbol=payload.symbol,
        evidence_links=[link.model_dump(mode="json") for link in payload.evidence_links],
        created_at=utcnow(),
    )
    try:
        session.add(row)
        session.commit()
        session.refresh(row)
    except Exception:
        session.rollback()
        raise
    return _to_model(row)


def list_hypotheses(
    session: Session,
    *,
    symbol: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[InvestmentHypothesis]:
    """Return saved decisions newest-first; absent symbols are not inferred."""

    statement = select(HypothesisRecord)
    if symbol is not None:
        statement = statement.where(HypothesisRecord.symbol == symbol)
    statement = (
        statement.order_by(HypothesisRecord.created_at.desc(), HypothesisRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_to_model(row) for row in session.scalars(statement).all()]


def get_hypothesis(session: Session, hypothesis_id: int) -> InvestmentHypothesis:
    """Return one saved decision or raise LookupError when it does not exist."""

    row = session.get(HypothesisRecord, hypothesis_id)
    if row is None:
        raise LookupError(f"Hypothesis {hypothesis_id} not found")
    return _to_model(row)
