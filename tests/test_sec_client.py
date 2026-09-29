from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from yowayowa.config import Settings
from yowayowa.domain import Instrument, LicenseClass
from yowayowa.providers.sec import SecClient, _RateLimiter


def _settings() -> Settings:
    return Settings(database_url="sqlite:///:memory:")


def _client() -> SecClient:
    return SecClient(_settings())


def _ticker_payload() -> dict[str, dict[str, Any]]:
    return {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
    }


def test_rate_limiter_sleeps_only_when_interval_not_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("yowayowa.providers.sec.time.sleep", sleeps.append)

    clock = {"now": 100.0}
    monkeypatch.setattr(
        "yowayowa.providers.sec.time.monotonic",
        lambda: clock["now"],
    )

    limiter = _RateLimiter(4.0)  # 0.25s interval

    limiter.wait()
    assert sleeps == []

    limiter.wait()
    # monotonic() still returns 100.0, so a full interval of sleep is required.
    assert sleeps == [0.25]

    limiter.wait()
    # _next_at keeps advancing one interval per call even though the frozen
    # clock never moves, so the required delay grows: 0.25 then 0.5.
    assert sleeps == [0.25, 0.5]


def test_rate_limiter_no_sleep_once_wall_clock_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("yowayowa.providers.sec.time.sleep", sleeps.append)
    clock = {"now": 100.0}
    monkeypatch.setattr(
        "yowayowa.providers.sec.time.monotonic",
        lambda: clock["now"],
    )

    limiter = _RateLimiter(10.0)  # 0.1s interval
    limiter.wait()
    clock["now"] = 100.5  # far past the scheduled slot

    limiter.wait()
    assert sleeps == []


def test_get_json_raises_for_status_and_returns_payload(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json={"ok": True})

    assert _client()._get_json("https://data.sec.gov/any.json") == {"ok": True}


def test_get_json_propagates_http_errors(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(status_code=403)

    with pytest.raises(httpx.HTTPStatusError):
        _client()._get_json("https://data.sec.gov/any.json")


def test_get_json_retries_network_errors_then_returns_response(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("temporary network error"))
    httpx_mock.add_exception(httpx.ConnectTimeout("temporary timeout"))
    httpx_mock.add_response(json={"ok": True})

    payload = _client()._get_json("https://data.sec.gov/any.json")

    assert payload == {"ok": True}
    assert len(httpx_mock.get_requests()) == 3


def test_ticker_map_builds_instruments_and_caches_for_24h(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json=_ticker_payload())
    client = _client()

    mapping = client.ticker_map()
    again = client.ticker_map()

    assert set(mapping) == {"AAPL", "MSFT", "BRK-B"}
    apple = mapping["AAPL"]
    assert isinstance(apple, Instrument)
    assert apple.name == "Apple Inc."
    assert apple.cik == "0000320193"
    assert apple.instrument_type == "equity"
    assert mapping is again
    assert len(httpx_mock.get_requests()) == 1

    # Beyond the 24h window the cache is refreshed with a new HTTP request.
    stale = datetime.now(UTC).fromtimestamp(datetime.now(UTC).timestamp() - 86_401, tz=UTC)
    httpx_mock.add_response(json=_ticker_payload())
    client._ticker_cache = (stale, mapping)

    client.ticker_map()

    assert len(httpx_mock.get_requests()) == 2


def test_search_ranks_exact_prefix_contains_and_limits(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    payload = {
        "0": {"cik_str": 1, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 2, "ticker": "AAP", "title": "Advanced Auto Parts"},
        "2": {"cik_str": 3, "ticker": "BAAP", "title": "Beta Apparel"},
    }
    httpx_mock.add_response(json=payload)
    client = _client()

    ranked = client.search("aap")

    assert [instrument.symbol for instrument in ranked] == ["AAP", "AAPL", "BAAP"]

    limited = client.search("aap", limit=2)
    assert [instrument.symbol for instrument in limited] == ["AAP", "AAPL"]


def test_search_is_case_insensitive_and_matches_name_prefix(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json=_ticker_payload())
    client = _client()

    by_symbol = client.search("msft")
    by_name = client.search("berkshire")

    assert [instrument.symbol for instrument in by_symbol] == ["MSFT"]
    assert [instrument.symbol for instrument in by_name] == ["BRK-B"]


def test_search_returns_empty_for_blank_query(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    assert _client().search("   ") == []
    assert _client().search("") == []
    assert httpx_mock.get_requests() == []


def _facts_payload() -> dict[str, Any]:
    return {
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-10-01",
                                "end": "2025-12-31",
                                "val": 1000,
                                "fy": 2026,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2026-01-30",
                                "accn": "0001-26-000001",
                            },
                            {
                                "end": "2025-12-31",
                                "val": 4000,
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-02",
                                "accn": "0001-26-000002",
                            },
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": "not-a-number",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                            }
                        ]
                    }
                },
                "EarningsPerShareDiluted": {
                    "units": {
                        "USD/shares": [
                            {
                                "end": "2025-12-31",
                                "val": 1.5,
                                "fy": 2026,
                                "fp": "Q1",
                                "form": "8-K",  # not periodic -> skipped
                            }
                        ]
                    }
                },
            }
        },
    }


