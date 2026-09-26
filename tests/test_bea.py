from __future__ import annotations

from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.bea import BeaClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttpClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, params: dict[str, object]) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload)


def fixture_payload() -> dict[str, Any]:
    return {
        "BEAAPI": {
            "Results": {
                "Data": [
                    {
                        "TableName": "T10101",
                        "SeriesCode": "A191RL",
                        "LineNumber": "1",
                        "LineDescription": "Gross domestic product",
                        "TimePeriod": "2025Q4",
                        "METRIC_NAME": "PercentChange",
                        "CL_UNIT": "Percent change",
                        "UNIT_MULT": "0",
                        "DataValue": "1,234.5",
                        "NoteRef": "T10101",
                    },
                    {
                        "TableName": "T10101",
                        "SeriesCode": "A006RL",
                        "LineNumber": "2",
                        "LineDescription": "Personal consumption expenditures",
                        "TimePeriod": "2025Q4",
                        "METRIC_NAME": "PercentChange",
                        "CL_UNIT": "Percent change",
                        "UNIT_MULT": "0",
                        "DataValue": "2.1",
                    },
                ],
                "Notes": [{"NoteText": "fixture note"}],
            }
        }
    }


def test_bea_catalog_and_nipa_use_official_public_provenance_and_cache() -> None:
    client = BeaClient(Settings(mode="personal", bea_api_key="registered-key"))
    fake = FakeHttpClient(fixture_payload())
    client.client = fake  # type: ignore[assignment]

    catalog = client.catalog()
    assert {item.table_name for item in catalog.tables} >= {"T10101", "T20305"}
    assert catalog.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC

    result = client.nipa("t10101", frequency="q", years=[2025], line_number=1)
    assert result.table_name == "T10101"
    assert result.frequency == "Q"
    assert result.years == ["2025"]
    assert len(result.rows) == 1
    assert result.rows[0].line_number == 1
    assert result.rows[0].value == 1234.5
    assert result.notes == ["fixture note"]
    assert result.provenance.provider == "bea"
    assert result.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC

    url, params = fake.calls[0]
    assert url == "https://apps.bea.gov/api/data/"
    assert params["UserID"] == "registered-key"
    assert params["method"] == "GetData"
    assert params["DataSetName"] == "NIPA"
    assert params["TableName"] == "T10101"
    assert params["Frequency"] == "Q"
    assert params["Year"] == "2025"
    assert params["ResultFormat"] == "JSON"

    cached = client.nipa("T10101", frequency="Q", years=[2025], line_number=1)
    assert cached == result
    assert len(fake.calls) == 1


def test_bea_requires_registered_key_only_when_data_is_requested() -> None:
    client = BeaClient(Settings(mode="personal"))

    assert client.catalog().tables
    with pytest.raises(RuntimeError, match="YOWAYOWA_BEA_API_KEY"):
        client.nipa("T10101", years=[2025])


def test_bea_validates_table_frequency_years_and_line_number() -> None:
    client = BeaClient(Settings(mode="personal", bea_api_key="registered-key"))

    with pytest.raises(ValueError, match="table name"):
        client.nipa("1.1.1")
    with pytest.raises(ValueError, match="frequency"):
        client.nipa("T10101", frequency="W")
    with pytest.raises(ValueError, match="line_number"):
        client.nipa("T10101", line_number=0)
    with pytest.raises(ValueError, match="at most 50"):
        client.nipa("T10101", years=list(range(1950, 2001)))
