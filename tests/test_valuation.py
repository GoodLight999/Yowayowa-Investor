from datetime import date
from decimal import Decimal

from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MarketQuote,
    MarketQuoteBatch,
    MetricPoint,
    MetricSeries,
    Provenance,
)
from yowayowa.services.valuation import valuation_snapshot


def _series(key: str, values: list[tuple[int, str]], unit: str = "USD") -> MetricSeries:
    return MetricSeries(
        key=key,
        label=key,
        points=[
            MetricPoint(
                period_start=date(year, 1, 1),
                period_end=date(year, 12, 31),
                fiscal_year=year,
                fiscal_period="FY",
                value=Decimal(value),
                unit=unit,
                form="10-K",
            )
            for year, value in values
        ],
    )


def test_valuation_uses_current_price_and_latest_annual_financial_facts() -> None:
    facts = Fundamentals(
        symbol="AAA",
        cik="0001",
        company_name="AAA Corp",
        metrics={
            "revenue": _series("revenue", [(2024, "80"), (2025, "100")]),
            "net_income": _series("net_income", [(2024, "8"), (2025, "10")]),
            "equity": _series("equity", [(2024, "45"), (2025, "50")]),
            "shares_diluted": _series("shares_diluted", [(2025, "10")], "shares"),
            "operating_cash_flow": _series("operating_cash_flow", [(2025, "30")]),
            "capex": _series("capex", [(2025, "10")]),
        },
        provenance=Provenance(
            provider="sec",
            source="SEC",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )
    quotes = MarketQuoteBatch(
        quotes={
            "AAA": MarketQuote(
                symbol="AAA",
                price=20,
                previous_close=19,
                as_of="2026-08-12T00:00:00Z",
            )
        },
        provenance=Provenance(
            provider="market",
            source="Market",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )

    result = valuation_snapshot(facts, quotes)

    assert result.market_cap == 200
    assert result.annual_period_end == date(2025, 12, 31)
    assert result.metrics["price_to_sales"] == 2
    assert result.metrics["price_to_earnings"] == 20
    assert result.metrics["price_to_book"] == 4
    assert result.metrics["price_to_free_cash_flow"] == 10
    assert result.metrics["earnings_yield"] == 0.05
    assert result.metrics["free_cash_flow_yield"] == 0.1
    assert result.metrics["revenue_growth_yoy"] == 0.25
    assert "financial-statement facts from this provider" in result.provenance[0].notes[-1]


def test_valuation_provenance_does_not_claim_sec_for_non_sec_provider() -> None:
    facts = Fundamentals(
        symbol="7203.T",
        cik=None,
        company_name="Toyota Motor Corporation",
        metrics={
            "revenue": _series("revenue", [(2025, "100")], "JPY"),
            "shares_diluted": _series("shares_diluted", [(2025, "10")], "shares"),
        },
        provenance=Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance financial statements",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )
    quotes = MarketQuoteBatch(
        quotes={"7203.T": MarketQuote(symbol="7203.T", price=20, as_of="2026-08-12T00:00:00Z")},
        provenance=Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )

    result = valuation_snapshot(facts, quotes)

    valuation_note = result.provenance[0].notes[-1]
    assert "SEC" not in valuation_note
    assert "this provider" in valuation_note


def test_valuation_does_not_report_misleading_negative_multiple() -> None:
    facts = Fundamentals(
        symbol="AAA",
        cik="0001",
        company_name="AAA Corp",
        metrics={
            "revenue": _series("revenue", [(2025, "100")]),
            "net_income": _series("net_income", [(2025, "-10")]),
            "shares_diluted": _series("shares_diluted", [(2025, "10")], "shares"),
        },
        provenance=Provenance(
            provider="sec",
            source="SEC",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )
    quotes = MarketQuoteBatch(
        quotes={"AAA": MarketQuote(symbol="AAA", price=20, as_of="2026-08-12T00:00:00Z")},
        provenance=Provenance(
            provider="market",
            source="Market",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )

    result = valuation_snapshot(facts, quotes)
    assert result.metrics["price_to_earnings"] is None
    assert result.metrics["earnings_yield"] is None
