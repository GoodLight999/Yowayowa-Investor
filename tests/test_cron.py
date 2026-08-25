import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from yowayowa.api.app import _authorize_cron
from yowayowa.config import Settings


def _request(authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_personal_mode_can_use_deployment_protection_without_cron_secret() -> None:
    settings = Settings(database_url="sqlite:///:memory:", mode="personal", cron_secret=None)
    _authorize_cron(_request(), settings)


def test_public_mode_requires_cron_secret() -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="public",
        api_token="api-token",
        cron_secret=None,
    )
    with pytest.raises(HTTPException) as exc:
        _authorize_cron(_request(), settings)
    assert exc.value.status_code == 503


def test_cron_secret_requires_matching_bearer_token() -> None:
    settings = Settings(database_url="sqlite:///:memory:", cron_secret="cron-token")
    with pytest.raises(HTTPException) as exc:
        _authorize_cron(_request("Bearer wrong"), settings)
    assert exc.value.status_code == 401
    _authorize_cron(_request("Bearer cron-token"), settings)


def test_daily_maintenance_skips_event_provider_without_subscriptions(monkeypatch) -> None:
    app_module = importlib.import_module("yowayowa.api.app")
    called = False

    def event_provider() -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(app_module, "yahoo_market_provider", object)
    monkeypatch.setattr(app_module, "yahoo_tracked_calendar_provider", event_provider)
    monkeypatch.setattr(app_module, "list_event_subscriptions", lambda session: [])

    result = app_module.daily_maintenance(_request())

    assert called is False
    assert result["event_subscriptions_checked"] == 0
    assert result["event_inbox_unread"] == 0
    assert result["event_failure"] is None
    assert result["personal_market_tasks_skipped"] is False


def test_daily_maintenance_isolates_event_evaluation_failure(monkeypatch) -> None:
    app_module = importlib.import_module("yowayowa.api.app")

    monkeypatch.setattr(app_module, "yahoo_market_provider", object)
    monkeypatch.setattr(app_module, "list_event_subscriptions", lambda session: [object()])
    monkeypatch.setattr(app_module, "yahoo_tracked_calendar_provider", object)

    def fail_events(session, provider) -> None:
        raise RuntimeError("fixture event failure")

    monkeypatch.setattr(app_module, "evaluate_event_subscriptions", fail_events)

    result = app_module.daily_maintenance(_request())

    assert result["ok"] is False
    assert result["event_failure"] == "RuntimeError"
    assert result["portfolio_failures"] == {}


def test_public_daily_maintenance_never_instantiates_personal_yahoo_providers(monkeypatch) -> None:
    app_module = importlib.import_module("yowayowa.api.app")
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="public",
        api_token="api-token",
        cron_secret="cron-token",
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)

    def forbidden_provider() -> object:
        raise AssertionError("personal provider must not run in public mode")

    monkeypatch.setattr(app_module, "yahoo_market_provider", forbidden_provider)
    monkeypatch.setattr(app_module, "yahoo_tracked_calendar_provider", forbidden_provider)

    result = app_module.daily_maintenance(_request("Bearer cron-token"))

    assert result["ok"] is True
    assert result["alerts_checked"] == 0
    assert result["portfolios_snapshotted"] == []
    assert result["personal_market_tasks_skipped"] is True


def test_daily_maintenance_syncs_only_one_completed_japan_day_for_edinet(monkeypatch) -> None:
    app_module = importlib.import_module("yowayowa.api.app")
    settings = Settings(
        database_url="sqlite:///:memory:",
        mode="public",
        api_token="api-token",
        cron_secret="cron-token",
        edinet_api_key="edinet-key",
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    provider = object()
    monkeypatch.setattr(app_module, "edinet_client", lambda: provider)
    synchronized: list[tuple[object, object, object]] = []

    def sync(session, client, start_date, end_date):
        synchronized.append((client, start_date, end_date))
        return SimpleNamespace(days_synced=1, failures=[])

    monkeypatch.setattr(app_module, "sync_filing_index", sync)

    result = app_module.daily_maintenance(_request("Bearer cron-token"))

    assert len(synchronized) == 1
    assert synchronized[0][0] is provider
    assert synchronized[0][1] == synchronized[0][2]
    assert result["edinet_index_days_synced"] == 1
    assert result["edinet_index_failure"] is None
    assert result["ok"] is True
