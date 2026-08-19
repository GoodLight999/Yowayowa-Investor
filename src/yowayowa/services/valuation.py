from __future__ import annotations

from datetime import UTC, date, datetime

from yowayowa.domain import Fundamentals, MarketQuoteBatch, MetricPoint, ValuationSnapshot
from yowayowa.services.screening import derived_metrics


def _latest_annual_point(fundamentals: Fundamentals, metric: str) -> MetricPoint | None:
    series = fundamentals.metrics.get(metric)
    if not series:
        return None
    annual = [
        point
        for point in series.points
        if point.fiscal_period == "FY" or (point.form or "").startswith("10-K")
    ]
    if not annual:
        return None
    return max(annual, key=lambda point: (point.period_end, point.filed or date.min))


def _latest_annual_value(fundamentals: Fundamentals, metric: str) -> float | None:
    point = _latest_annual_point(fundamentals, metric)
    return None if point is None else float(point.value)


def _annual_fcf(fundamentals: Fundamentals) -> tuple[float | None, date | None]:
    operating = fundamentals.metrics.get("operating_cash_flow")
    capex = fundamentals.metrics.get("capex")
    if not operating or not capex:
        return None, None
    operating_by_end = {
        point.period_end: point
        for point in operating.points
        if point.fiscal_period == "FY" or (point.form or "").startswith("10-K")
    }
    capex_by_end = {
        point.period_end: point
        for point in capex.points
        if point.fiscal_period == "FY" or (point.form or "").startswith("10-K")
    }
    common = operating_by_end.keys() & capex_by_end.keys()
    if not common:
        return None, None
    period_end = max(common)
    return (
        float(operating_by_end[period_end].value) - float(capex_by_end[period_end].value),
        period_end,
    )


def _positive_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or numerator <= 0 or denominator <= 0:
        return None
    return numerator / denominator


def valuation_snapshot(
    fundamentals: Fundamentals,
    quote_batch: MarketQuoteBatch,
) -> ValuationSnapshot:
    quote = quote_batch.quotes.get(fundamentals.symbol)
    if quote is None:
        raise LookupError(f"Quote unavailable for {fundamentals.symbol}")

    shares_point = _latest_annual_point(fundamentals, "shares_diluted")
    shares = None if shares_point is None else float(shares_point.value)
    market_cap = quote.price * shares if shares is not None and shares > 0 else None

    revenue_point = _latest_annual_point(fundamentals, "revenue")
    revenue = None if revenue_point is None else float(revenue_point.value)
    net_income = _latest_annual_value(fundamentals, "net_income")
    equity = _latest_annual_value(fundamentals, "equity")
    free_cash_flow, fcf_period = _annual_fcf(fundamentals)

    operating = derived_metrics(fundamentals)
    metrics: dict[str, float | None] = {
        "price_to_sales": _positive_ratio(market_cap, revenue),
        "price_to_earnings": _positive_ratio(market_cap, net_income),
        "price_to_book": _positive_ratio(market_cap, equity),
        "price_to_free_cash_flow": _positive_ratio(market_cap, free_cash_flow),
        "earnings_yield": _positive_ratio(net_income, market_cap),
        "free_cash_flow_yield": _positive_ratio(free_cash_flow, market_cap),
        "revenue_growth_yoy": operating.get("revenue_growth_yoy"),
        "operating_margin": operating.get("operating_margin"),
        "net_margin": operating.get("net_margin"),
        "return_on_assets": operating.get("return_on_assets"),
        "liabilities_to_equity": operating.get("liabilities_to_equity"),
        "annual_revenue": revenue,
        "annual_net_income": net_income,
        "annual_free_cash_flow": free_cash_flow,
        "annual_equity": equity,
    }
    annual_periods = [
        point.period_end for point in (revenue_point, shares_point) if point is not None
    ]
    if fcf_period is not None:
        annual_periods.append(fcf_period)

    sec_provenance = fundamentals.provenance.model_copy(
        update={
            "notes": [
                *fundamentals.provenance.notes,
                "Valuation denominators use the latest available annual SEC facts; they are not "
                "consensus estimates or trailing-twelve-month reconstructions.",
            ]
        }
    )
    market_provenance = quote_batch.provenance.model_copy(
        update={
            "notes": [
                *quote_batch.provenance.notes,
                "Market capitalization is current quote multiplied by latest annual diluted shares "
                "when both inputs are available.",
            ]
        }
    )
    return ValuationSnapshot(
        symbol=fundamentals.symbol,
        company_name=fundamentals.company_name,
        price=quote.price,
        shares_diluted=shares,
        market_cap=market_cap,
        annual_period_end=max(annual_periods) if annual_periods else None,
        metrics=metrics,
        provenance=[sec_provenance, market_provenance],
        evaluated_at=datetime.now(UTC),
    )
