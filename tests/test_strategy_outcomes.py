from __future__ import annotations

from datetime import UTC, datetime

from yowayowa.domain import (
    LicenseClass,
    MarketHistory,
    PriceBar,
    Provenance,
)
from yowayowa.providers.base import ProviderDescriptor
from yowayowa.services.strategy_outcomes import forward_outcome_report
from yowayowa.strategy_models import (
    StrategyCandidateEvaluation,
    StrategyPriorityFactor,
    StrategyResearchPriority,
    StrategyResearchSnapshot,
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


def _snapshot() -> StrategyResearchSnapshot:
    priority = StrategyResearchPriority(
        score=80,
        confidence=0.9,
        factors=[
            StrategyPriorityFactor(
                key="value",
                score=35,
                max_score=40,
                signals={"net_cash_ratio": 0.8},
            )
        ],
    )
    evaluation = StrategyCandidateEvaluation(
        symbol="TEST",
        company_name="Test Corp",
        market_cap=100,
        pe_ratio=10,
        current_assets=120,
        liabilities=40,
        net_cash=80,
        net_cash_ratio=0.8,
        net_cash_ratio_is_lower_bound=True,
        cash_neutral_pe=2,
        cash_neutral_pe_is_upper_bound=True,
        research_priority=priority,
        basis="conservative_floor_ex_investment_securities",
        provenance=_provenance("TEST"),
    )
    return StrategyResearchSnapshot(
        id=7,
        strategy_id="kiyohara_global_value_growth",
        scoring_version=priority.scoring_version,
        region="us",
        symbol="TEST",
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


def test_forward_outcome_starts_after_signal_date_and_measures_excess_return() -> None:
    provider = FakeProvider(
        {
            "TEST": _history(
                "TEST",
                [
                    (1, 999),
                    (2, 100),
                    (3, 110),
                    (4, 121),
                ],
            ),
            "^GSPC": _history(
                "^GSPC",
                [
                    (2, 200),
                    (3, 202),
                    (4, 204),
                ],
            ),
        }
    )

    report = forward_outcome_report(
        [_snapshot()],
        provider,  # type: ignore[arg-type]
        horizons=[2],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    outcome = report.outcomes[0]
    assert outcome.status == "available"
    assert outcome.entry_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert outcome.entry_price == 100
    assert outcome.exit_at == datetime(2026, 1, 4, tzinfo=UTC)
    assert outcome.exit_price == 121
    assert outcome.total_return == 0.21
    assert outcome.benchmark_symbol == "^GSPC"
    assert outcome.benchmark_return == 0.02
    assert outcome.excess_return == 0.19


def test_forward_outcome_is_pending_until_full_horizon_exists() -> None:
    provider = FakeProvider(
        {
            "TEST": _history("TEST", [(2, 100), (3, 105)]),
            "^GSPC": _history("^GSPC", [(2, 200), (3, 201)]),
        }
    )

    report = forward_outcome_report(
        [_snapshot()],
        provider,  # type: ignore[arg-type]
        horizons=[20],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    outcome = report.outcomes[0]
    assert outcome.status == "pending"
    assert outcome.entry_price == 100
    assert outcome.exit_price is None
    assert outcome.total_return is None


def test_forward_outcome_marks_missing_security_history_unavailable() -> None:
    report = forward_outcome_report(
        [_snapshot()],
        FakeProvider({}),  # type: ignore[arg-type]
        horizons=[20, 60],
        evaluated_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert [item.status for item in report.outcomes] == ["unavailable", "unavailable"]
    assert all(item.benchmark_symbol == "^GSPC" for item in report.outcomes)
