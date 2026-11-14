from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from yowayowa.config import Settings
from yowayowa.providers import yahoo_deep_research
from yowayowa.providers.yahoo_deep_research import YahooDeepResearchProvider
from yowayowa.research_models import ResearchSection


class FakeTicker:
    options = ("2026-09-18", "2026-10-16")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_info(self) -> dict[str, object]:
        return {
            "longName": "Rocket Lab USA, Inc.",
            "sector": "Industrials",
            "marketCap": 42_000_000_000,
            "shortPercentOfFloat": 0.12,
            "targetMeanPrice": 90.0,
        }

    def get_analyst_price_targets(self) -> dict[str, float]:
        return {"current": 80.0, "mean": 90.0, "high": 110.0, "low": 60.0}

    def get_recommendations(self) -> pd.DataFrame:
        return pd.DataFrame([{"period": "0m", "strongBuy": 4, "buy": 7}])

    def get_recommendations_summary(self) -> pd.DataFrame:
        return pd.DataFrame([{"period": "0m", "strongBuy": 4, "buy": 7}])

    def get_upgrades_downgrades(self) -> pd.DataFrame:
        return pd.DataFrame([{"Firm": "Example", "ToGrade": "Buy"}])

    def get_earnings_estimate(self) -> pd.DataFrame:
        return pd.DataFrame([{"avg": 0.1}], index=["0q"])

    def get_revenue_estimate(self) -> pd.DataFrame:
        return pd.DataFrame([{"avg": 600_000_000}], index=["0q"])

    def get_earnings_history(self) -> pd.DataFrame:
        return pd.DataFrame([{"epsActual": 0.02, "epsEstimate": 0.01}])

    def get_eps_trend(self) -> pd.DataFrame:
        return pd.DataFrame([{"current": 0.1}], index=["0q"])

    def get_eps_revisions(self) -> pd.DataFrame:
        return pd.DataFrame([{"upLast7days": 2}], index=["0q"])

    def get_growth_estimates(self) -> pd.DataFrame:
        return pd.DataFrame([{"stock": 0.25}], index=["0q"])

    def option_chain(self, expiration: str) -> SimpleNamespace:
        assert expiration == "2026-09-18"
        return SimpleNamespace(
            calls=pd.DataFrame(
                [{"contractSymbol": "RKLB260918C00080000", "strike": 80, "bid": 5.2}]
            ),
            puts=pd.DataFrame(
                [{"contractSymbol": "RKLB260918P00080000", "strike": 80, "bid": 4.8}]
            ),
            underlying={"symbol": "RKLB", "regularMarketPrice": 80.0},
        )


def test_deep_research_normalizes_profile_and_analyst(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", FakeTicker)
    provider = YahooDeepResearchProvider(Settings(database_url="sqlite:///:memory:"))

    result = provider.research(
        "rklb",
        [ResearchSection.PROFILE, ResearchSection.ANALYST],
    )

    assert result.symbol == "RKLB"
    assert result.sections["profile"]["sector"] == "Industrials"
    assert result.sections["profile"]["shortPercentOfFloat"] == 0.12
    assert result.sections["analyst"]["price_targets"]["mean"] == 90.0
    assert result.sections["analyst"]["eps_revisions"][0]["upLast7days"] == 2
    assert result.errors == {}


def test_option_chain_lists_expirations_and_normalizes_records(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(yahoo_deep_research.yf, "Ticker", FakeTicker)
    provider = YahooDeepResearchProvider(Settings(database_url="sqlite:///:memory:"))

    overview = provider.option_chain("RKLB")
    chain = provider.option_chain("RKLB", "2026-09-18")

    assert overview.expirations == ["2026-09-18", "2026-10-16"]
    assert overview.calls == []
    assert chain.calls[0]["contractSymbol"] == "RKLB260918C00080000"
    assert chain.puts[0]["bid"] == 4.8
    assert chain.underlying["regularMarketPrice"] == 80.0
