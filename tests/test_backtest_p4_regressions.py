from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

import pytest
from pydantic import ValidationError

from yowayowa.backtest_models import BacktestRunRequest, BacktestSignal, BacktestStrategyDefinition
from yowayowa.services.backtest import _bootstrap_cagr, _metrics, run_backtest
from yowayowa.stock_acquisition import StockOhlcvStore
from yowayowa.stock_models import StockOhlcvRecord


def _histories(count: int = 100) -> dict[str, list[dict[str, Any]]]:
    histories = {}
    for symbol in ("AAA", "BBB"):
        close = 100.0
        rows = []
        for index in range(count):
            opening = close
            close *= 1 + (-0.01 if index % 3 == 0 else 0.008)
            rows.append(
                {
                    "symbol": symbol,
                    "as_of": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
                    "open": opening,
                    "high": max(opening, close),
                    "low": min(opening, close),
                    "close": close,
                    "provider": "alpaca",
                    "currency": "USD",
                    "source_url": "https://example.invalid/fixture",
                    "license_class": "personal_only",
                    "retrieved_at": "2025-06-01T00:00:00Z",
                }
            )
        histories[symbol] = rows
    return histories


def _strategy(signal: BacktestSignal = "low_volatility") -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition(
        id="p4_fixture",
        name="P4 fixture",
        description="Regression only",
        signal=signal,
        universe=["AAA", "BBB"],
        rebalance="daily",
        max_positions=2,
    )


def _request(start: date = date(2025, 1, 1), end: date = date(2025, 4, 10)) -> BacktestRunRequest:
    return BacktestRunRequest(strategy_id="p4_fixture", start=start, end=end, bootstrap_samples=0)


@pytest.mark.parametrize("currency", ["USDT", "JPY", "", None])
def test_pre_start_quote_contamination_fails_closed(currency: object) -> None:
    histories = _histories()
    for row in histories["AAA"][:50]:
        row["currency"] = currency
        for key in ("open", "high", "low", "close"):
            row[key] *= 100
    with pytest.raises(ValueError, match=r"currenc|Non-USD"):
        run_backtest(_request(date(2025, 3, 4)), _strategy(), histories)


def test_uniform_usdt_is_accepted_but_future_rows_do_not_affect_run() -> None:
    histories = _histories()
    for rows in histories.values():
        for row in rows:
            row["currency"] = "USDT"
    request = _request(date(2025, 3, 4), date(2025, 4, 9))
    baseline = run_backtest(request, _strategy(), histories)
    histories["AAA"][-1]["currency"] = "JPY"
    histories["AAA"][-1].pop("retrieved_at")
    assert run_backtest(request, _strategy(), histories) == baseline


@pytest.mark.parametrize("signal", ["low_volatility", "mean_reversion_20"])
def test_full_60_return_warmup_applies_to_selection_and_weighting(signal: BacktestSignal) -> None:
    strategy = _strategy(signal).model_copy(update={"weighting": "inverse_volatility"})
    histories = _histories()
    result = run_backtest(_request(), strategy, histories, _include_oos=False)
    assert result.trades[0].date == date(2025, 3, 3)
    assert sum(row["as_of"] < result.trades[0].date.isoformat() for row in histories["AAA"]) == 61
    assert all(point.cash == point.equity for point in result.equity_curve[:61])
    short = run_backtest(_request(end=date(2025, 2, 15)), strategy, histories, _include_oos=False)
    assert short.trades == []
    assert short.warnings


def test_monthly_low_vol_waits_for_rebalance_after_warmup() -> None:
    strategy = _strategy().model_copy(update={"rebalance": "monthly"})
    result = run_backtest(_request(), strategy, _histories(), _include_oos=False)
    assert result.trades[0].date == date(2025, 4, 1)


@pytest.mark.parametrize("provider,expected", [("alpaca", 252), ("binance", 365)])
def test_annualization_default_is_asset_calendar_specific(provider: str, expected: int) -> None:
    request = BacktestRunRequest.model_validate(
        {
            "strategy_id": "p4_fixture",
            "start": "2025-01-01",
            "end": "2025-03-01",
            "provider": provider,
        }
    )
    assert request.annualization_days == expected
    explicit = BacktestRunRequest.model_validate(
        {**request.model_dump(), "annualization_days": 360}
    )
    assert explicit.annualization_days == 360


@pytest.mark.parametrize("value", [0, -1, 367, 365.5, True, None])
def test_invalid_annualization_fails_validation(value: object) -> None:
    with pytest.raises(ValidationError):
        BacktestRunRequest.model_validate({**_request().model_dump(), "annualization_days": value})


