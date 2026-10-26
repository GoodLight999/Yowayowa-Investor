"""Regression net for the ``/v1`` API error contract (kanban task t_1c346eaf).

What is locked down, for every core route declared in ``yowayowa/api/routes.py``:

* ``ok``       - the documented success status plus documented JSON top-level keys;
* ``invalid``  - 422 ``{"detail": ...}`` (list body for framework validation,
                 string body for route-authored rejections), plus 404/405 for
                 unknown paths and undeclared methods;
* ``unknown``  - 404 ``{"detail": "..."}`` for unknown ids;
* ``upstream`` - 403 / 424 / 502 ``{"detail": "..."}`` for provider failures;
* ``timeout``  - 502 ``{"detail": "..."}`` when an upstream call times out.

A failure body must stay JSON and must never carry a Python traceback, a frame
file name, a virtualenv / ``site-packages`` path, or an internal exception
payload. The suite also pins the generic 500 path: an unhandled defect returns a
bodyless ``Internal Server Error`` instead of leaking the exception.

Nothing here touches real network, a real database, or any external service: the
provider factories imported by ``routes.py`` are replaced with in-module stubs and
an autouse socket guard rejects every AF_INET/AF_INET6 connect, so an accidental
real call fails loudly instead of silently reaching the internet.

``src/`` is read-only for this task: a violated contract is reported with a
reproduction, never patched here.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from starlette.testclient import TestClient

from yowayowa.domain import (
    CalendarEvent,
    EventCalendar,
    Instrument,
    LicenseClass,
    MarketHistory,
    MarketOverview,
    MarketQuote,
    MarketQuoteBatch,
    NewsFeed,
    Provenance,
)
from yowayowa.providers.base import ProviderPolicyError

#: A TestClient response. Typed loosely because the httpx flavour resolved in
#: this environment differs from the one starlette re-exports.
ApiResponse = Any

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

#: Marker that must never reach a response body. It looks like an internal
#: identifier plus a filesystem path, i.e. exactly what a leaked exception
#: string would expose.
INTERNAL_MARKER = "INTERNAL-MARKER-9f3c7a:/root/private/ledger.sqlite"

#: Fragments that only appear when server-side internals are echoed back.
LEAK_FRAGMENTS = (
    "Traceback (most recent call last)",
    'File "',
    "site-packages",
    ".venv/",
    "src/yowayowa/",
    "pydantic_core",
    "sqlalchemy.exc",
)

#: Message a personal-mode-only provider raises when public mode blocks it.
POLICY_MESSAGE = "Yahoo research is personal mode only"


@dataclass(frozen=True)
class ContractRow:
    """One cell of the route x case contract table."""

    case: str
    route: str
    kind: str
    status: int
    body: str = "json"
    keys: tuple[str, ...] = ()
    detail: str | None = None
    detail_type: str | None = None


#: The contract table. ``route`` uses the declared method + path; ``*`` marks a
#: row that exercises the API surface instead of a single declared route.
CONTRACT: tuple[ContractRow, ...] = (
    # --- GET /v1/health -----------------------------------------------------
    ContractRow(
        "health.ok",
        "GET /v1/health",
        "ok",
        200,
        keys=("status", "mode", "market_provider", "capabilities"),
    ),
    ContractRow(
        "health.unauthorized",
        "GET /v1/health",
        "auth",
        401,
        keys=("detail",),
        detail="Invalid API token",
        detail_type="str",
    ),
    ContractRow("health.authorized", "GET /v1/health", "auth", 200, keys=("status",)),
    ContractRow(
        "health.method_not_allowed",
        "*",
        "invalid",
        405,
        keys=("detail",),
        detail="Method Not Allowed",
        detail_type="str",
    ),
    ContractRow(
        "api.unknown_path",
        "*",
        "invalid",
        404,
        keys=("detail",),
        detail="Not Found",
        detail_type="str",
    ),
    # --- GET /v1/instruments/search ----------------------------------------
    ContractRow("search.ok", "GET /v1/instruments/search", "ok", 200, body="list"),
    ContractRow(
        "search.missing_query",
        "GET /v1/instruments/search",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "search.query_too_long",
        "GET /v1/instruments/search",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "search.upstream_failure",
        "GET /v1/instruments/search",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "search.timeout",
        "GET /v1/instruments/search",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/news/{query} ----------------------------------------------
    ContractRow(
        "news.ok", "GET /v1/news/{query}", "ok", 200, keys=("query", "items", "provenance")
    ),
    ContractRow(
        "news.invalid_limit",
        "GET /v1/news/{query}",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "news.policy_blocked",
        "GET /v1/news/{query}",
        "upstream",
        403,
        keys=("detail",),
        detail=POLICY_MESSAGE,
        detail_type="str",
    ),
    ContractRow(
        "news.upstream_failure",
        "GET /v1/news/{query}",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/calendar ---------------------------------------------------
    ContractRow(
        "calendar.ok",
        "GET /v1/calendar",
        "ok",
        200,
        keys=("start", "end", "events", "provenance"),
    ),
    ContractRow(
        "calendar.inverted_range",
        "GET /v1/calendar",
        "invalid",
        422,
        keys=("detail",),
        detail="Calendar end must not be before start",
        detail_type="str",
    ),
    ContractRow(
        "calendar.range_too_wide",
        "GET /v1/calendar",
        "invalid",
        422,
        keys=("detail",),
        detail="Calendar range is limited to 93 days",
        detail_type="str",
    ),
    ContractRow(
        "calendar.unknown_event_type",
        "GET /v1/calendar",
        "invalid",
        422,
        keys=("detail",),
        detail="Unknown calendar event types: bogus",
        detail_type="str",
    ),
    ContractRow(
        "calendar.invalid_date",
        "GET /v1/calendar",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "calendar.policy_blocked",
        "GET /v1/calendar",
        "upstream",
        403,
        keys=("detail",),
        detail=POLICY_MESSAGE,
        detail_type="str",
    ),
    ContractRow(
        "calendar.upstream_failure",
        "GET /v1/calendar",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "calendar.timeout",
        "GET /v1/calendar",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/markets/overview ------------------------------------------
    ContractRow(
        "markets_overview.ok",
        "GET /v1/markets/overview",
        "ok",
        200,
        keys=("items", "unavailable_symbols", "provenance"),
    ),
    ContractRow(
        "markets_overview.policy_blocked",
        "GET /v1/markets/overview",
        "upstream",
        403,
        keys=("detail",),
        detail=POLICY_MESSAGE,
        detail_type="str",
    ),
    ContractRow(
        "markets_overview.upstream_failure",
        "GET /v1/markets/overview",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "markets_overview.timeout",
        "GET /v1/markets/overview",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/markets/quotes --------------------------------------------
    ContractRow(
        "markets_quotes.ok",
        "GET /v1/markets/quotes",
        "ok",
        200,
        keys=("quotes", "unavailable_symbols", "provenance"),
    ),
    ContractRow(
        "markets_quotes.empty_symbols",
        "GET /v1/markets/quotes",
        "invalid",
        422,
        keys=("detail",),
        detail="At least one symbol is required",
        detail_type="str",
    ),
    ContractRow(
        "markets_quotes.invalid_symbol",
        "GET /v1/markets/quotes",
        "invalid",
        422,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "markets_quotes.too_many_symbols",
        "GET /v1/markets/quotes",
        "invalid",
        422,
        keys=("detail",),
        detail="At most 100 symbols may be quoted at once",
        detail_type="str",
    ),
    ContractRow(
        "markets_quotes.upstream_failure",
        "GET /v1/markets/quotes",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "markets_quotes.timeout",
        "GET /v1/markets/quotes",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/markets/{symbol}/history ----------------------------------
    ContractRow(
        "markets_history.ok",
        "GET /v1/markets/{symbol}/history",
        "ok",
        200,
        keys=("symbol", "interval", "bars", "indicators", "provenance"),
    ),
    ContractRow(
        "markets_history.invalid_period",
        "GET /v1/markets/{symbol}/history",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "markets_history.unsupported_interval",
        "GET /v1/markets/{symbol}/history",
        "invalid",
        422,
        keys=("detail",),
        detail="Unsupported interval: 3h",
        detail_type="str",
    ),
    ContractRow(
        "markets_history.upstream_failure",
        "GET /v1/markets/{symbol}/history",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "markets_history.timeout",
        "GET /v1/markets/{symbol}/history",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/filings/edinet --------------------------------------------
    ContractRow(
        "filings_edinet.missing_date",
        "GET /v1/filings/edinet",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "filings_edinet.not_configured",
        "GET /v1/filings/edinet",
        "upstream",
        424,
        keys=("detail",),
        detail="EDINET_API_KEY is not configured",
        detail_type="str",
    ),
    ContractRow(
        "filings_edinet.upstream_failure",
        "GET /v1/filings/edinet",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- GET /v1/compare/metrics -------------------------------------------
    ContractRow("compare_metrics.ok", "GET /v1/compare/metrics", "ok", 200, body="list"),
    # --- watchlists ---------------------------------------------------------
    ContractRow("watchlists.list_ok", "GET /v1/watchlists", "ok", 200, body="list"),
    ContractRow(
        "watchlists.create_ok",
        "POST /v1/watchlists",
        "ok",
        201,
        keys=("id", "name", "symbols", "created_at", "updated_at"),
    ),
    ContractRow(
        "watchlists.create_conflict",
        "POST /v1/watchlists",
        "conflict",
        409,
        keys=("detail",),
        detail="Watchlist name already exists",
        detail_type="str",
    ),
    ContractRow(
        "watchlists.create_invalid_name",
        "POST /v1/watchlists",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "watchlists.add_symbols_ok",
        "POST /v1/watchlists/{watchlist_id}/symbols",
        "ok",
        200,
        keys=("id", "symbols"),
    ),
    ContractRow(
        "watchlists.add_symbols_unknown_id",
        "POST /v1/watchlists/{watchlist_id}/symbols",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "watchlists.add_symbols_invalid_symbol",
        "POST /v1/watchlists/{watchlist_id}/symbols",
        "invalid",
        422,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "watchlists.remove_symbol_ok",
        "DELETE /v1/watchlists/{watchlist_id}/symbols/{symbol}",
        "ok",
        200,
        keys=("id", "symbols"),
    ),
    ContractRow(
        "watchlists.remove_symbol_unknown_id",
        "DELETE /v1/watchlists/{watchlist_id}/symbols/{symbol}",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    # --- portfolios ---------------------------------------------------------
    ContractRow("portfolios.list_ok", "GET /v1/portfolios", "ok", 200, body="list"),
    ContractRow(
        "portfolios.get_ok",
        "GET /v1/portfolios/{portfolio_id}",
        "ok",
        200,
        keys=("id", "name", "base_currency", "positions", "created_at", "updated_at"),
    ),
    ContractRow(
        "portfolios.get_unknown",
        "GET /v1/portfolios/{portfolio_id}",
        "unknown",
        404,
        keys=("detail",),
        detail="Portfolio 999999 not found",
        detail_type="str",
    ),
    ContractRow(
        "portfolios.create_ok",
        "POST /v1/portfolios",
        "ok",
        201,
        keys=("id", "name", "base_currency", "positions", "created_at", "updated_at"),
    ),
    ContractRow(
        "portfolios.create_invalid_currency",
        "POST /v1/portfolios",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "portfolios.delete_ok",
        "DELETE /v1/portfolios/{portfolio_id}",
        "ok",
        200,
        keys=(
            "portfolio_id",
            "name",
            "force",
            "deleted_positions",
            "deleted_snapshots",
            "deleted_applied_executions",
        ),
    ),
    ContractRow(
        "portfolios.delete_unknown",
        "DELETE /v1/portfolios/{portfolio_id}",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.delete_nonempty",
        "DELETE /v1/portfolios/{portfolio_id}",
        "conflict",
        409,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.delete_force",
        "DELETE /v1/portfolios/{portfolio_id}",
        "ok",
        200,
        keys=("force", "deleted_positions"),
    ),
    ContractRow(
        "portfolios.position_upsert_ok",
        "PUT /v1/portfolios/{portfolio_id}/positions",
        "ok",
        200,
        keys=("id", "positions"),
    ),
    ContractRow(
        "portfolios.position_upsert_unknown_portfolio",
        "PUT /v1/portfolios/{portfolio_id}/positions",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.position_upsert_invalid_quantity",
        "PUT /v1/portfolios/{portfolio_id}/positions",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "portfolios.position_upsert_invalid_symbol",
        "PUT /v1/portfolios/{portfolio_id}/positions",
        "invalid",
        422,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.position_upsert_version_conflict",
        "PUT /v1/portfolios/{portfolio_id}/positions",
        "conflict",
        409,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.bulk_upsert_ok",
        "PUT /v1/portfolios/{portfolio_id}/positions/bulk",
        "ok",
        200,
        keys=("id", "positions"),
    ),
    ContractRow(
        "portfolios.remove_position_ok",
        "DELETE /v1/portfolios/{portfolio_id}/positions/{symbol}",
        "ok",
        200,
        keys=("id", "positions"),
    ),
    ContractRow(
        "portfolios.remove_position_version_conflict",
        "DELETE /v1/portfolios/{portfolio_id}/positions/{symbol}",
        "conflict",
        409,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.analytics_ok",
        "GET /v1/portfolios/{portfolio_id}/analytics",
        "ok",
        200,
        keys=(
            "portfolio_id",
            "net_market_value",
            "positions",
            "provenance",
            "evaluated_at",
        ),
    ),
    ContractRow(
        "portfolios.analytics_unknown",
        "GET /v1/portfolios/{portfolio_id}/analytics",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.analytics_upstream_failure",
        "GET /v1/portfolios/{portfolio_id}/analytics",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.analytics_timeout",
        "GET /v1/portfolios/{portfolio_id}/analytics",
        "timeout",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.snapshots_ok",
        "GET /v1/portfolios/{portfolio_id}/snapshots",
        "ok",
        200,
        body="list",
    ),
    ContractRow(
        "portfolios.snapshots_unknown",
        "GET /v1/portfolios/{portfolio_id}/snapshots",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "portfolios.export_ok",
        "GET /v1/portfolios/{portfolio_id}/export.csv",
        "ok",
        200,
        body="csv",
    ),
    ContractRow(
        "portfolios.export_unknown",
        "GET /v1/portfolios/{portfolio_id}/export.csv",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    # --- alerts -------------------------------------------------------------
    ContractRow("alerts.list_ok", "GET /v1/alerts", "ok", 200, body="list"),
    ContractRow(
        "alerts.create_ok",
        "POST /v1/alerts",
        "ok",
        201,
        keys=("id", "symbol", "operator", "target", "enabled", "created_at"),
    ),
    ContractRow(
        "alerts.create_invalid_target",
        "POST /v1/alerts",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
    ContractRow(
        "alerts.create_invalid_symbol",
        "POST /v1/alerts",
        "invalid",
        422,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow("alerts.delete_ok", "DELETE /v1/alerts/{alert_id}", "ok", 204, body="empty"),
    ContractRow(
        "alerts.delete_unknown",
        "DELETE /v1/alerts/{alert_id}",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow("alert_inbox.list_ok", "GET /v1/alert-inbox", "ok", 200, body="list"),
    ContractRow(
        "alert_inbox.ack_unknown",
        "POST /v1/alert-inbox/{notification_id}/ack",
        "unknown",
        404,
        keys=("detail",),
        detail_type="str",
    ),
    ContractRow(
        "alerts.evaluate_ok",
        "POST /v1/alerts/evaluate",
        "ok",
        200,
        keys=("alerts", "provenance", "evaluated_at"),
    ),
    ContractRow(
        "alerts.evaluate_upstream_failure",
        "POST /v1/alerts/evaluate",
        "upstream",
        502,
        keys=("detail",),
        detail_type="str",
    ),
    # --- operations ---------------------------------------------------------
    ContractRow(
        "operations_plan.ok",
        "POST /v1/operations/plan",
        "ok",
        200,
        keys=("summary", "operations", "confidence", "source"),
    ),
    ContractRow(
        "operations_plan.missing_text",
        "POST /v1/operations/plan",
        "invalid",
        422,
        keys=("detail",),
        detail_type="list",
    ),
)


def _provenance(provider: str = "stub") -> Provenance:
    return Provenance(
        provider=provider,
        source="test:stub",
        source_url=None,
        license_class=LicenseClass.SYNTHETIC,
        retrieved_at=NOW,
        as_of=NOW,
    )


class _StubSec:
    """SEC client stand-in; raises ``error`` from every method when given."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    def search(self, query: str, limit: int) -> list[Instrument]:
        if self._error is not None:
            raise self._error
        return [Instrument(symbol="AAPL", name="Apple Inc.", exchange="NMS", currency="USD")]


