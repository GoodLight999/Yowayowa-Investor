from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pandas as pd

from yowayowa.config import Settings
from yowayowa.domain import (
    IndicatorSeries,
    LicenseClass,
    MarketHistory,
    PriceBar,
    Provenance,
)
from yowayowa.providers import yahoo as yahoo_module
from yowayowa.providers.base import ProviderDescriptor
from yowayowa.providers.yahoo import YahooMarketProvider
from yowayowa.services import ai_agent
from yowayowa.services.ai_agent import InvestmentResearchAgent
from yowayowa.services.technical_context import read_technical_context


def test_agent_technical_tool_reads_calculated_series_with_provenance(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dates = pd.date_range("2026-08-01", periods=35, freq="D", tz="UTC", name="Date")
    closes = [20.0 + index for index in range(len(dates))]
    frame = pd.DataFrame(
        {
            "Open": closes,
            "High": [value + 1.0 for value in closes],
            "Low": [value - 1.0 for value in closes],
            "Close": closes,
            "Volume": [1000.0] * len(dates),
        },
        index=dates,
    )
    history_requests: list[dict[str, object]] = []
    calculated_tokens: list[list[str]] = []

    class FixtureTicker:
        def __init__(self, symbol: str) -> None:
            assert symbol == "AAPL"

        def history(self, **kwargs: object) -> pd.DataFrame:
            history_requests.append(kwargs)
            return frame

    monkeypatch.setattr(yahoo_module.yf, "Ticker", FixtureTicker)
    original_calculator = yahoo_module.compute_indicators

    def track_calculation(frame_arg: pd.DataFrame, tokens: list[str]) -> list[IndicatorSeries]:
        calculated_tokens.append(list(tokens))
        return original_calculator(frame_arg, tokens)

    monkeypatch.setattr(yahoo_module, "compute_indicators", track_calculation)
    provider = YahooMarketProvider(Settings(database_url="sqlite:///:memory:", mode="personal"))
    monkeypatch.setattr(ai_agent, "yahoo_market_provider", lambda: provider)
    agent = InvestmentResearchAgent(Settings(database_url="sqlite:///:memory:"), Mock())

    result = agent._execute_tool(
        "get_technical_context",
        {
            "symbol": "aapl",
            "period": "1y",
            "interval": "1d",
            "indicators": ["rsi14"],
            "limit": 2,
        },
    )

    assert result["status"] == "available"
    assert result["symbol"] == "AAPL"
    assert "bars" not in result
    assert len(result["indicators"]) == 1
    rsi = result["indicators"][0]
    assert rsi["name"] == "RSI 14"
    assert rsi["latest_value"] == 100.0
    assert len(rsi["points"]) == 2
    assert rsi["status"] == "available"
    provenance = result["provenance"]
    assert provenance["provider"] == "yahoo/yfinance"
    assert datetime.fromisoformat(provenance["as_of"]) == dates[-1].to_pydatetime()
    assert datetime.fromisoformat(rsi["as_of"]) == dates[-1].to_pydatetime()
    assert history_requests == [{"period": "1y", "interval": "1d", "auto_adjust": False}]
    # The agent delegates to YahooMarketProvider.history, which invokes the existing
    # technical.py calculation exactly once; it never derives indicators from bars.
    assert calculated_tokens == [["rsi14"]]


def test_technical_context_marks_unavailable_history_missing() -> None:
    class MissingProvider:
        descriptor = ProviderDescriptor(
            name="fixture",
            license_class=LicenseClass.PERSONAL_ONLY,
            redistributable=False,
            description="Test fixture",
        )

        def history(
            self, symbol: str, period: str, interval: str, indicators: list[str]
        ) -> MarketHistory:
            assert (symbol, period, interval, indicators) == (
                "MISSING",
                "1y",
                "1d",
                ["rsi14"],
            )
            raise LookupError("No market history returned for MISSING")

    result = read_technical_context(
        MissingProvider(),  # type: ignore[arg-type]
        symbol="missing",
        indicators=["rsi14"],
    )

    assert result["status"] == "missing"
    assert result["indicators"] == []
    assert result["provenance"] == {"provider": "fixture", "as_of": None}
    assert result["missing_reason"] == "No market history returned for MISSING"


def test_technical_context_preserves_missing_indicator_values() -> None:
    as_of = datetime(2026, 9, 28, tzinfo=UTC)
    history = MarketHistory(
        symbol="TEST",
        interval="1d",
        bars=[PriceBar(timestamp=as_of, open=10, high=11, low=9, close=10)],
        indicators=[
            IndicatorSeries(
                name="RSI 14",
                parameters={"length": 14},
                points=[(as_of, None)],
            )
        ],
        provenance=Provenance(
            provider="fixture/technical",
            source="Fixture history",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=as_of,
            as_of=as_of,
        ),
    )

    class NullIndicatorProvider:
        descriptor = ProviderDescriptor(
            name="fixture",
            license_class=LicenseClass.PERSONAL_ONLY,
            redistributable=False,
            description="Test fixture",
        )

        def history(
            self, symbol: str, period: str, interval: str, indicators: list[str]
        ) -> MarketHistory:
            return history

    result = read_technical_context(NullIndicatorProvider(), symbol="TEST")  # type: ignore[arg-type]

    assert result["status"] == "available"
    assert result["provenance"]["provider"] == "fixture/technical"
    assert datetime.fromisoformat(result["provenance"]["as_of"].replace("Z", "+00:00")) == as_of
    assert result["indicators"][0]["latest_value"] is None
    point_as_of = result["indicators"][0]["points"][0]["as_of"]
    assert datetime.fromisoformat(point_as_of) == as_of
    assert result["indicators"][0]["points"][0]["value"] is None
    assert result["indicators"][0]["status"] == "missing"
