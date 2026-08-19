from datetime import date
from decimal import Decimal

from yowayowa.domain import (
    FilterOperator,
    Fundamentals,
    LicenseClass,
    MetricPoint,
    MetricSeries,
    Provenance,
    ScreenFilter,
)
from yowayowa.services.screening import derived_metrics, screen


def fundamentals(symbol: str) -> Fundamentals:
    def duration_metric(key: str, prior: str, current: str) -> MetricSeries:
        return MetricSeries(
            key=key,
            label=key,
            points=[
                MetricPoint(
                    period_start=date(2024, 1, 1),
                    period_end=date(2024, 12, 31),
                    fiscal_year=2024,
                    fiscal_period="FY",
                    value=Decimal(prior),
                    unit="USD",
                ),
                MetricPoint(
                    period_start=date(2025, 1, 1),
                    period_end=date(2025, 12, 31),
                    fiscal_year=2025,
                    fiscal_period="FY",
                    value=Decimal(current),
                    unit="USD",
                ),
            ],
        )

    def instant_metric(key: str, start: str, end: str) -> MetricSeries:
        return MetricSeries(
            key=key,
            label=key,
            points=[
                MetricPoint(period_end=date(2024, 12, 31), value=Decimal(start), unit="USD"),
                MetricPoint(period_end=date(2025, 12, 31), value=Decimal(end), unit="USD"),
            ],
        )

    return Fundamentals(
        symbol=symbol,
        cik="0000000001",
        company_name=symbol,
        metrics={
            "revenue": duration_metric("revenue", "80", "100"),
            "gross_profit": duration_metric("gross_profit", "40", "50"),
            "operating_income": duration_metric("operating_income", "15", "20"),
            "net_income": duration_metric("net_income", "8", "10"),
            "eps_diluted": duration_metric("eps_diluted", "1", "1.25"),
            "assets": instant_metric("assets", "180", "200"),
            "current_assets": instant_metric("current_assets", "80", "100"),
            "liabilities": instant_metric("liabilities", "70", "80"),
            "current_liabilities": instant_metric("current_liabilities", "40", "50"),
            "equity": instant_metric("equity", "110", "120"),
            "cash": instant_metric("cash", "20", "30"),
            "operating_cash_flow": duration_metric("operating_cash_flow", "20", "30"),
            "capex": duration_metric("capex", "4", "5"),
        },
        provenance=Provenance(
            provider="test",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-01-01T00:00:00Z",
        ),
    )


def test_derived_metrics_cover_growth_profitability_efficiency_and_liquidity() -> None:
    values = derived_metrics(fundamentals("AAA"))

    assert values["revenue_growth_yoy"] == 0.25
    assert values["gross_profit_growth_yoy"] == 0.25
    assert round(values["operating_income_growth_yoy"] or 0, 4) == 0.3333
    assert values["net_income_growth_yoy"] == 0.25
    assert values["eps_growth_yoy"] == 0.25
    assert values["operating_cash_flow_growth_yoy"] == 0.5

    assert values["gross_margin"] == 0.5
    assert values["operating_margin"] == 0.2
    assert values["net_margin"] == 0.1
    assert values["operating_cash_flow_margin"] == 0.3
    assert values["free_cash_flow"] == 25.0
    assert values["free_cash_flow_margin"] == 0.25
    assert values["capex_to_revenue"] == 0.05

    assert round(values["liabilities_to_equity"] or 0, 4) == 0.6667
    assert values["equity_to_assets"] == 0.6
    assert values["liabilities_to_assets"] == 0.4
    assert values["cash_to_assets"] == 0.15
    assert values["current_ratio"] == 2.0
    assert values["cash_ratio"] == 0.6
    assert values["working_capital"] == 50.0

    assert round(values["return_on_assets"] or 0, 4) == 0.0528
    assert round(values["return_on_equity"] or 0, 4) == 0.0873
    assert round(values["asset_turnover"] or 0, 4) == 0.5281


def test_flow_ratios_require_matching_duration_not_only_matching_end_date() -> None:
    item = fundamentals("AAA")
    item.metrics["revenue"] = MetricSeries(
        key="revenue",
        label="revenue",
        points=[
            MetricPoint(
                period_start=date(2025, 4, 1),
                period_end=date(2025, 6, 30),
                fiscal_year=2025,
                fiscal_period="Q2",
                value=Decimal("100"),
                unit="USD",
            )
        ],
    )
    item.metrics["operating_cash_flow"] = MetricSeries(
        key="operating_cash_flow",
        label="operating_cash_flow",
        points=[
            MetricPoint(
                period_start=date(2025, 1, 1),
                period_end=date(2025, 6, 30),
                fiscal_year=2025,
                fiscal_period="Q2",
                value=Decimal("50"),
                unit="USD",
            )
        ],
    )

    values = derived_metrics(item)
    assert values["operating_cash_flow_margin"] is None
    assert values["free_cash_flow_margin"] is None


def test_screen_missing_or_failed_condition_does_not_pass() -> None:
    result = screen(
        [fundamentals("AAA")],
        [ScreenFilter(metric="return_on_equity", operator=FilterOperator.GT, value=0.1)],
    )
    assert result.rows[0].matched is False
    assert result.rows[0].failures == ["return_on_equity"]