class _StubMarket:
    """Market provider stand-in covering the three calls the core routes make."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    def _raise(self) -> None:
        if self._error is not None:
            raise self._error

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        self._raise()
        return MarketQuoteBatch(
            quotes={
                symbol: MarketQuote(
                    symbol=symbol,
                    price=100.0,
                    previous_close=99.0,
                    currency="USD",
                    currency_source="test",
                    as_of=NOW,
                )
                for symbol in symbols
            },
            provenance=_provenance("stub-market"),
        )

    def overview(self) -> MarketOverview:
        self._raise()
        return MarketOverview(items=[], provenance=_provenance("stub-market"))

    def history(
        self,
        symbol: str,
        period: str,
        interval: str,
        indicators: list[str],
    ) -> MarketHistory:
        self._raise()
        return MarketHistory(
            symbol=symbol,
            interval=interval,
            bars=[],
            provenance=_provenance("stub-market"),
        )


def _search_provider(*, error: Exception | None = None) -> type[Any]:
    class _StubSearch:
        def __init__(self, settings: Any) -> None:
            self._settings = settings

        def search(self, query: str, limit: int) -> list[Instrument]:
            if error is not None:
                raise error
            return [Instrument(symbol="AAPL", name="Apple Inc.", currency="USD")]

    return _StubSearch


def _research_provider(*, error: Exception | None = None) -> type[Any]:
    class _StubResearch:
        def __init__(self, settings: Any) -> None:
            self._settings = settings

        def _raise(self) -> None:
            if error is not None:
                raise error

        def news(self, query: str, limit: int = 12) -> NewsFeed:
            self._raise()
            return NewsFeed(query=query, items=[], provenance=_provenance("stub-news"))

        def calendar(
            self,
            start: date,
            end: date,
            event_types: list[str] | None = None,
            symbol: str | None = None,
            limit: int = 100,
        ) -> EventCalendar:
            self._raise()
            return EventCalendar(
                start=start,
                end=end,
                events=[CalendarEvent(event_type="earnings", title="Stub earnings")],
                provenance=_provenance("stub-calendar"),
            )

    return _StubResearch


def _edinet_client(*, error: Exception | None = None) -> Callable[[], Any]:
    class _StubEdinet:
        def documents(self, filing_date: date) -> dict[str, Any]:
            if error is not None:
                raise error
            return {"filing_date": filing_date.isoformat(), "documents": []}

    return _StubEdinet


def _patch(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    monkeypatch.setattr(f"yowayowa.api.routes.{name}", value)


def _patch_sec(monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None) -> None:
    stub = _StubSec(error=error)
    _patch(monkeypatch, "_sec", lambda settings: stub)


def _patch_market(monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None) -> None:
    stub = _StubMarket(error=error)
    _patch(monkeypatch, "yahoo_market_provider", lambda: stub)


#: Case id -> request that produces the response the contract row describes.
CASES: dict[str, Callable[[TestClient, pytest.MonkeyPatch], ApiResponse]] = {}


def case(
    case_id: str,
) -> Callable[
    [Callable[[TestClient, pytest.MonkeyPatch], ApiResponse]],
    Callable[[TestClient, pytest.MonkeyPatch], ApiResponse],
]:
    def register(
        func: Callable[[TestClient, pytest.MonkeyPatch], ApiResponse],
    ) -> Callable[[TestClient, pytest.MonkeyPatch], ApiResponse]:
        assert case_id not in CASES, f"duplicate case id: {case_id}"
        CASES[case_id] = func
        return func

    return register


@pytest.fixture(autouse=True)
def _forbid_outbound_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test instead of letting a stub miss reach the real network."""

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    inet_families = {socket.AF_INET, socket.AF_INET6}

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if self.family in inet_families:
            raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in inet_families:
            raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")
        return real_connect_ex(self, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from yowayowa.config import get_settings

    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    from yowayowa.api.app import app

    # raise_server_exceptions=False so the unhandled-exception contract (the
    # generic 500 body) is observable instead of re-raised into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    get_settings.cache_clear()


def _create_portfolio(client: TestClient, name: str = "Core") -> int:
    response = client.post("/v1/portfolios", json={"name": name, "base_currency": "USD"})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _create_position(client: TestClient, portfolio_id: int, symbol: str = "AAPL") -> None:
    response = client.put(
        f"/v1/portfolios/{portfolio_id}/positions",
        json={
            "symbol": symbol,
            "quantity": "10",
            "average_cost": "100",
            "currency": "USD",
        },
    )
    assert response.status_code == 200, response.text


def _main_watchlist_id(client: TestClient) -> int:
    response = client.get("/v1/watchlists")
    assert response.status_code == 200, response.text
    return int(response.json()[0]["id"])


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
@case("health.ok")
def _health_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/health")


@case("health.unauthorized")
def _health_unauthorized(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    from yowayowa.config import get_settings

    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "test-secret")
    get_settings.cache_clear()
    return client.get("/v1/health")


@case("health.authorized")
def _health_authorized(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    from yowayowa.config import get_settings

    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "test-secret")
    get_settings.cache_clear()
    return client.get("/v1/health", headers={"Authorization": "Bearer test-secret"})


@case("health.method_not_allowed")
def _health_method_not_allowed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.delete("/v1/health")


@case("api.unknown_path")
def _api_unknown_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/does-not-exist")


# --------------------------------------------------------------------------- #
# instruments search
# --------------------------------------------------------------------------- #
@case("search.ok")
def _search_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_sec(monkeypatch)
    _patch(monkeypatch, "YahooSearchProvider", _search_provider())
    return client.get("/v1/instruments/search", params={"q": "AAPL"})


@case("search.missing_query")
def _search_missing_query(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/instruments/search")


@case("search.query_too_long")
def _search_query_too_long(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/instruments/search", params={"q": "A" * 101})


@case("search.upstream_failure")
def _search_upstream_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_sec(monkeypatch, error=RuntimeError("SEC upstream returned HTTP 503"))
    _patch(
        monkeypatch,
        "YahooSearchProvider",
        _search_provider(error=RuntimeError("Yahoo upstream returned HTTP 503")),
    )
    return client.get("/v1/instruments/search", params={"q": "AAPL"})


@case("search.timeout")
def _search_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_sec(monkeypatch, error=TimeoutError("SEC request timed out after 20s"))
    _patch(
        monkeypatch,
        "YahooSearchProvider",
        _search_provider(error=TimeoutError("Yahoo request timed out after 20s")),
    )
    return client.get("/v1/instruments/search", params={"q": "AAPL"})


# --------------------------------------------------------------------------- #
# news
# --------------------------------------------------------------------------- #
@case("news.ok")
def _news_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(monkeypatch, "YahooResearchProvider", _research_provider())
    return client.get("/v1/news/AAPL")


@case("news.invalid_limit")
def _news_invalid_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/news/AAPL", params={"limit": 0})


@case("news.policy_blocked")
def _news_policy_blocked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(
        monkeypatch,
        "YahooResearchProvider",
        _research_provider(error=ProviderPolicyError(POLICY_MESSAGE)),
    )
    return client.get("/v1/news/AAPL")


@case("news.upstream_failure")
def _news_upstream_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(
        monkeypatch,
        "YahooResearchProvider",
        _research_provider(error=RuntimeError("Yahoo news upstream returned HTTP 503")),
    )
    return client.get("/v1/news/AAPL")


# --------------------------------------------------------------------------- #
# calendar
# --------------------------------------------------------------------------- #
@case("calendar.ok")
def _calendar_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(monkeypatch, "YahooResearchProvider", _research_provider())
    return client.get("/v1/calendar", params={"start": "2026-11-01", "end": "2026-11-07"})


@case("calendar.inverted_range")
def _calendar_inverted_range(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/calendar", params={"start": "2026-11-07", "end": "2026-11-01"})


@case("calendar.range_too_wide")
def _calendar_range_too_wide(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    wide_end = date(2026, 11, 1) + timedelta(days=120)
    return client.get(
        "/v1/calendar",
        params={"start": "2026-11-01", "end": wide_end.isoformat()},
    )


@case("calendar.unknown_event_type")
def _calendar_unknown_event_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/calendar", params={"types": "bogus"})


@case("calendar.invalid_date")
def _calendar_invalid_date(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/calendar", params={"start": "not-a-date"})


@case("calendar.policy_blocked")
def _calendar_policy_blocked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(
        monkeypatch,
        "YahooResearchProvider",
        _research_provider(error=ProviderPolicyError(POLICY_MESSAGE)),
    )
    return client.get("/v1/calendar", params={"start": "2026-11-01", "end": "2026-11-07"})


@case("calendar.upstream_failure")
def _calendar_upstream_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(
        monkeypatch,
        "YahooResearchProvider",
        _research_provider(error=RuntimeError("Yahoo calendar upstream returned HTTP 503")),
    )
    return client.get("/v1/calendar", params={"start": "2026-11-01", "end": "2026-11-07"})


@case("calendar.timeout")
def _calendar_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch(
        monkeypatch,
        "YahooResearchProvider",
        _research_provider(error=TimeoutError("Yahoo calendar request timed out after 20s")),
    )
    return client.get("/v1/calendar", params={"start": "2026-11-01", "end": "2026-11-07"})


# --------------------------------------------------------------------------- #
# markets overview / quotes / history
# --------------------------------------------------------------------------- #
@case("markets_overview.ok")
def _markets_overview_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch)
    return client.get("/v1/markets/overview")


@case("markets_overview.policy_blocked")
def _markets_overview_policy_blocked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=ProviderPolicyError(POLICY_MESSAGE))
    return client.get("/v1/markets/overview")


@case("markets_overview.upstream_failure")
def _markets_overview_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=RuntimeError("Yahoo overview upstream returned HTTP 503"))
    return client.get("/v1/markets/overview")


@case("markets_overview.timeout")
def _markets_overview_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch, error=TimeoutError("Yahoo overview request timed out after 20s"))
    return client.get("/v1/markets/overview")


@case("markets_quotes.ok")
def _markets_quotes_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch)
    return client.get("/v1/markets/quotes", params={"symbols": "AAPL,MSFT"})


@case("markets_quotes.empty_symbols")
def _markets_quotes_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/markets/quotes", params={"symbols": " "})


@case("markets_quotes.invalid_symbol")
def _markets_quotes_invalid_symbol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/markets/quotes", params={"symbols": "<script>"})


@case("markets_quotes.too_many_symbols")
def _markets_quotes_too_many(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    symbols = ",".join(f"SYM{index}" for index in range(101))
    return client.get("/v1/markets/quotes", params={"symbols": symbols})


@case("markets_quotes.upstream_failure")
def _markets_quotes_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=RuntimeError("Yahoo quotes upstream returned HTTP 503"))
    return client.get("/v1/markets/quotes", params={"symbols": "AAPL"})


@case("markets_quotes.timeout")
def _markets_quotes_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch, error=TimeoutError("Yahoo quotes request timed out after 20s"))
    return client.get("/v1/markets/quotes", params={"symbols": "AAPL"})


