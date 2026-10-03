from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.providers.fred import FredClient


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:///:memory:",
        "fred_api_key": "test-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(**overrides: object) -> FredClient:
    return FredClient(_settings(**overrides))


def test_fred_client_binds_settings_and_http_client() -> None:
    client = _client()

    assert client.settings.mode == "personal"
    assert client.client.timeout.connect == client.settings.request_timeout_seconds


def test_fred_key_fails_closed_without_api_key() -> None:
    client = _client(fred_api_key=None)

    with pytest.raises(RuntimeError, match="YOWAYOWA_FRED_API_KEY"):
        client._key()


def test_fred_key_returns_configured_key() -> None:
    assert _client()._key() == "test-key"


def test_fred_get_builds_query_with_type_aware_conversion(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={"seriess": []})
    client = _client()

    payload = client._get(
        "series",
        series_id="GDP",
        limit=10,
        offset=None,
        verbose=True,
        compact=False,
        ratio=1.5,
        units="pc1",
        generated_by=date(2024, 1, 1),
    )

    assert payload == {"seriess": []}
    request = httpx_mock.get_requests()[0]
    params = request.url.params
    assert request.url.path.endswith("/fred/series")
    assert request.url.host.endswith("stlouisfed.org")
    assert params["api_key"] == "test-key"
    assert params["file_type"] == "json"
    assert params["series_id"] == "GDP"
    assert params["limit"] == "10"
    assert params["verbose"] == "true"
    assert params["compact"] == "false"
    assert params["ratio"] == "1.5"
    assert params["units"] == "pc1"
    assert params["generated_by"] == "2024-01-01"
    assert "offset" not in params


def test_fred_get_falls_back_to_empty_dict_on_non_dict_payload(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json=["unexpected"])

    assert _client()._get("series", series_id="GDP") == {}


def test_fred_get_raises_on_http_error(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        _client()._get("series", series_id="GDP")


def test_fred_search_formats_payload_and_provenance(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        json={"seriess": [{"id": "GDP"}], "count": 1, "offset": 0, "limit": 50},
    )

    result = _client().search("gross domestic product", limit=50, offset=0)

    assert result["query"] == "gross domestic product"
    assert result["series"] == [{"id": "GDP"}]
    assert result["count"] == 1
    assert result["offset"] == 0
    assert result["limit"] == 50
    provenance = result["provenance"]
    assert provenance["provider"] == "fred"
    assert provenance["license_class"] == "user_key"
    assert provenance["source_url"].startswith("https://fred.stlouisfed.org")

    request = httpx_mock.get_requests()[0]
    assert request.url.path.endswith("/fred/series/search")
    params = request.url.params
    assert params["search_text"] == "gross domestic product"
    assert params["search_type"] == "full_text"
    assert params["order_by"] == "search_rank"
    assert params["sort_order"] == "asc"


def test_fred_search_echoes_requested_paging_when_payload_omits_it(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={})

    result = _client().search("cpi", limit=5, offset=25)

    assert result["series"] == []
    assert result["count"] is None
    assert result["offset"] == 25
    assert result["limit"] == 5


def test_fred_series_info_returns_first_metadata_row(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={"seriess": [{"id": "GDP", "title": "Gross Domestic Product"}]})

    result = _client().series_info("GDP")

    assert result["series_id"] == "GDP"
    assert result["metadata"] == {"id": "GDP", "title": "Gross Domestic Product"}
    assert result["provenance"]["source_url"] == "https://fred.stlouisfed.org/series/GDP"


def test_fred_series_info_without_rows_yields_none_metadata(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={})

    assert _client().series_info("GDP")["metadata"] is None


def test_fred_series_reflects_observation_options_in_request(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        json={"observations": [{"date": "2025-12-31", "value": "100"}]},
    )
    httpx_mock.add_response(json={"seriess": [{"id": "GDP"}]})
    client = _client()

    result = client.series(
        "GDP",
        limit=100,
        observation_start=date(2024, 1, 1),
        observation_end=date(2025, 12, 31),
        units="pc1",
        frequency="q",
        aggregation_method="avg",
    )

    observations_request, series_request = httpx_mock.get_requests()
    assert observations_request.url.path.endswith("/fred/series/observations")
    obs_params = observations_request.url.params
    assert obs_params["series_id"] == "GDP"
    assert obs_params["limit"] == "100"
    assert obs_params["observation_start"] == "2024-01-01"
    assert obs_params["observation_end"] == "2025-12-31"
    assert obs_params["units"] == "pc1"
    assert obs_params["frequency"] == "q"
    assert obs_params["aggregation_method"] == "avg"

    assert series_request.url.path.endswith("/fred/series")
    assert series_request.url.params["series_id"] == "GDP"
    assert "observation_start" not in series_request.url.params

    assert result["series_id"] == "GDP"
    assert result["metadata"] == {"id": "GDP"}
    assert result["observations"] == [{"date": "2025-12-31", "value": "100"}]
    assert result["provenance"]["source_url"] == "https://fred.stlouisfed.org/series/GDP"


def test_fred_series_without_options_omits_optional_params(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={})
    httpx_mock.add_response(json={"seriess": []})

    result = _client().series("GDP")

    obs_params = httpx_mock.get_requests()[0].url.params
    for key in (
        "observation_start",
        "observation_end",
        "units",
        "frequency",
        "aggregation_method",
    ):
        assert key not in obs_params
    assert result["metadata"] is None
    assert result["observations"] == []


def test_fred_release_dates_formats_payload(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(json={"release_dates": [{"date": "2026-01-29"}], "count": 1})

    result = _client().release_dates(limit=10, offset=5)

    assert result["release_dates"] == [{"date": "2026-01-29"}]
    assert result["count"] == 1
    assert result["offset"] == 5
    assert result["limit"] == 10
    assert result["provenance"]["provider"] == "fred"

    request = httpx_mock.get_requests()[0]
    assert request.url.path.endswith("/fred/releases/dates")
    params = request.url.params
    assert params["limit"] == "10"
    assert params["offset"] == "5"
    assert params["order_by"] == "release_date"
    assert params["sort_order"] == "desc"
    assert params["include_release_dates_with_no_data"] == "false"


def test_fred_provenance_source_url_depends_on_series_id() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)

    with_series = FredClient._provenance(now, "GDP")
    without_series = FredClient._provenance(now)

    assert with_series.source_url == "https://fred.stlouisfed.org/series/GDP"
    assert without_series.source_url == "https://fred.stlouisfed.org/"
    assert without_series.license_class.value == "user_key"
    assert without_series.retrieved_at == now
    assert without_series.notes
