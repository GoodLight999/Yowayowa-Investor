"""P5: a Yahoo statement-fetch failure must not read as "no statements".

Measured defect (``docs/SILENT_FAILURE_AUDIT_20261023.md`` §3 #6/#7):
``_safe_frame`` caught every yfinance exception and returned ``pd.DataFrame()``,
which downstream became ``LookupError("No Yahoo financial statements available
for <symbol>")`` and therefore HTTP 404 — an outage was reported as an issuer
that has no financials (AGENTS.md invariant 2: missing data is not zero).

Contract under test, all against the existing ``Provenance.notes`` and the
existing fundamentals API error mapping (no new response keys):

1. normal frames                      -> metrics, no degradation note
2. yfinance raises (rate limit)       -> YahooStatementsUnavailableError (502)
3. genuinely empty DataFrame          -> LookupError, unchanged message (404)
4. no period columns / no rows        -> YahooStatementsUnavailableError (502)
   (plus the row-level twin: a frame whose labels are all unrecognized)
5. partial failure                    -> metrics kept, failure counted in notes

No network access: ``yf.Ticker`` is stubbed and the API test stubs the provider
factory only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo_fundamentals
from yowayowa.providers.yahoo_fundamentals import (
    YahooFundamentalsProvider,
    YahooStatementsUnavailableError,
)

PERIODS = (pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"))


def _provider() -> YahooFundamentalsProvider:
    return YahooFundamentalsProvider(Settings(database_url="sqlite:///:memory:"))


def _revenue_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {PERIODS[0]: [1000.0], PERIODS[1]: [800.0]},
        index=["Total Revenue"],
    )


class _BaseTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_history_metadata(self) -> Mapping[str, Any]:
        return {"longName": "Example Japan Corp", "currency": "JPY", "symbol": self.symbol}


class RateLimitedTicker(_BaseTicker):
    """Yahoo answers every statement request with a rate-limit error."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        raise RuntimeError("HTTP Error 429: Too Many Requests")


class EmptyStatementsTicker(_BaseTicker):
    """Yahoo has no statements for this issuer: a genuinely empty frame each."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame()

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame()

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame()


class NoPeriodColumnsTicker(_BaseTicker):
    """Statement rows arrive without period columns (Yahoo format change)."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(index=["Total Revenue"])

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(index=["Total Assets"])

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(index=["Operating Cash Flow"])


class NoRowsTicker(_BaseTicker):
    """Period columns arrive without any statement rows."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(columns=list(PERIODS))

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(columns=list(PERIODS))

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame(columns=list(PERIODS))


class UnrecognizedRowsTicker(_BaseTicker):
    """Columns and rows exist but no row matches a known alias (row rename)."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame({PERIODS[0]: [1.0]}, index=["A Renamed Yahoo Line"])

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame({PERIODS[0]: [1.0]}, index=["A Renamed Yahoo Line"])

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        return pd.DataFrame({PERIODS[0]: [1.0]}, index=["A Renamed Yahoo Line"])


class PartialFailureTicker(_BaseTicker):
    """Income statements load; balance sheet and cash flow requests fail."""

    def get_income_stmt(self, **_: object) -> pd.DataFrame:
        return _revenue_frame()

    def get_balance_sheet(self, **_: object) -> pd.DataFrame:
        raise RuntimeError("connection reset by peer")

    def get_cash_flow(self, **_: object) -> pd.DataFrame:
        raise RuntimeError("connection reset by peer")


def test_statement_outage_raises_an_upstream_error_not_missing_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", RateLimitedTicker)

    with pytest.raises(YahooStatementsUnavailableError) as caught:
        _provider().company_facts("7203.T")

    error = caught.value
    assert not isinstance(error, LookupError)
    assert isinstance(error, RuntimeError)
    assert len(error.failures) == 6
    assert error.empty_frames == 0
    assert error.schema_frames == 0
    assert {failure.statement for failure in error.failures} == {
        "income_statement",
        "balance_sheet",
        "cash_flow",
    }
    assert {failure.frequency for failure in error.failures} == {"yearly", "quarterly"}
    assert all("429" in failure.reason for failure in error.failures)
    assert "6/6 raised" in str(error)
    assert "income_statement/yearly: RuntimeError: HTTP Error 429" in str(error)