def test_company_facts_builds_fundamentals_and_caches(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json=_ticker_payload())
    httpx_mock.add_response(json=_facts_payload())
    client = _client()

    fundamentals = client.company_facts("AAPL")
    cached = client.company_facts("AAPL")

    assert cached is fundamentals
    assert fundamentals.cik == "0000320193"
    assert fundamentals.company_name == "Apple Inc."
    assert fundamentals.provenance.license_class == LicenseClass.OFFICIAL_PUBLIC
    revenue = fundamentals.metrics["revenue"]
    assert revenue.label == "Revenue"
    assert len(revenue.points) == 2
    assert revenue.points[-1].value == Decimal("4000")
    assert revenue.points[-1].form == "10-K"
    # Non-periodic forms and unparseable values never become points.
    assert "eps_diluted" not in fundamentals.metrics

    assert len(httpx_mock.get_requests()) == 2


def test_company_facts_resolves_dotted_class_share_tickers(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json=_ticker_payload())
    httpx_mock.add_response(json=_facts_payload())
    client = _client()

    fundamentals = client.company_facts("BRK.B")

    assert fundamentals.cik == "0001067983"


def test_company_facts_raises_for_unknown_ticker(
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json=_ticker_payload())

    with pytest.raises(LookupError, match="ZZZZ"):
        _client().company_facts("ZZZZ")


def test_extract_metric_merges_aliases_by_priority() -> None:
    us_gaap = {
        "SalesRevenueNet": {
            "units": {
                "USD": [
                    {
                        "end": "2024-12-31",
                        "val": 100,
                        "form": "10-K",
                        "fy": 2024,
                        "fp": "FY",
                    }
                ]
            }
        },
        "Revenues": {
            "units": {
                "USD": [
                    {
                        "end": "2024-12-31",
                        "val": 200,
                        "form": "10-K",
                        "fy": 2024,
                        "fp": "FY",
                    }
                ]
            }
        },
        "RevenuesNeverValid": {
            "units": {
                "USD": [
                    {"end": "bogus-date", "val": 1, "form": "10-K"},
                ]
            }
        },
    }

    series = SecClient._extract_metric(
        us_gaap,
        "revenue",
        "Revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ),
    )

    assert len(series.points) == 1
    # Higher priority (lower index) alias wins on identical period keys.
    assert series.points[0].value == Decimal("100")
    assert series.points[0].period_end == date(2024, 12, 31)


def test_extract_metric_returns_empty_series_without_fact() -> None:
    series = SecClient._extract_metric({}, "assets", "Total assets", ("Assets",))

    assert series.key == "assets"
    assert series.points == []


def test_extract_metric_skips_facts_with_missing_end_or_value() -> None:
    us_gaap = {
        "Assets": {
            "units": {
                "USD": [
                    {"val": 100, "form": "10-K"},
                    {"end": "2025-12-31", "form": "10-K"},
                ]
            }
        }
    }

    series = SecClient._extract_metric(us_gaap, "assets", "Total assets", ("Assets",))

    assert series.points == []
