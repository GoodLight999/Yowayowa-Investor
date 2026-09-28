from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from yowayowa.domain import (
    LicenseClass,
    MarketHistory,
    PriceBar,
    Provenance,
)
from yowayowa.providers.base import ProviderDescriptor
from yowayowa.services.strategy_calibration import (
    calibration_report,
    spearman_rank_correlation,
)
from yowayowa.services.strategy_outcomes import forward_outcome_report
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyForwardOutcome,
    StrategyForwardOutcomeReport,
    StrategyPriorityFactor,
    StrategyResearchPriority,
    StrategyResearchSnapshot,
)


def _outcome(
    snapshot_id: int,
    captured_at: datetime,
    *,
    score: float,
    total_return: float | None = None,
    excess_return: float | None = None,
    horizon: int = 2,
    exit_at: datetime | str | None = "auto",
) -> StrategyForwardOutcome:
    """Direct outcome fixture. ``exit_at="auto"`` derives captured_at + horizon days."""
    resolved_exit: datetime | None
    if exit_at == "auto":
        resolved_exit = captured_at + timedelta(days=horizon)
    elif exit_at == "none":
        resolved_exit = None
    else:
        resolved_exit = exit_at  # type: ignore[assignment]
    return StrategyForwardOutcome(
        snapshot_id=snapshot_id,
        strategy_id="kiyohara_global_value_growth",
        scoring_version="kiyohara_priority_v1",
        region="us",
        symbol=f"S{snapshot_id}",
        research_priority_score=score,
        captured_at=captured_at,
        horizon_trading_days=horizon,
        status="available",
        entry_at=captured_at + timedelta(days=1),
        exit_at=resolved_exit,
        entry_price=100.0,
        exit_price=110.0,
        total_return=total_return,
        benchmark_symbol="^GSPC",
        benchmark_return=0.0 if excess_return is not None else None,
        excess_return=excess_return,
    )


def _outcome_report(outcomes: list[StrategyForwardOutcome]) -> StrategyForwardOutcomeReport:
    return StrategyForwardOutcomeReport(
        outcomes=outcomes,
        provenance=[],
        notes=[],
        evaluated_at=datetime(2026, 12, 31, tzinfo=UTC),
    )


def _provenance(symbol: str) -> Provenance:
    return Provenance(
        provider="fixture",
        source=f"history:{symbol}",
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=datetime(2026, 2, 1, tzinfo=UTC),
    )


def _history(symbol: str, values: list[tuple[int, float]]) -> MarketHistory:
    return MarketHistory(
        symbol=symbol,
        interval="1d",
        bars=[
            PriceBar(
                timestamp=datetime(2026, 1, day, tzinfo=UTC),
                open=value,
                high=value,
                low=value,
                close=value,
                volume=1,
            )
            for day, value in values
        ],
        provenance=_provenance(symbol),
    )


def _snapshot(
    snapshot_id: int,
    symbol: str,
    score: float,
    *,
    region: str = "us",
    scoring_version: str = "kiyohara_priority_v1",
    strategy_id: str = "kiyohara_global_value_growth",
    factors: list[StrategyPriorityFactor] | None = None,
) -> StrategyResearchSnapshot:
    priority = StrategyResearchPriority(
        score=score,
        confidence=0.9,
        factors=(
            factors
            if factors is not None
            else [
                StrategyPriorityFactor(
                    key="value",
                    score=35,
                    max_score=40,
                    signals={"net_cash_ratio": 0.8},
                )
            ]
        ),
    )
    evaluation = StrategyCandidateEvaluation(
        symbol=symbol,
        company_name=f"Corp {symbol}",
        market_cap=100,
        research_priority=priority,
        basis="conservative_floor_ex_investment_securities",
        provenance=_provenance(symbol),
    )
    return StrategyResearchSnapshot(
        id=snapshot_id,
        strategy_id=strategy_id,
        scoring_version=scoring_version,
        region=region,
        symbol=symbol,
        score=priority.score,
        confidence=priority.confidence,
        evaluation=evaluation,
        captured_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )


