"""FX Phase2 tests: Frankfurter (ECB) provider, pair routing, licensing registration.

Normal, failing, and boundary paths are covered per the task rules:
- normal: quote/history return normalized models with complete provenance;
- failure: network error -> LookupError (502 upstream), missing pair -> 404;
- boundary: weekend contraction, non-ECB pairs -> 404, crypto mode split,
  interval!=1d -> 422, cache hit, licensing registry entry.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.frankfurter import (
    ECB_REFERENCE_CURRENCIES,
    FRANKFURTER_BASE_URL,
    FrankfurterFxProvider,
)
from yowayowa.services.licensing import license_catalog, public_api_allowed, source_policy

# --------------------------------------------------------------------- fixtures


def _latest_payload(base: str = "USD", quote: str = "JPY", rate: float = 157.92) -> dict[str, Any]:
    return {"base": base, "date": "2026-09-23", "rates": {quote: rate}}


def _timeseries_payload(base: str = "USD", quote: str = "JPY") -> dict[str, Any]:
    return {
        "base": base,
        "start_date": "2026-09-14",
        "end_date": "2026-09-23",
        "rates": {
            # Weekend contraction: 09-19/09-20 are absent (ECB business days only).
            "2026-09-14": {quote: 157.10},
            "2026-09-15": {quote: 157.30},
            "2026-09-16": {quote: 157.50},
            "2026-09-17": {quote: 157.70},
            "2026-09-18": {quote: 157.80},
            "2026-09-21": {quote: 157.85},
            "2026-09-22": {quote: 157.90},
            "2026-09-23": {quote: 157.92},
        },
    }


class _StubTransport(httpx.HTTPTransport):
    """httpx transport stub mapping (method, url) to canned JSON payloads."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        super().__init__()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._handler(request)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(
            200,
            content=json.dumps(response).encode(),
            headers={"Content-Type": "application/json"},
            request=request,
        )


def _provider(monkeypatch: pytest.MonkeyPatch, handler: Any) -> FrankfurterFxProvider:
    """Build a real provider whose HTTP client hits the canned stub transport."""

    settings = get_settings()
    provider = FrankfurterFxProvider(settings)
    provider.client = httpx.Client(transport=_StubTransport(handler))
    return provider


def _not_found_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        404,
        content=json.dumps({"message": "not found"}).encode(),
        headers={"Content-Type": "application/json"},
        request=request,
    )


# --------------------------------------------------------------- ECB coverage


def test_ecb_reference_currency_set_matches_verified_coverage() -> None:
    assert len(ECB_REFERENCE_CURRENCIES) == 30
    assert {"AED", "ARS", "CLP", "COP", "PEN", "SAR", "TWD", "VND"}.isdisjoint(
        ECB_REFERENCE_CURRENCIES
    )
    assert "ISK" in ECB_REFERENCE_CURRENCIES


def test_frankfurter_descriptor_is_official_public() -> None:
    assert FrankfurterFxProvider.descriptor.name == "frankfurter-ecb"
    assert FrankfurterFxProvider.descriptor.license_class is LicenseClass.OFFICIAL_PUBLIC
    assert FrankfurterFxProvider.descriptor.redistributable is True


# ---------------------------------------------------------------- normal paths


def test_fx_quote_returns_snapshot_with_full_provenance(monkeypatch, tmp_path) -> None:
    del tmp_path  # settings come from the ambient environment in unit scope
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> dict[str, Any]:
        seen.append(request)
        return _timeseries_payload()

    provider = _provider(monkeypatch, handler)
    snapshot = provider.fx_quote("USDJPY")

    assert snapshot.pair == "USDJPY"
    assert snapshot.rate == 157.92
    assert snapshot.as_of == datetime(2026, 9, 23, tzinfo=UTC)
    assert snapshot.previous_close == 157.90
    assert snapshot.provenance.provider == "frankfurter-ecb"
    assert snapshot.provenance.license_class is LicenseClass.OFFICIAL_PUBLIC
    assert snapshot.provenance.as_of == date(2026, 9, 23)
    assert snapshot.provenance.source_url is not None
    notes = " ".join(snapshot.provenance.notes).lower()
    assert "ecb reference" in notes
    assert "daily" in notes
    assert "computed by frankfurter" in notes

    request = seen[0]
    assert request.url.host == "api.frankfurter.dev"
    # A single range request carries the whole 10-day window (latest + previous
    # business day fix); the exact dates are clock-derived, so match the shape.
    assert re.fullmatch(r"/v1/\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}", request.url.path)
    assert request.url.params["base"] == "USD"
    assert request.url.params["symbols"] == "JPY"


def test_fx_history_returns_daily_points_and_contracting_weekends(monkeypatch) -> None:
    provider = _provider(monkeypatch, lambda _request: _timeseries_payload())
    history = provider.fx_history("USDJPY", interval="1d", period="1mo")

    assert history.pair == "USDJPY"
    assert history.interval == "1d"
    assert len(history.points) == 8
    assert [point.close for point in history.points] == [
        157.10,
        157.30,
        157.50,
        157.70,
        157.80,
        157.85,
        157.90,
        157.92,
    ]
    # Weekend dates (Sat 09-19 / Sun 09-20) stay absent — never forward-filled.
    dates = {point.timestamp.date() for point in history.points}
    assert date(2026, 9, 19) not in dates
    assert date(2026, 9, 20) not in dates
    assert history.points[0].open is None and history.points[0].volume is None
    assert history.provenance.as_of == date(2026, 9, 23)


