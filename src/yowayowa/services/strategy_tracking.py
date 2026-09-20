from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.db import StrategyResearchSnapshotRecord
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyResearchSnapshot,
)
from yowayowa.symbols import normalize_symbol


def _to_model(row: StrategyResearchSnapshotRecord) -> StrategyResearchSnapshot:
    return StrategyResearchSnapshot(
        id=row.id,
        strategy_id=row.strategy_id,
        scoring_version=row.scoring_version,
        region=row.region,
        symbol=row.symbol,
        score=float(row.score),
        confidence=float(row.confidence),
        evaluation=StrategyCandidateEvaluation.model_validate(row.evaluation),
        captured_at=row.captured_at,
    )


def record_strategy_snapshots(
    session: Session,
    strategy_id: str,
    region: str,
    evaluations: list[StrategyCandidateEvaluation],
    *,
    captured_at: datetime | None = None,
) -> list[StrategyResearchSnapshot]:
    captured_at = captured_at or datetime.now(UTC)
    captured_on = captured_at.date()
    normalized_region = region.strip().lower() or "unknown"
    snapshots: list[StrategyResearchSnapshot] = []

    for evaluation in evaluations:
        priority = evaluation.research_priority
        if priority is None:
            continue
        symbol = normalize_symbol(evaluation.symbol)
        existing = session.scalar(
            select(StrategyResearchSnapshotRecord).where(
                StrategyResearchSnapshotRecord.strategy_id == strategy_id,
                StrategyResearchSnapshotRecord.scoring_version == priority.scoring_version,
                StrategyResearchSnapshotRecord.region == normalized_region,
                StrategyResearchSnapshotRecord.symbol == symbol,
                StrategyResearchSnapshotRecord.captured_on == captured_on,
            )
        )
        if existing is not None:
            snapshots.append(_to_model(existing))
            continue

        row = StrategyResearchSnapshotRecord(
            strategy_id=strategy_id,
            scoring_version=priority.scoring_version,
            region=normalized_region,
            symbol=symbol,
            score=Decimal(str(priority.score)),
            confidence=Decimal(str(priority.confidence)),
            evaluation=evaluation.model_dump(mode="json"),
            captured_on=captured_on,
            captured_at=captured_at,
        )
        session.add(row)
        session.flush()
        snapshots.append(_to_model(row))

    session.commit()
    return snapshots


def list_strategy_snapshots(
    session: Session,
    *,
    strategy_id: str | None = None,
    region: str | None = None,
    symbol: str | None = None,
    limit: int = 200,
) -> list[StrategyResearchSnapshot]:
    statement = select(StrategyResearchSnapshotRecord)
    if strategy_id:
        statement = statement.where(StrategyResearchSnapshotRecord.strategy_id == strategy_id)
    if region:
        statement = statement.where(
            StrategyResearchSnapshotRecord.region == region.strip().lower()
        )
    if symbol:
        statement = statement.where(
            StrategyResearchSnapshotRecord.symbol == normalize_symbol(symbol)
        )
    rows = session.scalars(
        statement.order_by(StrategyResearchSnapshotRecord.captured_at.desc()).limit(limit)
    ).all()
    return [_to_model(row) for row in rows]