class FakeProvider:
    descriptor = ProviderDescriptor(
        name="fixture",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="fixture",
    )

    def __init__(self, histories: dict[str, MarketHistory]) -> None:
        self.histories = histories

    def history(
        self,
        symbol: str,
        period: str,
        interval: str,
        indicators: list[str],
    ) -> MarketHistory:
        assert period == "1y"
        assert interval == "1d"
        assert indicators == []
        if symbol not in self.histories:
            raise LookupError(symbol)
        return self.histories[symbol]

    def quotes(self, symbols: list[str]):  # type: ignore[no-untyped-def]
        raise AssertionError(symbols)

    def overview(self):  # type: ignore[no-untyped-def]
        raise AssertionError


def _security_history(
    symbol: str,
    base: float,
    daily_drifts: list[float],
) -> MarketHistory:
    """One pre-signal bar plus N post-signal bars driven by per-day fractional drift."""
    value = base
    bars = [(1, base)]
    for drift in daily_drifts:
        value = value * (1 + drift)
        bars.append((len(bars) + 1, round(value, 8)))
    return _history(symbol, bars)


def test_spearman_matches_reference_values() -> None:
    assert spearman_rank_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert spearman_rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman_rank_correlation([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)
    # Reversed-pairs permutation: d = [-2, -2, 2, 2] -> 1 - 6*16/60 = -0.6.
    assert spearman_rank_correlation([1, 2, 3, 4], [3, 4, 1, 2]) == pytest.approx(-0.6)
    # Ties use averaged ranks: 1 - 6*sum(d^2)/(n*(n^2-1)) with d = [0, 0, 1.5, -1.5].
    tied = spearman_rank_correlation([1, 2, 2, 4], [1, 2, 3, 4])
    assert tied == pytest.approx(0.948683, rel=1e-5)


def test_calibration_monotone_scores_rank_ic_and_deciles() -> None:
    # 10 securities, higher snapshot score -> strictly higher forward return.
    histories: dict[str, MarketHistory] = {}
    snapshots = []
    for index in range(10):
        symbol = f"S{index}"
        growth = 0.01 + 0.02 * index  # strictly above the flat benchmark
        histories[symbol] = _security_history(symbol, 100, [growth] * 3)
        histories["^GSPC"] = _history("^GSPC", [(1, 100), (2, 100), (3, 100), (4, 100)])
        snapshots.append(
            _snapshot(
                snapshot_id=index + 1,
                symbol=symbol,
                score=50 + 5 * index,
                factors=[
                    StrategyPriorityFactor(
                        key="value",
                        score=1 + index,
                        max_score=10,
                        signals={},
                    ),
                    StrategyPriorityFactor(
                        key="growth",
                        score=10 - index,
                        max_score=10,
                        signals={},
                    ),
                ],
            )
        )

    provider = FakeProvider(histories)
    outcome_report = forward_outcome_report(
        snapshots,
        provider,  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)

    assert len(report.buckets) == 1
    bucket = report.buckets[0]
    assert bucket.strategy_id == "kiyohara_global_value_growth"
    assert bucket.scoring_version == "kiyohara_priority_v1"
    assert bucket.horizon_trading_days == 2
    assert bucket.sample_total == 10
    assert bucket.sample_available == 10
    assert bucket.sample_pending == 0
    assert bucket.sample_unavailable == 0
    assert bucket.minimum_sample_warning is True  # 10 < 30
    assert bucket.ic_insufficient is False
    assert bucket.ic_sample_count == 10
    assert bucket.rank_ic == pytest.approx(1.0)

    # Monotone returns: decile 10 must beat decile 1 on both means.
    assert bucket.score_deciles[0].decile == 1
    assert bucket.score_deciles[-1].decile == 10
    assert bucket.score_deciles[-1].mean_total_return > bucket.score_deciles[0].mean_total_return
    assert (
        bucket.score_deciles[-1].median_excess_return > bucket.score_deciles[0].median_excess_return
    )
    assert bucket.score_deciles[0].score_min == 50
    assert bucket.score_deciles[-1].score_max == 95

    # Positive excess hit rate must be 1.0: every security beat the flat benchmark.
    assert bucket.positive_excess_hit_rate == pytest.approx(1.0)

    # Factor deciles: value factor is monotone with returns (IC +1 in spirit),
    # growth factor is anti-monotone. Their top deciles must disagree.
    by_key = {summary.factor_key: summary for summary in bucket.factor_deciles}
    assert by_key["value"].decile == 10
    assert by_key["growth"].decile == 10  # growth score fraction is 1.0 at index 9
    assert by_key["value"].mean_total_return > by_key["growth"].mean_total_return


def test_calibration_excludes_pending_and_unavailable_without_zero_filling() -> None:
    histories = {
        "GOOD": _security_history("GOOD", 100, [0.05] * 3),
        "^GSPC": _history("^GSPC", [(1, 100), (2, 100), (3, 100), (4, 100)]),
    }
    snapshots = [
        _snapshot(1, "GOOD", 80),
        # History exists but the 2-day horizon has not matured -> pending.
        _snapshot(2, "SHORT", 60),
    ]

    provider = FakeProvider(
        {
            **histories,
            "SHORT": _security_history("SHORT", 100, [0.01]),
        }
    )
    outcome_report = forward_outcome_report(
        snapshots,
        provider,  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)

    bucket = report.buckets[0]
    assert bucket.sample_total == 2
    assert bucket.sample_available == 1
    assert bucket.sample_pending == 1
    assert bucket.sample_unavailable == 0
    assert bucket.median_total_return is not None and bucket.median_total_return > 0
    assert bucket.ic_insufficient is True
    assert bucket.rank_ic is None
    assert any("fewer than 10" in note for note in bucket.notes)
    assert any("Rank IC omitted" in note for note in bucket.notes)

    # Missing history entirely -> unavailable, also never zero-filled.
    snapshots.append(_snapshot(3, "MISSING", 40))
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider(
            {
                **histories,
                "SHORT": _security_history("SHORT", 100, [0.01]),
            }
        ),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)
    bucket = report.buckets[0]
    assert bucket.sample_total == 3
    assert bucket.sample_unavailable == 1
    assert bucket.sample_pending == 1
    assert bucket.sample_available == 1
    assert bucket.median_total_return is not None and bucket.median_total_return > 0


