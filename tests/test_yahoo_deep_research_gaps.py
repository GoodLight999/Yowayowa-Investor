"""Batch 2 gap coverage for the deep-research provider.

Covers the remaining fail-closed / type-conversion branches measured missing:
``research`` per-section exception isolation, ``option_chain`` expiration
validation, every yfinance section loader, ``_fund`` defensive branches, and
``_jsonable``'s recursive type dispatch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo_deep_research
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.research_models import ResearchSection


def _provider() -> YahooDeepResearchProvider:
    return YahooDeepResearchProvider(Settings(database_url="sqlite:///:memory:"))


class BoomTicker:
    """Ticker whose every getter raises: one failure must not erase others."""

    options: tuple[str, ...] = ()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def __getattr__(self, name: str):
        def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError(f"{name} exploded")

        return _boom

    def get_info(self) -> dict[str, object]:
        raise RuntimeError("info exploded")


def test_research_isolates_failure_and_keeps_working_sections(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failing section is recorded in ``errors``; siblings still load."""

    class MixedTicker(BoomTicker):
        def get_info(self) -> dict[str, object]:
            return {"longName": "Rocket Lab"}

        def get_analyst_price_targets(self) -> dict[str, float]:
            return {"mean": 90.0}

        def get_recommendations(self) -> pd.DataFrame:
            return pd.DataFrame([{"period": "0m", "strongBuy": 4}])

        def get_recommendations_summary(self) -> pd.DataFrame:
            return pd.DataFrame([{"period": "0m"}])

        def get_upgrades_downgrades(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_earnings_estimate(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_revenue_estimate(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_earnings_history(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_eps_trend(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_eps_revisions(self) -> pd.DataFrame:
            return pd.DataFrame()

        def get_growth_estimates(self) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: MixedTicker(symbol))
    provider = _provider()

    result = provider.research(
        "rklb",
        [ResearchSection.PROFILE, ResearchSection.ANALYST, ResearchSection.ESG],
    )

    assert result.symbol == "RKLB"
    assert result.sections["profile"] == {"longName": "Rocket Lab"}
    assert result.sections["analyst"]["recommendations"][0]["strongBuy"] == 4
    assert set(result.errors) == {"esg"}
    assert result.errors["esg"].startswith("RuntimeError: ")


def test_research_failure_of_one_section_does_not_mark_provenance_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class MixedTicker(BoomTicker):
        def get_info(self) -> dict[str, object]:
            return {"sector": "Industrials"}

    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", lambda symbol: MixedTicker(symbol))
    provider = _provider()

    result = provider.research("RKLB", [ResearchSection.PROFILE, ResearchSection.OWNERSHIP])

    assert result.sections["profile"]["sector"] == "Industrials"
    assert set(result.errors) == {"ownership"}
    assert result.provenance.license_class.value == "personal_only"


def test_option_chain_rejects_unknown_expiration(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", BoomTicker)
    provider = _provider()

    with pytest.raises(ValueError, match=r"2027-01-15.*unavailable"):
        provider.option_chain("RKLB", "2027-01-15")


def test_option_chain_skips_non_dict_underlying(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class ChainOnlyTicker:
        options = ("2026-09-18",)

        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def option_chain(self, expiration: str) -> SimpleNamespace:
            return SimpleNamespace(
                calls=pd.DataFrame([{"strike": 80}]),
                puts=pd.DataFrame(),
                underlying="not-a-dict",
            )

    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", ChainOnlyTicker)

    snapshot = _provider().option_chain("RKLB", "2026-09-18")

    assert snapshot.underlying == {}
    assert snapshot.calls == [{"index": 0, "strike": 80}]
    assert snapshot.puts == []


def test_profile_skips_null_and_absent_profile_keys(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class NullInfoTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def get_info(self) -> dict[str, object]:
            return {"longName": "Rocket Lab", "sector": None, "unrelated": "dropped"}

    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", NullInfoTicker)

    section = _provider()._profile(NullInfoTicker("RKLB"))

    assert section == {"longName": "Rocket Lab"}


def test_profile_returns_empty_dict_for_non_dict_info() -> None:
    class WeirdTicker:
        def get_info(self) -> object:
            return ["not", "a", "dict"]

    provider = _provider()

    assert provider._profile(WeirdTicker()) == {}  # type: ignore[arg-type]


def test_ownership_insiders_esg_actions_filings_loaders(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    class SectionsTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def get_major_holders(self) -> pd.DataFrame:
            calls.append("major_holders")
            return pd.DataFrame([{"value": 0.3}])

        def get_institutional_holders(self) -> pd.DataFrame:
            calls.append("institutional")
            return pd.DataFrame([{"holder": "Vanguard"}])

        def get_mutualfund_holders(self) -> pd.DataFrame:
            calls.append("mutualfund")
            return pd.DataFrame()

        def get_shares(self) -> pd.DataFrame:
            calls.append("shares")
            return pd.DataFrame([{"shares": 1_000}])

        def get_insider_purchases(self) -> pd.DataFrame:
            calls.append("purchases")
            return pd.DataFrame([{"net": 100}])

        def get_insider_transactions(self) -> pd.DataFrame:
            calls.append("transactions")
            return pd.DataFrame()

        def get_insider_roster_holders(self) -> pd.DataFrame:
            calls.append("roster")
            return pd.DataFrame()

        def get_sustainability(self) -> dict[str, object]:
            calls.append("sustainability")
            return {"totalEsg": 12.5}

        def get_dividends(self, period: str) -> pd.Series:
            calls.append(f"dividends:{period}")
            return pd.Series([0.01], index=[pd.Timestamp("2026-08-01")])

        def get_splits(self, period: str) -> pd.Series:
            calls.append(f"splits:{period}")
            return pd.Series(dtype=float)

        def get_capital_gains(self, period: str) -> pd.Series:
            calls.append(f"capital_gains:{period}")
            return pd.Series(dtype=float)

        def get_sec_filings(self) -> list[dict[str, object]]:
            calls.append("filings")
            return [{"title": "8-K"}]

    ticker = SectionsTicker("RKLB")

    ownership = YahooDeepResearchProvider._ownership(ticker)  # type: ignore[arg-type]
    insiders = YahooDeepResearchProvider._insiders(ticker)  # type: ignore[arg-type]
    esg = YahooDeepResearchProvider._esg(ticker)  # type: ignore[arg-type]
    actions = YahooDeepResearchProvider._actions(ticker)  # type: ignore[arg-type]
    filings = YahooDeepResearchProvider._filings(ticker)  # type: ignore[arg-type]

    ownership_view = YahooDeepResearchProvider._jsonable(ownership)
    assert ownership_view["major_holders"] == [{"index": 0, "value": 0.3}]
    assert ownership_view["institutional_holders"][0]["holder"] == "Vanguard"
    assert ownership_view["mutual_fund_holders"] == []
    assert set(insiders) == {"purchases", "transactions", "roster"}
    assert esg == {"totalEsg": 12.5}
    assert set(actions) == {"dividends", "splits", "capital_gains"}
    dividends = YahooDeepResearchProvider._jsonable(actions["dividends"])
    assert dividends[0]["value"] == 0.01
    assert filings == [{"title": "8-K"}]
    assert calls[:5] == ["major_holders", "institutional", "mutualfund", "shares", "purchases"]
    assert "dividends:10y" in calls


def test_fund_returns_empty_dict_when_funds_data_is_none() -> None:
    class NoFundTicker:
        def get_funds_data(self) -> None:
            return None

    assert YahooDeepResearchProvider._fund(NoFundTicker()) == {}


def test_fund_skips_failing_and_none_attributes() -> None:
    class FundData:
        @property
        def description(self) -> str:
            return "A fund"

        @property
        def fund_overview(self) -> str:
            raise RuntimeError("yfinance raised")

        @property
        def top_holdings(self) -> None:
            return None

    class FundTicker:
        def get_funds_data(self) -> FundData:
            return FundData()

    assert YahooDeepResearchProvider._fund(FundTicker()) == {"description": "A fund"}  # type: ignore[arg-type]


def test_fund_collects_present_attributes() -> None:
    overview = {"category": "Large Blend"}
    classes = {"equity": 0.9}
    holdings = pd.DataFrame([{"symbol": "AAP"}])

    class FundData:
        description = "Total market fund"
        fund_overview = overview
        asset_classes = classes
        top_holdings = holdings

    class FundTicker:
        def get_funds_data(self) -> FundData:
            return FundData()

    section = YahooDeepResearchProvider._fund(FundTicker())  # type: ignore[arg-type]

    assert section["description"] == "Total market fund"
    assert section["fund_overview"] == {"category": "Large Blend"}
    assert section["asset_classes"] == {"equity": 0.9}
    assert section["top_holdings"] is not None


def test_jsonable_scalars_dates_and_special_floats() -> None:
    convert = YahooDeepResearchProvider._jsonable

    assert convert(None) is None
    assert convert("text") == "text"
    assert convert(42) == 42
    assert convert(True) is True
    assert convert(float("nan")) is None
    assert convert(float("inf")) is None
    assert convert(date(2026, 9, 27)) == "2026-09-27"
    assert convert(datetime(2026, 9, 27, 12, 0, tzinfo=UTC)) == "2026-09-27T12:00:00+00:00"
    assert convert(pd.Timestamp("2026-09-27")) == "2026-09-27T00:00:00"
    assert convert((1, 2)) == [1, 2]
    assert convert({3.5}) == [3.5]
    assert convert({"a": [1, {"b": 2}]}) == {"a": [1, {"b": 2}]}


def test_jsonable_unwraps_numpy_like_item_and_falls_back_to_str() -> None:
    class NumpyLike:
        def item(self) -> int:
            return 7

    class BrokenItem:
        def item(self) -> int:
            raise ValueError("no scalar")

        def __str__(self) -> str:
            return "broken"

    class BareObject:
        pass

    convert = YahooDeepResearchProvider._jsonable

    assert convert(NumpyLike()) == 7
    assert convert(BrokenItem()) == "broken"
    assert isinstance(convert(BareObject()), str)


def test_records_returns_empty_list_for_non_list_payload() -> None:
    assert YahooDeepResearchProvider._records(None) == []
    frame = pd.DataFrame([{"x": 1}])
    assert YahooDeepResearchProvider._records(frame) == [{"index": 0, "x": 1}]
    # A non-DataFrame, non-list payload (e.g. a bare dict) also collapses to [].
    assert YahooDeepResearchProvider._records({"unexpected": "shape"}) == []  # type: ignore[arg-type]
