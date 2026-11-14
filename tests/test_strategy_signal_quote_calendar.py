from __future__ import annotations

import copy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from yowayowa.api import backtest_routes
from yowayowa.api.deps import require_api_token
from yowayowa.services.strategy_signals import compute_daily_strategy_signals
from yowayowa.stock_acquisition import StockOhlcvStore
from yowayowa.stock_models import StockOhlcvRecord


def _history(count: int = 81) -> list[dict[str, Any]]:
    return [
        {
            "symbol": "AAA",
            "as_of": (date(2025, 1, 1) + timedelta(days=index)).isoformat() + "T00:00:00Z",
            "close": 200.0 - index,
            "open": 200.0 - index,
            "high": 200.0,
            "low": 50.0,
            "provider": "alpaca",
            "currency": "USD",
            "source_url": f"https://example.invalid/data/{index}",
            "license_class": "personal_only",
            "retrieved_at": "2025-06-01T00:00:00Z",
        }
        for index in range(count)
    ]


def _assert_healthy_control(bad: list[dict[str, Any]], reason: str) -> None:
    good = _history()
    before = copy.deepcopy(good)
    control = compute_daily_strategy_signals({"GOOD": good})
    result = compute_daily_strategy_signals({"BAD": bad, "GOOD": good})
    assert result["unavailable"]["BAD"] == reason
    assert "BAD" not in result["provenance"]
    for field in ("as_of", "low_volatility", "mean_reversion", "alerts", "provenance"):
        assert result[field] == control[field]
    assert good == before


@pytest.mark.parametrize("index", [0, 20, 40, 60, 80])
@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        (None, "missing_currency"),
        ("", "missing_currency"),
        ("   ", "missing_currency"),
        (True, "invalid_currency"),
        (123, "invalid_currency"),
        ([], "invalid_currency"),
        ({}, "invalid_currency"),
        ("usd", "invalid_currency"),
        (" USD", "invalid_currency"),
        ("USD ", "invalid_currency"),
        ("US1", "invalid_currency"),
        ("\uff35\uff33\uff24", "invalid_currency"),
        ("US", "invalid_currency"),
        ("ABCDEFGHI", "invalid_currency"),
        ("JPY", "mixed_currencies"),
        ("USDT", "mixed_currencies"),
    ],
)
def test_every_quote_failure_isolated_before_calendar(
    index: int, quote: object, reason: str
) -> None:
    bad = _history()
    bad[index]["currency"] = quote
    bad.append({**bad[-1], "as_of": "2026-01-01T00:00:00Z"})
    _assert_healthy_control(bad, reason)


@pytest.mark.parametrize("index", [0, 40, 80])
def test_absent_quote_key_is_not_defaulted(index: int) -> None:
    bad = _history()
    del bad[index]["currency"]
    _assert_healthy_control(bad, "missing_currency")


@pytest.mark.parametrize("quote", ["USD", "JPY", "USDT"])
def test_uniform_quote_and_different_provider_dimensionless_comparison(quote: str) -> None:
    rows = _history()
    for row in rows:
        row["currency"] = quote
        row["provider"] = "other"
        row["close"] *= 100
    result = compute_daily_strategy_signals({"OTHER": rows, "GOOD": _history()})
    assert result["unavailable"] == {}
    assert {row["symbol"] for row in result["alerts"]} == {"OTHER", "GOOD"}
    returns = {row["symbol"]: row["return_20d"] for row in result["mean_reversion"]}
    assert returns["OTHER"] == pytest.approx(returns["GOOD"])
    lineage = result["provenance"]["OTHER"]
    assert lineage["currency"] == quote
    assert all(row["currency"] == quote for row in lineage["observations"])
    alert = next(row for row in result["alerts"] if row["symbol"] == "OTHER")
    assert alert["provenance"]["observations"] == [
        {key: row[key] for key in lineage["observations"][0]} for row in rows[-21:]
    ]


@pytest.mark.parametrize("count", [21, 60, 61, 81])
@pytest.mark.parametrize("future", [False, True])
def test_derived_invalid_series_never_authorizes_calendar(count: int, future: bool) -> None:
    bad = _history(count)
    bad[-21]["close"] = 1e-11
    bad[-1]["close"] = 1e308
    if future:
        bad[-1]["as_of"] = "2026-01-01T00:00:00Z"
    _assert_healthy_control(bad, "invalid_returns")
    result = compute_daily_strategy_signals({"BAD": bad})
    assert result["as_of"] is None
    assert result["provenance"] == {}
    assert result["alerts"] == []


def test_adjacent_overflow_with_later_row_does_not_hide_good() -> None:
    bad = _history()
    bad[-21]["close"] = 1e-11
    bad[-20]["close"] = 1e308
    bad.append({**bad[-1], "as_of": "2026-01-01T00:00:00Z"})
    _assert_healthy_control(bad, "invalid_returns")


def test_typed_record_does_not_invent_missing_quote() -> None:
    row = _history()[0]
    del row["currency"]
    with pytest.raises(ValidationError, match="currency"):
        StockOhlcvRecord.model_validate(row)


@pytest.mark.parametrize("path", ["/v1/screening/strategy", "/v1/backtest/signals/daily"])
@pytest.mark.parametrize("fault", ["mixed_quote", "adjacent_overflow"])
def test_typed_persistence_routes_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, fault: str
) -> None:
    store = StockOhlcvStore(tmp_path)
    bad = _history(100)
    good = _history(100)
    if fault == "mixed_quote":
        for row in bad[-20:]:
            row["currency"] = "JPY"
            for field in ("close", "open", "high", "low"):
                row[field] /= 100
        reason = "mixed_currencies"
    else:
        bad[-21]["close"] = 1e-11
        bad[-20]["close"] = 1e308
        bad.append({**bad[-1], "as_of": "2026-01-01T00:00:00Z"})
        reason = "invalid_returns"
    for symbol, rows in {"BAD": bad, "GOOD": good}.items():
        store.append(
            symbol, [StockOhlcvRecord.model_validate({**row, "symbol": symbol}) for row in rows]
        )
    assert {row["currency"] for row in store.read("BAD", limit=1000)} == (
        {"USD", "JPY"} if fault == "mixed_quote" else {"USD"}
    )
    monkeypatch.setattr(backtest_routes, "_store", lambda: store)
    monkeypatch.setattr(backtest_routes, "enforce_provider_policy", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(backtest_routes.strategy_router)
    app.include_router(backtest_routes.router)
    app.dependency_overrides[require_api_token] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code == 200
    result = response.json()
    control = compute_daily_strategy_signals({"GOOD": store.read("GOOD", limit=1000)})
    assert result["as_of"] == control["as_of"].isoformat()
    assert result["unavailable"] == {"BAD": reason}
    for field in ("low_volatility", "mean_reversion"):
        assert result[field] == control[field]
    assert [row["symbol"] for row in result["alerts"]] == ["GOOD"]
    assert result["alerts"][0]["provenance"]["currency"] == "USD"
    assert all(
        row["currency"] == "USD" for row in result["alerts"][0]["provenance"]["observations"]
    )
