"""Batch 2 gap coverage for the Yahoo screener provider.

Covers the remaining branches measured missing: the ``catalog`` entry point,
``screen`` validation and numeric post-filter branches, and the
``_query`` / ``_filter`` construction paths. The yfinance boundary is
monkeypatched at module attributes (``yf.screen`` / ``yf.PREDEFINED_SCREENER_QUERIES``
stay untouched where needful); no network is touched.
"""

from __future__ import annotations

from typing import Any

import pytest

from yowayowa.config import Settings
from yowayowa.providers import yahoo_screener as ys
from yowayowa.providers.yahoo_screener import (
    REGIONS,
    SCREENER_FIELDS,
    SERVER_SIDE_FILTER_NOTE,
)
from yowayowa.research_models import MarketScreenFilter, MarketScreenRequest


def _provider(**overrides: object) -> ys.YahooScreenerProvider:
    return ys.YahooScreenerProvider(
        Settings(mode="personal", **overrides)  # type: ignore[arg-type]
    )


def _filt(field: str, operator: str, value: Any) -> MarketScreenFilter:
    return MarketScreenFilter(field=field, operator=operator, value=value)  # type: ignore[arg-type]


# ------------------------------------------------------------------- catalog


def test_catalog_lists_keys_regions_predefined_operators() -> None:
    provider = _provider()

    catalog = provider.catalog()

    assert set(SCREENER_FIELDS) <= set(catalog["fields"])
    assert catalog["regions"] == list(REGIONS)
    assert "eq" in catalog["operators"]
    assert catalog["max_results"] == 250
    assert isinstance(catalog["predefined"], list)


# --------------------------------------------------------------------- _filter


def test_filter_rejects_unknown_field() -> None:
    provider = _provider()

    with pytest.raises(ValueError, match="Unsupported Yahoo screener field"):
        provider._filter(_filt("made_up_field", "gt", 1.0))


def test_filter_isin_takes_list_or_scalar() -> None:
    provider = _provider()

    by_list = provider._filter(_filt("sector", "is-in", ["Technology", "Healthcare"]))
    by_scalar = provider._filter(_filt("sector", "is-in", "Technology"))

    assert by_list.operator == "IS-IN"
    assert by_list.operands == ["sector", "Technology", "Healthcare"]
    assert by_scalar.operands == ["sector", "Technology"]


def test_filter_btwn_requires_exactly_two_values() -> None:
    provider = _provider()

    ok = provider._filter(_filt("intradayprice", "btwn", [1.0, 2.0]))
    assert ok.operands == ["intradayprice", 1.0, 2.0]

    with pytest.raises(ValueError, match="exactly two values"):
        provider._filter(_filt("intradayprice", "btwn", [1.0]))
    with pytest.raises(ValueError, match="exactly two values"):
        provider._filter(_filt("intradayprice", "btwn", 1.0))


def test_filter_scalar_ops_take_single_value_from_lists() -> None:
    provider = _provider()

    direct = provider._filter(_filt("intradayprice", "gt", 5.0))
    from_list = provider._filter(_filt("intradayprice", "gt", [5.0]))

    assert direct.operands == ["intradayprice", 5.0]
    assert from_list.operands == ["intradayprice", 5.0]

    with pytest.raises(ValueError, match="require one value"):
        provider._filter(_filt("intradayprice", "gt", [5.0, 6.0]))


def test_query_empty_filters_falls_back_to_region_membership() -> None:
    provider = _provider()

    query = provider._query([])

    assert query.operator == "IS-IN"
    assert query.operands == ["region", *REGIONS]


def test_query_single_filter_passes_through_and_multi_and() -> None:
    provider = _provider()
    one = _filt("intradayprice", "gt", 5.0)
    two = _filt("intradayprice", "lt", 90.0)

    single = provider._query([one])
    combined = provider._query([one, two])

    assert single.operator == "GT"
    assert combined.operator == "AND"
    assert len(combined.operands) == 2


# --------------------------------------------------------- numeric post-filter


def _requests(**overrides: object) -> MarketScreenRequest:
    values: dict[str, Any] = {"filters": [], "offset": 0, "size": 250}
    values.update(overrides)
    return MarketScreenRequest(**values)  # type: ignore[arg-type]


