from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from yowayowa import __version__
from yowayowa.api.ai_integration_routes import router as ai_integration_router
from yowayowa.api.ai_routes import router as ai_router
from yowayowa.api.calendar_routes import router as calendar_router
from yowayowa.api.chart_routes import router as chart_router
from yowayowa.api.edinet_routes import router as edinet_router
from yowayowa.api.edinet_ux_routes import router as edinet_ux_router
from yowayowa.api.fundamentals_routes import router as fundamentals_router
from yowayowa.api.institutional_routes import router as institutional_router
from yowayowa.api.license_routes import router as license_router
from yowayowa.api.macro_routes import router as macro_router
from yowayowa.api.rate_routes import router as rate_router
from yowayowa.api.research_routes import router as research_router
from yowayowa.api.risk_routes import router as risk_router
from yowayowa.api.routes import router
from yowayowa.api.sector_routes import router as sector_router
from yowayowa.api.settings_routes import router as settings_router
from yowayowa.config import Settings, get_settings
from yowayowa.db import (
    dispose_database,
    get_or_create_default_watchlist,
    get_session,
    init_database,
)
from yowayowa.providers.registry import (
    edinet_client,
    yahoo_market_provider,
    yahoo_tracked_calendar_provider,
)
from yowayowa.services.alerts import (
    evaluate_alerts,
    evaluate_event_subscriptions,
    list_event_subscriptions,
)
from yowayowa.services.edinet_index import sync_filing_index
from yowayowa.services.licensing import license_catalog
from yowayowa.services.portfolios import (
    list_portfolios,
    portfolio_analytics,
    record_portfolio_snapshot,
)
from yowayowa.symbols import InputValidationError
from yowayowa.web.calendar_i18n import messages as calendar_messages
from yowayowa.web.calendar_i18n import translate as calendar_translate
from yowayowa.web.chart_i18n import messages as chart_messages
from yowayowa.web.chart_i18n import translate as chart_translate
from yowayowa.web.edinet_i18n import messages as edinet_messages
from yowayowa.web.edinet_i18n import translate as edinet_translate
from yowayowa.web.expansion_i18n import messages as expansion_messages
from yowayowa.web.expansion_i18n import translate as expansion_translate
from yowayowa.web.i18n import (
    LANGUAGE_COOKIE,
    SUPPORTED_LOCALES,
    messages,
    resolve_locale,
    translate,
)
from yowayowa.web.institutional_i18n import messages as institutional_messages
from yowayowa.web.institutional_i18n import translate as institutional_translate
from yowayowa.web.licensing_i18n import messages as licensing_messages
from yowayowa.web.licensing_i18n import translate as licensing_translate
from yowayowa.web.rate_i18n import messages as rate_messages
from yowayowa.web.rate_i18n import translate as rate_translate
from yowayowa.web.risk_i18n import messages as risk_messages
from yowayowa.web.risk_i18n import translate as risk_translate
from yowayowa.web.sector_i18n import messages as sector_messages
from yowayowa.web.sector_i18n import translate as sector_translate
from yowayowa.web.ux_i18n import messages as ux_messages
from yowayowa.web.ux_i18n import translate as ux_translate

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PACKAGE_ROOT / "web"
STATIC_ROOT = WEB_ROOT / "static"
templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))


