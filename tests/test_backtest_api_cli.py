from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api import backtest_routes
from yowayowa.api.app import app
from yowayowa.backtest_cli import app as backtest_cli_app
from yowayowa.config import Settings
from yowayowa.services.backtest_definitions import list_strategies


def _ohlcv(start: date, count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    day = start
    close = 100.0
    while len(rows) < count:
        if day.weekday() < 5:
            opening = close
            close *= 1.001
            rows.append(
                {
                    "as_of": day.isoformat(),
                    "open": opening,
                    "high": close,
                    "low": opening,
                    "close": close,
                    "provider": "alpaca",
                    "source_url": "https://data.example/source",
                    "license_class": "personal_only",
                    "currency": "USD",
                    "retrieved_at": datetime(2025, 6, 1, tzinfo=UTC).isoformat(),
                }
            )
        day = date.fromordinal(day.toordinal() + 1)
    return rows


def test_api_lists_data_defined_strategies_and_unknown_strategy_is_404(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(backtest_routes, "get_settings", lambda: Settings(mode="personal"))

    class EmptyStore:
        def read(self, symbol: str, *, provider: str, limit: int) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(backtest_routes, "_store", lambda: EmptyStore())
    with TestClient(app) as client:
        response = client.get("/v1/backtest/strategies")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {item.id for item in list_strategies()}
        unknown = client.post(
            "/v1/backtest/run",
            json={"strategy_id": "not-a-strategy", "start": "2025-01-01", "end": "2025-03-01"},
        )
        assert unknown.status_code == 404


def test_api_fails_closed_for_personal_only_provider_in_public_mode(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        backtest_routes, "get_settings", lambda: Settings(mode="public", api_token="token")
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/backtest/run",
            headers={"Authorization": "Bearer token"},
            json={"strategy_id": "equal_weight", "start": "2025-01-01", "end": "2025-03-01"},
        )
    assert response.status_code == 404


def test_api_run_reads_persisted_store_and_returns_metrics(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(backtest_routes, "get_settings", lambda: Settings(mode="personal"))
    rows_by_symbol = {
        symbol: _ohlcv(date(2025, 1, 1), 75) for symbol in list_strategies()[-1].universe
    }

    class Store:
        def read(self, symbol: str, *, provider: str, limit: int) -> list[dict[str, Any]]:
            assert provider == "alpaca"
            assert limit == 10_000
            return rows_by_symbol[symbol]

    monkeypatch.setattr(backtest_routes, "_store", lambda: Store())
    with TestClient(app) as client:
        response = client.post(
            "/v1/backtest/run",
            json={
                "strategy_id": "equal_weight",
                "start": "2025-01-01",
                "end": "2025-05-01",
                "bootstrap_samples": 100,
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "complete"
    assert payload["metrics"]["cagr"]["status"] == "available"
    assert payload["provenance"][0]["license_class"] == "personal_only"


def test_cli_parses_iso_dates_and_calls_api_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_run(
        strategy: str,
        start: date,
        end: date,
        base_url: str,
        token: str | None,
        commission_bps: float,
        slippage_bps: float,
        provider: str,
    ) -> dict[str, object]:
        captured.update(strategy=strategy, start=start, end=end)
        return {
            "strategy": {"name": "Fixture"},
            "status": "insufficient",
            "metrics": {},
            "trades": [],
            "equity_curve": [],
            "warnings": [],
        }

    monkeypatch.setattr("yowayowa.backtest_cli._run_request", fake_run)
    result = CliRunner().invoke(
        backtest_cli_app,
        ["--strategy", "equal_weight", "--start", "2025-01-01", "--end", "2025-03-01"],
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "strategy": "equal_weight",
        "start": date(2025, 1, 1),
        "end": date(2025, 3, 1),
    }


def test_cli_rejects_invalid_date() -> None:
    result = CliRunner().invoke(
        backtest_cli_app,
        ["--strategy", "equal_weight", "--start", "invalid", "--end", "2025-03-01"],
    )
    assert result.exit_code != 0


def test_strategy_screening_api_uses_stored_universe(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(backtest_routes, "get_settings", lambda: Settings(mode="personal"))

    class Store:
        def list_symbols(self) -> list[str]:
            return ["AAA", "BBB"]

        def read(self, symbol: str, *, provider: str, limit: int) -> list[dict[str, Any]]:
            assert provider == "alpaca"
            assert limit == 10_000
            return _ohlcv(date(2025, 1, 1), 70)

    monkeypatch.setattr(backtest_routes, "_store", lambda: Store())
    with TestClient(app) as client:
        response = client.get("/v1/screening/strategy")
    assert response.status_code == 200, response.text
    assert response.json()["universe"] == ["AAA", "BBB"]
    assert len(response.json()["low_volatility"]) == 2
