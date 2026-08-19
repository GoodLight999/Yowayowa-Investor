from __future__ import annotations

from datetime import UTC, date, datetime

from yowayowa.domain import (
    FilterOperator,
    Fundamentals,
    MetricPoint,
    ScreenFilter,
    ScreenResponse,
    ScreenRow,
)


def latest_point(fundamentals: Fundamentals, metric: str) -> MetricPoint | None:
    series = fundamentals.metrics.get(metric)
    if not series or not series.points:
        return None
    return max(series.points, key=lambda item: (item.period_end, item.filed or item.period_end))


def latest_metric(fundamentals: Fundamentals, metric: str) -> float | None:
    point = latest_point(fundamentals, metric)
    return float(point.value) if point is not None else None


def _period_key(point: MetricPoint) -> tuple[date | None, date, str | None]:
    return point.period_start, point.period_end, point.fiscal_period


def _latest_common_points(
    fundamentals: Fundamentals,
    *metric_names: str,
) -> list[MetricPoint] | None:
    if not metric_names:
        return None
    keyed: list[dict[tuple[date | None, date, str | None], MetricPoint]] = []
    for metric in metric_names:
        series = fundamentals.metrics.get(metric)
        if not series or not series.points:
            return None
        by_period: dict[tuple[date | None, date, str | None], MetricPoint] = {}
        for point in series.points:
            key = _period_key(point)
            current = by_period.get(key)
            if current is None or (point.filed or date.min) > (current.filed or date.min):
                by_period[key] = point
        keyed.append(by_period)

    common_keys = set(keyed[0])
    for values in keyed[1:]:
        common_keys &= values.keys()
    if not common_keys:
        return None
    selected = max(
        common_keys,
        key=lambda key: (key[1], key[0] or date.min, key[2] or ""),
    )
    return [values[selected] for values in keyed]


def _ratio_on_common_period(
    fundamentals: Fundamentals,
    numerator: str,
    denominator: str,
) -> float | None:
    points = _latest_common_points(fundamentals, numerator, denominator)
    if points is None:
        return None
    numerator_point, denominator_point = points
    denominator_value = float(denominator_point.value)
    if denominator_value == 0:
        return None
    return float(numerator_point.value) / denominator_value


def _difference_on_common_period(
    fundamentals: Fundamentals,
    left_metric: str,
    right_metric: str,
) -> float | None:
    points = _latest_common_points(fundamentals, left_metric, right_metric)
    if points is None:
        return None
    left, right = points
    return float(left.value) - float(right.value)


def _year_over_year_growth(fundamentals: Fundamentals, metric: str) -> float | None:
    series = fundamentals.metrics.get(metric)
    if not series or len(series.points) < 2:
        return None
    latest = max(series.points, key=lambda point: (point.period_end, point.filed or date.min))
    candidates = [
        point
        for point in series.points
        if point.period_end < latest.period_end
        and point.fiscal_period == latest.fiscal_period
        and (
            latest.fiscal_year is None
            or point.fiscal_year is None
            or point.fiscal_year == latest.fiscal_year - 1
        )
    ]
    if not candidates:
        return None
    prior = max(candidates, key=lambda point: (point.period_end, point.filed or date.min))
    prior_value = float(prior.value)
    if prior_value <= 0:
        return None
    return (float(latest.value) - prior_value) / prior_value


def _annualized_flow_to_average_balance(
    fundamentals: Fundamentals,
    flow_metric: str,
    balance_metric: str,
) -> float | None:
    flow = latest_point(fundamentals, flow_metric)
    balances = fundamentals.metrics.get(balance_metric)
    if flow is None or flow.period_start is None or not balances or not balances.points:
        return None

    end_candidates = [point for point in balances.points if point.period_end == flow.period_end]
    start_candidates = [point for point in balances.points if point.period_end <= flow.period_start]
    if not end_candidates or not start_candidates:
        return None

    end_balance = max(end_candidates, key=lambda point: point.filed or date.min)
    start_balance = max(start_candidates, key=lambda point: point.period_end)
    average_balance = (float(start_balance.value) + float(end_balance.value)) / 2
    duration_days = (flow.period_end - flow.period_start).days
    if average_balance <= 0 or duration_days <= 0:
        return None
    return (float(flow.value) / average_balance) * (365.25 / duration_days)


def _free_cash_flow_margin(fundamentals: Fundamentals) -> float | None:
    points = _latest_common_points(
        fundamentals,
        "operating_cash_flow",
        "capex",
        "revenue",
    )
    if points is None:
        return None
    operating_cash_flow, capex, revenue = points
    revenue_value = float(revenue.value)
    if revenue_value == 0:
        return None
    return (float(operating_cash_flow.value) - float(capex.value)) / revenue_value


