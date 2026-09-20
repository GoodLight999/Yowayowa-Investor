from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yowayowa.db import Base
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.services.strategy_tracking import (
    list_strategy_snapshots,
    record_strategy_snapshots,
)
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyPriorityFactor,
    StrategyResearchPriority,
)


@contextmanager
def _session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _evaluation(score: float = 75.0) -> StrategyCandidateEvaluation:
    return StrategyCandidateEvaluation(
        symbol="test",
        company_name="Test Corp",
        market_cap=100,
        pe_ratio=10,
        current_assets=120,
        liabilities=40,
        yowayowa_conservative_net_cash=80,
        yowayowa_conservative_net_cash_ratio=0.8,
        net_cash=80,
        net_cash_ratio=0.8,
        net_cash_ratio_is_lower_bound=True,
        cash_neutral_pe=2,
        cash_neutral_pe_is_upper_bound=True,
        deep_value_net_cash=False,
        research_priority=StrategyResearchPriority(
            score=score,
            confidence=0.8,
            factors=[
                StrategyPriorityFactor(
                    key="value",
                    score=35,
                    max_score=40,
                    signals={"net_cash_ratio": 0.8},
                )
            ],
            flags=["net_cash_ratio_lower_bound"],
            next_checks=["verify_exact_investment_securities_from_primary_filing"],
        ),
        basis="conservative_floor_ex_investment_securities",
        provenance=Provenance(
            provider="fixture",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime(2026, 9, 21, tzinfo=UTC),
        ),
    )


def test_strategy_snapshot_is_point_in_time_and_deduplicated_per_day() -> None:
    captured = datetime(2026, 9, 21, 3, 0, tzinfo=UTC)
    with _session() as session:
        first = record_strategy_snapshots(
            session,
            "kiyohara_global_value_growth",
            "jp",
            [_evaluation(75)],
            captured_at=captured,
        )
        second = record_strategy_snapshots(
            session,
            "kiyohara_global_value_growth",
            "jp",
            [_evaluation(99)],
            captured_at=captured.replace(hour=8),
        )
        listed = list_strategy_snapshots(session, region="JP", symbol="test")

    assert [item.id for item in first] == [item.id for item in second]
    assert len(listed) == 1
    assert listed[0].region == "jp"
    assert listed[0].symbol == "TEST"
    assert listed[0].score == 75
    assert listed[0].scoring_version == "kiyohara_priority_v1"
    assert listed[0].evaluation.research_priority is not None
    assert listed[0].evaluation.research_priority.score == 75


def test_strategy_snapshot_keeps_separate_daily_observations() -> None:
    with _session() as session:
        record_strategy_snapshots(
            session,
            "kiyohara_global_value_growth",
            "us",
            [_evaluation(70)],
            captured_at=datetime(2026, 9, 20, 20, 0, tzinfo=UTC),
        )
        record_strategy_snapshots(
            session,
            "kiyohara_global_value_growth",
            "us",
            [_evaluation(80)],
            captured_at=datetime(2026, 9, 21, 20, 0, tzinfo=UTC),
        )
        listed = list_strategy_snapshots(
            session,
            strategy_id="kiyohara_global_value_growth",
            region="us",
        )

    assert len(listed) == 2
    assert [item.score for item in listed] == [80, 70]