def test_genuinely_empty_statements_keep_the_missing_data_contract(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", EmptyStatementsTicker)

    with pytest.raises(LookupError) as caught:
        _provider().company_facts("7203.T")

    assert not isinstance(caught.value, YahooStatementsUnavailableError)
    assert str(caught.value) == "No Yahoo financial statements available for 7203.T"


@pytest.mark.parametrize("ticker", [NoPeriodColumnsTicker, NoRowsTicker])
def test_statement_schema_break_is_not_missing_data(monkeypatch, ticker) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", ticker)

    with pytest.raises(YahooStatementsUnavailableError) as caught:
        _provider().company_facts("7203.T")

    error = caught.value
    assert not isinstance(error, LookupError)
    assert error.schema_frames == 6
    assert error.empty_frames == 0
    assert len(error.failures) == 0
    assert "6/6 had no period columns" in str(error)


def test_unrecognized_statement_rows_are_not_missing_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", UnrecognizedRowsTicker)

    with pytest.raises(YahooStatementsUnavailableError) as caught:
        _provider().company_facts("7203.T")

    error = caught.value
    assert not isinstance(error, LookupError)
    assert error.unmapped_frames == 6
    assert len(error.failures) == 0
    assert "6/6 had no recognized line item" in str(error)


def test_partial_failure_keeps_metrics_and_counts_the_failure_in_notes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", PartialFailureTicker)

    result = _provider().company_facts("7203.T")

    revenue = result.metrics["revenue"]
    annual = [point.value for point in revenue.points if point.fiscal_period == "FY"]
    assert annual == [Decimal("800.0"), Decimal("1000.0")]
    assert revenue.points[-1].unit == "JPY"
    assert revenue.points[-1].period_end == date(2025, 12, 31)
    notes = result.provenance.notes
    failure_note = next(note for note in notes if "could not be retrieved" in note)
    assert "4 of 6 Yahoo statement frames" in failure_note
    assert "balance_sheet/yearly: RuntimeError: connection reset by peer" in failure_note
    assert "cash_flow/quarterly: RuntimeError: connection reset by peer" in failure_note
    # Existing provenance lines are untouched by the new note.
    assert any("approximate" in note for note in notes)
    assert notes[-1] == "Do not redistribute this normalized Yahoo dataset from public mode."


def test_normal_load_adds_no_degradation_note(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class HealthyTicker(_BaseTicker):
        def get_income_stmt(self, **_: object) -> pd.DataFrame:
            return _revenue_frame()

        def get_balance_sheet(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame({PERIODS[0]: [2000.0]}, index=["Total Assets"])

        def get_cash_flow(self, **_: object) -> pd.DataFrame:
            return pd.DataFrame({PERIODS[0]: [120.0]}, index=["Operating Cash Flow"])

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", HealthyTicker)

    result = _provider().company_facts("7203.T")

    assert result.metrics["revenue"].points[-1].value == Decimal("1000.0")
    notes = result.provenance.notes
    for marker in ("could not be retrieved", "no period columns", "no recognized line item"):
        assert not any(marker in note for note in notes)


def _api_fixtures(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'fundamentals-failure.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    from yowayowa.config import get_settings

    get_settings.cache_clear()


def _api_client(monkeypatch: pytest.MonkeyPatch, ticker: type[_BaseTicker]) -> Any:
    """Route a real provider through a stubbed yfinance ticker."""

    from starlette.testclient import TestClient

    from yowayowa.api import fundamentals_routes
    from yowayowa.api.app import app

    monkeypatch.setattr(yahoo_fundamentals.yf, "Ticker", ticker)
    provider = _provider()
    monkeypatch.setattr(fundamentals_routes, "fundamentals_provider", lambda: provider)
    return TestClient(app)


def test_fundamentals_api_reports_statement_outage_as_upstream_failure(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outage is 502, never 404: the issuer is not statement-less."""

    _api_fixtures(tmp_path, monkeypatch)

    from yowayowa.vercel_observability import UPSTREAM_UNAVAILABLE_DETAIL

    client = _api_client(monkeypatch, RateLimitedTicker)
    try:
        with client:
            response = client.get("/v1/fundamentals/7203.T")
            assert response.status_code == 502
            assert response.json()["detail"] == UPSTREAM_UNAVAILABLE_DETAIL
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()


def test_fundamentals_api_reports_empty_statements_as_not_found(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_fixtures(tmp_path, monkeypatch)

    client = _api_client(monkeypatch, EmptyStatementsTicker)
    try:
        with client:
            response = client.get("/v1/fundamentals/7203.T")
            assert response.status_code == 404
            assert response.json()["detail"] == (
                "No Yahoo financial statements available for 7203.T"
            )
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()


def test_fundamentals_api_serves_partial_statements_with_the_failure_note(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _api_fixtures(tmp_path, monkeypatch)

    client = _api_client(monkeypatch, PartialFailureTicker)
    try:
        with client:
            response = client.get("/v1/fundamentals/7203.T")
            assert response.status_code == 200
            payload = response.json()
            # Decimal values keep their string form on the wire (unchanged).
            assert payload["metrics"]["revenue"]["points"][-1]["value"] == "1000.0"
            notes = payload["provenance"]["notes"]
            assert any("4 of 6 Yahoo statement frames" in note for note in notes)
            # Existing response keys stay exactly as before.
            for key in ("symbol", "cik", "company_name", "metrics", "provenance"):
                assert key in payload
    finally:
        from yowayowa.config import get_settings

        get_settings.cache_clear()