def derived_metrics(fundamentals: Fundamentals) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {
        key: latest_metric(fundamentals, key)
        for key in (
            "revenue",
            "gross_profit",
            "operating_income",
            "net_income",
            "assets",
            "current_assets",
            "liabilities",
            "current_liabilities",
            "equity",
            "cash",
            "operating_cash_flow",
            "capex",
            "eps_diluted",
        )
    }
    metrics["revenue_growth_yoy"] = _year_over_year_growth(fundamentals, "revenue")
    metrics["gross_profit_growth_yoy"] = _year_over_year_growth(fundamentals, "gross_profit")
    metrics["operating_income_growth_yoy"] = _year_over_year_growth(
        fundamentals, "operating_income"
    )
    metrics["net_income_growth_yoy"] = _year_over_year_growth(fundamentals, "net_income")
    metrics["eps_growth_yoy"] = _year_over_year_growth(fundamentals, "eps_diluted")
    metrics["operating_cash_flow_growth_yoy"] = _year_over_year_growth(
        fundamentals, "operating_cash_flow"
    )
    metrics["gross_margin"] = _ratio_on_common_period(fundamentals, "gross_profit", "revenue")
    metrics["operating_margin"] = _ratio_on_common_period(
        fundamentals,
        "operating_income",
        "revenue",
    )
    metrics["net_margin"] = _ratio_on_common_period(fundamentals, "net_income", "revenue")
    metrics["operating_cash_flow_margin"] = _ratio_on_common_period(
        fundamentals,
        "operating_cash_flow",
        "revenue",
    )
    metrics["free_cash_flow_margin"] = _free_cash_flow_margin(fundamentals)
    metrics["capex_to_revenue"] = _ratio_on_common_period(fundamentals, "capex", "revenue")
    metrics["return_on_assets"] = _annualized_flow_to_average_balance(
        fundamentals,
        "net_income",
        "assets",
    )
    metrics["return_on_equity"] = _annualized_flow_to_average_balance(
        fundamentals,
        "net_income",
        "equity",
    )
    metrics["asset_turnover"] = _annualized_flow_to_average_balance(
        fundamentals,
        "revenue",
        "assets",
    )
    metrics["liabilities_to_equity"] = _ratio_on_common_period(
        fundamentals,
        "liabilities",
        "equity",
    )
    metrics["equity_to_assets"] = _ratio_on_common_period(fundamentals, "equity", "assets")
    metrics["liabilities_to_assets"] = _ratio_on_common_period(
        fundamentals,
        "liabilities",
        "assets",
    )
    metrics["cash_to_assets"] = _ratio_on_common_period(fundamentals, "cash", "assets")
    metrics["current_ratio"] = _ratio_on_common_period(
        fundamentals,
        "current_assets",
        "current_liabilities",
    )
    metrics["cash_ratio"] = _ratio_on_common_period(
        fundamentals,
        "cash",
        "current_liabilities",
    )
    metrics["working_capital"] = _difference_on_common_period(
        fundamentals,
        "current_assets",
        "current_liabilities",
    )
    metrics["free_cash_flow"] = _difference_on_common_period(
        fundamentals,
        "operating_cash_flow",
        "capex",
    )
    return metrics


def _matches(value: float | None, rule: ScreenFilter) -> bool:
    if value is None:
        return False
    if rule.operator == FilterOperator.BETWEEN:
        assert isinstance(rule.value, list) and len(rule.value) == 2
        low, high = rule.value
        return low <= value <= high
    assert not isinstance(rule.value, list)
    target = rule.value
    return {
        FilterOperator.GT: value > target,
        FilterOperator.GTE: value >= target,
        FilterOperator.LT: value < target,
        FilterOperator.LTE: value <= target,
        FilterOperator.EQ: value == target,
    }[rule.operator]


def screen(fundamentals: list[Fundamentals], filters: list[ScreenFilter]) -> ScreenResponse:
    rows: list[ScreenRow] = []
    for item in fundamentals:
        metrics = derived_metrics(item)
        failures = [rule.metric for rule in filters if not _matches(metrics.get(rule.metric), rule)]
        rows.append(
            ScreenRow(
                symbol=item.symbol,
                metrics=metrics,
                matched=not failures,
                failures=failures,
            )
        )
    rows.sort(key=lambda row: (not row.matched, row.symbol))
    return ScreenResponse(rows=rows, evaluated_at=datetime.now(UTC))
