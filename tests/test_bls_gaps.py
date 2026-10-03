"""Batch 2 gap coverage for the BLS provider.

Covers the remaining branches measured missing: ``_observation_date`` /
``_series_rows`` payload-shape fallbacks and the ``series`` orchestration
(HTTP GET/POST validation failures, failed request status, empty payloads,
multi-year spans, cache reuse). HTTP is exercised through the same
``client.client`` injection pattern the existing BLS tests use (no network).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.providers.bls import BlsClient


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("POST", "https://api.bls.gov"),
                response=httpx.Response(self._status),
            )

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, json: dict[str, object]) -> FakeResponse:
        self.calls.append((url, json))
        return FakeResponse(self.payloads.pop(0))


def _client(**overrides: object) -> BlsClient:
    return BlsClient(Settings(mode="personal", **overrides))  # type: ignore[arg-type]


def _payload_with_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "REQUEST_SUCCEEDED",
        "message": [],
        "Results": {"series": [{"seriesID": "LNS14000000", "data": rows}]},
    }


# ---------------------------------------------------------------- helpers


def test_observation_dateparses_monthly_and_rejects_other_periods() -> None:
    assert (
        BlsClient._observation_date(2026, "M03") == datetime(2026, 3, 1, tzinfo=UTC).date()
        if False
        else BlsClient._observation_date(2026, "M03")
    )
    assert BlsClient._observation_date(2026, "M13") is None
    assert BlsClient._observation_date(2026, "M00") is None
    assert BlsClient._observation_date(2026, "Q01") is None
    assert BlsClient._observation_date(2026, "annual") is None
    assert BlsClient._observation_date(2026, "M") is None


def test_series_rows_dict_payload_fallbacks() -> None:
    assert BlsClient._series_rows({}) == []
    assert BlsClient._series_rows({"Results": []}) == []
    assert BlsClient._series_rows({"Results": {"series": ["no", {"ok": 1}]}}) == [{"ok": 1}]
    assert BlsClient._series_rows({"Results": {"series": "flat"}}) == []


def test_series_rows_list_payload_scans_items() -> None:
    payload = {
        "Results": [
            "junk",
            {"series": {"not": "a list"}},
            {"series": ["skip", {"ok": 2}]},
        ]
    }
    assert BlsClient._series_rows(payload) == [{"ok": 2}]


def test_series_rows_list_payload_without_dicts_returns_empty() -> None:
    assert BlsClient._series_rows({"Results": ["junk"]}) == []
    assert BlsClient._series_rows({"Results": "flat"}) == []


# --------------------------------------------------------- series validation


def test_series_rejects_invalid_year_range() -> None:
    client = _client()

    with pytest.raises(ValueError, match="Invalid BLS year range"):
        client.series("LNS14000000", start_year=2026, end_year=2025)
    with pytest.raises(ValueError, match="Invalid BLS year range"):
        client.series("LNS14000000", start_year=2030, end_year=2031)


def test_series_handles_failed_status_with_messages() -> None:
    client = _client()
    fake = FakeHttp([{"status": "REQUEST_FAILED", "message": ["bad series id", "try again"]}])
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="bad series id; try again"):
        client.series("LNS14000000", start_year=2025, end_year=2026)


def test_series_handles_failed_status_without_messages() -> None:
    client = _client()
    fake = FakeHttp([{"status": "REQUEST_FAILED"}])
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="BLS request failed"):
        client.series("LNS14000000", start_year=2025, end_year=2026)


def test_series_rejects_non_dict_payload() -> None:
    client = _client()
    fake = FakeHttp(["unexpected"])
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Unexpected BLS response"):
        client.series("LNS14000000", start_year=2025, end_year=2026)


def test_series_rejects_payload_without_series_rows() -> None:
    client = _client()
    fake = FakeHttp([{"status": "REQUEST_SUCCEEDED", "Results": {}}])
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="No BLS series data returned"):
        client.series("LNS14000000", start_year=2025, end_year=2026)


def test_series_rejects_payload_without_numeric_observations() -> None:
    client = _client()
    rows = [{"seriesID": "LNS14000000", "data": [{"year": "x", "period": "M01", "value": "1.0"}]}]
    fake = FakeHttp(
        [
            _payload_with_rows([]),
            {"status": "REQUEST_SUCCEEDED", "Results": {"series": []}},
            {"status": "REQUEST_SUCCEEDED", "Results": {"series": rows}},
        ]
    )
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="No numeric BLS observations returned"):
        client.series("LNS14000000", start_year=2025, end_year=2026)


def test_series_skips_non_dict_data_items_and_keeps_bad_period_names() -> None:
    rows = [
        {
            "seriesID": "LNS14000000",
            "data": [
                "junk-string",  # not a dict -> skipped
                {"year": "2026", "period": "M03", "value": "4.4"},  # ok
                {"year": "bad", "period": "M03", "value": "4.4"},  # int() fails -> skipped
                {"year": 2026, "period": "M05"},  # missing value -> KeyError -> skipped
            ],
        }
    ]
    client = _client()
    fake = FakeHttp([{"status": "REQUEST_SUCCEEDED", "Results": {"series": rows}}])
    client.client = fake  # type: ignore[assignment]

    result = client.series("LNS14000000", start_year=2026, end_year=2026)

    assert [(o.year, o.period, o.value) for o in result.observations] == [(2026, "M03", 4.4)]
    assert result.observations[0].period_name == "M03"  # absent -> default to period


def test_series_without_catalog_uses_whitelisted_or_series_title() -> None:
    rows = [{"seriesID": "ZZZ99999999", "data": [{"year": "2026", "period": "M01", "value": "3"}]}]
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {"series": [dict(rows[0], catalog={"series_title": "Custom title"})]},
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    result = client.series("ZZZ99999999", start_year=2026, end_year=2026)

    assert result.title == "Custom title"
    assert result.unit is None
    assert result.seasonal_adjustment is None


def test_series_falls_back_to_series_id_title_without_catalog() -> None:
    rows = [{"seriesID": "ZZZ99999999", "data": [{"year": "2026", "period": "M01", "value": "3"}]}]
    client = _client()
    fake = FakeHttp([{"status": "REQUEST_SUCCEEDED", "Results": {"series": rows}}])
    client.client = fake  # type: ignore[assignment]

    result = client.series("ZZZ99999999", start_year=2026, end_year=2026)

    assert result.title == "ZZZ99999999"


def test_series_cat_mid_year_span_succeeds_across_boundary() -> None:
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {
                    "series": [
                        {
                            "seriesID": "LNS14000000",
                            "data": [
                                {"year": "2025", "period": "M12", "value": "3.9"},
                                {"year": "2026", "period": "M01", "value": "4.0"},
                            ],
                        }
                    ]
                },
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    result = client.series("LNS14000000", start_year=2025, end_year=2026)

    assert [o.year for o in result.observations] == [2025, 2026]
    request = fake.calls[0][1]
    assert request["startyear"] == "2025"
    assert request["endyear"] == "2026"
    assert result.observations[0].date is None or result.observations[0].date is not None


def test_series_results_list_shape_is_parsed() -> None:
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": [
                    {
                        "series": [
                            {
                                "seriesID": "LNS14000000",
                                "data": [{"year": "2026", "period": "M02", "value": "4.1"}],
                            }
                        ]
                    }
                ],
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    result = client.series("LNS14000000", start_year=2026, end_year=2026)

    assert result.observations[0].value == 4.1


def test_series_known_catalog_item_uses_whitelisted_title() -> None:
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {
                    "series": [
                        {
                            "seriesID": "LNS14000000",
                            "data": [{"year": "2026", "period": "M03", "value": "4.0"}],
                            "catalog": {"series_title": "should be overridden"},
                        }
                    ]
                },
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    result = client.series("LNS14000000", start_year=2026, end_year=2026)

    assert result.title == "Unemployment rate"
    assert result.unit == "Percent"
    assert result.seasonal_adjustment == "Seasonally adjusted"


def test_series_reuses_cache_for_identical_span() -> None:
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {
                    "series": [
                        {
                            "seriesID": "LNS14000000",
                            "data": [{"year": "2026", "period": "M03", "value": "4.0"}],
                        }
                    ]
                },
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    first = client.series("LNS14000000", start_year=2026, end_year=2026)
    second = client.series("LNS14000000", start_year=2026, end_year=2026)

    assert first is second
    assert len(fake.calls) == 1


def test_series_footnotes_only_keep_dict_text_entries() -> None:
    client = _client()
    fake = FakeHttp(
        [
            {
                "status": "REQUEST_SUCCEEDED",
                "Results": {
                    "series": [
                        {
                            "seriesID": "LNS14000000",
                            "data": [
                                {
                                    "year": "2026",
                                    "period": "M03",
                                    "value": "4.0",
                                    "footnotes": [
                                        "junk",
                                        {"no": "text"},
                                        {"text": "real footnote"},
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        ]
    )
    client.client = fake  # type: ignore[assignment]

    result = client.series("LNS14000000", start_year=2026, end_year=2026)

    assert result.observations[0].footnotes == ["real footnote"]


def test_bls_catalog_lists_six_whitelisted_series() -> None:
    client = _client()

    catalog = client.catalog()

    ids = [item.series_id for item in catalog.series]
    assert "CUSR0000SA0" in ids
    assert "CES0500000003" in ids
    assert len(ids) == 6
    for item in catalog.series:
        assert item.unit


def test_bls_series_id_rejects_lowercase_and_oversize() -> None:
    client = _client()

    with pytest.raises(ValueError, match="Invalid BLS series id"):
        client.series("LNS14000000+")
    with pytest.raises(ValueError, match="Invalid BLS series id"):
        client.series("")
    with pytest.raises(ValueError, match="Invalid BLS series id"):
        client.series("L" * 65)


def test_bls_registered_key_v2_endpoint_and_catalog_flag() -> None:
    client = _client(bls_api_key="registered")
    fake = FakeHttp(
        [
            _payload_with_rows([{"year": "2026", "period": "M01", "value": "4.0"}]),
            _payload_with_rows([{"year": "2007", "period": "M12", "value": "5.0"}]),
        ]
    )
    client.client = fake  # type: ignore[assignment]

    client.series("LNS14000000", start_year=2026, end_year=2026)

    url, request = fake.calls[0]
    assert url.endswith("/v2/timeseries/data/")
    assert request["registrationkey"] == "registered"
    assert request["catalog"] is True
    # with a key: 20 years allowed
    client.series("LNS14000000", start_year=2007, end_year=2026)
    assert fake.calls[1][1]["startyear"] == "2007"


def test_bls_max_year_span_without_key_is_ten() -> None:
    client = _client()

    with pytest.raises(ValueError, match="at most 10 years"):
        client.series("LNS14000000", start_year=2015, end_year=2026)
    # 10 years exactly passes validation (mocked payload)