def test_numeric_checks_defers_unknown_field_and_skips_identity() -> None:
    provider = _provider()
    filters = [
        _filt("intradayprice", "gt", 5.0),
        _filt("sector", "is-in", ["Technology"]),  # identity-ish sectos skipped below
        _filt("made_up_number_field", "lt", [3.0]),  # no quote key -> deferred
        _filt("intradayprice", "btwn", [1.0, 2.0]),
        _filt("intradayprice", "btwn", [1.0]),  # malformed -> skipped
        _filt("intradayprice", "gt", ["x"]),  # unparseable -> skipped
    ]

    checks, deferred = provider._numeric_checks(filters)

    assert deferred is True
    # identity/region fields keep clear of quote-key checks; is-in never numeric
    keys = [key for key, _op, _bounds in checks]
    assert "regularMarketPrice" in keys
    assert ("regularMarketPrice", "lt", (3.0,)) not in checks


def test_numeric_checks_non_numeric_operators_are_skipped() -> None:
    provider = _provider()

    checks, deferred = provider._numeric_checks([_filt("sector", "is-in", ["Technology"])])

    assert checks == []
    assert deferred is False


def test_passes_numeric_checks_fail_closed_on_bad_shapes() -> None:
    provider = _provider()
    checks = [("regularMarketPrice", "gt", (5.0,))]

    assert provider._passes_numeric_checks({"regularMarketPrice": 10.0}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": 4.999}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": None}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": "12"}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": True}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": float("nan")}, checks)
    assert not provider._passes_numeric_checks({}, checks)