def _asset(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


templates.env.globals.update(
    inline_css=_asset("styles.css") + "\n" + _asset("expansion.css") + "\n" + _asset("ux.css"),
    inline_app_js=_asset("app.js"),
    inline_ux_js=_asset("ux.js"),
    inline_market_js=_asset("market.js"),
    inline_portfolio_js=_asset("portfolio.js"),
    inline_compare_js=_asset("compare.js"),
    inline_screener_js=_asset("screener.js"),
    inline_instrument_js=_asset("instrument.js"),
    inline_instrument_ux_js=_asset("instrument_ux.js"),
    inline_alerts_js=_asset("alerts.js"),
    inline_calendar_js=_asset("calendar.js"),
    inline_calendar_ux_js=_asset("calendar_ux.js"),
    inline_news_js=_asset("news.js"),
    inline_discover_js=_asset("discover.js"),
    inline_research_js=_asset("research.js"),
    inline_macro_js=_asset("macro.js"),
    inline_ai_js=_asset("ai.js"),
    inline_settings_js=_asset("settings.js"),
    inline_charts_js=_asset("charts.js"),
    inline_charts_ux_js=_asset("charts_ux.js"),
    inline_rates_js=_asset("rates.js"),
    inline_institutional_js=_asset("institutional.js"),
    inline_edinet_js=_asset("edinet.js"),
    inline_edinet_ux_js=_asset("edinet_ux.js"),
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_database()
    try:
        with get_session() as session:
            get_or_create_default_watchlist(session)
        yield
    finally:
        dispose_database()


def _remove_superseded_core_routes() -> None:
    """Keep richer replacement endpoints unambiguous in routing and OpenAPI."""

    superseded_paths = {
        "/v1/macro/fred/{series_id}",
        "/v1/fundamentals/{symbol}",
        "/v1/valuation/{symbol}",
        "/v1/screen",
        "/v1/compare",
    }
    router.routes[:] = [
        route for route in router.routes if getattr(route, "path", None) not in superseded_paths
    ]


_remove_superseded_core_routes()

app = FastAPI(
    title="Yowayowa-Investor API",
    version=__version__,
    summary="Provenance-aware investment research API",
    lifespan=lifespan,
)
app.include_router(research_router)
app.include_router(macro_router)
app.include_router(license_router)
app.include_router(risk_router)
app.include_router(calendar_router)
app.include_router(chart_router)
app.include_router(sector_router)
app.include_router(rate_router)
app.include_router(institutional_router)
app.include_router(edinet_router)
app.include_router(edinet_ux_router)
app.include_router(ai_router)
app.include_router(ai_integration_router)
app.include_router(settings_router)
app.include_router(fundamentals_router)
app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")


@app.exception_handler(InputValidationError)
async def invalid_market_identifier(_: Request, exc: InputValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _render_page(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
) -> Response:
    locale = resolve_locale(request)
    settings = get_settings()
    merged_messages = {
        **dict(messages(locale)),
        **dict(expansion_messages(locale)),
        **dict(risk_messages(locale)),
        **dict(calendar_messages(locale)),
        **dict(chart_messages(locale)),
        **dict(sector_messages(locale)),
        **dict(rate_messages(locale)),
        **dict(institutional_messages(locale)),
        **dict(edinet_messages(locale)),
        **dict(licensing_messages(locale)),
        **dict(ux_messages(locale)),
    }
    page_context: dict[str, Any] = {
        "version": __version__,
        "locale": locale,
        "app_mode": settings.mode,
        "bea_available": bool(settings.bea_api_key),
        "t": partial(translate, locale),
        "x": partial(expansion_translate, locale),
        "r": partial(risk_translate, locale),
        "c": partial(calendar_translate, locale),
        "h": partial(chart_translate, locale),
        "s": partial(sector_translate, locale),
        "q": partial(rate_translate, locale),
        "i": partial(institutional_translate, locale),
        "e": partial(edinet_translate, locale),
        "l": partial(licensing_translate, locale),
        "u": partial(ux_translate, locale),
        "i18n_json": json.dumps(merged_messages, ensure_ascii=False),
    }
    if context:
        page_context.update(context)
    response = templates.TemplateResponse(
        request=request,
        name=template_name,
        context=page_context,
    )
    requested_locale = request.query_params.get("lang")
    if requested_locale in SUPPORTED_LOCALES:
        response.set_cookie(
            LANGUAGE_COOKIE,
            requested_locale,
            max_age=365 * 24 * 60 * 60,
            samesite="lax",
        )
    return response


def _personal_page(
    request: Request,
    template_name: str,
    context: dict[str, Any] | None = None,
) -> Response:
    if get_settings().mode == "public":
        return RedirectResponse(url="/macro", status_code=307)
    return _render_page(request, template_name, context)


def _authorize_cron(request: Request, settings: Settings) -> None:
    if settings.cron_secret:
        if request.headers.get("authorization") != f"Bearer {settings.cron_secret}":
            raise HTTPException(status_code=401, detail="Invalid cron authorization")
        return
    if settings.mode == "public":
        raise HTTPException(status_code=503, detail="CRON_SECRET is required in public mode")


@app.get("/internal/cron/daily", include_in_schema=False)
def daily_maintenance(request: Request) -> dict[str, object]:
    settings = get_settings()
    _authorize_cron(request, settings)
    snapshotted: list[int] = []
    portfolio_failures: dict[int, str] = {}
    event_subscriptions_checked = 0
    event_inbox_unread = 0
    event_failure: str | None = None
    alerts_checked = 0
    edinet_index_days_synced = 0
    edinet_index_failure: str | None = None
    personal_market_tasks_skipped = settings.mode == "public"
    with get_session() as session:
        if settings.edinet_api_key:
            yesterday_jst = datetime.now(ZoneInfo("Asia/Tokyo")).date() - timedelta(days=1)
            try:
                index_result = sync_filing_index(
                    session,
                    edinet_client(),
                    yesterday_jst,
                    yesterday_jst,
                )
                edinet_index_days_synced = index_result.days_synced
                if index_result.failures:
                    edinet_index_failure = index_result.failures[0].error
            except Exception as exc:
                edinet_index_failure = type(exc).__name__
        if settings.mode == "personal":
            provider = yahoo_market_provider()
            alert_result = evaluate_alerts(session, provider)
            alerts_checked = len(alert_result.alerts)
            if list_event_subscriptions(session):
                try:
                    event_result = evaluate_event_subscriptions(
                        session,
                        yahoo_tracked_calendar_provider(),
                    )
                    event_subscriptions_checked = len(event_result.subscriptions)
                    event_inbox_unread = len(event_result.inbox)
                except Exception as exc:
                    event_failure = type(exc).__name__
            for portfolio in list_portfolios(session):
                if not portfolio.positions:
                    continue
                try:
                    analytics = portfolio_analytics(portfolio, provider)
                    record_portfolio_snapshot(session, analytics)
                    snapshotted.append(portfolio.id)
                except Exception as exc:
                    portfolio_failures[portfolio.id] = type(exc).__name__
    return {
        "ok": (not portfolio_failures and event_failure is None and edinet_index_failure is None),
        "alerts_checked": alerts_checked,
        "event_subscriptions_checked": event_subscriptions_checked,
        "event_inbox_unread": event_inbox_unread,
        "event_failure": event_failure,
        "edinet_index_days_synced": edinet_index_days_synced,
        "edinet_index_failure": edinet_index_failure,
        "portfolios_snapshotted": snapshotted,
        "portfolio_failures": portfolio_failures,
        "personal_market_tasks_skipped": personal_market_tasks_skipped,
    }


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request) -> Response:
    return _personal_page(request, "dashboard.html")


@app.get("/markets", response_class=HTMLResponse, include_in_schema=False)
def markets_page(request: Request) -> Response:
    return _personal_page(request, "market.html")


@app.get("/discover", response_class=HTMLResponse, include_in_schema=False)
def discover_page(request: Request) -> Response:
    return _personal_page(request, "discover.html")


@app.get("/portfolio", response_class=HTMLResponse, include_in_schema=False)
def portfolio_page(request: Request) -> Response:
    return _personal_page(request, "portfolio.html")


@app.get("/instrument/{symbol}", response_class=HTMLResponse, include_in_schema=False)
def instrument_page(request: Request, symbol: str) -> Response:
    return _personal_page(request, "instrument.html", {"symbol": symbol.upper()})


@app.get("/research/{symbol}", response_class=HTMLResponse, include_in_schema=False)
def research_page(request: Request, symbol: str) -> Response:
    return _personal_page(request, "research.html", {"symbol": symbol.upper()})


@app.get("/compare", response_class=HTMLResponse, include_in_schema=False)
def compare_page(request: Request) -> Response:
    return _render_page(request, "compare.html")


@app.get("/screener", response_class=HTMLResponse, include_in_schema=False)
def screener_page(request: Request) -> Response:
    return _render_page(request, "screener.html")


@app.get("/charts", response_class=HTMLResponse, include_in_schema=False)
def charts_page(request: Request) -> Response:
    return _personal_page(request, "charts.html")


@app.get("/rates", response_class=HTMLResponse, include_in_schema=False)
def rates_page(request: Request) -> Response:
    return _render_page(request, "rates.html")


@app.get("/institutional", response_class=HTMLResponse, include_in_schema=False)
def institutional_page(request: Request) -> Response:
    return _render_page(request, "institutional.html")


@app.get("/edinet", response_class=HTMLResponse, include_in_schema=False)
def edinet_page(request: Request) -> Response:
    settings = get_settings()
    return _render_page(
        request,
        "edinet.html",
        {"edinet_available": bool(settings.edinet_api_key)},
    )


@app.get("/news", response_class=HTMLResponse, include_in_schema=False)
def news_page(request: Request) -> Response:
    return _personal_page(request, "news.html")


@app.get("/calendar", response_class=HTMLResponse, include_in_schema=False)
def calendar_page(request: Request) -> Response:
    return _personal_page(request, "calendar.html")


@app.get("/macro", response_class=HTMLResponse, include_in_schema=False)
def macro_page(request: Request) -> Response:
    return _render_page(request, "macro.html")


@app.get("/licenses", response_class=HTMLResponse, include_in_schema=False)
def licenses_page(request: Request) -> Response:
    settings = get_settings()
    return _render_page(
        request,
        "licenses.html",
        {"source_licenses": license_catalog(settings.mode)},
    )


@app.get("/alerts", response_class=HTMLResponse, include_in_schema=False)
def alerts_page(request: Request) -> Response:
    return _personal_page(request, "alerts.html")


@app.get("/ai", response_class=HTMLResponse, include_in_schema=False)
def ai_page(request: Request) -> Response:
    return _personal_page(request, "ai.html")


@app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(request: Request) -> Response:
    return _personal_page(request, "settings.html")
