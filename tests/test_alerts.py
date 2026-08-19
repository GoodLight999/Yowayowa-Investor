from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from yowayowa.calendar_models import TrackedCalendarEvent, TrackedEventType
from yowayowa.db import Base
from yowayowa.domain import (
    AlertOperator,
    LicenseClass,
    MarketQuote,
    MarketQuoteBatch,
    PriceAlertCreate,
    Provenance,
)
from yowayowa.news_models import EventSubscriptionCreate
from yowayowa.services.alerts import (
    acknowledge_event_inbox,
    create_alert,
    create_event_subscription,
    delete_event_subscription,
    evaluate_alerts,
    evaluate_event_subscriptions,
    list_event_inbox,
)
from yowayowa.services.watchlists import add_symbols, create_watchlist


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        self.calls.append(symbols)
        return MarketQuoteBatch(
            quotes={
                "AAA": MarketQuote(
                    symbol="AAA",
                    price=101,
                    previous_close=99,
                    as_of=datetime.now(UTC),
                ),
                "BBB": MarketQuote(
                    symbol="BBB",
                    price=49,
                    previous_close=51,
                    as_of=datetime.now(UTC),
                ),
            },
            provenance=Provenance(
                provider="fake",
                source="fixture",
                license_class=LicenseClass.OFFICIAL_PUBLIC,
                retrieved_at=datetime.now(UTC),
            ),
        )


class FakeEventProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], date, date, list[TrackedEventType] | None]] = []

    def events(
        self,
        symbols: list[str],
        start: date,
        end: date,
        event_types: list[TrackedEventType] | None = None,
    ) -> tuple[list[TrackedCalendarEvent], list[str], Provenance]:
        self.calls.append((symbols, start, end, event_types))
        event_date = start + timedelta(days=3)
        return (
            [
                TrackedCalendarEvent(
                    event_type="earnings",
                    subtype="earnings",
                    starts_at=datetime.combine(event_date, time(), tzinfo=UTC),
                    title="Earnings date",
                    symbol="RKLB",
                )
            ],
            [],
            Provenance(
                provider="fake-events",
                source="fixture calendar",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=datetime.now(UTC),
            ),
        )


def test_alert_evaluation_batches_symbols_and_disables_triggered_alerts() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        with Session(engine, expire_on_commit=False) as session:
            create_alert(
                session,
                PriceAlertCreate(symbol="aaa", operator=AlertOperator.ABOVE, target="100"),
            )
            create_alert(
                session,
                PriceAlertCreate(symbol="bbb", operator=AlertOperator.BELOW, target="50"),
            )
            result = evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert provider.calls == [["AAA", "BBB"]]
            assert all(item.triggered_at is not None for item in result.alerts)
            assert all(item.enabled is False for item in result.alerts)
    finally:
        engine.dispose()


def test_empty_alert_evaluation_does_not_call_market_provider() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        with Session(engine, expire_on_commit=False) as session:
            result = evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert result.alerts == []
            assert provider.calls == []
            assert "no market-data request" in result.provenance.notes[0].lower()
    finally:
        engine.dispose()


def test_event_subscription_deduplicates_inbox_and_acknowledges() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeEventProvider()
        with Session(engine, expire_on_commit=False) as session:
            watchlist = create_watchlist(session, "Events")
            add_symbols(session, watchlist.id, ["RKLB"])
            subscription = create_event_subscription(
                session,
                EventSubscriptionCreate(
                    scope="watchlist",
                    scope_id=watchlist.id,
                    event_types=["earnings"],
                    lead_days=7,
                ),
            )

            first = evaluate_event_subscriptions(
                session,
                provider,
                today=date(2026, 8, 13),
            )
            second = evaluate_event_subscriptions(
                session,
                provider,
                today=date(2026, 8, 13),
            )

            assert len(first.inbox) == 1
            assert len(second.inbox) == 1
            assert first.inbox[0].symbol == "RKLB"
            assert len(provider.calls) == 2
            assert provider.calls[0][0] == ["RKLB"]
            assert provider.calls[0][3] == ["earnings"]

            acknowledged = acknowledge_event_inbox(session, first.inbox[0].id)
            assert acknowledged.acknowledged_at is not None
            assert list_event_inbox(session) == []
            assert len(list_event_inbox(session, include_acknowledged=True)) == 1

            delete_event_subscription(session, subscription.id)
            assert list_event_inbox(session, include_acknowledged=True) == []
    finally:
        engine.dispose()


def test_event_subscription_evaluation_without_subscriptions_skips_provider() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeEventProvider()
        with Session(engine, expire_on_commit=False) as session:
            result = evaluate_event_subscriptions(
                session,
                provider,
                today=date(2026, 8, 13),
            )
            assert result.subscriptions == []
            assert result.inbox == []
            assert provider.calls == []
            assert "no enabled event subscriptions" in result.provenance.notes[0].lower()
    finally:
        engine.dispose()


def test_event_subscription_api_lifecycle(monkeypatch) -> None:
    from yowayowa.api import calendar_routes
    from yowayowa.api.app import app

    provider = FakeEventProvider()
    monkeypatch.setattr(calendar_routes, "yahoo_tracked_calendar_provider", lambda: provider)

    with TestClient(app) as client:
        watchlist = client.post("/v1/watchlists", json={"name": "API Events"})
        assert watchlist.status_code == 201
        watchlist_id = watchlist.json()["id"]
        client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=["RKLB"]).raise_for_status()

        created = client.post(
            "/v1/event-subscriptions",
            json={
                "scope": "watchlist",
                "scope_id": watchlist_id,
                "event_types": ["earnings"],
                "lead_days": 7,
            },
        )
        assert created.status_code == 201
        subscription_id = created.json()["id"]

        evaluated = client.post("/v1/event-subscriptions/evaluate")
        assert evaluated.status_code == 200
        payload = evaluated.json()
        assert len(payload["inbox"]) == 1
        assert payload["inbox"][0]["symbol"] == "RKLB"
        inbox_id = payload["inbox"][0]["id"]

        acknowledged = client.post(f"/v1/event-inbox/{inbox_id}/ack")
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged_at"] is not None
        assert client.get("/v1/event-inbox").json() == []
        assert len(client.get("/v1/event-inbox?include_acknowledged=true").json()) == 1

        removed = client.delete(f"/v1/event-subscriptions/{subscription_id}")
        assert removed.status_code == 204
        assert client.get("/v1/event-subscriptions").json() == []
        assert client.get("/v1/event-inbox?include_acknowledged=true").json() == []
