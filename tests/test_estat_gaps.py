"""Batch 2 gap coverage for the e-Stat provider.

Covers the remaining helpers/branches measured missing: ``_as_list`` /
``_text`` / ``_attr`` / ``_integer`` / ``_date`` / ``_updated`` / ``_number``
lossy-value branches, the ``_get`` HTTP + JSON-shape failures, and the
boundary branches of ``search_tables`` / ``metadata`` / ``data`` (empty
payloads, filter validation, cache collision avoidance).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.providers.estat import EstatClient


def _client() -> EstatClient:
    return EstatClient(Settings(mode="personal", estat_app_id="registered-app"))


def _payload(root: dict[str, Any]) -> dict[str, Any]:
    return {**root}


# ----------------------------------------------------------------- helpers


class TestValueHelpers:
    def test_as_list_filters_non_dict_items(self) -> None:
        assert EstatClient._as_list(None) == []
        assert EstatClient._as_list("scalar") == []
        assert EstatClient._as_list([1, {"a": 1}, "x", {"b": 2}]) == [{"a": 1}, {"b": 2}]
        assert EstatClient._as_list({"single": "dict"}) == [{"single": "dict"}]

    def test_text_dict_and_scalar_shapes(self) -> None:
        assert EstatClient._text({"$": " value "}) == "value"
        assert EstatClient._text({}) is None
        assert EstatClient._text(None) is None
        assert EstatClient._text("") is None
        assert EstatClient._text("  ") is None
        assert EstatClient._text(7) == "7"

    def test_attr_missing_node_or_attribute(self) -> None:
        assert EstatClient._attr(None, "code") is None
        assert EstatClient._attr("not-a-dict", "code") is None
        assert EstatClient._attr({}, "code") is None
        assert EstatClient._attr({"@code": None}, "code") is None
        assert EstatClient._attr({"@code": "  "}, "code") is None
        assert EstatClient._attr({"@code": "99"}, "code") == "99"

    def test_integer_edge_values(self) -> None:
        assert EstatClient._integer(None) is None
        assert EstatClient._integer("") is None
        assert EstatClient._integer("12") == 12
        assert EstatClient._integer(3.0) is None  # str(3.0)="3.0" is not an int literal
        assert EstatClient._integer("abc") is None
        assert EstatClient._integer([]) is None

    def test_date_edge_values(self) -> None:
        assert EstatClient._date(None) is None
        assert EstatClient._date("") is None
        assert EstatClient._date("2026-08-18") == date(2026, 8, 18)
        assert EstatClient._date("2026-08-18T10:00:00+09:00") == date(2026, 8, 18)
        assert EstatClient._date("garbage") is None

    def test_updated_datetime_date_and_invalid(self) -> None:
        assert EstatClient._updated(None) is None
        assert EstatClient._updated("") is None
        assert EstatClient._updated("2026-08-18T12:00:00Z") == datetime(
            2026, 8, 18, 12, 0, tzinfo=UTC
        )
        assert EstatClient._updated("2026-08-18") == datetime(2026, 8, 18)
        assert EstatClient._updated("not a date") is None

    def test_number_padding_and_sentinels(self) -> None:
        assert EstatClient._number("1,234.5") == Decimal("1234.5")
        assert EstatClient._number("") is None
        assert EstatClient._number("-") is None
        assert EstatClient._number("...") is None
        assert EstatClient._number("…") is None
        assert EstatClient._number("X") is None
        assert EstatClient._number("x") is None
        assert EstatClient._number("NA") is None
        assert EstatClient._number("N/A") is None
        assert EstatClient._number("abc") is None
        assert EstatClient._number("-5.2") == Decimal("-5.2")


# ------------------------------------------------------------------- _get


class FakeEstatResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code

    def raise_for_status(self) -> None:
        if self._status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://api.e-stat.go.jp"),
                response=httpx.Response(self._status_code),
            )

    def json(self) -> Any:
        return self._payload


class FakeEstatHttp:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, params: dict[str, object]) -> FakeEstatResponse:
        self.calls.append((url, params))
        return FakeEstatResponse(self.payload)


def test_get_rejects_non_dict_payload() -> None:
    client = _client()
    fake = FakeEstatHttp(["not", "a", "dict"])
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Unexpected e-Stat response"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_get_rejects_missing_or_flat_root() -> None:
    client = _client()
    client.client = FakeEstatHttp({"OTHER_ROOT": {}})  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Unexpected e-Stat response root"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_get_rejects_missing_result_metadata() -> None:
    client = _client()
    client.client = FakeEstatHttp({"GET_STATS_LIST": {}})  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Missing e-Stat result metadata"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_get_rejects_non_integer_status() -> None:
    client = _client()
    client.client = FakeEstatHttp(  # type: ignore[assignment]
        {"GET_STATS_LIST": {"RESULT": {"STATUS": "n/a"}}}
    )

    with pytest.raises(LookupError, match="Invalid e-Stat result status"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_get_propagates_http_errors() -> None:
    client = _client()
    fake = FakeEstatHttp({})
    client.client = fake  # type: ignore[assignment]

    import unittest.mock as mock

    with (
        mock.patch.object(  # type: ignore[union-attr]
            client.client, "get", side_effect=httpx.ConnectError("down")
        ),
        pytest.raises(httpx.ConnectError),
    ):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_get_injects_app_id_into_request_params() -> None:
    client = _client()
    fake = FakeEstatHttp({"GET_STATS_LIST": {"RESULT": {"STATUS": 0}}})
    client.client = fake  # type: ignore[assignment]

    root = client._get("getStatsList", "GET_STATS_LIST", {"lang": "J"})

    assert root == {"RESULT": {"STATUS": 0}}
    url, params = fake.calls[0]
    assert url == "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsList"
    assert params["appId"] == "registered-app"
    assert params["lang"] == "J"


# ------------------------------------------------------- search_tables ordering


def test_search_tables_empty_datalist_returns_blank_result() -> None:
    client = _client()
    fake = FakeEstatHttp({"GET_STATS_LIST": {"RESULT": {"STATUS": 0}}})
    client.client = fake  # type: ignore[assignment]

    result = client.search_tables("GDP")

    assert result.tables == []
    assert result.matched_count == 0
    assert result.next_key is None
    assert result.query == "GDP"


def test_search_tables_normalizes_query_and_lang() -> None:
    client = _client()
    fake = FakeEstatHttp({"GET_STATS_LIST": {"RESULT": {"STATUS": 0}}})
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(ValueError, match="query is required"):
        client.search_tables("   ")
    with pytest.raises(ValueError, match="lang must be J or E"):
        client.search_tables("GDP", lang="fr")

    client.search_tables(" GDP ")
    params = fake.calls[0][1]
    assert params["searchWord"] == "GDP"
    assert params["lang"] == "J"
    assert params["searchKind"] == 1
    assert params["limit"] == 50


def test_search_tables_result_inf_and_number_fallbacks() -> None:
    client = _client()
    payload = {
        "GET_STATS_LIST": {
            "RESULT": {"STATUS": 0},
            "DATALIST_INF": {
                "NUMBER": None,  # falls back to len(tables)
                "RESULT_INF": "not-a-dict",  # -> next_key None
                "TABLE_INF": {"@id": "1", "STAT_NAME": {"$": "n"}, "TITLE": {"$": "t"}},
            },
        }
    }
    fake = FakeEstatHttp(payload)
    client.client = fake  # type: ignore[assignment]

    result = client.search_tables("GDP", limit=7)

    assert result.matched_count == 1
    assert result.next_key is None
    assert result.tables[0].stats_data_id == "1"
    assert fake.calls[0][1]["limit"] == 7


def test_search_tables_next_key_from_result_inf() -> None:
    client = _client()
    payload = {
        "GET_STATS_LIST": {
            "RESULT": {"STATUS": 0},
            "DATALIST_INF": {
                "NUMBER": 10,
                "RESULT_INF": {"NEXT_KEY": "51"},
                "TABLE_INF": {"@id": "1", "STAT_NAME": {"$": "n"}, "TITLE": {"$": "t"}},
            },
        }
    }
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    result = client.search_tables("GDP")

    assert result.matched_count == 10
    assert result.next_key == 51


# --------------------------------------------------------------- metadata


def test_metadata_requires_id_and_lang_bounds() -> None:
    client = _client()

    with pytest.raises(ValueError, match="Invalid e-Stat statistics table ID"):
        client.metadata("   ")
    with pytest.raises(ValueError, match="Invalid e-Stat statistics table ID"):
        client.metadata("x" * 65)
    with pytest.raises(ValueError, match="lang must be J or E"):
        client.metadata("1", lang="z")


def test_metadata_missing_metadata_inf_raises() -> None:
    client = _client()
    payload = {"GET_META_INFO": {"RESULT": {"STATUS": 0}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    with pytest.raises(LookupError, match="No e-Stat metadata returned"):
        client.metadata("0001")


def test_metadata_without_table_keeps_none_summary() -> None:
    client = _client()
    payload = {"GET_META_INFO": {"RESULT": {"STATUS": 0}, "METADATA_INF": {}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    result = client.metadata("0001")

    assert result.table is None
    assert result.dimensions == []
    assert result.stats_data_id == "0001"


# -------------------------------------------------------------------- data


def test_data_validates_limit_start_position_and_lang() -> None:
    client = _client()

    with pytest.raises(ValueError, match="Invalid e-Stat statistics table ID"):
        client.data("  ")
    with pytest.raises(ValueError, match="lang must be J or E"):
        client.data("1", lang="v")
    with pytest.raises(ValueError, match="limit must be between 1 and 10000"):
        client.data("1", limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 10000"):
        client.data("1", limit=10001)
    with pytest.raises(ValueError, match="start_position must be positive"):
        client.data("1", start_position=0)


def test_data_validates_filter_values() -> None:
    client = _client()

    with pytest.raises(ValueError, match="Invalid e-Stat filter value"):
        client.data("1", filters={"cd_area": "   "})
    with pytest.raises(ValueError, match="Invalid e-Stat filter value"):
        client.data("1", filters={"cd_area": "x" * 513})


def test_data_missing_statistical_data_raises() -> None:
    client = _client()
    payload = {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    with pytest.raises(LookupError, match="No e-Stat data returned"):
        client.data("0001")


def test_data_missing_result_inf_raises() -> None:
    client = _client()
    payload = {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Missing e-Stat data result metadata"):
        client.data("0001")


def test_data_without_data_inf_defaults_notes_and_annotations() -> None:
    client = _client()
    payload = {
        "GET_STATS_DATA": {
            "RESULT": {"STATUS": 0},
            "STATISTICAL_DATA": {"RESULT_INF": {"TOTAL_NUMBER": 1}},
        }
    }
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    result = client.data("0001")

    assert result.notes == {}
    assert result.annotations == {}
    assert result.values == []
    # total_number = _integer(TOTAL_NUMBER) or len(values) -> 1
    assert result.total_number == 1


def test_data_start_position_and_mapped_filters_reach_request() -> None:
    client = _client()
    payload = {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {"RESULT_INF": {}}}}
    fake = FakeEstatHttp(payload)
    client.client = fake  # type: ignore[assignment]

    client.data(
        "0001",
        start_position=2,
        limit=10,
        filters={"cd_cat01": "0001", "cd_area": "00000"},
    )

    params = fake.calls[0][1]
    assert params["startPosition"] == 2
    assert params["cdCat01"] == "0001"
    assert params["cdArea"] == "00000"
    assert params["metaGetFlg"] == "Y"
    assert params["annotationGetFlg"] == "Y"
    assert params["replaceSpChar"] == 0


def test_data_cache_key_distinguishes_filters_and_paging() -> None:
    client = _client()
    payload = {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {"RESULT_INF": {}}}}
    fake = FakeEstatHttp(payload)
    client.client = fake  # type: ignore[assignment]

    first = client.data("0001", filters={"cd_area": "00000"})
    second = client.data("0001", filters={"cd_area": "01001"})

    assert first is not second
    assert len(fake.calls) == 2

    repeat = client.data("0001", filters={"cd_area": "00000"})
    assert repeat is first
    assert len(fake.calls) == 2


def test_data_value_rows_skip_null_text_and_keep_dimensions() -> None:
    client = _client()
    payload = {
        "GET_STATS_DATA": {
            "RESULT": {"STATUS": 0},
            "STATISTICAL_DATA": {
                "RESULT_INF": {},
                "DATA_INF": {
                    "VALUE": [
                        {"@area": "00000", "@unit": "x", "$": "12.5"},
                        {"@area": "01001"},  # no "$" -> skipped
                    ]
                },
            },
        }
    }
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    result = client.data("0001")

    assert len(result.values) == 1
    assert result.values[0].numeric_value == Decimal("12.5")
    assert result.values[0].unit == "x"
    assert result.values[0].dimensions == {"area": "00000"}
    assert result.total_number == 1


class TestRemainingEstatBranches:
    def test_app_id_missing_fails_closed(self) -> None:
        missing = EstatClient(Settings(mode="personal"))
        with pytest.raises(RuntimeError, match=r"YOWAYOWA_EST_AT_APP_ID|YOWAYOWA_ESTAT"):
            missing._app_id()


def test_estat_get_elevated_status_reports_error_message() -> None:
    client = _client()
    payload = {"GET_STATS_LIST": {"RESULT": {"STATUS": 101, "ERROR_MSG": {"$": "bad request"}}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    with pytest.raises(LookupError, match="e-Stat API error 101: bad request"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_estat_get_elevated_status_defaults_message_when_blank() -> None:
    client = _client()
    payload = {"GET_STATS_LIST": {"RESULT": {"STATUS": 150, "ERROR_MSG": "  "}}}
    client.client = FakeEstatHttp(payload)  # type: ignore[assignment]

    with pytest.raises(LookupError, match="e-Stat API error 150: e-Stat API error"):
        client._get("getStatsList", "GET_STATS_LIST", {})


def test_estat_search_tables_cache_hit_skips_http() -> None:
    client = _client()
    payload = {"GET_STATS_LIST": {"RESULT": {"STATUS": 0}}}
    fake = FakeEstatHttp(payload)
    client.client = fake  # type: ignore[assignment]

    first = client.search_tables("GDP")
    second = client.search_tables("GDP")

    assert first is second
    assert len(fake.calls) == 1


def test_estat_metadata_cache_hit_skips_http() -> None:
    client = _client()
    payload = {"GET_META_INFO": {"RESULT": {"STATUS": 0}, "METADATA_INF": {}}}
    fake = FakeEstatHttp(payload)
    client.client = fake  # type: ignore[assignment]

    first = client.metadata("0001")
    second = client.metadata("0001")

    assert first is second
    assert len(fake.calls) == 1


def test_estat_dimensions_skip_partial_or_nameless_entries() -> None:
    class_inf = {
        "CLASS_OBJ": [
            {"@id": ""},  # no id -> skipped
            {"@id": "tab"},  # no @name -> skipped
            {
                "@id": "cat01",
                "@name": "cat",
                "CLASS": [
                    {"@code": ""},  # no code -> skipped
                    {"@code": "c1"},  # no @name -> skipped
                    {"@code": "c2", "@name": "n2", "@level": "1", "@parentCode": "p", "@unit": "u"},
                ],
            },
        ]
    }

    dims = EstatClient._dimensions(class_inf)

    assert len(dims) == 1
    assert dims[0].id == "cat01"
    assert len(dims[0].items) == 1
    assert dims[0].items[0].code == "c2"
    assert dims[0].items[0].parent_code == "p"
    assert dims[0].items[0].unit == "u"


def test_estat_dimensions_non_dict_class_inf_returns_empty() -> None:
    assert EstatClient._dimensions(None) == []
    assert EstatClient._dimensions("junk") == []


def test_estat_table_summary_requires_all_core_fields() -> None:
    """A raw entry without id/title/stat_name yields None (filtered out)."""

    assert EstatClient._table_summary({}) is None  # type: ignore[arg-type]
    assert (
        EstatClient._table_summary({"@id": "1", "STAT_NAME": {"$": "n"}}) is None  # type: ignore[arg-type]
    )


def test_estat_search_tables_limit_bounds() -> None:
    client = _client()

    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        client.search_tables("GDP", limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        client.search_tables("GDP", limit=101)


def test_estat_invalid_filter_key_raises() -> None:
    """Un-allowed filter keys are rejected before the request is built."""
    client = _client()

    with pytest.raises(ValueError, match="Unsupported e-Stat filter: arbitrary"):
        client.data("0001", filters={"arbitrary": "x"})
