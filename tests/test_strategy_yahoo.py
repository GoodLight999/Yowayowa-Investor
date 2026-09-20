from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from yowayowa.domain import Fundamentals, LicenseClass, MetricPoint, MetricSeries, Provenance
from yowayowa.services.strategy_yahoo import balance_sheet_supplement


def _point(
    value: float,
    *,
    period_end: date,
    unit: str = "JPY",
    fiscal_period: str = "Q",
) -> MetricPoint:
    return MetricPoint(
        period_start=date(period_end.year, 1, 1),
        period_end=period_end,
        fiscal_period=fiscal_period,
        value=Decimal(str(value)),
        unit=unit,
        form="Yahoo normalized statement",
    )


def _series(key: str, *points: MetricPoint) -> MetricSeries:
    return MetricSeries(key=key, label=key, points=list(points))


def _fundamentals(provider: str = "yahoo/yfinance") -> Fundamentals:
    return Fundamentals(
        symbol="TEST.T",
        cik="",
        company_name="Test Corp",
        metrics={
            "current_assets": _series(
                "current_assets",
                _point(100, period_end=date(2025, 12, 31), fiscal_period="FY"),
                _point(130, period_end=date(2026, 6, 30)),
                _point(999, period_end=date(2026, 9, 30)),
            ),
            "liabilities": _series(
                "liabilities",
                _point(40, period_end=date(2025, 12, 31), fiscal_period="FY"),
                _point(50, period_end=date(2026, 6, 30)),
            ),
        },
        provenance=Provenance(
            provider=provider,
            source="Yahoo Finance financial statements",
            source_url="https://finance.yahoo.com/",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=datetime(2026, 9, 21, tzinfo=UTC),
            as_of=date(2026, 9, 30),
        ),
    )


def test_yahoo_strategy_supplement_uses_latest_common_statement_period() -> None:
    supplement = balance_sheet_supplement(_fundamentals())

    assert supplement is not None
    assert supplement.current_assets == 130
    assert supplement.liabilities == 50
    assert supplement.investment_securities is None
    assert supplement.provenance.as_of == date(2026, 6, 30)
    assert any("lower bound" in note for note in supplement.provenance.notes)


def test_yahoo_strategy_supplement_requires_same_currency_and_frequency() -> None:
    data = _fundamentals()
    liabilities = data.metrics["liabilities"].points[-1].model_copy(update={"unit": "USD"})
    data.metrics["liabilities"] = _series("liabilities", liabilities)

    supplement = balance_sheet_supplement(data)

    assert supplement is not None
    assert supplement.provenance.as_of == date(2025, 12, 31)
    assert supplement.current_assets == 100
    assert supplement.liabilities == 40


def test_yahoo_strategy_supplement_rejects_non_yahoo_source() -> None:
    assert balance_sheet_supplement(_fundamentals(provider="fixture")) is None


def test_yahoo_strategy_supplement_rejects_negative_values() -> None:
    data = _fundamentals()
    bad = data.metrics["current_assets"].points[1].model_copy(update={"value": Decimal("-1")})
    data.metrics["current_assets"] = _series("current_assets", bad)
    data.metrics["liabilities"] = _series(
        "liabilities",
        _point(50, period_end=date(2026, 6, 30)),
    )

    assert balance_sheet_supplement(data) is None