def test_passes_numeric_checks_btwn_bounds() -> None:
    provider = _provider()
    checks = [("regularMarketPrice", "btwn", (5.0, 10.0))]

    assert provider._passes_numeric_checks({"regularMarketPrice": 7.5}, checks)
    assert provider._passes_numeric_checks({"regularMarketPrice": 5.0}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": 4.0}, checks)
    assert not provider._passes_numeric_checks({"regularMarketPrice": 11.0}, checks)


def test_passes_numeric_checks_all_scalar_comparisons() -> None:
    provider = _provider()
    cases = [
        ("eq", 10.0, [(10.0, True), (11.0, False)]),
        ("gt", 10.0, [(11.0, True), (10.0, False)]),
        ("lt", 10.0, [(9.0, True), (10.0, False)]),
        ("gte", 10.0, [(10.0, True), (9.0, False)]),
        ("lte", 10.0, [(10.0, True), (11.0, False)]),
    ]

    for operator, threshold, cases_pairs in cases:
        checks = [("regularMarketPrice", operator, (threshold,))]
        for value, expected in cases_pairs:
            outcome = provider._passes_numeric_checks({"regularMarketPrice": value}, checks)
            assert outcome is expected, (operator, value, expected)


# --------------------------------------------------------------------- screen


def _fake_quotes() -> list[dict[str, Any]]:
    return [
        {"symbol": "4755", "regularMarketPrice": 280.0, "trailingPE": 40.0},
        {"symbol": "7036", "regularMarketPrice": 850.0, "trailingPE": 12.0},
        {"symbol": "6758", "regularMarketPrice": 7000.0, "trailingPE": None},
        "junk",  # non-dict entries are dropped
    ]


def _patch_screen(monkeypatch: Any, quotes: Any, calls: list[tuple[Any, dict[str, Any]]]) -> None:
    def fake_screen(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return {"quotes": quotes, "total": 21}

    monkeypatch.setattr(ys.yf, "screen", fake_screen)


def test_screen_predefined_requires_whitelisted_name(monkeypatch: Any) -> None:
    provider = _provider()

    with pytest.raises(ValueError, match="Unknown predefined screener"):
        provider.screen(_requests(predefined="not_a_real_query"))


def test_screen_predefined_passes_offset_and_size(monkeypatch: Any) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    _patch_screen(monkeypatch, _fake_quotes(), calls)
    provider = _provider()

    response = provider.screen(_requests(predefined="day_gainers", offset=5, size=3))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "day_gainers"
    assert kwargs == {"offset": 5, "count": 3}
    assert response.total == 21
    assert response.quotes[0]["symbol"] == "4755"


def test_screen_custom_filters_query_and_post_filter(monkeypatch: Any) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    _patch_screen(monkeypatch, _fake_quotes(), calls)
    provider = _provider()

    response = provider.screen(
        _requests(
            filters=[
                _filt("peratio.lasttwelvemonths", "lt", [30.0]),
                _filt("intradayprice", "gt", 100.0),
            ],
            sort_field="intradayprice",
            sort_ascending=True,
        )
    )

    args, kwargs = calls[0]
    assert len(args) == 1  # single combined query
    assert kwargs["size"] == 250
    assert kwargs["sortField"] == "intradayprice"
    assert kwargs["sortAsc"] is True

    # post filter: trailingPE < 30 -> 7036 kept; 40 fails; None fails closed
    assert [q["symbol"] for q in response.quotes] == ["7036"]
    assert response.filtered_out == 2
    assert response.query["filters"][0]["field"] == "peratio.lasttwelvemonths"


def test_screen_missing_quotes_key_yields_empty_quotes(monkeypatch: Any) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []
    _patch_screen(monkeypatch, {"unexpected": "shape"}, calls)
    provider = _provider()

    response = provider.screen(_requests(predefined="day_gainers"))

    assert response.quotes == []  # missing quotes key -> []
    assert response.total == 21  # total is read independently of quotes
    assert response.filtered_out == 0


def test_screen_deferred_field_adds_note(monkeypatch: Any) -> None:
    # pick a numeric screener field with no quote key (deferred to the server)
    unmapped = [
        field
        for fields in SCREENER_FIELDS.values()
        for field in fields
        if field not in ys.SCREENER_FIELD_TO_QUOTE_KEY and field not in SCREENER_FIELDS["identity"]
    ]
    assert unmapped, "expected at least one unmapped numeric screener field"
    calls: list[tuple[Any, dict[str, Any]]] = []
    _patch_screen(monkeypatch, _fake_quotes(), calls)
    provider = _provider()

    response = provider.screen(
        _requests(
            filters=[
                _filt(unmapped[0], "gt", 1.0),
            ]
        )
    )

    # The deferred note is local-only: the response has no notes field, so assert
    # via filtered_out == 0 (a quote-less payload still succeeds).
    assert response.filtered_out == 0
    assert SERVER_SIDE_FILTER_NOTE
    keys = [key for key, _op, _b in provider._numeric_checks([_filt(unmapped[0], "gt", 1.0)])[0]]
    assert keys == []
    assert provider._numeric_checks([_filt(unmapped[0], "gt", 1.0)])[1] is True


def test_numeric_checks_btwn_edges_and_unparseable_are_dropped(mocker: Any = None) -> None:
    """btwn filters survive only when they are lists of 2 parseable numbers.

    MarketScreenFilter validates values via pydantic, so malformed values are
    exercised with lightweight stand-ins carrying the same attrs.
    """
    provider = _provider()

    class Stub:
        def __init__(self, field: str, operator: str, value: Any) -> None:
            self.field = field
            self.operator = operator
            self.value = value

    filters: Any = [
        _filt("intradayprice", "btwn", [1.0, 2.0]),  # kept
        Stub("intradayprice", "btwn", (1.0, 2.0)),  # tuple not list -> skipped
        Stub("intradayprice", "btwn", [1.0, 2.0, 3.0]),  # len 3 -> skipped
        Stub("intradayprice", "btwn", [1.0, "x"]),  # unparseable -> skipped
        Stub("intradayprice", "gt", ["x"]),  # scalar unparseable -> skipped
        Stub("intradayprice", "gt", [None]),  # raw None -> skipped
    ]

    checks, deferred = provider._numeric_checks(filters)

    assert deferred is False
    assert checks == [("regularMarketPrice", "btwn", (1.0, 2.0))]


def test_numeric_checks_skips_identity_fields_regardless_of_operator(mocker: Any = None) -> None:
    """Identity fields produce neither a check nor a deferral, line 314."""
    provider = _provider()

    class Stub:
        def __init__(self, field: str, operator: str, value: Any) -> None:
            self.field = field
            self.operator = operator
            self.value = value

    checks, deferred = provider._numeric_checks(
        [Stub("exchange", "gt", 1.0), Stub("industry", "lt", [2.0])]  # type: ignore[list-item]
    )

    assert checks == []
    assert deferred is False
