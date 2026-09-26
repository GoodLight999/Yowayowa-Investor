from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from yowayowa.db import PortfolioRecord, PortfolioSnapshotRecord, PositionRecord, utcnow
from yowayowa.domain import (
    CurrencyExposure,
    Portfolio,
    PortfolioAnalytics,
    PortfolioSnapshot,
    Position,
    PositionAnalytics,
    PositionBulkUpsert,
    PositionUpsert,
)
from yowayowa.providers.base import MarketDataProvider
from yowayowa.symbols import normalize_currency, normalize_symbol


def _to_model(row: PortfolioRecord) -> Portfolio:
    return Portfolio(
        id=row.id,
        name=row.name,
        base_currency=row.base_currency,
        positions=[
            Position(
                symbol=item.symbol,
                quantity=item.quantity,
                average_cost=item.average_cost,
                currency=item.currency,
            )
            for item in row.positions
        ],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot_to_model(row: PortfolioSnapshotRecord) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        id=row.id,
        portfolio_id=row.portfolio_id,
        net_market_value=float(row.net_market_value),
        gross_market_value=float(row.gross_market_value),
        unrealized_pnl=float(row.unrealized_pnl),
        day_pnl=float(row.day_pnl),
        captured_at=row.captured_at,
    )


def list_portfolios(session: Session) -> list[Portfolio]:
    rows = session.scalars(select(PortfolioRecord).order_by(PortfolioRecord.name)).unique().all()
    return [_to_model(row) for row in rows]


def get_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    row = session.get(PortfolioRecord, portfolio_id)
    if row is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    return _to_model(row)


