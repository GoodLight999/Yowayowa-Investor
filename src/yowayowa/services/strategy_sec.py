from __future__ import annotations

from datetime import date

from yowayowa.domain import Fundamentals, MetricPoint
from yowayowa.strategy_models import StrategyBalanceSheetSupplement


def _instant_points(fundamentals: Fundamentals, metric: str) -> list[MetricPoint]:
    series = fundamentals.metrics.get(metric)
    if series is None:
        return []
    return [
        point
        for point in series.points
        if point.period_start is None and point.accession is not None and point.unit == "USD"
    ]


def _by_filing_key(points: list[MetricPoint]) -> dict[tuple[date, str, str], MetricPoint]:
    result: dict[tuple[date, str, str], MetricPoint] = {}
    for point in points:
        assert point.accession is not None
        key = (point.period_end, point.unit, point.accession)
        current = result.get(key)
        if current is None or (point.filed or date.min) > (current.filed or date.min):
            result[key] = point
    return result


def balance_sheet_supplement(
    fundamentals: Fundamentals,
) -> StrategyBalanceSheetSupplement | None:
    if fundamentals.provenance.provider != "sec-edgar":
        return None

    current_assets = _by_filing_key(_instant_points(fundamentals, "current_assets"))
    liabilities = _by_filing_key(_instant_points(fundamentals, "liabilities"))
    common_keys = set(current_assets) & set(liabilities)
    if not common_keys:
        return None

    key = max(
        common_keys,
        key=lambda item: (
            item[0],
            current_assets[item].filed or date.min,
            item[2],
        ),
    )
    current_assets_point = current_assets[key]
    liabilities_point = liabilities[key]
    values = (
        float(current_assets_point.value),
        float(liabilities_point.value),
    )
    if any(value < 0 for value in values):
        return None

    investment_securities: float | None = None
    investments = _by_filing_key(_instant_points(fundamentals, "marketable_securities_noncurrent"))
    investment_point = investments.get(key)
    if investment_point is not None:
        value = float(investment_point.value)
        if value >= 0:
            investment_securities = value

    period_end, unit, accession = key
    notes = [
        *fundamentals.provenance.notes,
        (
            "Strategy balance-sheet inputs use SEC Company Facts values from the same "
            f"filing accession {accession}, period end {period_end.isoformat()}, and {unit} unit."
        ),
    ]
    if investment_securities is not None:
        notes.append(
            "Investment securities use the direct us-gaap:MarketableSecuritiesNoncurrent "
            "entity-wide fact; current marketable securities are not added because they are "
            "already included in current assets."
        )
    else:
        notes.append(
            "No same-filing us-gaap:MarketableSecuritiesNoncurrent fact was available; "
            "the strategy therefore keeps the conservative net-cash lower bound."
        )
    provenance = fundamentals.provenance.model_copy(
        update={
            "as_of": period_end,
            "notes": notes,
        }
    )
    return StrategyBalanceSheetSupplement(
        current_assets=values[0],
        liabilities=values[1],
        investment_securities=investment_securities,
        provenance=provenance,
    )
