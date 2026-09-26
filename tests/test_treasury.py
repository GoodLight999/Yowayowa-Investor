from datetime import UTC, date, datetime

import pytest

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
