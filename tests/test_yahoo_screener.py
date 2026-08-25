from __future__ import annotations

from yowayowa.config import Settings
from yowayowa.providers import yahoo_screener
from yowayowa.providers.yahoo_screener import YahooScreenerProvider
from yowayowa.research_models import MarketScreenFilter, MarketScreenRequest


class FakeEquityQuery:
    def __init__(self, operator: str, operand: list[object]) -> None:
        self.operator = operator
        self.operand = operand


def test_custom_screener_builds_equity_query(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_screen(query: object, **kwargs: object) -> dict[str, object]:
        captured["query"] = query
        captured.update(kwargs)
        return {
            "total": 2,
            "quotes": [
                {"symbol": "RKLB", "intradaymarketcap": 40_000_000_000},
                {"symbol": "ASTS", "intradaymarketcap": 20_000_000_000},
            ],
        }

    monkeypatch.setattr(yahoo_screener.yf, "EquityQuery", FakeEquityQuery)
    monkeypatch.setattr(yahoo_screener.yf, "screen", fake_screen)
    monkeypatch.setattr(
        yahoo_screener.yf,
        "PREDEFINED_SCREENER_QUERIES",
        {"most_actives": object()},
        raising=False,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="region", operator="is-in", value=["us", "jp"]),
            MarketScreenFilter(field="intradaymarketcap", operator="gt", value=1_000_000_000),
        ],
        sort_field="intradaymarketcap",
        size=100,
    )

    result = provider.screen(request)
    query = captured["query"]

    assert isinstance(query, FakeEquityQuery)
    assert query.operator == "and"
    assert len(query.operand) == 2
    assert captured["size"] == 100
    assert captured["sortField"] == "intradaymarketcap"
    assert [row["symbol"] for row in result.quotes] == ["RKLB", "ASTS"]
    assert result.total == 2


def test_predefined_screener_uses_count_parameter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_screen(query: object, **kwargs: object) -> dict[str, object]:
        captured["query"] = query
        captured.update(kwargs)
        return {"total": 0, "quotes": []}

    monkeypatch.setattr(yahoo_screener.yf, "screen", fake_screen)
    monkeypatch.setattr(
        yahoo_screener.yf,
        "PREDEFINED_SCREENER_QUERIES",
        {"most_actives": object()},
        raising=False,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    result = provider.screen(MarketScreenRequest(predefined="most_actives", size=25))

    assert captured["query"] == "most_actives"
    assert captured["count"] == 25
    assert result.quotes == []
