from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MetricPoint,
    MetricSeries,
    Provenance,
)
from yowayowa.services.strategy_sec import balance_sheet_supplement


def _point(
    value: float,
    *,
    accession: str,
    period_end: date = date(2026, 6, 30),
    unit: str = "USD",
) -> MetricPoint:
    return MetricPoint(
        period_end=period_end,
        value=Decimal(str(value)),
        unit=unit,
        accession=accession,
        filed=date(2026, 8, 1),
        form="10-Q",
        fiscal_year=2026,
        fiscal_period="Q2",
    )


def _series(key: str, *points: MetricPoint) -> MetricSeries:
    return MetricSeries(key=key, label=key, points=list(points))


def _fundamentals(
    *,
    investment_accession: str | None = "same",
    provider: str = "sec-edgar",
) -> Fundamentals:
    accession = "0000000000-26-000001"
    metrics = {
        "current_assets": _series("current_assets", _point(120, accession=accession)),
        "liabilities": _series("liabilities", _point(40, accession=accession)),
    }
    if investment_accession is not None:
        inv_accession = accession if investment_accession == "same" else investment_accession
        metrics["marketable_securities_noncurrent"] = _series(
            "marketable_securities_noncurrent",
            _point(30, accession=inv_accession),
        )
    return Fundamentals(
        symbol="TEST",
        cik="0000000000",
        company_name="Test Corp",
        metrics=metrics,
        provenance=Provenance(
            provider=provider,
            source="SEC EDGAR Company Facts",
            source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000000000.json",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime(2026, 8, 2, tzinfo=UTC),
            as_of=date(2026, 6, 30),
        ),
    )


def test_sec_strategy_supplement_uses_same_filing_direct_noncurrent_securities() -> None:
    supplement = balance_sheet_supplement(_fundamentals())

    assert supplement is not None
    assert supplement.current_assets == 120
    assert supplement.liabilities == 40
    assert supplement.investment_securities == 30
    assert supplement.provenance.as_of == date(2026, 6, 30)
    assert any("MarketableSecuritiesNoncurrent" in note for note in supplement.provenance.notes)


def test_sec_strategy_supplement_keeps_lower_bound_when_investment_fact_is_other_filing() -> None:
    supplement = balance_sheet_supplement(
        _fundamentals(investment_accession="0000000000-26-000099")
    )

    assert supplement is not None
    assert supplement.current_assets == 120
    assert supplement.liabilities == 40
    assert supplement.investment_securities is None
    assert any("lower bound" in note for note in supplement.provenance.notes)


def test_sec_strategy_supplement_requires_same_usd_filing_for_balance_sheet_pair() -> None:
    data = _fundamentals()
    liabilities = data.metrics["liabilities"].points[0].model_copy(update={"unit": "EUR"})
    data.metrics["liabilities"] = _series("liabilities", liabilities)

    assert balance_sheet_supplement(data) is None


def test_sec_strategy_supplement_only_accepts_sec_fundamentals() -> None:
    assert balance_sheet_supplement(_fundamentals(provider="fixture")) is None


def test_sec_strategy_supplement_rejects_negative_balance_values() -> None:
    data = _fundamentals()
    current_assets = data.metrics["current_assets"].points[0].model_copy(
        update={"value": Decimal("-1")}
    )
    data.metrics["current_assets"] = _series("current_assets", current_assets)

    assert balance_sheet_supplement(data) is None