@case("markets_history.ok")
def _markets_history_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch)
    return client.get("/v1/markets/AAPL/history", params={"period": "6mo"})


@case("markets_history.invalid_period")
def _markets_history_invalid_period(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/markets/AAPL/history", params={"period": "nope"})


@case("markets_history.unsupported_interval")
def _markets_history_unsupported_interval(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=ValueError("Unsupported interval: 3h"))
    return client.get("/v1/markets/AAPL/history", params={"interval": "3h"})


@case("markets_history.upstream_failure")
def _markets_history_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=RuntimeError("Yahoo history upstream returned HTTP 503"))
    return client.get("/v1/markets/AAPL/history")


@case("markets_history.timeout")
def _markets_history_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch, error=TimeoutError("Yahoo history request timed out after 20s"))
    return client.get("/v1/markets/AAPL/history")


# --------------------------------------------------------------------------- #
# filings
# --------------------------------------------------------------------------- #
@case("filings_edinet.missing_date")
def _filings_edinet_missing_date(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/filings/edinet")


@case("filings_edinet.not_configured")
def _filings_edinet_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch(
        monkeypatch,
        "edinet_client",
        _edinet_client(error=RuntimeError("EDINET_API_KEY is not configured")),
    )
    return client.get("/v1/filings/edinet", params={"filing_date": "2026-10-22"})


@case("filings_edinet.upstream_failure")
def _filings_edinet_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch(
        monkeypatch,
        "edinet_client",
        _edinet_client(error=OSError("EDINET upstream returned HTTP 503")),
    )
    return client.get("/v1/filings/edinet", params={"filing_date": "2026-10-22"})


# --------------------------------------------------------------------------- #
# watchlists
# --------------------------------------------------------------------------- #
@case("compare_metrics.ok")
def _compare_metrics_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/compare/metrics")


@case("watchlists.list_ok")
def _watchlists_list_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/watchlists")


@case("watchlists.create_ok")
def _watchlists_create_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.post("/v1/watchlists", json={"name": "High Conviction"})


@case("watchlists.create_conflict")
def _watchlists_create_conflict(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    client.post("/v1/watchlists", json={"name": "Main"})
    return client.post("/v1/watchlists", json={"name": "Main"})


@case("watchlists.create_invalid_name")
def _watchlists_create_invalid_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post("/v1/watchlists", json={"name": ""})


@case("watchlists.add_symbols_ok")
def _watchlists_add_symbols_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    watchlist_id = _main_watchlist_id(client)
    return client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=["AAPL", "MSFT"])


@case("watchlists.add_symbols_unknown_id")
def _watchlists_add_symbols_unknown_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post("/v1/watchlists/999999/symbols", json=["AAPL"])


@case("watchlists.add_symbols_invalid_symbol")
def _watchlists_add_symbols_invalid_symbol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    watchlist_id = _main_watchlist_id(client)
    return client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=["<script>"])


