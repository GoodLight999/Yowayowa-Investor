from __future__ import annotations

from datetime import date

from yowayowa.domain import Fundamentals, MetricPoint
from yowayowa.strategy_models import StrategyBalanceSheetSupplement


def _points(fundamentals: Fundamentals, metric: str) -> list[MetricPoint]:
    series = fundamentals.metrics.get(metric)
    if series is None:
        return []
    return [point for point in series.points if point.unit]


def _by_period(points: list[MetricPoint]) -> dict[tuple[date, str, str | None], MetricPoint]:
    result: dict[tuple[date, str, str | None], MetricPoint] = {}
    for point in points:
        key = (point.period_end, point.unit, point.fiscal_period)
        current = result.get(key)
        if current is None or (point.filed or date.min) > (current.filed or date.min):
            result[key] = point
    return result


def balance_sheet_supplement(
    fundamentals: Fundamentals,
) -> StrategyBalanceSheetSupplement | None:
    """Build a period-consistent conservative strategy balance sheet from Yahoo data.

    Yahoo's normalized international statements are useful for personal-mode research,
    but their broad investment rows are not a sufficiently precise cross-market analogue
    of Kiyohara's investment-securities term. We therefore align current assets and total
    liabilities to one statement period/currency and intentionally leave investment
    securities missing so the evaluator exposes a lower bound rather than guessing.
    """
    if fundamentals.provenance.provider != "yahoo/yfinance":
        return None

    current_assets = _by_period(_points(fundamentals, "current_assets"))
    liabilities = _by_period(_points(fundamentals, "liabilities"))
    common_keys = set(current_assets) & set(liabilities)
    if not common_keys:
        return None

    key = max(common_keys, key=lambda item: (item[0], item[2] or ""))
    current_assets_point = current_assets[key]
    liabilities_point = liabilities[key]
    values = (
        float(current_assets_point.value),
        float(liabilities_point.value),
    )
    if any(value < 0 for value in values):
        return None

    period_end, unit, fiscal_period = key
    provenance = fundamentals.provenance.model_copy(
        update={
            "as_of": period_end,
            "notes": [
                *fundamentals.provenance.notes,
                (
                    "Strategy balance-sheet inputs use Yahoo normalized current assets and "
                    f"liabilities from the same {period_end.isoformat()} statement period, "
                    f"{unit} unit, and {fiscal_period or 'unspecified'} frequency."
                ),
                (
                    "Yahoo investment rows are not treated as an exact cross-market equivalent "
                    "of Kiyohara investment securities; the strategy therefore keeps the "
                    "conservative net-cash lower bound."
                ),
            ],
        }
    )
    return StrategyBalanceSheetSupplement(
        current_assets=values[0],
        liabilities=values[1],
        investment_securities=None,
        provenance=provenance,
    )
