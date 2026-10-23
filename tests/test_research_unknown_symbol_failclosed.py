"""Boundary tests for fail-closed research answers on unknown symbols.

The bug being pinned: ``GET /v1/research/{symbol}`` answered **200** with every
section empty for a symbol Yahoo does not know (e.g. the 5-digit EDINET code
``29010``), so "no data" and "wrong ticker" were indistinguishable.
"""

from __future__ import annotations

import pandas as pd
import pytest
from starlette.testclient import TestClient

from yowayowa.api.app import app
from yowayowa.config import Settings
from yowayowa.providers import yahoo_deep_research
from yowayowa.providers.yahoo_deep_research import (
    ResearchSymbolUnavailable,
    ResearchUpstreamUnavailable,
    YahooDeepResearchProvider,
    section_has_data,
)
from yowayowa.research_models import ResearchSection


def _provider() -> YahooDeepResearchProvider:
    return YahooDeepResearchProvider(Settings(database_url="sqlite:///:memory:"))


class EmptyTicker:
    """yfinance stand-in for a symbol Yahoo has no research data for.

    Mirrors the measured 29010 shape: every module returns an empty payload and
    ``get_shares`` raises yfinance's ``YFNotImplementedError`` (a
    ``NotImplementedError`` subclass).
    """

    options: tuple[str, ...] = ()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def __getattr__(self, name: str):
        if name == "get_info":
            return lambda: {}
        if name == "get_shares":

            def _not_implemented() -> object:
                raise NotImplementedError("Have not implemented fetching 'shares' from Yahoo API")

            return _not_implemented
        if name == "get_funds_data":
            return lambda: None
        if name == "get_sec_filings":
            return lambda: {}

        def _empty(*_args: object, **_kwargs: object) -> pd.DataFrame:
            return pd.DataFrame()

        return _empty


class PartialTicker(EmptyTicker):
    """Known symbol: profile/actions have data, one module is unimplemented."""

    def get_info(self) -> dict[str, object]:
        return {"longName": "Wel-Dish.Incorporated", "currency": "JPY", "marketCap": 2_145_194_752}

    def get_dividends(self, period: str | None = None) -> pd.Series:
        assert period == "10y"
        return pd.Series([3.0, 4.0], index=pd.to_datetime(["2025-03-31", "2026-03-31"]))


class ExplodingTicker:
    """Every module raises a provider-level error (upstream outage)."""

    options: tuple[str, ...] = ()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def __getattr__(self, name: str):
        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError(f"{name} unavailable")

        return _boom


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ({}, False),
        ([], False),
        ("", False),
        ("   ", False),
        ({"profile": {}, "analyst": {"price_targets": {}}}, False),
        ({"dividends": None}, False),
        (["", None], False),
        ([[]], False),
        ({"dividends": None, "splits": []}, False),
        ("Wel-Dish", True),
        ({"longName": "Wel-Dish"}, True),
        ({"dividends": None, "splits": [3.0]}, True),
        (0, True),
    ],
)
def test_section_has_data_boundaries(value: object, expected: bool) -> None:
    assert section_has_data(value) is expected


def test_unknown_symbol_raises_research_data_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: EmptyTicker(symbol))

    with pytest.raises(ResearchSymbolUnavailable) as excinfo:
        _provider().research("29010")

    exc = excinfo.value
    assert isinstance(exc, LookupError)
    assert exc.code == "research_symbol_unavailable"
    assert exc.symbol == "29010"
    assert exc.sections_requested == [section.value for section in ResearchSection]
    assert set(exc.section_errors) == {"ownership"}
    assert exc.section_errors["ownership"].startswith("NotImplementedError: ")
    message = str(exc)
    assert "research_symbol_unavailable" in message
    assert "29010" in message
    assert "ownership (NotImplementedError)" in message


def test_unknown_symbol_detail_is_machine_readable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: EmptyTicker(symbol))

    with pytest.raises(ResearchSymbolUnavailable) as excinfo:
        _provider().research("29010", [ResearchSection.PROFILE, ResearchSection.OWNERSHIP])

    detail = excinfo.value.as_detail()

    assert detail["code"] == "research_symbol_unavailable"
    assert detail["symbol"] == "29010"
    assert detail["reason"] == "every_requested_section_empty"
    assert detail["sections_requested"] == ["profile", "ownership"]
    assert set(detail["sections_failed"]) == {"ownership"}
    assert "29010" in detail["message"]


def test_subset_request_that_is_entirely_empty_raises(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: EmptyTicker(symbol))

    with pytest.raises(ResearchSymbolUnavailable) as excinfo:
        _provider().research("29010", [ResearchSection.PROFILE])

    assert excinfo.value.sections_requested == ["profile"]
    assert excinfo.value.section_errors == {}


def test_known_symbol_with_one_missing_section_still_answers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: PartialTicker(symbol))

    result = _provider().research(
        "2901.T",
        [ResearchSection.PROFILE, ResearchSection.OWNERSHIP, ResearchSection.ACTIONS],
    )

    assert result.symbol == "2901.T"
    assert result.sections["profile"]["longName"] == "Wel-Dish.Incorporated"
    assert result.sections["actions"]["dividends"][0]["value"] == 3.0
    assert set(result.errors) == {"ownership"}
    assert result.errors["ownership"].startswith("NotImplementedError: ")
    # Empty-but-successful sections are not relabelled as errors: only the
    # raising module is reported.
    assert "profile" not in result.errors
    assert "actions" not in result.errors


def test_provider_outage_on_every_section_raises_upstream_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: ExplodingTicker(symbol))

    with pytest.raises(ResearchUpstreamUnavailable) as excinfo:
        _provider().research("RKLB", [ResearchSection.PROFILE, ResearchSection.ANALYST])

    exc = excinfo.value
    assert exc.status_code == 502
    assert exc.code == "research_upstream_unavailable"
    assert set(exc.section_errors) == {"profile", "analyst"}
    assert "every requested section failed" in str(exc)
    assert "failed sections: analyst (RuntimeError), profile (RuntimeError)" in str(exc)
    # A total provider failure is not a 404: the ticker may well be fine.
    assert not isinstance(exc, LookupError)


def test_api_provider_outage_returns_502_with_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: ExplodingTicker(symbol))

    with TestClient(app) as client:
        response = client.get("/v1/research/RKLB")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "research_upstream_unavailable"
    assert detail["reason"] == "every_requested_section_failed"
    assert set(detail["sections_failed"]) == {section.value for section in ResearchSection}


def test_api_unknown_symbol_returns_404_with_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: EmptyTicker(symbol))

    with TestClient(app) as client:
        response = client.get("/v1/research/29010")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "research_symbol_unavailable"
    assert detail["symbol"] == "29010"
    assert detail["reason"] == "every_requested_section_empty"
    assert detail["sections_requested"] == [section.value for section in ResearchSection]
    assert set(detail["sections_failed"]) == {"ownership"}


def test_api_known_symbol_keeps_success_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: PartialTicker(symbol))

    with TestClient(app) as client:
        response = client.get("/v1/research/2901.T")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "2901.T"
    # The raising module is the only one missing from ``sections``; every other
    # requested section is still present, exactly as before the change.
    assert set(body["sections"]) == {
        section.value for section in ResearchSection if section is not ResearchSection.OWNERSHIP
    }
    assert body["sections"]["profile"]["currency"] == "JPY"
    # Unchanged contract: a module that raises lands in ``errors`` while the
    # rest of the payload is still returned with 200.
    assert set(body["errors"]) == {"ownership"}
    assert body["sections"]["esg"] == []
    assert body["provenance"]["provider"] == "yahoo/yfinance"
