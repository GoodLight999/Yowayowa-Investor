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
        cik="",
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


# --------------------------------------------------- audit Y04/Y05: valuation


def _annual_point(
    value: str,
    *,
    start: date = date(2025, 1, 1),
    end: date = date(2025, 12, 31),
    currency: str | None = None,
    accession: str | None = None,
    fiscal_year: int | None = 2025,
    fiscal_period: str | None = "FY",
    form: str | None = None,
    unit: str = "USD",
) -> MetricPoint:
    return MetricPoint(
        period_start=start,
        period_end=end,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        value=Decimal(value),
        unit=unit,
        currency=currency,
        accession=accession,
        form=form,
    )


def _simple_series(key: str, point: MetricPoint) -> MetricSeries:
    return MetricSeries(key=key, label=key, points=[point])


def _valuation_facts(**metrics: MetricSeries) -> Fundamentals:
    return Fundamentals(
        symbol="AUDIT",
        cik="",
        company_name="合成企業",
        metrics=metrics,
        provenance=Provenance(
            provider="test",
            source="fixture",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at="2026-08-13T00:00:00Z",
        ),
    )


def _quote_batch(currency: str | None = None) -> MarketQuoteBatch:
    return MarketQuoteBatch(
        quotes={
            "AUDIT": MarketQuote(
                symbol="AUDIT",
                price=20,
                currency=currency,
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


def test_valuation_currency_mismatch_refuses_price_to_earnings() -> None:
    """Audit Y04: USD quote vs JPY net income -> no P/E; None quote computes."""

    facts = _valuation_facts(
        net_income=_simple_series(
            "net_income", _annual_point("10", currency="JPY", accession="doc-J")
        ),
        shares_diluted=_simple_series("shares_diluted", _annual_point("10", unit="shares")),
    )
    usd_result = valuation_snapshot(facts, _quote_batch(currency="USD"))
    assert usd_result.metrics["price_to_earnings"] is None
    assert usd_result.metrics["earnings_yield"] is None

    # Unknown quote currency stays computed (currency-unverified, legacy).
    unknown_result = valuation_snapshot(facts, _quote_batch(currency=None))
    assert unknown_result.metrics["price_to_earnings"] == 20.0


def test_valuation_same_currency_still_computes() -> None:
    """Audit Y04: matching quote/statement currencies keep the ratio."""

    facts = _valuation_facts(
        net_income=_simple_series(
            "net_income", _annual_point("10", currency="USD", accession="doc-U")
        ),
        shares_diluted=_simple_series("shares_diluted", _annual_point("10", unit="shares")),
    )
    result = valuation_snapshot(facts, _quote_batch(currency="USD"))
    assert result.metrics["price_to_earnings"] == 20.0
    assert result.metrics["earnings_yield"] == 0.05


def test_annual_fcf_does_not_mix_q4_capex_with_full_year_operating_cf() -> None:
    """Audit Y05: FY operating CF with a Q4-stub capex produces no FCF."""

    facts = _valuation_facts(
        operating_cash_flow=_simple_series(
            "operating_cash_flow",
            _annual_point(
                "100",
                start=date(2025, 1, 1),
                end=date(2025, 12, 31),
                fiscal_period="FY",
            ),
        ),
        capex=_simple_series(
            "capex",
            _annual_point(
                "10",
                start=date(2025, 10, 1),
                end=date(2025, 12, 31),
                fiscal_period="Q4",
            ),
        ),
    )
    result = valuation_snapshot(facts, _quote_batch())
    assert result.metrics["annual_free_cash_flow"] is None
    assert result.metrics["price_to_free_cash_flow"] is None
    assert result.metrics["free_cash_flow_yield"] is None


def test_annual_fcf_computes_for_matching_full_year_points() -> None:
    """Audit Y05: matching full-year CF+capex still yields FCF (90)."""

    facts = _valuation_facts(
        operating_cash_flow=_simple_series(
            "operating_cash_flow",
            _annual_point(
                "100",
                start=date(2025, 1, 1),
                end=date(2025, 12, 31),
                currency="USD",
                accession="doc-U",
                fiscal_period="FY",
            ),
        ),
        capex=_simple_series(
            "capex",
            _annual_point(
                "10",
                start=date(2025, 1, 1),
                end=date(2025, 12, 31),
                currency="USD",
                accession="doc-U",
                fiscal_period="FY",
            ),
        ),
    )
    result = valuation_snapshot(facts, _quote_batch())
    assert result.metrics["annual_free_cash_flow"] == 90.0


def test_annual_fcf_refuses_currency_accession_or_year_mismatch() -> None:
    """Audit Y05: same span but different currency/accession/year -> None."""

    base_kwargs = dict(start=date(2025, 1, 1), end=date(2025, 12, 31))
    variants = [
        # currency mismatch
        dict(operating=dict(currency="USD"), capex=dict(currency="JPY")),
        # accession mismatch
        dict(operating=dict(accession="doc-A"), capex=dict(accession="doc-B")),
        # fiscal_year mismatch
        dict(operating=dict(fiscal_year=2025), capex=dict(fiscal_year=2024)),
    ]
    for variant in variants:
        facts = _valuation_facts(
            operating_cash_flow=_simple_series(
                "operating_cash_flow",
                _annual_point("100", **{**base_kwargs, **variant["operating"]}),  # type: ignore[arg-type]
            ),
            capex=_simple_series(
                "capex",
                _annual_point("10", **{**base_kwargs, **variant["capex"]}),  # type: ignore[arg-type]
            ),
        )
        result = valuation_snapshot(facts, _quote_batch())
        assert result.metrics["annual_free_cash_flow"] is None, variant


def test_latest_annual_point_ignores_newer_q4_stub_without_full_year() -> None:
    """Audit Y05: the newest point wins only when its real span is a year."""

    from yowayowa.services.valuation import _latest_annual_point

    facts = _valuation_facts(
        net_income=_simple_series(
            "net_income",
            _annual_point(
                "10",
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                fiscal_year=2024,
                fiscal_period="FY",
            ),
        ),
    )
    # Newest point is a Q4 stub (90 days) — must be ignored; the previous
    # real-year FY point is selected.
    facts.metrics["net_income"].points.append(
        _annual_point(
            "30",
            start=date(2025, 10, 1),
            end=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="Q4",
        )
    )
    point = _latest_annual_point(facts, "net_income")
    assert point is not None
    assert point.period_end == date(2024, 12, 31)


def test_fy_label_alone_is_not_evidence_of_annual_period() -> None:
    """Audit Y05: fiscal_period='FY' with a 90-day span is not annual."""

    from yowayowa.services.valuation import _latest_annual_point

    facts = _valuation_facts(
        net_income=_simple_series(
            "net_income",
            _annual_point(
                "30",
                start=date(2025, 10, 1),
                end=date(2025, 12, 31),
                fiscal_period="FY",
            ),
        ),
    )
    assert _latest_annual_point(facts, "net_income") is None