@case("watchlists.remove_symbol_ok")
def _watchlists_remove_symbol_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    watchlist_id = _main_watchlist_id(client)
    client.post(f"/v1/watchlists/{watchlist_id}/symbols", json=["AAPL"])
    return client.delete(f"/v1/watchlists/{watchlist_id}/symbols/AAPL")


@case("watchlists.remove_symbol_unknown_id")
def _watchlists_remove_symbol_unknown_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.delete("/v1/watchlists/999999/symbols/AAPL")


# --------------------------------------------------------------------------- #
# portfolios
# --------------------------------------------------------------------------- #
@case("portfolios.list_ok")
def _portfolios_list_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/portfolios")


@case("portfolios.get_ok")
def _portfolios_get_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.get(f"/v1/portfolios/{portfolio_id}")


@case("portfolios.get_unknown")
def _portfolios_get_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/portfolios/999999")


@case("portfolios.create_ok")
def _portfolios_create_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.post("/v1/portfolios", json={"name": "Core", "base_currency": "USD"})


@case("portfolios.create_invalid_currency")
def _portfolios_create_invalid_currency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post("/v1/portfolios", json={"name": "Core", "base_currency": "US"})


@case("portfolios.delete_ok")
def _portfolios_delete_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.delete(f"/v1/portfolios/{portfolio_id}")