def test_calibration_empty_bucket_statistics_are_none_not_zero() -> None:
    # Every snapshot's history is missing -> unavailable -> no returns at all.
    snapshots = [_snapshot(index + 1, f"M{index}", 70) for index in range(3)]
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider({}),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)
    bucket = report.buckets[0]
    assert bucket.sample_available == 0
    assert bucket.median_total_return is None
    assert bucket.mean_total_return is None
    assert bucket.median_excess_return is None
    assert bucket.mean_excess_return is None
    assert bucket.positive_excess_hit_rate is None
    assert bucket.rank_ic is None
    assert bucket.ic_insufficient is True
    assert any(
        "no available outcome has a benchmark excess return" in note for note in bucket.notes
    )


def test_calibration_no_benchmark_yields_ic_insufficient_with_reason() -> None:
    # Security histories exist but the regional benchmark is absent.
    histories = {
        f"B{index}": _security_history(f"B{index}", 100, [0.01] * 3) for index in range(10)
    }
    snapshots = [_snapshot(index + 1, f"B{index}", 40 + 5 * index) for index in range(10)]
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider(histories),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)
    bucket = report.buckets[0]
    assert bucket.sample_available == 10
    assert all(outcome.excess_return is None for outcome in outcome_report.outcomes)
    assert bucket.median_excess_return is None
    assert bucket.mean_excess_return is None
    assert bucket.positive_excess_hit_rate is None
    assert bucket.rank_ic is None
    assert bucket.ic_insufficient is True
    assert any(
        "no available outcome has a benchmark excess return" in note for note in bucket.notes
    )


