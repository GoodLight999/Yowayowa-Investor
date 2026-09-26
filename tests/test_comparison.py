from datetime import date
from decimal import Decimal

from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.services.comparison import available_metrics, compare
from yowayowa.services.screening import derived_metrics


def fundamentals(symbol: str, revenue: str, prior_revenue: str) -> Fundamentals:
    return Fundamentals(
        symbol=symbol,
        cik="0000000001",
        company_name=f"{symbol} Corp",
        metrics={
            "revenue": MetricSeries(
                key="revenue",
                label="Revenue",
                points=[
                    MetricPoint(
                        period_start=date(2024, 1, 1),
                        period_end=date(2024, 12, 31),
                        fiscal_year=2024,
                        fiscal_period="FY",
                        value=Decimal(prior_revenue),
                        unit="USD",
                    ),
                    MetricPoint(
                        period_start=date(2025, 1, 1),
                        period_end=date(2025, 12, 31),
                        fiscal_year=2025,
                        fiscal_period="FY",
                        value=Decimal(revenue),
                        unit="USD",
                    ),
                ],
            ),
            "operating_income": MetricSeries(
                key="operating_income",
                label="Operating income",
                points=[
                    MetricPoint(
                        period_start=date(2025, 1, 1),
                        period_end=date(2025, 12, 31),
                        value=Decimal("20"),
                        unit="USD",
                    )
                ],
            ),
        },
        provenance=Provenance(
            provider="test",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-01-01T00:00:00Z",
        ),
    )


def test_revenue_growth_uses_same_fiscal_period_previous_year() -> None:
    values = derived_metrics(fundamentals("AAA", "120", "100"))
    assert round(values["revenue_growth_yoy"] or 0, 4) == 0.2


def test_comparison_uses_shared_metric_definitions_and_provenance() -> None:
    result = compare(
        [fundamentals("BBB", "130", "100"), fundamentals("AAA", "110", "100")],
        ["revenue_growth_yoy", "operating_margin"],
    )
    assert [metric.key for metric in result.metrics] == [
        "revenue_growth_yoy",
        "operating_margin",
    ]
    assert [row.symbol for row in result.rows] == ["AAA", "BBB"]
    assert result.rows[0].metrics["revenue_growth_yoy"] == 0.1
    assert result.rows[1].metrics["revenue_growth_yoy"] == 0.3
    assert result.rows[0].provenance.provider == "test"


def test_metric_catalog_exposes_full_shared_financial_quality_surface() -> None:
    metrics = available_metrics()
    by_key = {metric.key: metric for metric in metrics}
    defaults = [metric.key for metric in metrics if metric.default_selected]

    assert len(metrics) == 35
    assert {
        "gross_profit_growth_yoy",
        "operating_cash_flow_growth_yoy",
        "free_cash_flow_margin",
        "return_on_equity",
        "asset_turnover",
        "current_ratio",
        "cash_ratio",
        "equity_to_assets",
        "working_capital",
        "current_assets",
    } <= by_key.keys()
    assert {
        "revenue_growth_yoy",
        "operating_margin",
        "free_cash_flow_margin",
        "return_on_equity",
        "current_ratio",
        "liabilities_to_equity",
    } <= set(defaults)
    assert by_key["liabilities_to_equity"].higher_is_better is False
    assert by_key["revenue"].higher_is_better is None
    assert by_key["free_cash_flow"].higher_is_better is None
