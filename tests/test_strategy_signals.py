from __future__ import annotations

from datetime import date, timedelta

from yowayowa.services.alerts import evaluate_strategy_signal_alerts
from yowayowa.services.strategy_signals import compute_daily_strategy_signals

UNIVERSE = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "XOM", "SPY")


def _history(symbol_index: int = 0, count: int = 62) -> list[dict[str, object]]:
    start = date(2025, 1, 1)
    return [
        {
            "as_of": start + timedelta(days=index),
            "close": 100 + symbol_index * 10 + index * (symbol_index + 1),
            "provider": "alpaca",
            "source_url": "https://example.invalid/data",
            "license_class": "personal_only",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        }
        for index in range(count)
    ]


def test_daily_signals_rank_volatility_and_20_day_return_with_provenance() -> None:
    histories = {symbol: _history(index) for index, symbol in enumerate(UNIVERSE)}
    histories["AAPL"] = _history(0)
    histories["MSFT"] = [{**row, "close": 200 - index * 2} for index, row in enumerate(_history(1))]

    result = compute_daily_strategy_signals(histories)

    assert result["as_of"] == date(2025, 3, 3)
    assert len(result["low_volatility"]) == 5
    assert [item["daily_volatility"] for item in result["low_volatility"]] == sorted(
        item["daily_volatility"] for item in result["low_volatility"]
    )
    assert result["mean_reversion"][0]["symbol"] == "MSFT"
    assert result["mean_reversion"][0]["return_20d"] < 0
    assert result["provenance"]["AAPL"]["provider"] == "alpaca"
    assert evaluate_strategy_signal_alerts(histories)[0]["symbol"] == "MSFT"


def test_missing_common_session_fails_closed_without_signals() -> None:
    histories = {symbol: _history(index) for index, symbol in enumerate(UNIVERSE)}
    histories["SPY"] = [
        {**row, "as_of": date(2026, 1, 1) + timedelta(days=index)}
        for index, row in enumerate(_history(9, count=61))
    ]

    result = compute_daily_strategy_signals(histories)

    assert result["as_of"] is None
    assert result["low_volatility"] == []
    assert result["mean_reversion"] == []


def test_short_history_is_reported_as_unavailable() -> None:
    histories = {symbol: _history(index, count=10) for index, symbol in enumerate(UNIVERSE)}

    result = compute_daily_strategy_signals(histories)

    assert result["low_volatility"] == []
    assert result["mean_reversion"] == []
    assert result["unavailable"]["AAPL"] == "insufficient_60_day_history"
