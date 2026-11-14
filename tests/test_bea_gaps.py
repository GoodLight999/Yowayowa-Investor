"""Batch 2 gap coverage for the BEA provider.

Covers the remaining branches measured missing: ``_normalize_years`` /
``_number`` / ``_integer`` exception paths, ``_results`` malformed-response
branches, and the ``nipa`` option branches (custom years, line filtering,
cache hits, notes parsing). HTTP goes through the injected fake client used
by the existing BEA tests (no network).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.providers.bea import BeaClient


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "http error",
                request=httpx.Request("GET", "https://apps.bea.gov"),
                response=httpx.Response(self._status),
            )

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, params: dict[str, object]) -> FakeResponse:
        self.calls.append((url, params))
        return FakeResponse(self.payload)


def _client(**overrides: object) -> BeaClient:
    return BeaClient(Settings(mode="personal", bea_api_key="registered", **overrides))  # type: ignore[arg-type]


def _row(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
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
    }
    values.update(overrides)
    return values


def _payload(rows: list[dict[str, Any]], notes: Any = None) -> dict[str, Any]:
    results: dict[str, Any] = {"Data": rows}
    if notes is not None:
        results["Notes"] = notes
    return {"BEAAPI": {"Results": results}}


# ------------------------------------------------------------------ helpers


def test_normalize_years_defaults_to_recent_ten() -> None:
    resolved, joined = BeaClient._normalize_years(None)

    current = datetime.now(UTC).year
    assert resolved == [str(year) for year in range(max(1947, current - 9), current + 1)]
    assert joined == ",".join(resolved)


def test_normalize_years_dedups_sorts_and_validates() -> None:
    resolved, joined = BeaClient._normalize_years([2025, 2023, 2023])

    assert resolved == ["2023", "2025"]
    assert joined == "2023,2025"


def test_normalize_years_rejects_way_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="at most 50 values between 1929"):
        BeaClient._normalize_years([1928])
    with pytest.raises(ValueError, match="at most 50 values between 1929"):
        BeaClient._normalize_years([datetime.now(UTC).year + 1])
    with pytest.raises(ValueError, match="at most 50 values between 1929"):
        BeaClient._normalize_years(list(range(1929, 2000)))


def test_number_value_shapes() -> None:
    assert BeaClient._number(None) is None
    assert BeaClient._number("") is None
    assert BeaClient._number("   ") is None
    assert BeaClient._number("---") is None
    assert BeaClient._number("(NA)") is None
    assert BeaClient._number("NA") is None
    assert BeaClient._number("1,234.5") == 1234.5
    assert BeaClient._number("-2.5") == -2.5
    assert BeaClient._number("junk") is None


def test_integer_value_shapes() -> None:
    assert BeaClient._integer(None) is None
    assert BeaClient._integer("3") == 3
    assert BeaClient._integer("x") is None
    assert BeaClient._integer([]) is None


# ---------------------------------------------------------------- _results


def test_results_rejects_missing_or_invalid_root() -> None:
    with pytest.raises(LookupError, match="Unexpected BEA response"):
        BeaClient._results({})
    with pytest.raises(LookupError, match="Unexpected BEA response"):
        BeaClient._results({"BEAAPI": "flat"})


def test_results_rejects_missing_results_block() -> None:
    with pytest.raises(LookupError, match="Unexpected BEA results"):
        BeaClient._results({"BEAAPI": {}})


def test_results_error_dict_prefers_description_then_code() -> None:
    with pytest.raises(LookupError, match="BEA API error: desc"):
        BeaClient._results({"BEAAPI": {"Results": {"Error": {"APIErrorDescription": "desc"}}}})
    with pytest.raises(LookupError, match="BEA API error: 42"):
        BeaClient._results({"BEAAPI": {"Results": {"Error": {"APIErrorCode": 42}}}})
    # An empty dict Error falls out of `if error:` (falsy) and is returned as-is.
    assert BeaClient._results({"BEAAPI": {"Results": {"Error": {}}}}) == {"Error": {}}


def test_results_error_scalar_is_reported_directly() -> None:
    with pytest.raises(LookupError, match="BEA API error: boom"):
        BeaClient._results({"BEAAPI": {"Results": {"Error": "boom"}}})


# --------------------------------------------------------------------- nipa


def test_nipa_rejects_invalid_frequency_and_line_number() -> None:
    client = _client()

    with pytest.raises(ValueError, match="frequency must be A, Q, or M"):
        client.nipa("T10101", frequency="w")
    with pytest.raises(ValueError, match="line_number must be positive"):
        client.nipa("T10101", line_number=0)


def test_nipa_rejects_non_dict_payload() -> None:
    client = _client()

    class FakeJson:
        def raise_for_status(self) -> None: ...

        def json(self) -> Any:
            return "definitely not a dict"

    class FakeJsonClient:
        def get(self, url: str, params: dict[str, object]) -> FakeJson:
            return FakeJson()

    client.client = FakeJsonClient()  # type: ignore[assignment]

    with pytest.raises(LookupError, match="Unexpected BEA response"):
        client.nipa("T10101", years=[2025])


def test_nipa_missing_rows_raises() -> None:
    client = _client()
    fake = FakeHttp(_payload([]))
    client.client = fake  # type: ignore[assignment]

    with pytest.raises(LookupError, match="No BEA NIPA data returned"):
        client.nipa("T10101", years=[2025])


def test_nipa_row_filtering_and_partial_fields() -> None:
    client = _client()
    fake = FakeHttp(
        _payload(
            [
                _row(LineNumber="1"),  # matches line filter
                _row(LineNumber="2"),  # filtered by line_number
                _row(LineNumber="1", LineDescription=""),  # empty description -> skipped
                _row(LineNumber="1", TimePeriod="  "),  # blank period -> skipped
                "junk",  # not a dict -> skipped (type: ignore for mixed list)
                _row(LineNumber="1", SeriesCode="", METRIC_NAME="", CL_UNIT=""),
            ]  # type: ignore[list-item]
        )
    )
    client.client = fake  # type: ignore[assignment]

    result = client.nipa("T10101", years=[2025], line_number=1)

    assert len(result.rows) == 2
    rich, bare = result.rows  # plain row first, stripped row second
    assert rich.series_code == "A191RL"
    assert rich.note_ref == "T10101"
    assert bare.series_code is None
    assert bare.metric_name is None
    assert bare.unit is None
    assert bare.note_ref == "T10101"  # NoteRef survives the field clearing


def test_nipa_notes_only_keep_dict_text_entries() -> None:
    client = _client()
    fake = FakeHttp(
        _payload(
            [_row()],
            notes=["junk", {"no": "text"}, {"NoteText": "real note"}],
        )
    )
    client.client = fake  # type: ignore[assignment]

    result = client.nipa("T10101", years=[2025])

    assert result.notes == ["real note"]


def test_nipa_notes_default_empty_when_absent() -> None:
    client = _client()
    fake = FakeHttp(_payload([_row()]))
    client.client = fake  # type: ignore[assignment]

    result = client.nipa("T10101", years=[2025])

    assert result.notes == []


def test_nipa_cache_hits_same_key_once() -> None:
    client = _client()
    fake = FakeHttp(_payload([_row()]))
    client.client = fake  # type: ignore[assignment]

    first = client.nipa("T10101", years=[2025], frequency="Q")
    second = client.nipa("T10101", years=[2025], frequency="Q")

    assert first is second
    assert len(fake.calls) == 1


def test_nipa_custom_years_shape_the_year_param() -> None:
    client = _client()
    fake = FakeHttp(_payload([_row()]))
    client.client = fake  # type: ignore[assignment]

    client.nipa("T10101", years=[2025, 2023])

    assert fake.calls[0][1]["Year"] == "2023,2025"


def test_bea_catalog_lists_whitelisted_tables() -> None:
    client = _client()

    catalog = client.catalog()

    names = [t.table_name for t in catalog.tables]
    assert "T10101" in names
    assert len(names) == 2  # catalog has exactly two whitelisted tables


def test_bea_requires_api_key() -> None:
    client = BeaClient(Settings(mode="personal"))

    with pytest.raises(RuntimeError, match="BEA requires YOWAYOWA_BEA_API_KEY"):
        client.nipa("T10101")


def test_bea_rejects_invalid_table_name() -> None:
    client = _client()

    with pytest.raises(ValueError, match="table name must look like T10101"):
        client.nipa("T101x")


def test_bea_non_jpy_lookup_via_fake_http() -> None:
    client = _client()
    fake = FakeHttp(
        _payload(
            [
                _row(TimePeriod="2025Q3", DataValue="2.0"),
                _row(TimePeriod="2025Q4", DataValue="bad"),
            ]
        )
    )
    client.client = fake  # type: ignore[assignment]

    result = client.nipa("T10101", years=[2025])

    values = [row.value for row in result.rows]
    assert values[0] == 2.0
    assert values[1] is None  # unparseable DataValue -> None