def test_every_annualized_metric_and_ci_uses_selected_calendar() -> None:
    returns = [-0.01, 0.008, 0.008] * 30
    m252 = _metrics(returns, 2, None, False, 252)
    m365 = _metrics(returns, 2, None, False, 365)
    growth = math.prod(1 + item for item in returns)
    expected_cagr = growth ** (365 / len(returns)) - 1
    assert m365.cagr.value == pytest.approx(expected_cagr)
    assert m365.sharpe_ratio.value == pytest.approx(
        fmean(returns) / stdev(returns) * math.sqrt(365)
    )
    downside = math.sqrt(fmean(min(item, 0) ** 2 for item in returns))
    assert m365.sortino_ratio.value == pytest.approx(fmean(returns) / downside * math.sqrt(365))
    assert m365.annual_turnover.value == pytest.approx(2 * 365 / len(returns))
    assert m365.max_drawdown.value is not None
    assert m365.calmar_ratio.value == pytest.approx(expected_cagr / abs(m365.max_drawdown.value))
    assert m365.max_drawdown == m252.max_drawdown
    assert m365.hit_rate == m252.hit_rate
    ci252 = _bootstrap_cagr(returns, 100, 42, 252)
    ci365 = _bootstrap_cagr(returns, 100, 42, 365)
    assert ci252 is not None and ci365 is not None
    assert ci365 == pytest.approx(tuple((1 + bound) ** (365 / 252) - 1 for bound in ci252))
    assert ci365 == _bootstrap_cagr(returns, 100, 42, 365)


def test_annualization_propagates_to_response_holdouts_and_insufficient_metrics() -> None:
    strategy = _strategy("equal_weight")
    request = _request().model_copy(update={"annualization_days": 365})
    result = run_backtest(request, strategy, _histories())
    assert result.annualization_days == result.metrics.annualization_days == 365
    assert (
        result.in_sample_metrics is not None and result.in_sample_metrics.annualization_days == 365
    )
    assert result.oos_metrics is not None and result.oos_metrics.annualization_days == 365
    empty = run_backtest(request, strategy, {})
    assert empty.annualization_days == empty.metrics.annualization_days == 365


@pytest.mark.parametrize(
    "signal,cash_key",
    [("value_fundamental", "net_cash_ratio"), ("kiyohara_value", "kiyohara_net_cash_ratio")],
)
def test_financial_inputs_survive_model_and_store_and_generate_trades(
    tmp_path: Path,
    signal: BacktestSignal,
    cash_key: str,
) -> None:
    histories = _histories()
    store = StockOhlcvStore(tmp_path)
    restored = {}
    for symbol, rows in histories.items():
        for row in rows:
            row.update(
                disclosure_date="2024-12-31",
                pbr=1,
                per=10,
                net_cash_ratio=0.2,
                kiyohara_net_cash_ratio=0.3,
            )
        records = [StockOhlcvRecord.model_validate(row) for row in rows]
        assert store.append(symbol, records) == len(rows)
        assert store.append(symbol, records) == 0
        restored[symbol] = store.read(symbol, provider="alpaca", limit=1000)
        assert restored[symbol][0][cash_key] == rows[-1][cash_key]
        assert restored[symbol][0]["disclosure_date"] == "2024-12-31"
    strategy = _strategy(signal)
    request = _request(date(2025, 1, 2))
    raw = run_backtest(request, strategy, histories)
    persisted = run_backtest(request, strategy, restored)
    assert persisted.trades
    assert persisted.trades == raw.trades
    assert persisted.metrics == raw.metrics
    assert persisted.equity_curve == raw.equity_curve
    assert persisted.warnings == []


@pytest.mark.parametrize(
    "signal,cash_key",
    [("value_fundamental", "net_cash_ratio"), ("kiyohara_value", "kiyohara_net_cash_ratio")],
)
@pytest.mark.parametrize("missing_field", ["pbr", "per", "cash", "disclosure_date"])
def test_financial_gaps_expose_symbols_dates_and_retained_holdings(
    signal: BacktestSignal,
    cash_key: str,
    missing_field: str,
) -> None:
    histories = _histories()
    for rows in histories.values():
        for row in rows:
            row.update(disclosure_date="2024-12-31", pbr=1, per=10, **{cash_key: 0.2})
        for row in rows[1:]:
            row.pop(cash_key if missing_field == "cash" else missing_field)
    result = run_backtest(
        _request(date(2025, 1, 2)), _strategy(signal), histories, _include_oos=False
    )
    assert result.trades
    assert result.equity_curve[-1].cash == pytest.approx(0)
    assert any(
        "AAA" in warning and "2025-01-03 through 2025-04-10" in warning
        for warning in result.warnings
    )
    assert any("BBB" in warning for warning in result.warnings)
    if missing_field != "disclosure_date":
        assert any("existing holdings retained" in warning for warning in result.warnings)
        assert len(result.trades) == 2


def test_price_only_records_keep_financial_fields_null_after_roundtrip(tmp_path: Path) -> None:
    model = StockOhlcvRecord.model_validate(_histories()["AAA"][0])
    store = StockOhlcvStore(tmp_path)
    store.append("AAA", [model])
    row = store.read("AAA")[0]
    for key in ("disclosure_date", "pbr", "per", "net_cash_ratio", "kiyohara_net_cash_ratio"):
        assert row[key] is None


@pytest.mark.parametrize("signal,prior_closes", [("momentum_12_1", 253), ("mean_reversion_20", 21)])
def test_two_asset_equal_weight_rankings_hold_both_without_sign_filter(
    signal: BacktestSignal,
    prior_closes: int,
) -> None:
    histories = _histories(300)
    result = run_backtest(
        _request(end=date(2025, 10, 27)),
        _strategy(signal),
        histories,
        _include_oos=False,
    )
    first = result.trades[0].date
    assert first == date(2025, 1, 1) + timedelta(days=prior_closes)
    assert {trade.symbol for trade in result.trades if trade.date == first} == {"AAA", "BBB"}
    assert all(trade.target_weight == 0.5 for trade in result.trades)
