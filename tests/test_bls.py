from __future__ import annotations

from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.bls import BlsClient


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

    def post(self, url: str, json: dict[str, object]) -> FakeResponse:
        self.calls.append((url, json))
        return FakeResponse(self.payload)


def fixture_payload() -> dict[str, Any]:
    return {
        "status": "REQUEST_SUCCEEDED",
        "message": [],
        "Results": {
            "series": [
                {
                    "seriesID": "LNS14000000",
                    "data": [
                        {
                            "year": "2026",
                            "period": "M02",
                            "periodName": "February",
                            "value": "4.2",
                            "footnotes": [{"text": "fixture note"}],
                        },
                        {
                            "year": "2026",
                            "period": "M01",
                            "periodName": "January",
                            "value": "4.0",
                            "footnotes": [{}],
                        },
                    ],
                }
            ]
        },
    }


def test_bls_catalog_and_series_have_public_domain_provenance() -> None:
    client = BlsClient(Settings(mode="personal"))
    fake = FakeHttpClient(fixture_payload())
    client.client = fake  # type: ignore[assignment]

    catalog = client.catalog()
    assert "CUSR0000SA0" in {item.series_id for item in catalog.series}
    assert catalog.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC

    result = client.series("LNS14000000", start_year=2026, end_year=2026)
    assert [item.date.isoformat() for item in result.observations if item.date] == [
        "2026-01-01",
        "2026-02-01",
    ]
    assert result.observations[-1].value == 4.2
    assert result.observations[-1].footnotes == ["fixture note"]
    assert result.provenance.provider == "bls"
    assert result.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC
    assert fake.calls[0][0].endswith("/v1/timeseries/data/")
    assert "registrationkey" not in fake.calls[0][1]


def test_bls_registered_key_uses_v2_and_allows_twenty_year_span() -> None:
    client = BlsClient(Settings(mode="personal", bls_api_key="free-registration-key"))
    fake = FakeHttpClient(fixture_payload())
    client.client = fake  # type: ignore[assignment]

    client.series("LNS14000000", start_year=2007, end_year=2026)

    url, request = fake.calls[0]
    assert url.endswith("/v2/timeseries/data/")
    assert request["registrationkey"] == "free-registration-key"
    assert request["catalog"] is True


def test_bls_validates_series_id_and_access_level_year_limits() -> None:
    client = BlsClient(Settings(mode="personal"))

    with pytest.raises(ValueError, match="Invalid BLS series id"):
        client.series("bad series id")
    with pytest.raises(ValueError, match="at most 10 years"):
        client.series("LNS14000000", start_year=2016, end_year=2026)