def test_fx_quote_and_history_hit_cache_without_second_request(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> dict[str, Any]:
        calls.append(request)
        return _timeseries_payload()

    provider = _provider(monkeypatch, handler)
    first = provider.fx_quote("USDJPY")
    second = provider.fx_quote("USDJPY")
    assert first is second
    assert len(calls) == 1

    history_calls = len(calls)
    first_history = provider.fx_history("USDJPY", interval="1d", period="1mo")
    again = provider.fx_history("USDJPY", interval="1d", period="1mo")
    assert first_history is again
    assert len(calls) == history_calls + 1


# -------------------------------------------------------------- failing paths


def test_fx_quote_unknown_pair_404_response_becomes_lookup_error(monkeypatch) -> None:
    provider = _provider(monkeypatch, _not_found_handler)
    with pytest.raises(LookupError, match="no rate data"):
        provider.fx_quote("USDJPY")


def test_fx_quote_network_error_is_transport_error_not_zero(monkeypatch) -> None:
    from yowayowa.providers.frankfurter import FrankfurterTransportError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(monkeypatch, handler)
    with pytest.raises(FrankfurterTransportError, match="unreachable"):
        provider.fx_quote("USDJPY")


def test_fx_history_missing_series_is_lookup_error(monkeypatch) -> None:
    provider = _provider(monkeypatch, _not_found_handler)
    with pytest.raises(LookupError):
        provider.fx_history("USDJPY", interval="1d", period="1mo")


def test_fx_quote_payload_without_rates_is_lookup_error(monkeypatch) -> None:
    provider = _provider(monkeypatch, lambda _request: {"base": "USD", "date": "2026-09-23"})
    with pytest.raises(LookupError, match="no rates"):
        provider.fx_quote("USDJPY")


# ------------------------------------------------------------ boundary paths


def test_non_ecb_pairs_fail_closed_without_network_call(monkeypatch) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected for non-ECB pairs")

    provider = _provider(monkeypatch, failing_handler)
    for pair in ("AEDUSD", "USDVND", "TWDJPY", "SARPEN"):
        with pytest.raises(LookupError, match="does not fall back"):
            provider.fx_quote(pair)
        with pytest.raises(LookupError, match="does not fall back"):
            provider.fx_history(pair, interval="1d", period="1mo")


@pytest.mark.parametrize(
    ("interval", "period"),
    [
        ("1m", "1mo"),
        ("5m", "1mo"),
        ("15m", "1mo"),
        ("30m", "1mo"),
        ("1h", "1mo"),
        ("1wk", "1mo"),
        ("1mo", "1mo"),
        ("1d", "bogus"),
    ],
)
def test_fx_history_refuses_non_daily_intervals_and_unknown_periods(
    monkeypatch, interval: str, period: str
) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network call expected for refused intervals")

    provider = _provider(monkeypatch, failing_handler)
    daily_only = interval != "1d"
    with pytest.raises(ValueError, match="daily only" if daily_only else "period"):
        provider.fx_history("USDJPY", interval=interval, period=period)


def test_fallback_host_is_used_when_primary_is_unreachable(monkeypatch) -> None:
    hosts: list[str | None] = []

    def handler(request: httpx.Request) -> dict[str, Any] | httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.frankfurter.dev":
            return httpx.Response(503, request=request)
        return _timeseries_payload()

    provider = _provider(monkeypatch, handler)
    snapshot = provider.fx_quote("USDJPY")
    assert snapshot.rate == 157.92
    assert hosts == ["api.frankfurter.dev", "api.frankfurter.app"]


# --------------------------------------------------------- licensing registry


def test_frankfurter_ecb_is_registered_public_safe_source() -> None:
    policy = source_policy("frankfurter-ecb")
    assert policy is not None
    assert policy.key == "frankfurter-ecb"
    assert policy.license_class is LicenseClass.OFFICIAL_PUBLIC
    assert policy.attribution == "Source: European Central Bank reference exchange rates"
    assert policy.terms_url is not None and policy.terms_url.startswith("https://")
    assert policy.reviewed_on == date(2026, 9, 24)

    # The legacy alias must resolve to the same policy (no silent divergence).
    assert source_policy("ecb-fx") is policy

    assert public_api_allowed("frankfurter-ecb") is True
    assert public_api_allowed("frankfurter") is True
    assert public_api_allowed("yahoo") is False
    assert public_api_allowed("fred") is False

    catalog = license_catalog("public")
    assert "frankfurter-ecb" in catalog.public_safe_sources
    assert "frankfurter-ecb" not in catalog.blocked_sources
    assert FRANKFURTER_BASE_URL == "https://api.frankfurter.dev/v1"
