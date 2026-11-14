from collections import Counter
from datetime import UTC, date, datetime

from starlette.testclient import TestClient

from yowayowa.calendar_models import TrackedCalendarEvent
from yowayowa.domain import LicenseClass, NewsFeed, NewsItem, Provenance


def _provenance(source: str = "Fixture ticker calendar") -> Provenance:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    return Provenance(
        provider="fixture",
        source=source,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=now,
        as_of=now,
    )


class FakeTrackedCalendarProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], date, date, list[str] | None]] = []

    def events(self, symbols, start, end, event_types=None):  # type: ignore[no-untyped-def]
        self.calls.append((list(symbols), start, end, event_types))
        events = []
        if "RKLB" in symbols:
            events.append(
                TrackedCalendarEvent(
                    event_type="dividend",
                    subtype="ex_dividend",
                    starts_at=datetime(2026, 8, 16, tzinfo=UTC),
                    title="Ex-dividend date",
                    symbol="RKLB",
                )
            )
        return events, [], _provenance()


class FakeNewsProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def news(self, query: str, limit: int = 12) -> NewsFeed:
        self.calls.append((query, limit))
        if query == "SOFI":
            raise RuntimeError("fixture unavailable")
        now = datetime(2026, 8, 13, tzinfo=UTC)
        return NewsFeed(
            query=query,
            items=[
                NewsItem(
                    id="shared-story",
                    title="Shared space story",
                    publisher="Fixture Wire",
                    published_at=now,
                    url="https://example.test/shared",
                    symbol=query,
                )
            ],
            provenance=_provenance("Fixture news"),
        )


def test_tracked_calendar_api_resolves_all_saved_symbols(monkeypatch) -> None:
    from yowayowa.api import calendar_routes
    from yowayowa.api.app import app

    provider = FakeTrackedCalendarProvider()
    monkeypatch.setattr(calendar_routes, "yahoo_tracked_calendar_provider", lambda: provider)

    with TestClient(app) as client:
        watchlist = client.post("/v1/watchlists", json={"name": "Tracked"})
        assert watchlist.status_code == 201
        watchlist_id = watchlist.json()["id"]
        added = client.post(
            f"/v1/watchlists/{watchlist_id}/symbols",
            json=["RKLB"],
        )
        assert added.status_code == 200

        portfolio = client.post(
            "/v1/portfolios",
            json={"name": "Growth", "base_currency": "USD"},
        )
        assert portfolio.status_code == 201
        portfolio_id = portfolio.json()["id"]
        position = client.put(
            f"/v1/portfolios/{portfolio_id}/positions",
            json={"symbol": "ASTS", "quantity": "5", "currency": "USD"},
        )
        assert position.status_code == 200

        response = client.get(
            "/v1/calendar/tracked",
            params={
                "start": "2026-08-13",
                "end": "2026-08-20",
                "scope": "all",
                "types": "earnings,dividend",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["symbols"] == ["ASTS", "RKLB"]
        assert payload["events"][0]["symbol"] == "RKLB"
        assert payload["events"][0]["subtype"] == "ex_dividend"
        assert provider.calls[0][0] == ["ASTS", "RKLB"]
        assert provider.calls[0][3] == ["earnings", "dividend"]


def test_tracked_calendar_api_supports_watchlist_scope(monkeypatch) -> None:
    from yowayowa.api import calendar_routes
    from yowayowa.api.app import app

    provider = FakeTrackedCalendarProvider()
    monkeypatch.setattr(calendar_routes, "yahoo_tracked_calendar_provider", lambda: provider)

    with TestClient(app) as client:
        watchlist = client.post("/v1/watchlists", json={"name": "Income"})
        assert watchlist.status_code == 201
        watchlist_id = watchlist.json()["id"]
        client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=["RKLB"]).raise_for_status()

        response = client.get(
            "/v1/calendar/tracked",
            params={
                "start": "2026-08-13",
                "end": "2026-08-20",
                "scope": "watchlist",
                "scope_id": watchlist_id,
            },
        )

        assert response.status_code == 200
        assert response.json()["symbols"] == ["RKLB"]
        assert provider.calls[0][0] == ["RKLB"]


def test_tracked_calendar_api_requires_scope_id_for_named_scope() -> None:
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get(
            "/v1/calendar/tracked",
            params={"scope": "portfolio"},
        )

    assert response.status_code == 422
    assert "scope_id is required" in response.json()["detail"]


def test_saved_news_api_merges_related_symbols_isolates_failures_and_caches(monkeypatch) -> None:
    from yowayowa.api import research_routes
    from yowayowa.api.app import app

    provider = FakeNewsProvider()
    monkeypatch.setattr(research_routes, "YahooResearchProvider", lambda settings: provider)

    with TestClient(app) as client:
        watchlist = client.post("/v1/watchlists", json={"name": "News"}).json()
        client.post(
            f"/v1/watchlists/{watchlist['id']}/symbols", json=["RKLB", "SOFI"]
        ).raise_for_status()
        portfolio = client.post(
            "/v1/portfolios",
            json={"name": "News Portfolio", "base_currency": "USD"},
        ).json()
        client.put(
            f"/v1/portfolios/{portfolio['id']}/positions",
            json={"symbol": "ASTS", "quantity": "5", "currency": "USD"},
        ).raise_for_status()

        params = {"scope": "all", "limit": 20, "per_symbol_limit": 4}
        response = client.get("/v1/news/saved", params=params)
        second = client.get("/v1/news/saved", params=params)

    assert response.status_code == 200
    assert second.status_code == 200
    payload = response.json()
    assert payload["symbols"] == ["ASTS", "RKLB", "SOFI"]
    assert payload["unavailable_symbols"] == ["SOFI"]
    assert payload["items"][0]["symbols"] == ["ASTS", "RKLB"]
    counts = Counter(symbol for symbol, _ in provider.calls)
    assert counts == Counter({"SOFI": 2, "ASTS": 1, "RKLB": 1})
    assert all(limit == 4 for _, limit in provider.calls)


def test_saved_news_api_requires_scope_id_for_named_scope() -> None:
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/news/saved", params={"scope": "portfolio"})

    assert response.status_code == 422
    assert "scope_id is required" in response.json()["detail"]
