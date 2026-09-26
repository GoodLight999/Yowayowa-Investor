from datetime import UTC, datetime
from decimal import Decimal
from math import sqrt

import numpy as np
import pandas as pd
import pytest

from yowayowa.domain import (
    LicenseClass,
    Portfolio,
    PortfolioAnalytics,
    Position,
    PositionAnalytics,
    Provenance,
)
from yowayowa.providers.yahoo_risk import HistoricalReturnData, YahooRiskProvider
from yowayowa.services.risk import _max_drawdown, portfolio_risk_analytics


def _provenance(source: str) -> Provenance:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=now,
        as_of=now,
    )


def _portfolio_and_valuation() -> tuple[Portfolio, PortfolioAnalytics]:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    portfolio = Portfolio(
        id=7,
        name="Hedged",
        base_currency="USD",
        positions=[
            Position(symbol="LONG", quantity=Decimal("8"), currency="USD"),
            Position(symbol="SHORT", quantity=Decimal("-4"), currency="USD"),
        ],
        created_at=now,
        updated_at=now,
    )
    valuation = PortfolioAnalytics(
        portfolio_id=7,
        name="Hedged",
        base_currency="USD",
        net_market_value=600.0,
        gross_market_value=1000.0,
        known_cost_basis=0.0,
        known_cost_market_value=0.0,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        largest_position_weight=0.8,
        concentration_hhi=0.68,
        positions=[
            PositionAnalytics(
                symbol="LONG",
                quantity=Decimal("8"),
                currency="USD",
                price=100.0,
                fx_to_base=1.0,
                market_value_base=800.0,
                weight=0.8,
                as_of=now,
            ),
            PositionAnalytics(
                symbol="SHORT",
                quantity=Decimal("-4"),
                currency="USD",
                price=50.0,
                fx_to_base=1.0,
                market_value_base=-200.0,
                weight=0.2,
                as_of=now,
            ),
        ],
        currency_exposure=[],
        provenance=_provenance("current quotes"),
        evaluated_at=now,
    )
    return portfolio, valuation


def test_portfolio_risk_uses_signed_gross_weights() -> None:
    portfolio, valuation = _portfolio_and_valuation()
    index = pd.date_range("2026-08-03", periods=5, tz="UTC")
    returns = pd.DataFrame(
        {
            "LONG": [0.01, -0.02, 0.015, 0.005, -0.004],
            "SHORT": [0.03, -0.01, 0.02, -0.04, 0.01],
        },
        index=index,
    )
    benchmark = pd.Series([0.005, -0.01, 0.012, 0.003, -0.002], index=index, name="^GSPC")
    history = HistoricalReturnData(
        returns=returns,
        benchmark_returns=benchmark,
        unavailable_symbols=[],
        provenance=_provenance("history"),
    )

    result = portfolio_risk_analytics(
        portfolio,
        valuation,
        history,
        benchmark="^GSPC",
        period="1y",
    )

    expected = returns["LONG"] * 0.8 - returns["SHORT"] * 0.2
    assert result.observations == 5
    assert result.covered_gross_weight == pytest.approx(1.0)
    assert result.positions[0].signed_weight == pytest.approx(0.8)
    assert result.positions[1].signed_weight == pytest.approx(-0.2)
    assert result.annualized_volatility == pytest.approx(expected.std(ddof=1) * sqrt(252))
    assert result.max_drawdown is not None
    assert result.max_drawdown <= 0.0
    assert result.value_at_risk_95 is not None
    assert result.expected_shortfall_95 is not None
    assert result.beta is not None
    assert result.benchmark_correlation is not None
    assert len(result.correlations) == 1
    contributions = [item.variance_contribution for item in result.positions]
    assert all(item is not None for item in contributions)
    assert sum(item or 0.0 for item in contributions) == pytest.approx(1.0)


def test_max_drawdown_is_zero_when_wealth_only_sets_new_highs() -> None:
    assert _max_drawdown(np.asarray([0.01, 0.02, 0.005], dtype=np.float64)) == pytest.approx(0.0)


def test_portfolio_risk_reports_partial_historical_coverage() -> None:
    portfolio, valuation = _portfolio_and_valuation()
    index = pd.date_range("2026-08-03", periods=4, tz="UTC")
    long_returns = pd.Series([0.01, -0.02, 0.015, 0.005], index=index)
    history = HistoricalReturnData(
        returns=pd.DataFrame({"LONG": long_returns}),
        benchmark_returns=pd.Series([0.0, 0.01, -0.01, 0.005], index=index),
        unavailable_symbols=["SHORT"],
        provenance=_provenance("history"),
    )

    result = portfolio_risk_analytics(
        portfolio,
        valuation,
        history,
        benchmark="^GSPC",
        period="1y",
    )

    assert result.covered_gross_weight == pytest.approx(0.8)
    assert result.unavailable_symbols == ["SHORT"]
    assert result.annualized_volatility == pytest.approx(long_returns.std(ddof=1) * sqrt(252))
    assert any("covered sleeve" in note for note in result.notes)


def test_yahoo_risk_fx_symbols_cover_usd_and_cross_rates() -> None:
    assert YahooRiskProvider._fx_symbol("USD", "JPY") == "JPY=X"
    assert YahooRiskProvider._fx_symbol("JPY", "USD") == "JPYUSD=X"
    assert YahooRiskProvider._fx_symbol("EUR", "JPY") == "EURJPY=X"
    with pytest.raises(ValueError, match="identical"):
        YahooRiskProvider._fx_symbol("USD", "USD")


def test_yahoo_risk_close_frame_normalizes_yfinance_multiindex() -> None:
    index = pd.date_range("2026-08-01", periods=2, tz="UTC")
    columns = pd.MultiIndex.from_product([["Close", "Open"], ["AAA", "BBB"]])
    frame = pd.DataFrame(
        [[10.0, 20.0, 9.0, 19.0], [11.0, 21.0, 10.0, 20.0]],
        index=index,
        columns=columns,
    )

    close = YahooRiskProvider._close_frame(frame, ["AAA", "BBB"])

    assert list(close.columns) == ["AAA", "BBB"]
    assert close.loc[index[0], "AAA"] == pytest.approx(10.0)
    assert close.loc[index[1], "BBB"] == pytest.approx(21.0)
