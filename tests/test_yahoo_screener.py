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
                {"symbol": "RKLB", "marketCap": 40_000_000_000},
                {"symbol": "ASTS", "marketCap": 20_000_000_000},
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


def _patch_custom_screen(monkeypatch, quotes: list[dict[str, object]], total: int) -> None:  # type: ignore[no-untyped-def]
    def fake_screen(query: object, **kwargs: object) -> dict[str, object]:
        return {"total": total, "quotes": quotes}

    monkeypatch.setattr(yahoo_screener.yf, "EquityQuery", FakeEquityQuery)
    monkeypatch.setattr(yahoo_screener.yf, "screen", fake_screen)


def test_screen_excludes_quotes_missing_filtered_field(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[
            {"symbol": "6178.T", "priceToBook": None, "regularMarketVolume": 150000},
            {"symbol": "7203.T", "priceToBook": 0.7, "regularMarketVolume": 900000},
        ],
        total=2,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="region", operator="is-in", value=["jp"]),
            MarketScreenFilter(field="pricebookratio.quarterly", operator="lt", value=1.0),
        ],
        sort_field="intradaymarketcap",
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["7203.T"]
    assert result.filtered_out == 1
    assert result.total == 2
    assert "Quotes missing a filtered field are excluded (fail-closed)." in (
        result.provenance.notes
    )
    assert (
        "Numeric filters on fields absent from screener quotes rely on the server-side filter."
        not in result.provenance.notes
    )


def test_screen_compound_filter_excludes_missing_field_even_when_other_matches(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[
            {"symbol": "FLST", "regularMarketVolume": 500000, "priceToBook": None},
            {"symbol": "7203.T", "regularMarketVolume": 900000, "priceToBook": 0.7},
        ],
        total=2,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="region", operator="is-in", value=["jp"]),
            MarketScreenFilter(field="dayvolume", operator="gt", value=100000),
            MarketScreenFilter(field="pricebookratio.quarterly", operator="lt", value=1.0),
        ],
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["7203.T"]
    assert result.filtered_out == 1


def test_screen_local_recheck_overrides_server_verdict(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[
            {"symbol": "BAD.T", "priceToBook": 1.5},
            {"symbol": "GOOD.T", "priceToBook": 0.7},
        ],
        total=2,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[MarketScreenFilter(field="pricebookratio.quarterly", operator="lt", value=1.0)],
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["GOOD.T"]
    assert result.filtered_out == 1


def test_screen_region_filter_does_not_exclude(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[
            {"symbol": "6178.T", "regularMarketVolume": 150000},
            {"symbol": "7203.T", "regularMarketVolume": 900000},
        ],
        total=2,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[MarketScreenFilter(field="region", operator="is-in", value=["jp"])],
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["6178.T", "7203.T"]
    assert result.filtered_out == 0
    assert "Quotes missing a filtered field are excluded (fail-closed)." not in (
        result.provenance.notes
    )


def test_screen_unresolvable_field_relies_on_server_side_filter(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[
            {"symbol": "6178.T", "priceToBook": 1.2},
            {"symbol": "7203.T", "priceToBook": 0.7},
        ],
        total=2,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[
            MarketScreenFilter(field="region", operator="is-in", value=["jp"]),
            MarketScreenFilter(field="esg_score", operator="lt", value=10),
        ],
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["6178.T", "7203.T"]
    assert result.filtered_out == 0
    assert (
        "Numeric filters on fields absent from screener quotes rely on the server-side filter."
        in result.provenance.notes
    )
    assert "Quotes missing a filtered field are excluded (fail-closed)." not in (
        result.provenance.notes
    )


def test_screen_beta_filter_is_not_locally_verified(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _patch_custom_screen(
        monkeypatch,
        quotes=[{"symbol": "7203.T", "priceToBook": 0.7}],
        total=1,
    )
    provider = YahooScreenerProvider(Settings(database_url="sqlite:///:memory:"))
    request = MarketScreenRequest(
        filters=[MarketScreenFilter(field="beta", operator="gt", value=1.0)],
        size=100,
    )

    result = provider.screen(request)

    assert [row["symbol"] for row in result.quotes] == ["7203.T"]
    assert result.filtered_out == 0
    assert (
        "Numeric filters on fields absent from screener quotes rely on the server-side filter."
        in result.provenance.notes
    )
