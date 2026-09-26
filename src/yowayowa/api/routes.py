from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yowayowa.api.deps import db_session, require_api_token
from yowayowa.config import Settings, get_settings
from yowayowa.domain import (
    AlertEvaluation,
    ComparisonMetric,
    ComparisonRequest,
    ComparisonResponse,
    EventCalendar,
    Fundamentals,
    Instrument,
    MarketHistory,
    MarketOverview,
    MarketQuoteBatch,
    NewsFeed,
    OperationPlan,
    Portfolio,
    PortfolioAnalytics,
    PortfolioCreate,
    PortfolioSnapshot,
    PositionBulkUpsert,
    PositionUpsert,
    PriceAlert,
    PriceAlertCreate,
    ScreenRequest,
    ScreenResponse,
    ScreenRow,
    ValuationSnapshot,
    Watchlist,
    WatchlistCreate,
)
from yowayowa.providers.base import ProviderPolicyError
from yowayowa.providers.registry import (
    edinet_client,
    fred_client,
    sec_client,
    yahoo_market_provider,
)
from yowayowa.providers.sec import SecClient
from yowayowa.providers.yahoo_research import EventType, YahooResearchProvider
from yowayowa.providers.yahoo_search import YahooSearchProvider
from yowayowa.services.alerts import create_alert, delete_alert, evaluate_alerts, list_alerts
from yowayowa.services.comparison import available_metrics, compare
from yowayowa.services.operations import plan_operation
from yowayowa.services.portfolios import (
    bulk_upsert_positions,
    create_portfolio,
    get_portfolio,
    list_portfolio_snapshots,
    list_portfolios,
    portfolio_analytics,
    record_portfolio_snapshot,
    remove_position,
    upsert_position,
)
from yowayowa.services.screening import screen
from yowayowa.services.valuation import valuation_snapshot
from yowayowa.services.watchlists import (
    add_symbols,
    create_watchlist,
    list_watchlists,
    remove_symbol,
)
from yowayowa.symbols import normalize_symbol

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_token)])


def _sec(settings: Settings) -> SecClient:
    return sec_client()


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "mode": settings.mode,
        "market_provider": settings.market_provider,
        "capabilities": {
            "sec": True,
            "fred": bool(settings.fred_api_key),
            "edinet": bool(settings.edinet_api_key),
            "ai_byok": bool(settings.openai_compatible_api_key),
        },
    }