def create_portfolio(session: Session, name: str, base_currency: str) -> Portfolio:
    now = utcnow()
    row = PortfolioRecord(
        name=name.strip(),
        base_currency=normalize_currency(base_currency),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _to_model(row)


def upsert_position(session: Session, portfolio_id: int, payload: PositionUpsert) -> Portfolio:
    row = session.get(PortfolioRecord, portfolio_id)
    if row is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    symbol = normalize_symbol(payload.symbol)
    currency = normalize_currency(payload.currency)
    existing = next((position for position in row.positions if position.symbol == symbol), None)
    if existing is None:
        row.positions.append(
            PositionRecord(
                symbol=symbol,
                quantity=payload.quantity,
                average_cost=payload.average_cost,
                currency=currency,
            )
        )
    else:
        existing.quantity = payload.quantity
        existing.average_cost = payload.average_cost
        existing.currency = currency
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _to_model(row)


def bulk_upsert_positions(
    session: Session,
    portfolio_id: int,
    payload: PositionBulkUpsert,
) -> Portfolio:
    row = session.get(PortfolioRecord, portfolio_id)
    if row is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")

    normalized: dict[str, tuple[PositionUpsert, str]] = {}
    for item in payload.positions:
        symbol = normalize_symbol(item.symbol)
        currency = normalize_currency(item.currency)
        normalized[symbol] = (item, currency)

    if payload.replace:
        row.positions.clear()
    existing_by_symbol = {position.symbol: position for position in row.positions}
    for symbol, (item, currency) in normalized.items():
        existing = existing_by_symbol.get(symbol)
        if existing is None:
            row.positions.append(
                PositionRecord(
                    symbol=symbol,
                    quantity=item.quantity,
                    average_cost=item.average_cost,
                    currency=currency,
                )
            )
        else:
            existing.quantity = item.quantity
            existing.average_cost = item.average_cost
            existing.currency = currency
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _to_model(row)


def remove_position(session: Session, portfolio_id: int, symbol: str) -> Portfolio:
    row = session.get(PortfolioRecord, portfolio_id)
    if row is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    normalized = normalize_symbol(symbol)
    existing = next((position for position in row.positions if position.symbol == normalized), None)
    if existing is not None:
        row.positions.remove(existing)
        row.updated_at = utcnow()
        session.commit()
        session.refresh(row)
    return _to_model(row)


def record_portfolio_snapshot(
    session: Session,
    analytics: PortfolioAnalytics,
) -> PortfolioSnapshot:
    row = PortfolioSnapshotRecord(
        portfolio_id=analytics.portfolio_id,
        net_market_value=Decimal(str(analytics.net_market_value)),
        gross_market_value=Decimal(str(analytics.gross_market_value)),
        unrealized_pnl=Decimal(str(analytics.unrealized_pnl)),
        day_pnl=Decimal(str(analytics.day_pnl)),
        captured_at=analytics.evaluated_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _snapshot_to_model(row)


def list_portfolio_snapshots(
    session: Session,
    portfolio_id: int,
    limit: int = 365,
) -> list[PortfolioSnapshot]:
    if session.get(PortfolioRecord, portfolio_id) is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    rows = session.scalars(
        select(PortfolioSnapshotRecord)
        .where(PortfolioSnapshotRecord.portfolio_id == portfolio_id)
        .order_by(PortfolioSnapshotRecord.captured_at.desc())
        .limit(limit)
    ).all()
    return [_snapshot_to_model(row) for row in reversed(rows)]


def _fx_symbol(source_currency: str, base_currency: str) -> str | None:
    source = normalize_currency(source_currency)
    base = normalize_currency(base_currency)
    if source == base:
        return None
    if source == "USD":
        return f"{base}=X"
    return f"{source}{base}=X"


def portfolio_analytics(
    portfolio: Portfolio,
    provider: MarketDataProvider,
) -> PortfolioAnalytics:
    quote_symbols = [position.symbol for position in portfolio.positions]
    fx_by_currency: dict[str, str] = {}
    for position in portfolio.positions:
        fx_symbol = _fx_symbol(position.currency, portfolio.base_currency)
        if fx_symbol:
            fx_by_currency[position.currency] = fx_symbol
            quote_symbols.append(fx_symbol)

    batch = provider.quotes(quote_symbols)
    positions: list[PositionAnalytics] = []
    unavailable: list[str] = []
    currency_net: defaultdict[str, float] = defaultdict(float)
    currency_gross: defaultdict[str, float] = defaultdict(float)
    net_market_value = 0.0
    gross_market_value = 0.0
    known_cost_basis = 0.0
    known_cost_market_value = 0.0
    unrealized_pnl = 0.0
    day_pnl = 0.0
    previous_gross_value = 0.0

    for position in portfolio.positions:
        quote = batch.quotes.get(position.symbol)
        fx_symbol = fx_by_currency.get(position.currency)
        fx_quote = batch.quotes.get(fx_symbol) if fx_symbol else None
        if quote is None or (fx_symbol and fx_quote is None):
            unavailable.append(position.symbol)
            continue

        fx_to_base = fx_quote.price if fx_quote else 1.0
        previous_fx_to_base = fx_to_base
        if fx_quote and fx_quote.previous_close not in {None, 0}:
            assert fx_quote.previous_close is not None
            previous_fx_to_base = fx_quote.previous_close
        quantity = float(position.quantity)
        market_value = quantity * quote.price * fx_to_base
        position_gross = abs(market_value)
        net_market_value += market_value
        gross_market_value += position_gross
        currency_net[position.currency] += market_value
        currency_gross[position.currency] += position_gross

        cost_basis: float | None = None
        position_unrealized: float | None = None
        unrealized_pct: float | None = None
        if position.average_cost is not None:
            average_cost = float(position.average_cost)
            cost_basis = abs(quantity * average_cost * fx_to_base)
            position_unrealized = (quote.price - average_cost) * quantity * fx_to_base
            known_cost_basis += cost_basis
            known_cost_market_value += position_gross
            unrealized_pnl += position_unrealized
            if cost_basis:
                unrealized_pct = position_unrealized / cost_basis

        position_day_pnl: float | None = None
        position_day_change: float | None = None
        if quote.previous_close not in {None, 0}:
            assert quote.previous_close is not None
            previous_value = quantity * quote.previous_close * previous_fx_to_base
            previous_exposure = abs(previous_value)
            position_day_pnl = market_value - previous_value
            day_pnl += position_day_pnl
            previous_gross_value += previous_exposure
            if previous_exposure:
                position_day_change = position_day_pnl / previous_exposure

        positions.append(
            PositionAnalytics(
                symbol=position.symbol,
                quantity=position.quantity,
                currency=position.currency,
                average_cost=position.average_cost,
                price=quote.price,
                previous_close=quote.previous_close,
                fx_to_base=fx_to_base,
                previous_fx_to_base=previous_fx_to_base,
                market_value_base=market_value,
                cost_basis_base=cost_basis,
                unrealized_pnl_base=position_unrealized,
                unrealized_pnl_pct=unrealized_pct,
                day_pnl_base=position_day_pnl,
                day_change_pct=position_day_change,
                as_of=quote.as_of,
            )
        )

    if gross_market_value:
        positions = [
            item.model_copy(update={"weight": abs(item.market_value_base) / gross_market_value})
            for item in positions
        ]
    positions.sort(key=lambda item: (-item.weight, item.symbol))

    currency_exposure = [
        CurrencyExposure(
            currency=currency,
            market_value_base=currency_net[currency],
            weight=(gross / gross_market_value if gross_market_value else 0.0),
        )
        for currency, gross in sorted(
            currency_gross.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    provenance = batch.provenance.model_copy(
        update={
            "notes": [
                *batch.provenance.notes,
                "Daily P/L includes both security-price and FX-rate movement when prior "
                "FX is available.",
                "Average-cost unrealized P/L is calculated in the position currency and "
                "translated at current FX; acquisition-time FX is not reconstructed.",
            ]
        }
    )

    return PortfolioAnalytics(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        net_market_value=net_market_value,
        gross_market_value=gross_market_value,
        known_cost_basis=known_cost_basis,
        known_cost_market_value=known_cost_market_value,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=(unrealized_pnl / known_cost_basis if known_cost_basis else None),
        day_pnl=day_pnl,
        day_change_pct=(day_pnl / previous_gross_value if previous_gross_value else None),
        largest_position_weight=max((item.weight for item in positions), default=0.0),
        concentration_hhi=sum(item.weight**2 for item in positions),
        positions=positions,
        currency_exposure=currency_exposure,
        unavailable_symbols=sorted(set(unavailable)),
        provenance=provenance,
        evaluated_at=datetime.now(UTC),
    )
