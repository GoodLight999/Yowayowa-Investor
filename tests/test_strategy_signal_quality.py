from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yowayowa.api import backtest_routes
from yowayowa.api.deps import require_api_token
from yowayowa.services.alerts import evaluate_strategy_signal_alerts
from yowayowa.services.strategy_signals import compute_daily_strategy_signals
from yowayowa.stock_acquisition import StockOhlcvStore


def _history(count: int = 81) -> list[dict[str, Any]]:
    return [
        {
            "as_of": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
            "close": 200.0 - index,
            "provider": "alpaca",
            "source_url": f"https://example.invalid/data/{index}",
            "license_class": "personal_only",
            "retrieved_at": "2025-06-01T00:00:00Z",
        }
        for index in range(count)
    ]


def _assert_isolated(rows: list[dict[str, Any]], reason: str) -> None:
    result = compute_daily_strategy_signals({"BAD": rows, "GOOD": _history()})
    assert result["unavailable"]["BAD"] == reason
    for field in ("low_volatility", "mean_reversion", "alerts"):
        assert [item["symbol"] for item in result[field]] == ["GOOD"]


def test_duplicate_calendar_sessions_fail_closed() -> None:
    rows = _history()
    rows.append({**rows[-1], "as_of": f"{rows[-1]['as_of']}T15:00:00Z"})
    _assert_isolated(rows, "duplicate_sessions")


def test_interior_gap_does_not_stretch_observations_into_sessions() -> None:
    _assert_isolated(_history()[::2], "interior_gaps")


def test_gap_outside_lookback_does_not_invalidate_current_signals() -> None:
    rows = _history()
    del rows[1]
    result = compute_daily_strategy_signals({"AAA": rows, "BBB": _history()})
    assert result["unavailable"] == {}
    assert len(result["low_volatility"]) == 2
    assert len(result["alerts"]) == 2


def test_session_calendar_does_not_invent_weekend_prices() -> None:
    rows = _history()
    weekdays = [date(2025, 1, 1) + timedelta(days=index) for index in range(120)]
    weekdays = [session for session in weekdays if session.weekday() < 5]
    for row, session in zip(rows, weekdays, strict=False):
        row["as_of"] = session.isoformat()
    result = compute_daily_strategy_signals({"AAA": rows})
    assert result["unavailable"] == {}
    assert len(result["alerts"]) == 1


@pytest.mark.parametrize(
    "bad_close", [None, "not-a-number", True, 0, -1, math.nan, math.inf, 1e-320]
)
def test_invalid_close_isolated_without_exception(bad_close: object) -> None:
    rows = _history()
    rows[40]["close"] = bad_close
    _assert_isolated(rows, "invalid_close")


def test_missing_close_key_isolated_without_exception() -> None:
    rows = _history()
    del rows[40]["close"]
    _assert_isolated(rows, "invalid_close")


def test_rejected_future_dated_symbol_cannot_make_healthy_symbol_stale() -> None:
    rows = _history()
    rows[-1]["as_of"] = "2026-01-01"
    rows[40]["close"] = None
    _assert_isolated(rows, "invalid_close")


def test_nonfinite_derived_returns_fail_closed() -> None:
    rows = _history()
    rows[-21]["close"] = 1e-11
    rows[-20]["close"] = 1e308
    _assert_isolated(rows, "invalid_returns")


def test_stale_symbol_cannot_rewind_fresh_symbol() -> None:
    result = compute_daily_strategy_signals({"STALE": _history(61), "FRESH": _history()})
    assert result["as_of"] == date(2025, 3, 22)
    assert result["unavailable"]["STALE"] == "stale_data"
    assert [item["symbol"] for item in result["alerts"]] == ["FRESH"]
    assert result["provenance"]["STALE"]["as_of"] == "2025-03-02"


@pytest.mark.parametrize("field", ["provider", "source_url", "license_class", "retrieved_at"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_provenance_required_on_every_observation(field: str, value: object) -> None:
    rows = _history()
    rows[40][field] = value
    _assert_isolated(rows, "missing_provenance")


@pytest.mark.parametrize(
    ("field", "value"),
    [("license_class", "unknown"), ("retrieved_at", "not-a-date"), ("source_url", "not-a-url")],
)
def test_malformed_provenance_fails_closed(field: str, value: object) -> None:
    rows = _history()
    rows[40][field] = value
    _assert_isolated(rows, "invalid_provenance")


def test_mixed_provider_fails_closed() -> None:
    rows = _history()
    rows[40]["provider"] = "other"
    _assert_isolated(rows, "mixed_providers")


def test_missing_session_isolated_without_exception() -> None:
    rows = _history()
    del rows[40]["as_of"]
    _assert_isolated(rows, "invalid_session")


def test_alert_evaluator_preserves_source_metadata_for_entire_lookback() -> None:
    rows = _history()
    result = compute_daily_strategy_signals({"AAA": rows})
    alerts = evaluate_strategy_signal_alerts({"AAA": rows})
    assert alerts == result["alerts"]
    provenance = alerts[0]["provenance"]
    assert provenance["provider"] == "alpaca"
    assert provenance["as_of"] == rows[-1]["as_of"]
    assert provenance["observations"] == [
        {
            key: row[key]
            for key in ("provider", "source_url", "license_class", "retrieved_at", "as_of")
        }
        for row in rows[-21:]
    ]


@pytest.mark.parametrize("path", ["/v1/screening/strategy", "/v1/backtest/signals/daily"])
@pytest.mark.parametrize("bad_close", [None, "not-a-number", 1e-320])
def test_api_bad_symbol_returns_200_and_keeps_healthy_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, bad_close: object
) -> None:
    rows = _history()
    rows[40]["close"] = bad_close
    store = StockOhlcvStore(tmp_path)
    for symbol, history in {"BAD": rows, "GOOD": _history()}.items():
        store._path(symbol).write_text("".join(json.dumps(row) + "\n" for row in history))
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
    assert result["unavailable"]["BAD"] == "invalid_close"
    assert [item["symbol"] for item in result["alerts"]] == ["GOOD"]
    assert result["alerts"][0]["provenance"]["provider"] == "alpaca"


@pytest.mark.parametrize("provider", [None, "other"])
def test_api_does_not_filter_away_invalid_or_mixed_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: object
) -> None:
    rows = _history()
    rows[40]["provider"] = provider
    store = StockOhlcvStore(tmp_path)
    store._path("AAA").write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setattr(backtest_routes, "_store", lambda: store)
    monkeypatch.setattr(backtest_routes, "enforce_provider_policy", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(backtest_routes.strategy_router)
    app.dependency_overrides[require_api_token] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/screening/strategy")
    assert response.status_code == 200
    assert not response.json()["alerts"]
    expected = "missing_provenance" if provider is None else "mixed_providers"
    assert response.json()["unavailable"]["AAA"] == expected