@case("portfolios.delete_unknown")
def _portfolios_delete_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.delete("/v1/portfolios/999999")


@case("portfolios.delete_nonempty")
def _portfolios_delete_nonempty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.delete(f"/v1/portfolios/{portfolio_id}")


@case("portfolios.delete_force")
def _portfolios_delete_force(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.delete(f"/v1/portfolios/{portfolio_id}", params={"force": True})


@case("portfolios.position_upsert_ok")
def _portfolios_position_upsert_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.put(
        f"/v1/portfolios/{portfolio_id}/positions",
        json={
            "symbol": "aapl",
            "quantity": "10",
            "average_cost": "100",
            "currency": "usd",
        },
    )


@case("portfolios.position_upsert_unknown_portfolio")
def _portfolios_position_upsert_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.put(
        "/v1/portfolios/999999/positions",
        json={"symbol": "AAPL", "quantity": "1", "currency": "USD"},
    )


@case("portfolios.position_upsert_invalid_quantity")
def _portfolios_position_upsert_invalid_quantity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.put(
        f"/v1/portfolios/{portfolio_id}/positions",
        json={"symbol": "AAPL", "quantity": "not-a-number", "currency": "USD"},
    )


@case("portfolios.position_upsert_invalid_symbol")
def _portfolios_position_upsert_invalid_symbol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.put(
        f"/v1/portfolios/{portfolio_id}/positions",
        json={"symbol": "<script>", "quantity": "1", "currency": "USD"},
    )


@case("portfolios.position_upsert_version_conflict")
def _portfolios_position_upsert_version_conflict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.put(
        f"/v1/portfolios/{portfolio_id}/positions",
        json={
            "symbol": "AAPL",
            "quantity": "11",
            "currency": "USD",
            "expected_version": 99,
        },
    )


@case("portfolios.bulk_upsert_ok")
def _portfolios_bulk_upsert_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.put(
        f"/v1/portfolios/{portfolio_id}/positions/bulk",
        json={
            "positions": [
                {"symbol": "AAPL", "quantity": "1", "currency": "USD"},
                {"symbol": "MSFT", "quantity": "2", "currency": "USD"},
            ],
            "replace": True,
        },
    )


@case("portfolios.remove_position_ok")
def _portfolios_remove_position_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.delete(f"/v1/portfolios/{portfolio_id}/positions/AAPL")


@case("portfolios.remove_position_version_conflict")
def _portfolios_remove_position_version_conflict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.delete(
        f"/v1/portfolios/{portfolio_id}/positions/AAPL",
        params={"expected_version": 99},
    )


@case("portfolios.analytics_ok")
def _portfolios_analytics_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    _patch_market(monkeypatch)
    return client.get(f"/v1/portfolios/{portfolio_id}/analytics")


@case("portfolios.analytics_unknown")
def _portfolios_analytics_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/portfolios/999999/analytics")


@case("portfolios.analytics_upstream_failure")
def _portfolios_analytics_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    _patch_market(monkeypatch, error=RuntimeError("Yahoo quotes upstream returned HTTP 503"))
    return client.get(f"/v1/portfolios/{portfolio_id}/analytics")


@case("portfolios.analytics_timeout")
def _portfolios_analytics_timeout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    _patch_market(monkeypatch, error=TimeoutError("Yahoo quotes request timed out after 20s"))
    return client.get(f"/v1/portfolios/{portfolio_id}/analytics")


@case("portfolios.snapshots_ok")
def _portfolios_snapshots_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    return client.get(f"/v1/portfolios/{portfolio_id}/snapshots")


@case("portfolios.snapshots_unknown")
def _portfolios_snapshots_unknown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.get("/v1/portfolios/999999/snapshots")


@case("portfolios.export_ok")
def _portfolios_export_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    portfolio_id = _create_portfolio(client)
    _create_position(client, portfolio_id)
    return client.get(f"/v1/portfolios/{portfolio_id}/export.csv")


@case("portfolios.export_unknown")
def _portfolios_export_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/portfolios/999999/export.csv")


# --------------------------------------------------------------------------- #
# alerts
# --------------------------------------------------------------------------- #
@case("alerts.list_ok")
def _alerts_list_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/alerts")


@case("alerts.create_ok")
def _alerts_create_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.post("/v1/alerts", json={"symbol": "aapl", "operator": "above", "target": "120"})


@case("alerts.create_invalid_target")
def _alerts_create_invalid_target(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post("/v1/alerts", json={"symbol": "AAPL", "operator": "above", "target": "0"})


@case("alerts.create_invalid_symbol")
def _alerts_create_invalid_symbol(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post(
        "/v1/alerts", json={"symbol": "<script>", "operator": "above", "target": "120"}
    )


@case("alerts.delete_ok")
def _alerts_delete_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    created = client.post(
        "/v1/alerts", json={"symbol": "AAPL", "operator": "above", "target": "120"}
    )
    assert created.status_code == 201, created.text
    return client.delete(f"/v1/alerts/{created.json()['id']}")


@case("alerts.delete_unknown")
def _alerts_delete_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.delete("/v1/alerts/999999")


@case("alert_inbox.list_ok")
def _alert_inbox_list_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.get("/v1/alert-inbox")


@case("alert_inbox.ack_unknown")
def _alert_inbox_ack_unknown(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.post("/v1/alert-inbox/999999/ack")


@case("alerts.evaluate_ok")
def _alerts_evaluate_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    _patch_market(monkeypatch)
    client.post("/v1/alerts", json={"symbol": "AAPL", "operator": "above", "target": "120"})
    return client.post("/v1/alerts/evaluate")


@case("alerts.evaluate_upstream_failure")
def _alerts_evaluate_upstream_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    _patch_market(monkeypatch, error=RuntimeError("Yahoo quotes upstream returned HTTP 503"))
    client.post("/v1/alerts", json={"symbol": "AAPL", "operator": "above", "target": "120"})
    return client.post("/v1/alerts/evaluate")


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #
@case("operations_plan.ok")
def _operations_plan_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> ApiResponse:
    return client.post("/v1/operations/plan", params={"text": "add AAPL to watchlist"})


@case("operations_plan.missing_text")
def _operations_plan_missing_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> ApiResponse:
    return client.post("/v1/operations/plan")


# --------------------------------------------------------------------------- #
# assertions
# --------------------------------------------------------------------------- #
def _assert_no_internal_leak(response: ApiResponse, label: str) -> None:
    body = response.text
    for fragment in LEAK_FRAGMENTS:
        assert fragment not in body, f"{label}: internal fragment {fragment!r} leaked: {body[:400]}"
    assert INTERNAL_MARKER not in body, f"{label}: internal marker leaked: {body[:400]}"


def _assert_contract(response: ApiResponse, row: ContractRow) -> None:
    label = f"{row.case} ({row.route} -> {row.status})"
    assert response.status_code == row.status, f"{label}: {response.text[:400]}"

    if row.body == "empty":
        assert response.content == b"", (
            f"{label}: expected an empty body, got {response.text[:200]}"
        )
    elif row.body == "csv":
        assert response.headers["content-type"].startswith("text/csv"), label
    elif row.body == "list":
        assert response.headers["content-type"].startswith("application/json"), label
        payload = response.json()
        assert isinstance(payload, list), f"{label}: expected a JSON array, got {payload!r}"
    else:
        assert response.headers["content-type"].startswith("application/json"), label
        payload = response.json()
        assert isinstance(payload, dict), f"{label}: expected a JSON object, got {payload!r}"
        missing = [key for key in row.keys if key not in payload]
        assert not missing, f"{label}: missing body keys {missing} in {payload!r}"
        if row.detail is not None or row.detail_type is not None:
            detail = payload.get("detail")
            if row.detail is not None:
                assert detail == row.detail, f"{label}: detail {detail!r} != {row.detail!r}"
            if row.detail_type == "str":
                assert isinstance(detail, str), f"{label}: detail is not a string: {detail!r}"
            elif row.detail_type == "list":
                assert isinstance(detail, list) and detail, f"{label}: detail is not a list"
                for entry in detail:
                    assert isinstance(entry, dict), f"{label}: bad validation entry {entry!r}"
                    assert {"loc", "msg", "type"} <= set(entry), f"{label}: {entry!r}"

    _assert_no_internal_leak(response, label)


def test_contract_table_has_one_row_per_case() -> None:
    row_ids = [row.case for row in CONTRACT]
    assert len(row_ids) == len(set(row_ids)), "duplicate case id in CONTRACT"
    assert set(row_ids) == set(CASES), (
        f"rows without a case: {sorted(set(row_ids) - set(CASES))}; "
        f"cases without a row: {sorted(set(CASES) - set(row_ids))}"
    )
    assert len({row.route for row in CONTRACT}) >= 15, "coverage shrank unexpectedly"


def test_contract_table_covers_every_declared_core_route() -> None:
    from yowayowa.api.app import app  # noqa: F401  (applies superseded-route removal)
    from yowayowa.api.routes import router

    declared = {
        f"{method} {route.path}"
        for route in router.routes
        for method in sorted(getattr(route, "methods", None) or ())
    }
    documented = {row.route for row in CONTRACT if row.route != "*"}
    assert documented == declared, (
        f"undocumented routes: {sorted(declared - documented)}; "
        f"stale rows: {sorted(documented - declared)}"
    )


@pytest.mark.parametrize("case_id", list(CASES), ids=list(CASES))
def test_api_error_contract(
    case_id: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = next(row for row in CONTRACT if row.case == case_id)
    response = CASES[case_id](client, monkeypatch)
    _assert_contract(response, row)


def test_unhandled_exception_returns_generic_500_without_leaking(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(session: Any) -> Any:
        raise RuntimeError(INTERNAL_MARKER)

    monkeypatch.setattr("yowayowa.api.routes.list_watchlists", _explode)

    response = client.get("/v1/watchlists")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    _assert_no_internal_leak(response, "unhandled 500")


def test_internal_exception_message_is_not_echoed_in_error_body(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sec(monkeypatch, error=RuntimeError(INTERNAL_MARKER))
    _patch(
        monkeypatch,
        "YahooSearchProvider",
        _search_provider(error=RuntimeError(INTERNAL_MARKER)),
    )

    response = client.get("/v1/instruments/search", params={"q": "AAPL"})

    assert response.status_code == 502
    assert INTERNAL_MARKER not in response.text


def test_generic_500_keeps_request_correlation_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(session: Any) -> Any:
        raise RuntimeError(INTERNAL_MARKER)

    monkeypatch.setattr("yowayowa.api.routes.list_watchlists", _explode)

    response = client.get("/v1/watchlists")

    assert response.status_code == 500
    assert response.headers.get("x-yowayowa-request-id")