def test_calibration_separates_scoring_versions_into_distinct_buckets() -> None:
    histories = {
        "V1": _security_history("V1", 100, [0.1] * 3),
        "V2": _security_history("V2", 100, [-0.1] * 3),
        "^GSPC": _history("^GSPC", [(1, 100), (2, 100), (3, 100), (4, 100)]),
    }
    snapshots = [
        _snapshot(1, "V1", 90, scoring_version="kiyohara_priority_v1"),
        _snapshot(2, "V2", 90, scoring_version="kiyohara_priority_v2"),
    ]
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider(histories),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)

    versions = [bucket.scoring_version for bucket in report.buckets]
    assert versions == ["kiyohara_priority_v1", "kiyohara_priority_v2"]
    first, second = report.buckets
    assert first.mean_total_return > 0
    assert second.mean_total_return < 0


def test_calibration_propagates_outcome_provenance_unchanged() -> None:
    histories = {
        "P1": _security_history("P1", 100, [0.02] * 3),
        "^GSPC": _history("^GSPC", [(1, 100), (2, 100), (3, 100), (4, 100)]),
    }
    snapshots = [_snapshot(1, "P1", 75)]
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider(histories),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)

    assert report.provenance == outcome_report.provenance
    assert len(report.provenance) == 2
    assert {item.source for item in report.provenance} == {"history:P1", "history:^GSPC"}
    assert all(item.provider == "fixture" for item in report.provenance)
    assert all(item.license_class == LicenseClass.PERSONAL_ONLY for item in report.provenance)
    assert all(item.retrieved_at == datetime(2026, 2, 1, tzinfo=UTC) for item in report.provenance)


