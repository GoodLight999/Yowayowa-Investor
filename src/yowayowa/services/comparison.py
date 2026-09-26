from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from yowayowa.domain import ComparisonMetric, ComparisonResponse, ComparisonRow, Fundamentals
from yowayowa.services.screening import derived_metrics

Display = Literal["percent", "ratio", "compact", "number"]


def _metric(
    key: str,
    label: str,
    display: Display,
    higher_is_better: bool | None,
    *,
    default: bool = False,
) -> ComparisonMetric:
    return ComparisonMetric(
        key=key,
        label=label,
        display=display,
        higher_is_better=higher_is_better,
        default_selected=default,
    )


_METRIC_LIST = [
    _metric("revenue_growth_yoy", "Revenue growth YoY", "percent", True, default=True),
    _metric("gross_profit_growth_yoy", "Gross profit growth YoY", "percent", True),
    _metric("operating_income_growth_yoy", "Operating income growth YoY", "percent", True),
    _metric("net_income_growth_yoy", "Net income growth YoY", "percent", True),
    _metric("eps_growth_yoy", "Diluted EPS growth YoY", "percent", True),
    _metric("operating_cash_flow_growth_yoy", "Operating cash flow growth YoY", "percent", True),
    _metric("gross_margin", "Gross margin", "percent", True),
    _metric("operating_margin", "Operating margin", "percent", True, default=True),
    _metric("net_margin", "Net margin", "percent", True, default=True),
    _metric("operating_cash_flow_margin", "Operating cash flow margin", "percent", True),
    _metric("free_cash_flow_margin", "Free cash flow margin", "percent", True, default=True),
    _metric("return_on_assets", "Return on assets", "percent", True, default=True),
    _metric("return_on_equity", "Return on equity", "percent", True, default=True),
    _metric("asset_turnover", "Asset turnover", "ratio", True),
    _metric("capex_to_revenue", "Capex / revenue", "percent", None),
    _metric("current_ratio", "Current ratio", "ratio", True, default=True),
    _metric("cash_ratio", "Cash ratio", "ratio", True),
    _metric("liabilities_to_equity", "Liabilities / equity", "ratio", False, default=True),
    _metric("equity_to_assets", "Equity / assets", "percent", True),
    _metric("liabilities_to_assets", "Liabilities / assets", "percent", False),
    _metric("cash_to_assets", "Cash / assets", "percent", True),
    _metric("working_capital", "Working capital", "compact", None),
    _metric("revenue", "Revenue", "compact", None),
    _metric("gross_profit", "Gross profit", "compact", None),
    _metric("operating_income", "Operating income", "compact", None),
    _metric("net_income", "Net income", "compact", None),
    _metric("operating_cash_flow", "Operating cash flow", "compact", None),
    _metric("free_cash_flow", "Free cash flow", "compact", None),
    _metric("cash", "Cash & equivalents", "compact", None),
    _metric("current_assets", "Current assets", "compact", None),
    _metric("current_liabilities", "Current liabilities", "compact", None),
    _metric("assets", "Total assets", "compact", None),
    _metric("liabilities", "Total liabilities", "compact", None),
    _metric("equity", "Stockholders' equity", "compact", None),
    _metric("eps_diluted", "Diluted EPS", "number", None),
]

METRICS: dict[str, ComparisonMetric] = {metric.key: metric for metric in _METRIC_LIST}
DEFAULT_METRICS = [key for key, metric in METRICS.items() if metric.default_selected]


def available_metrics() -> list[ComparisonMetric]:
    return list(METRICS.values())


def compare(
    fundamentals: list[Fundamentals], requested_metrics: list[str] | None = None
) -> ComparisonResponse:
    keys = requested_metrics or DEFAULT_METRICS
    metrics = [METRICS[key] for key in keys if key in METRICS]
    rows: list[ComparisonRow] = []
    for item in fundamentals:
        calculated = derived_metrics(item)
        rows.append(
            ComparisonRow(
                symbol=item.symbol,
                company_name=item.company_name,
                metrics={metric.key: calculated.get(metric.key) for metric in metrics},
                provenance=item.provenance,
            )
        )
    rows.sort(key=lambda row: row.symbol)
    return ComparisonResponse(metrics=metrics, rows=rows, evaluated_at=datetime.now(UTC))
