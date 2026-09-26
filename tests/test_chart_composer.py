from datetime import UTC, date, datetime
from decimal import Decimal

from yowayowa.chart_models import ChartComposeRequest
from yowayowa.domain import (
    Fundamentals,
    LicenseClass,
    MarketHistory,
    MetricPoint,
    MetricSeries,
    PriceBar,
    Provenance,
)
from yowayowa.services.charts import compose_chart


def _provenance(provider: str) -> Provenance:
    return Provenance(
        provider=provider,
        source=f"{provider} fixture",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        retrieved_at=datetime(2026, 1, 5, tzinfo=UTC),
    )


class FakeMarket:
    def history(self, symbol, period="1y", interval="1d", indicators=None):
        bars = [
            PriceBar(
                timestamp=datetime(2026, 1, day, tzinfo=UTC),
                open=value,
                high=value,
                low=value,
                close=value,
                volume=100,
            )
            for day, value in [(1, 10.0), (2, 12.0), (3, 14.0), (4, 16.0)]
        ]
        return MarketHistory(
            symbol=symbol,
            interval=interval,
            bars=bars,
            provenance=_provenance("market"),
        )


class FakeFundamentals:
    def company_facts(self, symbol):
        return Fundamentals(
            symbol=symbol,
            cik="0000000001",
            company_name="Fixture Corp",
            metrics={
                "revenue": MetricSeries(
                    key="revenue",
                    label="Revenue",
                    points=[
                        MetricPoint(
                            period_end=date(2025, 12, 31),
                            value=Decimal("100"),
                            unit="USD",
                        ),
                        MetricPoint(
                            period_end=date(2026, 1, 3),
                            value=Decimal("120"),
                            unit="USD",
                        ),
                    ],
                )
            },
            provenance=_provenance("sec"),
        )


class FakeFred:
    def series(self, series_id, **kwargs):
        return {
            "series_id": series_id,
            "metadata": {"title": "Fixture CPI", "units": "Index"},
            "observations": [
                {"date": "2026-01-02", "value": "2"},
                {"date": "2026-01-04", "value": "4"},
            ],
            "provenance": _provenance("fred").model_dump(mode="json"),
        }


def test_chart_composer_mixes_sources_and_avoids_lookahead() -> None:
    request = ChartComposeRequest.model_validate(
        {
            "sources": [
                {"id": "px", "source": "price", "symbol": "AAA", "period": "1y"},
                {
                    "id": "rev",
                    "source": "fundamental",
                    "symbol": "AAA",
                    "metric": "revenue",
                },
                {"id": "cpi", "source": "fred", "series_id": "CPIAUCSL"},
            ],
            "transforms": [
                {"id": "ratio", "kind": "ratio", "left": "px", "right": "cpi"},
                {"id": "spread", "kind": "spread", "left": "px", "right": "cpi"},
            ],
        }
    )
    result = compose_chart(request, FakeMarket(), FakeFundamentals(), FakeFred())

    assert result.errors == {}
    by_id = {series.id: series for series in result.series}
    assert [point.date for point in by_id["px"].points] == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
    ]
    assert by_id["rev"].unit == "USD"
    assert by_id["cpi"].label == "Fixture CPI"

    ratio = by_id["ratio"]
    assert [point.date for point in ratio.points] == [
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
    ]
    assert [point.value for point in ratio.points] == [6.0, 7.0, 4.0]
    assert {item.provider for item in ratio.provenance} == {"market", "fred"}
    assert "no backfill/look-ahead" in result.notes[0]


def test_chart_composer_isolates_source_and_dependent_transform_failures() -> None:
    request = ChartComposeRequest.model_validate(
        {
            "sources": [
                {"id": "px", "source": "price", "symbol": "AAA"},
                {
                    "id": "missing",
                    "source": "fundamental",
                    "symbol": "AAA",
                    "metric": "does_not_exist",
                },
            ],
            "transforms": [{"id": "ratio", "kind": "ratio", "left": "px", "right": "missing"}],
        }
    )
    result = compose_chart(request, FakeMarket(), FakeFundamentals(), FakeFred())

    assert [series.id for series in result.series] == ["px"]
    assert "Fundamental metric does_not_exist is unavailable" in result.errors["missing"]
    assert result.errors["ratio"] == "A required source is unavailable"
