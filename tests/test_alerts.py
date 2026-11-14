from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from yowayowa.calendar_models import TrackedCalendarEvent, TrackedEventType
from yowayowa.config import Settings
from yowayowa.db import (
    Base,
    PriceAlertRecord,
    dispose_database,
    get_session,
    init_database,
)
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
    acknowledge_price_alert_notification,
    create_alert,
    create_event_subscription,
    delete_alert,
    delete_event_subscription,
    evaluate_alerts,
    evaluate_event_subscriptions,
    list_event_inbox,
    list_price_alert_notifications,
)
from yowayowa.services.watchlists import add_symbols, create_watchlist


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.failure: Exception | None = None

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        self.calls.append(symbols)
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
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


def test_triggered_price_alert_is_deduplicated_and_acknowledgeable() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        with Session(engine, expire_on_commit=False) as session:
            alert = create_alert(
                session,
                PriceAlertCreate(symbol="aaa", operator=AlertOperator.ABOVE, target="100"),
            )
            first = evaluate_alerts(session, provider)  # type: ignore[arg-type]
            notifications = list_price_alert_notifications(session)
            assert len(notifications) == 1
            assert notifications[0].alert_id == alert.id
            assert notifications[0].symbol == "AAA"
            assert notifications[0].triggered_price == Decimal("101")
            assert notifications[0].provenance.provider == "fake"
            assert notifications[0].provenance.source == "fixture"
            assert notifications[0].acknowledged_at is None
            assert first.alerts[0].enabled is False

            second = evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert len(second.alerts) == 1
            assert len(provider.calls) == 1
            assert len(list_price_alert_notifications(session)) == 1

            # Removing a rule does not erase its still-unread trigger notice.
            delete_alert(session, alert.id)
            assert len(list_price_alert_notifications(session)) == 1

            notification_id = notifications[0].id
            acknowledged = acknowledge_price_alert_notification(session, notification_id)
            assert acknowledged.acknowledged_at is not None
            assert list_price_alert_notifications(session) == []
            assert len(list_price_alert_notifications(session, include_acknowledged=True)) == 1
            assert (
                acknowledge_price_alert_notification(session, notification_id).acknowledged_at
                == acknowledged.acknowledged_at
            )

            # If SQLite reuses a deleted alert's integer id, the new generation
            # must still receive its own notification.
            create_alert(
                session,
                PriceAlertCreate(symbol="aaa", operator=AlertOperator.ABOVE, target="100"),
            )
            evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert len(list_price_alert_notifications(session)) == 1
            assert len(list_price_alert_notifications(session, include_acknowledged=True)) == 2
    finally:
        engine.dispose()


def test_provider_failure_does_not_suppress_notification_retry() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        provider = FakeProvider()
        provider.failure = RuntimeError("fixture quote failure")
        with Session(engine, expire_on_commit=False) as session:
            create_alert(
                session,
                PriceAlertCreate(symbol="aaa", operator=AlertOperator.ABOVE, target="100"),
            )
            with pytest.raises(RuntimeError, match="fixture quote failure"):
                evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert list_price_alert_notifications(session) == []
            assert session.query(PriceAlertRecord).one().enabled is True

            result = evaluate_alerts(session, provider)  # type: ignore[arg-type]
            assert result.alerts[0].triggered_at is not None
            assert len(provider.calls) == 2
            assert len(list_price_alert_notifications(session)) == 1
    finally:
        engine.dispose()


def test_price_alert_notification_api_lifecycle(monkeypatch) -> None:
    from yowayowa.api import routes
    from yowayowa.api.app import app

    provider = FakeProvider()
    monkeypatch.setattr(routes, "yahoo_market_provider", lambda: provider)

    with TestClient(app) as client:
        created = client.post(
            "/v1/alerts",
            json={"symbol": "AAA", "operator": "above", "target": "100"},
        )
        assert created.status_code == 201
        evaluated = client.post("/v1/alerts/evaluate")
        assert evaluated.status_code == 200

        inbox = client.get("/v1/alert-inbox")
        assert inbox.status_code == 200
        assert len(inbox.json()) == 1
        notification_id = inbox.json()[0]["id"]

        acknowledged = client.post(f"/v1/alert-inbox/{notification_id}/ack")
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged_at"] is not None
        assert client.get("/v1/alert-inbox").json() == []
        assert len(client.get("/v1/alert-inbox?include_acknowledged=true").json()) == 1
        assert client.post("/v1/alert-inbox/999/ack").status_code == 404


def test_init_database_adds_alert_inbox_without_losing_legacy_alert(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy-alerts.db'}"
    old_engine = create_engine(database_url)
    try:
        Base.metadata.create_all(old_engine, tables=[cast(Table, PriceAlertRecord.__table__)])
        with Session(old_engine) as session:
            session.add(
                PriceAlertRecord(
                    symbol="AAA",
                    operator="above",
                    target=Decimal("100"),
                    enabled=True,
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()
    finally:
        old_engine.dispose()

    init_database(Settings(database_url=database_url))
    try:
        with get_session() as session:
            assert len(list_price_alert_notifications(session)) == 0
            assert session.query(PriceAlertRecord).one().symbol == "AAA"
    finally:
        dispose_database()


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