@router.get("/instruments/search")
def search_instruments(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> list[Instrument]:
    sec_results: list[Instrument] = []
    yahoo_results: list[Instrument] = []
    errors: list[Exception] = []
    try:
        sec_results = _sec(settings).search(q, limit)
    except Exception as exc:
        errors.append(exc)
    try:
        yahoo_results = YahooSearchProvider(settings).search(q, limit)
    except ProviderPolicyError:
        pass
    except Exception as exc:
        errors.append(exc)

    merged: dict[str, Instrument] = {item.symbol: item for item in yahoo_results}
    for item in sec_results:
        existing = merged.get(item.symbol)
        if existing is None:
            merged[item.symbol] = item
        else:
            merged[item.symbol] = item.model_copy(
                update={
                    "exchange": item.exchange or existing.exchange,
                    "instrument_type": item.instrument_type or existing.instrument_type,
                    "currency": item.currency or existing.currency,
                }
            )
    if not merged and errors:
        raise HTTPException(status_code=502, detail=str(errors[0]))
    return list(merged.values())[:limit]


@router.get("/fundamentals/{symbol}", response_model=Fundamentals)
def fundamentals(symbol: str, settings: Settings = Depends(get_settings)) -> Fundamentals:
    try:
        return _sec(settings).company_facts(symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/valuation/{symbol}", response_model=ValuationSnapshot)
def valuation(symbol: str, settings: Settings = Depends(get_settings)) -> ValuationSnapshot:
    normalized = normalize_symbol(symbol)
    try:
        facts = _sec(settings).company_facts(normalized)
        quotes = yahoo_market_provider().quotes([normalized])
        return valuation_snapshot(facts, quotes)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/news/{query}", response_model=NewsFeed)
def news(
    query: str,
    limit: int = Query(default=12, ge=1, le=50),
    settings: Settings = Depends(get_settings),
) -> NewsFeed:
    try:
        return YahooResearchProvider(settings).news(query, limit)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/calendar", response_model=EventCalendar)
def calendar_events(
    start: date | None = None,
    end: date | None = None,
    types: str = Query(default="earnings,economic,ipo,split", max_length=100),
    symbol: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> EventCalendar:
    resolved_start = start or date.today()
    resolved_end = end or (resolved_start + timedelta(days=7))
    if resolved_end < resolved_start:
        raise HTTPException(status_code=422, detail="Calendar end must not be before start")
    if (resolved_end - resolved_start).days > 93:
        raise HTTPException(status_code=422, detail="Calendar range is limited to 93 days")
    allowed = {"earnings", "economic", "ipo", "split"}
    requested_raw = [token.strip().lower() for token in types.split(",") if token.strip()]
    unknown = sorted(set(requested_raw) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown calendar event types: {', '.join(unknown)}",
        )
    requested = [cast(EventType, token) for token in dict.fromkeys(requested_raw)]
    try:
        return YahooResearchProvider(settings).calendar(
            resolved_start,
            resolved_end,
            event_types=requested,
            symbol=normalize_symbol(symbol) if symbol else None,
            limit=limit,
        )
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/markets/overview", response_model=MarketOverview)
def market_overview() -> MarketOverview:
    try:
        return yahoo_market_provider().overview()
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/markets/quotes", response_model=MarketQuoteBatch)
def market_quotes(
    symbols: str = Query(min_length=1, max_length=4000),
) -> MarketQuoteBatch:
    requested = [token.strip() for token in symbols.replace(";", ",").split(",") if token.strip()]
    if len(requested) > 100:
        raise HTTPException(status_code=422, detail="At most 100 symbols may be quoted at once")
    normalized = list(dict.fromkeys(normalize_symbol(symbol) for symbol in requested))
    if not normalized:
        raise HTTPException(status_code=422, detail="At least one symbol is required")
    try:
        return yahoo_market_provider().quotes(normalized)
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/markets/{symbol}/history", response_model=MarketHistory)
def market_history(
    symbol: str,
    period: str = Query(default="1y", pattern=r"^(?:[0-9]+(?:d|mo|y)|max)$"),
    interval: str = Query(default="1d", max_length=10),
    indicators: str = Query(default="sma20,rsi14", max_length=200),
    settings: Settings = Depends(get_settings),
) -> MarketHistory:
    requested = [token for token in indicators.split(",") if token.strip()]
    try:
        return yahoo_market_provider().history(symbol, period, interval, requested)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/macro/fred/{series_id}")
def fred_series(series_id: str, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    try:
        return fred_client().series(series_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/filings/edinet")
def edinet_documents(
    filing_date: date, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    try:
        return edinet_client().documents(filing_date)
    except RuntimeError as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/watchlists", response_model=list[Watchlist])
def get_watchlists(session: Session = Depends(db_session)) -> list[Watchlist]:
    return list_watchlists(session)


@router.post("/watchlists", response_model=Watchlist, status_code=201)
def post_watchlist(payload: WatchlistCreate, session: Session = Depends(db_session)) -> Watchlist:
    try:
        return create_watchlist(session, payload.name)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Watchlist name already exists") from exc


@router.post("/watchlists/{watchlist_id}/symbols", response_model=Watchlist)
def post_watchlist_symbols(
    watchlist_id: int, symbols: list[str], session: Session = Depends(db_session)
) -> Watchlist:
    try:
        return add_symbols(session, watchlist_id, symbols)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/watchlists/{watchlist_id}/symbols/{symbol}", response_model=Watchlist)
def delete_watchlist_symbol(
    watchlist_id: int, symbol: str, session: Session = Depends(db_session)
) -> Watchlist:
    try:
        return remove_symbol(session, watchlist_id, symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios", response_model=list[Portfolio])
def get_portfolios(session: Session = Depends(db_session)) -> list[Portfolio]:
    return list_portfolios(session)


@router.get("/portfolios/{portfolio_id}", response_model=Portfolio)
def get_portfolio_by_id(portfolio_id: int, session: Session = Depends(db_session)) -> Portfolio:
    try:
        return get_portfolio(session, portfolio_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/portfolios", response_model=Portfolio, status_code=201)
def post_portfolio(payload: PortfolioCreate, session: Session = Depends(db_session)) -> Portfolio:
    try:
        return create_portfolio(session, payload.name, payload.base_currency)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Portfolio name already exists") from exc


@router.put("/portfolios/{portfolio_id}/positions", response_model=Portfolio)
def put_position(
    portfolio_id: int, payload: PositionUpsert, session: Session = Depends(db_session)
) -> Portfolio:
    try:
        return upsert_position(session, portfolio_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/portfolios/{portfolio_id}/positions/bulk", response_model=Portfolio)
def put_positions_bulk(
    portfolio_id: int, payload: PositionBulkUpsert, session: Session = Depends(db_session)
) -> Portfolio:
    try:
        return bulk_upsert_positions(session, portfolio_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/portfolios/{portfolio_id}/positions/{symbol}", response_model=Portfolio)
def delete_position(
    portfolio_id: int, symbol: str, session: Session = Depends(db_session)
) -> Portfolio:
    try:
        return remove_position(session, portfolio_id, symbol)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/analytics", response_model=PortfolioAnalytics)
def get_portfolio_analytics(
    portfolio_id: int,
    session: Session = Depends(db_session),
) -> PortfolioAnalytics:
    try:
        portfolio = get_portfolio(session, portfolio_id)
        analytics = portfolio_analytics(portfolio, yahoo_market_provider())
        record_portfolio_snapshot(session, analytics)
        return analytics
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/snapshots", response_model=list[PortfolioSnapshot])
def get_portfolio_snapshots(
    portfolio_id: int,
    limit: int = Query(default=365, ge=1, le=5000),
    session: Session = Depends(db_session),
) -> list[PortfolioSnapshot]:
    try:
        return list_portfolio_snapshots(session, portfolio_id, limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/portfolios/{portfolio_id}/export.csv", response_class=Response)
def export_portfolio_csv(
    portfolio_id: int,
    session: Session = Depends(db_session),
) -> Response:
    try:
        portfolio = get_portfolio(session, portfolio_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["symbol", "quantity", "average_cost", "currency"])
    for position in portfolio.positions:
        writer.writerow(
            [
                position.symbol,
                str(position.quantity),
                "" if position.average_cost is None else str(position.average_cost),
                position.currency,
            ]
        )
    filename = f"portfolio-{portfolio.id}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/alerts", response_model=list[PriceAlert])
def get_alerts(session: Session = Depends(db_session)) -> list[PriceAlert]:
    return list_alerts(session)


@router.post("/alerts", response_model=PriceAlert, status_code=201)
def post_alert(payload: PriceAlertCreate, session: Session = Depends(db_session)) -> PriceAlert:
    return create_alert(session, payload)


@router.post("/alerts/evaluate", response_model=AlertEvaluation)
def post_alert_evaluation(session: Session = Depends(db_session)) -> AlertEvaluation:
    try:
        return evaluate_alerts(session, yahoo_market_provider())
    except ProviderPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/alerts/{alert_id}", status_code=204)
def remove_alert(alert_id: int, session: Session = Depends(db_session)) -> Response:
    try:
        delete_alert(session, alert_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/screen", response_model=ScreenResponse)
def post_screen(
    payload: ScreenRequest, settings: Settings = Depends(get_settings)
) -> ScreenResponse:
    client = _sec(settings)
    data: list[Fundamentals] = []
    failures: list[str] = []
    for symbol in payload.symbols:
        try:
            data.append(client.company_facts(symbol))
        except Exception:
            failures.append(symbol.upper())
    result = screen(data, payload.filters)
    for symbol in failures:
        result.rows.append(
            ScreenRow(symbol=symbol, metrics={}, matched=False, failures=["data_unavailable"])
        )
    return result


@router.get("/compare/metrics", response_model=list[ComparisonMetric])
def comparison_metrics() -> list[ComparisonMetric]:
    return available_metrics()


@router.post("/compare", response_model=ComparisonResponse)
def post_compare(
    payload: ComparisonRequest, settings: Settings = Depends(get_settings)
) -> ComparisonResponse:
    client = _sec(settings)
    data: list[Fundamentals] = []
    unavailable: list[str] = []
    for symbol in dict.fromkeys(symbol.upper() for symbol in payload.symbols):
        try:
            data.append(client.company_facts(symbol))
        except Exception:
            unavailable.append(symbol)
    if len(data) < 2:
        detail = "At least two comparable SEC issuers are required"
        if unavailable:
            detail += f"; unavailable: {', '.join(unavailable)}"
        raise HTTPException(status_code=422, detail=detail)
    return compare(data, payload.metrics or None)


@router.post("/operations/plan", response_model=OperationPlan)
def operation_plan(text: str, settings: Settings = Depends(get_settings)) -> OperationPlan:
    return plan_operation(text, settings)
