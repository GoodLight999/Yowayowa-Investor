from __future__ import annotations

from datetime import UTC, date, datetime

from yowayowa.domain import Fundamentals, MarketQuoteBatch, MetricPoint, ValuationSnapshot
from yowayowa.services.screening import derived_metrics

# Annuality is judged from the real period (audit Y05): a point is annual
# only when it has a period_start spanning roughly one year. The FY label
# and a 10-K form are NOT evidence — a 10-K can carry quarterly data and a
# Q4 stub can be mislabelled FY. Missing period_start is never annual
# (fail closed).
_MIN_ANNUAL_DAYS = 300
_MAX_ANNUAL_DAYS = 400


def _is_annual_period(point: MetricPoint) -> bool:
    if point.period_start is None:
        return False
    days = (point.period_end - point.period_start).days
    return _MIN_ANNUAL_DAYS <= days <= _MAX_ANNUAL_DAYS


def _currency_compatible(
    quote_currency: str | None,
    denominator_point: MetricPoint | None,
) -> bool:
    """Price-currency vs financial-statement currency gate (audit Y04).

    When both currencies are known they must match; when either is unknown
    (None) the ratio is computed as before — currency-unverified, kept for
    compatibility with existing data that predates the currency field.
    """

    if quote_currency is None or denominator_point is None:
        return True
    denominator_currency = denominator_point.currency
    if denominator_currency is None:
        return True
    return quote_currency == denominator_currency


def _latest_annual_point(fundamentals: Fundamentals, metric: str) -> MetricPoint | None:
    """Most recent annual point, judged by real period length (audit Y05)."""

    series = fundamentals.metrics.get(metric)
    if not series:
        return None
    annual = [point for point in series.points if _is_annual_period(point)]
    if not annual:
        return None
    return max(annual, key=lambda point: (point.period_end, point.filed or date.min))


def _latest_annual_value(fundamentals: Fundamentals, metric: str) -> float | None:
    point = _latest_annual_point(fundamentals, metric)
    return None if point is None else float(point.value)


def _annual_fcf(fundamentals: Fundamentals) -> tuple[float | None, date | None]:
    """Annual free cash flow from matching full-year CF and capex (audit Y05).

    Both points must be annual by real period length, share the exact
    (period_start, period_end, currency, accession) key, and agree on
    fiscal_year when both are known. Same period-end with different spans,
    currencies or filing versions never mix.
    """

    operating = fundamentals.metrics.get("operating_cash_flow")
    capex = fundamentals.metrics.get("capex")
    if not operating or not capex:
        return None, None

    def keyed_annual(
        points: list[MetricPoint],
    ) -> dict[tuple[date | None, date, str, str], MetricPoint]:
        keyed: dict[tuple[date | None, date, str, str], MetricPoint] = {}
        for point in points:
            if not _is_annual_period(point):
                continue
            key = (
                point.period_start,
                point.period_end,
                point.currency or "",
                point.accession or "",
            )
            current = keyed.get(key)
            if current is None or (point.filed or date.min) > (current.filed or date.min):
                keyed[key] = point
        return keyed

    operating_by_key = keyed_annual(operating.points)
    capex_by_key = keyed_annual(capex.points)
    common = operating_by_key.keys() & capex_by_key.keys()
    best: tuple[date | None, date, str, str] | None = None
    for key in common:
        if best is None or key[1] > best[1]:
            best = key
    if best is None:
        return None, None
    operating_point = operating_by_key[best]
    capex_point = capex_by_key[best]
    if (
        operating_point.fiscal_year is not None
        and capex_point.fiscal_year is not None
        and operating_point.fiscal_year != capex_point.fiscal_year
    ):
        return None, None
    return (
        float(operating_point.value) - float(capex_point.value),
        best[1],
    )


def _positive_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or numerator <= 0 or denominator <= 0:
        return None
    return numerator / denominator


def valuation_snapshot(
    fundamentals: Fundamentals,
    quote_batch: MarketQuoteBatch,
) -> ValuationSnapshot:
    """Value one symbol from a current quote and annual statement facts.

    Currency handling (audit Y04): each ratio's denominator keeps its
    MetricPoint so the quote currency can be checked against it. When both
    the quote currency and the denominator currency are known and differ,
    the ratio is not computed (None). When either side is unknown the ratio
    is computed as before — currency-unverified: the legacy behaviour is
    deliberately preserved for data that carries no currency, and the
    operator should treat such multiples as currency-unverified until the
    providers populate ``MarketQuote.currency``/``MetricPoint.currency``.
    """

    quote = quote_batch.quotes.get(fundamentals.symbol)
    if quote is None:
        raise LookupError(f"Quote unavailable for {fundamentals.symbol}")

    shares_point = _latest_annual_point(fundamentals, "shares_diluted")
    shares = None if shares_point is None else float(shares_point.value)
    market_cap = quote.price * shares if shares is not None and shares > 0 else None

    revenue_point = _latest_annual_point(fundamentals, "revenue")
    revenue = None if revenue_point is None else float(revenue_point.value)
    net_income_point = _latest_annual_point(fundamentals, "net_income")
    net_income = None if net_income_point is None else float(net_income_point.value)
    equity_point = _latest_annual_point(fundamentals, "equity")
    equity = None if equity_point is None else float(equity_point.value)
    free_cash_flow, fcf_period = _annual_fcf(fundamentals)

    # Denominator currency gates: unknown on either side stays computed
    # (currency-unverified), a known mismatch refuses the ratio (fail closed).
    if not _currency_compatible(quote.currency, revenue_point):
        revenue = None
    if not _currency_compatible(quote.currency, net_income_point):
        net_income = None
    if not _currency_compatible(quote.currency, equity_point):
        equity = None

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

    fundamentals_provenance = fundamentals.provenance.model_copy(
        update={
            "notes": [
                *fundamentals.provenance.notes,
                "Valuation denominators use the latest available annual financial-statement facts "
                "from this provider; they are not consensus estimates or trailing-twelve-month "
                "reconstructions.",
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
        provenance=[fundamentals_provenance, market_provenance],
        evaluated_at=datetime.now(UTC),
    )
