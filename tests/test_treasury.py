from datetime import UTC, date, datetime

import pytest

from yowayowa.config import Settings
from yowayowa.providers.treasury import TreasuryYieldCurveProvider

XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE m:type="Edm.DateTime">2026-08-14T00:00:00</d:NEW_DATE>
        <d:BC_1MONTH m:type="Edm.Double">4.10</d:BC_1MONTH>
        <d:BC_1_5MONTH m:type="Edm.Double">4.08</d:BC_1_5MONTH>
        <d:BC_2MONTH m:type="Edm.Double">4.05</d:BC_2MONTH>
        <d:BC_3MONTH m:type="Edm.Double">4.00</d:BC_3MONTH>
        <d:BC_6MONTH m:type="Edm.Double">3.90</d:BC_6MONTH>
        <d:BC_1YEAR m:type="Edm.Double">3.80</d:BC_1YEAR>
        <d:BC_2YEAR m:type="Edm.Double">3.70</d:BC_2YEAR>
        <d:BC_3YEAR m:type="Edm.Double">3.75</d:BC_3YEAR>
        <d:BC_5YEAR m:type="Edm.Double">3.90</d:BC_5YEAR>
        <d:BC_7YEAR m:type="Edm.Double">4.05</d:BC_7YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.20</d:BC_10YEAR>
        <d:BC_20YEAR m:type="Edm.Double">4.70</d:BC_20YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.80</d:BC_30YEAR>
      </m:properties>
    </content>
  </entry>
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE m:type="Edm.DateTime">2026-08-15T00:00:00</d:NEW_DATE>
        <d:BC_1MONTH m:type="Edm.Double">4.11</d:BC_1MONTH>
        <d:BC_3MONTH m:type="Edm.Double">4.01</d:BC_3MONTH>
        <d:BC_4MONTH m:type="Edm.Double">3.97</d:BC_4MONTH>
        <d:BC_2YEAR m:type="Edm.Double">3.71</d:BC_2YEAR>
        <d:BC_10YEAR m:type="Edm.Double">4.21</d:BC_10YEAR>
        <d:BC_30YEAR m:type="Edm.Double">4.81</d:BC_30YEAR>
      </m:properties>
    </content>
  </entry>
</feed>
"""


def test_treasury_xml_parser_preserves_missing_maturities_and_spreads() -> None:
    result = TreasuryYieldCurveProvider._parse_xml(
        XML,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert len(result.history) == 2
    assert result.latest.date == date(2026, 8, 15)
    assert [point.maturity for point in result.latest.points] == [
        "1M",
        "3M",
        "4M",
        "2Y",
        "10Y",
        "30Y",
    ]
    assert result.latest.spread_10y_2y == pytest.approx(0.50)
    assert result.latest.spread_10y_3m == pytest.approx(0.20)
    assert result.provenance.provider == "us-treasury"
    assert result.provenance.as_of == date(2026, 8, 15)
    assert "missing maturities" in result.provenance.notes[0].lower()


def test_treasury_xml_parser_rejects_empty_feed() -> None:
    with pytest.raises(LookupError, match="No Treasury par yield curve observations"):
        TreasuryYieldCurveProvider._parse_xml(
            "<feed xmlns='http://www.w3.org/2005/Atom'></feed>",
            retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        )


def _settings() -> Settings:
    return Settings(database_url="sqlite:///:memory:")


def _entry(date_text: str, fields: dict[str, str]) -> str:
    parts = [f'<d:NEW_DATE m:type="Edm.DateTime">{date_text}T00:00:00</d:NEW_DATE>']
    parts.extend(
        f'<d:{name} m:type="Edm.Double">{value}</d:{name}>' for name, value in fields.items()
    )
    return (
        '<entry><content type="application/xml">'
        "<m:properties>" + "".join(parts) + "</m:properties>"
        "</content></entry>"
    )


def test_treasury_parse_xml_skips_entries_without_date() -> None:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        ' xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"'
        ' xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        + _entry(
            "2026-08-14",
            {"BC_1MONTH": "4.10", "BC_2YEAR": "3.70", "BC_10YEAR": "4.20"},
        )
        + "</feed>"
    )

    result = TreasuryYieldCurveProvider._parse_xml(
        xml,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert len(result.history) == 1
    assert result.latest.date == date(2026, 8, 14)
    assert result.latest.spread_10y_2y == pytest.approx(0.50)
    assert result.latest.spread_10y_3m is None


def test_treasury_parse_xml_skips_unparseable_dates_and_rates() -> None:
    good = _entry("2026-08-14", {"BC_10YEAR": "4.20"})
    bad_date = _entry("not-a-date", {"BC_10YEAR": "4.20"})
    empty_date = _entry("", {"BC_10YEAR": "4.20"})
    bad_rate = _entry("2026-08-15", {"BC_10YEAR": "N/A"})
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        ' xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"'
        ' xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        + bad_date
        + empty_date
        + bad_rate
        + good
        + "</feed>"
    )

    result = TreasuryYieldCurveProvider._parse_xml(
        xml,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert len(result.history) == 1
    assert result.history[0].date == date(2026, 8, 14)
    assert [point.yield_percent for point in result.history[0].points] == [4.20]


def test_treasury_parse_xml_rejects_empty_feed() -> None:
    with pytest.raises(LookupError, match="No Treasury par yield curve observations"):
        TreasuryYieldCurveProvider._parse_xml(
            "<feed xmlns='http://www.w3.org/2005/Atom'></feed>",
            retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        )


def test_treasury_curve_validates_year_bounds() -> None:
    provider = TreasuryYieldCurveProvider(_settings())

    with pytest.raises(ValueError, match="between 1990 and the current year"):
        provider.curve(1989)

    with pytest.raises(ValueError, match="between 1990 and the current year"):
        provider.curve(datetime.now(UTC).year + 1)

    assert len(provider._cache) == 0


def test_treasury_curve_fetches_parses_and_caches(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        ' xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"'
        ' xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        + _entry(
            "2026-08-14",
            {
                "BC_1MONTH": "4.10",
                "BC_3MONTH": "4.00",
                "BC_2YEAR": "3.70",
                "BC_10YEAR": "4.20",
                "BC_30YEAR": "4.80",
            },
        )
        + "</feed>"
    )
    httpx_mock.add_response(text=xml)

    provider = TreasuryYieldCurveProvider(_settings())

    first = provider.curve(2026)
    second = provider.curve(2026)

    assert first is second
    assert first.latest.date == date(2026, 8, 14)
    assert first.latest.spread_10y_2y == pytest.approx(0.50)
    assert len(provider._cache) == 1

    (request,) = httpx_mock.get_requests()
    assert request.url.host == "home.treasury.gov"
    assert request.url.path.endswith("/xml")
    assert request.url.params["data"] == "daily_treasury_yield_curve"
    assert request.url.params["field_tdr_date_value"] == "2026"