def test_calibration_factor_rows_exclude_outcomes_without_factor_payload() -> None:
    histories = {
        f"F{index}": _security_history(f"F{index}", 100, [0.01] * 3) for index in range(10)
    }
    snapshots = [
        _snapshot(index + 1, f"F{index}", 50 + 4 * index, factors=[]) for index in range(10)
    ]
    outcome_report = forward_outcome_report(
        snapshots,
        FakeProvider(histories),  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    report = calibration_report(snapshots, outcome_report)
    bucket = report.buckets[0]
    # Score deciles exist (10 available outcomes) but factor deciles cannot.
    assert bucket.score_deciles
    assert bucket.factor_deciles == []
    assert any("carry no factor scores" in note for note in bucket.notes)


def test_calibration_api_route_with_fixture_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'calibration.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    from yowayowa.config import get_settings

    get_settings.cache_clear()

    from starlette.testclient import TestClient

    from yowayowa.api import fundamentals_routes
    from yowayowa.api.app import app
    from yowayowa.db import Base
    from yowayowa.domain import MarketHistory
    from yowayowa.services.strategy_tracking import record_strategy_snapshots

    def history(symbol: str, values: list[float]) -> MarketHistory:
        return MarketHistory(
            symbol=symbol,
            interval="1d",
            bars=[
                PriceBar(
                    timestamp=datetime(2026, 1, day + 1, tzinfo=UTC),
                    open=value,
                    high=value,
                    low=value,
                    close=value,
                    volume=1,
                )
                for day, value in enumerate(values)
            ],
            provenance=_provenance(symbol),
        )

    class FakeMarketProvider:
        descriptor = ProviderDescriptor(
            name="fixture",
            license_class=LicenseClass.PERSONAL_ONLY,
            redistributable=False,
            description="fixture",
        )

        def __init__(self) -> None:
            self.histories = {
                "RT": history("RT", [100, 100, 110, 121]),
                "^GSPC": history("^GSPC", [200, 200, 202, 204]),
            }

        def history(
            self,
            symbol: str,
            period: str,
            interval: str,
            indicators: list[str],
        ) -> MarketHistory:
            return self.histories[symbol]

    monkeypatch.setattr(fundamentals_routes, "yahoo_market_provider", lambda: FakeMarketProvider())

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from yowayowa.db import get_session

        engine = create_engine(f"sqlite:///{tmp_path / 'calibration.db'}")
        Base.metadata.create_all(engine)

        def override_db_session():  # type: ignore[no-untyped-def]
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_session] = override_db_session
        try:
            with Session(engine) as session:
                record_strategy_snapshots(
                    session,
                    "kiyohara_global_value_growth",
                    "us",
                    [
                        StrategyCandidateEvaluation(
                            symbol="RT",
                            company_name="Route Test Corp",
                            market_cap=100,
                            research_priority=StrategyResearchPriority(
                                score=88,
                                confidence=0.9,
                                factors=[
                                    StrategyPriorityFactor(
                                        key="value",
                                        score=36,
                                        max_score=40,
                                        signals={},
                                    )
                                ],
                            ),
                            basis="conservative_floor_ex_investment_securities",
                            provenance=_provenance("RT"),
                        )
                    ],
                    captured_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
                )
                session.commit()

            with TestClient(app) as client:
                response = client.get(
                    "/v1/strategy-research/calibration",
                    params={
                        "strategy_id": "kiyohara_global_value_growth",
                        "region": "us",
                        "horizons": "2",
                    },
                )
                assert response.status_code == 200
                payload = response.json()
                assert payload["buckets"]
                bucket = payload["buckets"][0]
                assert bucket["scoring_version"] == "kiyohara_priority_v1"
                assert bucket["horizon_trading_days"] == 2
                assert bucket["sample_total"] == 1
                assert bucket["sample_available"] == 1
                assert bucket["sample_pending"] == 0
                assert bucket["minimum_sample_warning"] is True
                assert bucket["median_total_return"] == pytest.approx(0.21)
                sources = {item["source"] for item in payload["provenance"]}
                assert sources == {"history:RT", "history:^GSPC"}

                bad_horizons = client.get(
                    "/v1/strategy-research/calibration", params={"horizons": "abc"}
                )
                assert bad_horizons.status_code == 422

                schema = client.get("/openapi.json").json()
                calibration_path = schema["paths"]["/v1/strategy-research/calibration"]
                assert "get" in calibration_path
                assert calibration_path["get"]["operationId"].startswith(
                    "strategy_research_calibration"
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            engine.dispose()
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# Walk-forward / out-of-sample (purged split) tests T1-T8.
# --------------------------------------------------------------------------


def _spaced_outcomes(
    count: int,
    *,
    interval_days: int,
    horizon: int,
    oos_min_sample: int,
) -> list[StrategyForwardOutcome]:
    """Deterministic outcome fixtures: 10-day spacing with horizon 2 gives
    non-overlapping windows (interval > horizon); daily spacing with horizon 10
    gives overlapping windows. Scores and returns are positively correlated."""
    outcomes: list[StrategyForwardOutcome] = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(count):
        captured_at = base + timedelta(days=interval_days * index)
        score = 50.0 + 5.0 * (index % 7)
        total = 0.01 + 0.001 * (index % 7)
        outcomes.append(
            _outcome(
                snapshot_id=index + 1,
                captured_at=captured_at,
                score=score,
                total_return=total,
                excess_return=total - 0.005,
                horizon=horizon,
            )
        )
    assert oos_min_sample > 0
    return outcomes


def test_oos_split_non_overlapping_windows_counts_and_ic() -> None:
    """T1: interval > horizon, n=20, m=10 -> is 10 / oos 10 / purge 0, IC computable."""
    outcomes = _spaced_outcomes(20, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(outcomes))
    bucket = report.buckets[0]
    assert bucket.sample_available == 20
    assert bucket.oos_sample_count == 10
    assert bucket.is_sample_count == 10
    assert bucket.purged_count == 0
    assert bucket.exit_at_unknown_count == 0
    assert bucket.oos_ic_insufficient is False
    assert bucket.is_ic_insufficient is False
    assert bucket.oos_split_at == outcomes[10].captured_at
    # oos_rank_ic is the Spearman IC over the last 10 outcomes; recompute directly.
    expected_oos = spearman_rank_correlation(
        [outcome.research_priority_score for outcome in outcomes[10:]],
        [outcome.excess_return for outcome in outcomes[10:]],
    )
    assert bucket.oos_rank_ic == pytest.approx(expected_oos)
    assert bucket.oos_ic_sample_count == 10
    assert bucket.is_ic_sample_count == 10
    # In-sample = windows fully closed at or before the split.
    assert all(outcome.exit_at <= bucket.oos_split_at for outcome in outcomes[:10])


def test_oos_disjoint_sets_identity_and_value_invariance() -> None:
    """T2: is ∩ oos = ∅, count identity, and split independent of sample values."""
    outcomes = _spaced_outcomes(20, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(outcomes))
    bucket = report.buckets[0]

    split_at = bucket.oos_split_at
    assert split_at is not None
    # Recompute segment membership exactly as the design prescribes.
    oos_ids = {outcome.snapshot_id for outcome in outcomes[10:]}
    is_ids = {outcome.snapshot_id for outcome in outcomes[:10] if outcome.exit_at <= split_at}
    assert is_ids & oos_ids == set()
    assert (
        bucket.is_sample_count
        + bucket.oos_sample_count
        + bucket.purged_count
        + bucket.exit_at_unknown_count
        == bucket.sample_available
    )

    # Scale all sample values: scores x3, returns +1000. The split is pure
    # point-in-time, so nothing segment-related may move (no leakage).
    scaled = [
        StrategyForwardOutcome.model_validate(
            {
                **outcome.model_dump(mode="json"),
                "research_priority_score": outcome.research_priority_score * 3,
                "total_return": (
                    outcome.total_return + 1000.0 if outcome.total_return is not None else None
                ),
                "excess_return": (
                    outcome.excess_return + 1000.0 if outcome.excess_return is not None else None
                ),
            }
        )
        for outcome in outcomes
    ]
    scaled_report = calibration_report([], _outcome_report(scaled))
    scaled_bucket = scaled_report.buckets[0]
    assert scaled_bucket.oos_split_at == bucket.oos_split_at
    assert scaled_bucket.is_sample_count == bucket.is_sample_count
    assert scaled_bucket.oos_sample_count == bucket.oos_sample_count
    assert scaled_bucket.purged_count == bucket.purged_count
    assert scaled_bucket.exit_at_unknown_count == bucket.exit_at_unknown_count


def test_oos_boundary_n_equals_m_and_n_equals_m_plus_one() -> None:
    """T3: n=11 -> is 1 / oos 10; n=10 -> is 0 / oos 10, is_rank_ic None, no exception."""
    eleven = _spaced_outcomes(11, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(eleven))
    bucket = report.buckets[0]
    assert bucket.is_sample_count == 1
    assert bucket.oos_sample_count == 10
    assert bucket.purged_count == 0

    ten = _spaced_outcomes(10, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(ten))
    bucket = report.buckets[0]
    assert bucket.is_sample_count == 0
    assert bucket.oos_sample_count == 10
    assert bucket.purged_count == 0
    assert bucket.is_rank_ic is None
    assert bucket.is_ic_insufficient is True
    assert bucket.oos_ic_insufficient is False
    assert bucket.oos_split_at == ten[0].captured_at
    assert any("In-sample rank IC omitted" in note for note in bucket.oos_notes)

    # Explicit oos_min_sample overrides the default.
    three = _spaced_outcomes(6, interval_days=10, horizon=2, oos_min_sample=3)
    explicit = calibration_report([], _outcome_report(three), oos_min_sample=3)
    bucket = explicit.buckets[0]
    assert bucket.oos_sample_count == 3
    assert bucket.is_sample_count == 3
    assert bucket.oos_split_at == three[3].captured_at


def test_oos_insufficient_sample_skips_without_zero_filling() -> None:
    """T4: available < oos_min_sample -> oos counts stay 0/None/True with a reason."""
    outcomes = _spaced_outcomes(9, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(outcomes))
    bucket = report.buckets[0]
    assert bucket.oos_sample_count == 0
    assert bucket.oos_rank_ic is None
    assert bucket.oos_ic_insufficient is True
    assert bucket.oos_split_at is None
    assert bucket.purged_count == 0
    assert any("skipped" in note and "split not defined" in note for note in bucket.oos_notes)
    # Existing aggregate semantics unchanged.
    assert bucket.sample_available == 9
    assert bucket.ic_insufficient is True  # 9 < 10 decile minimum


def test_oos_purge_removes_windows_crossing_the_split() -> None:
    """T5: interval <= horizon, n=20, m=10 -> is 1 / oos 10 / purged 9 (probed)."""
    outcomes = _spaced_outcomes(20, interval_days=1, horizon=10, oos_min_sample=10)
    report = calibration_report([], _outcome_report(outcomes))
    bucket = report.buckets[0]
    assert bucket.oos_sample_count == 10
    assert bucket.purged_count == 9
    assert bucket.is_sample_count == 1

    split_at = bucket.oos_split_at
    assert split_at is not None
    oos_ids = {outcome.snapshot_id for outcome in outcomes[10:]}
    purged_ids = {outcome.snapshot_id for outcome in outcomes[:10] if outcome.exit_at > split_at}
    assert len(purged_ids) == 9
    # Purged snapshot ids appear in neither segment.
    assert purged_ids & oos_ids == set()
    # All pre-split windows cross the split (purged) except the oldest, which
    # closes exactly at the split instant (in-sample).
    assert {outcome.snapshot_id for outcome in outcomes[:10]} - purged_ids == {1}
    assert outcomes[0].exit_at == split_at
    assert (
        bucket.is_sample_count + bucket.oos_sample_count + bucket.purged_count
        == bucket.sample_available
    )


def test_oos_split_same_instant_tie_crossing_window_is_purged() -> None:
    """T5b: boundary regression — a window with ``captured_at == split_at`` and
    ``exit_at > split_at`` is a crossing window and is purged (the old
    implementation kept it in the in-sample segment)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    split = base + timedelta(days=10)
    outcomes = [
        _outcome(
            snapshot_id=1,
            captured_at=base,
            score=51.0,
            total_return=0.011,
            excess_return=0.006,
            exit_at=base + timedelta(days=2),
        ),
        _outcome(
            snapshot_id=2,
            captured_at=base + timedelta(days=5),
            score=52.0,
            total_return=0.012,
            excess_return=0.007,
            exit_at=base + timedelta(days=7),
        ),
        _outcome(
            snapshot_id=3,
            captured_at=split,
            score=53.0,
            total_return=0.013,
            excess_return=0.008,
            exit_at=split + timedelta(days=2),
        ),
        _outcome(
            snapshot_id=4,
            captured_at=split,
            score=54.0,
            total_return=0.014,
            excess_return=0.009,
            exit_at=split + timedelta(days=2),
        ),
        _outcome(
            snapshot_id=5,
            captured_at=split,
            score=55.0,
            total_return=0.015,
            excess_return=0.010,
            exit_at=split + timedelta(days=2),
        ),
    ]
    report = calibration_report([], _outcome_report(outcomes), oos_min_sample=2)
    bucket = report.buckets[0]
    # Candidate OOS is the last two of the tie group, so split_at == the tie instant.
    assert bucket.oos_split_at == split
    assert bucket.oos_sample_count == 2
    # Both fully-closed windows remain in-sample; the tie-group leftover crosses.
    assert bucket.is_sample_count == 2
    assert bucket.purged_count == 1
    assert bucket.exit_at_unknown_count == 0
    assert (
        bucket.is_sample_count
        + bucket.oos_sample_count
        + bucket.purged_count
        + bucket.exit_at_unknown_count
        == bucket.sample_available
    )


def test_bootstrap_ci_bounds_and_degenerate_cases() -> None:
    """T6: CI low <= median <= high; n<2 or zero variance -> None, no exception."""
    from statistics import median as stat_median

    from yowayowa.services.strategy_calibration import _bootstrap_ci

    values = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]
    ci = _bootstrap_ci(values, statistic=stat_median, resamples=200, seed=20260928)
    assert ci is not None
    low, high = ci
    assert low <= stat_median(values) <= high

    # n < 2 -> None.
    assert _bootstrap_ci([0.05], statistic=stat_median, resamples=200) is None
    # Zero variance -> None (never 0.0).
    flat = _bootstrap_ci([0.05, 0.05, 0.05, 0.05], statistic=stat_median, resamples=200)
    assert flat is None
    # Determinism: identical seed reproduces identical bounds.
    again = _bootstrap_ci(values, statistic=stat_median, resamples=200, seed=20260928)
    assert again == ci


def test_oos_bootstrap_ci_on_bucket_and_empty_bucket_none_defaults() -> None:
    """T6b/T4b: bucket-level CI sanity; empty-bucket OOS stays None (not 0)."""
    outcomes = _spaced_outcomes(20, interval_days=10, horizon=2, oos_min_sample=10)
    report = calibration_report([], _outcome_report(outcomes))
    bucket = report.buckets[0]
    assert bucket.oos_median_total_return is not None
    assert bucket.oos_median_total_return_ci_low is not None
    assert bucket.oos_median_total_return_ci_high is not None
    assert (
        bucket.oos_median_total_return_ci_low
        <= bucket.oos_median_total_return
        <= bucket.oos_median_total_return_ci_high
    )
    assert bucket.oos_median_excess_return_ci_low <= bucket.oos_median_excess_return
    assert bucket.oos_median_excess_return_ci_high >= bucket.oos_median_excess_return
    assert any(
        "front-half statistics" in note and "never fitted" in note for note in bucket.oos_notes
    )

    # Empty bucket: OOS fields are None/0/True defaults, never fabricated zeros.
    empty = calibration_report([], _outcome_report([]))
    assert empty.buckets == []
    unavailable = [
        StrategyForwardOutcome(
            snapshot_id=1,
            strategy_id="s",
            scoring_version="v",
            region="us",
            symbol="X",
            research_priority_score=1.0,
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            horizon_trading_days=2,
            status="unavailable",
        )
    ]
    none_bucket = calibration_report([], _outcome_report(unavailable)).buckets[0]
    assert none_bucket.oos_median_total_return is None
    assert none_bucket.oos_mean_total_return is None
    assert none_bucket.oos_rank_ic is None
    assert none_bucket.oos_sample_count == 0
    assert none_bucket.oos_ic_insufficient is True
    assert none_bucket.oos_split_at is None


def test_calibration_endpoint_oos_min_sample_query_and_tool_schema() -> None:
    """T8: ?oos_min_sample=5 -> 200; oos_min_sample=0 -> 422; tool schema carries the key."""
    monkeypatch_env = {"YOWAYOWA_MODE": "personal"}
    import os

    saved = {key: os.environ.get(key) for key in monkeypatch_env}
    os.environ.update({key: value for key, value in monkeypatch_env.items() if value is not None})
    try:
        from starlette.testclient import TestClient

        from yowayowa.api.app import app

        with TestClient(app) as client:
            ok = client.get("/v1/strategy-research/calibration", params={"oos_min_sample": 5})
            assert ok.status_code == 200
            assert ok.json()["buckets"] == []  # empty production-like DB path

            bad = client.get("/v1/strategy-research/calibration", params={"oos_min_sample": 0})
            assert bad.status_code == 422

            too_big = client.get(
                "/v1/strategy-research/calibration", params={"oos_min_sample": 1001}
            )
            assert too_big.status_code == 422

            schema = app.openapi()
            parameter_names = {
                parameter["name"]
                for parameter in schema["paths"]["/v1/strategy-research/calibration"]["get"][
                    "parameters"
                ]
            }
            assert "oos_min_sample" in parameter_names
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # Tool schema: the parameter exists and the tool name is unchanged (CI pin).
    from unittest.mock import Mock

    from yowayowa.config import Settings
    from yowayowa.services.ai_agent import InvestmentResearchAgent

    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())
    spec = agent.tools["get_strategy_calibration"]
    assert "oos_min_sample" in spec.parameters["properties"]
    assert spec.parameters["properties"]["oos_min_sample"]["minimum"] == 1
    assert spec.parameters["properties"]["oos_min_sample"]["maximum"] == 1000
