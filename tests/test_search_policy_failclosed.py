"""Fail-closed contract for ``GET /v1/instruments/search`` provider refusals.

A ``ProviderPolicyError`` from either search provider means *the source may not
be used*, which is not the same fact as *the query matched nothing*. Before
t_5a078314 the Yahoo refusal was swallowed (``except ProviderPolicyError: pass``)
and the SEC refusal was counted as a generic upstream defect, so:

* SEC empty + Yahoo refused -> ``200 []`` (refusal looked like "0 results");
* SEC refused + Yahoo empty -> ``502`` (a licensing decision looked like an outage).

The locked-down behaviour:

* at least one merged instrument            -> ``200`` list (unchanged);
* nothing merged and a generic defect       -> ``502`` fixed upstream detail (unchanged,
  and this wins over a co-occurring refusal);
* nothing merged, no generic defect, refusal -> ``403`` with the fixed detail
  ``Search is unavailable under the configured provider policy``;
* nothing merged, both providers healthy     -> ``200 []``.

Nothing here touches the network: both provider factories are replaced with
in-module stubs and every AF_INET/AF_INET6 connect fails the test.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from yowayowa.domain import Instrument
from yowayowa.providers.base import ProviderPolicyError

SEARCH_PATH = "/v1/instruments/search"

#: Fixed client-facing wording for the policy refusal. The exception text, the
#: provider name, and anything else internal stay out of the body.
POLICY_DETAIL = "Search is unavailable under the configured provider policy"

#: Fixed wording already used for upstream 502s (t_efafb717 contract).
UPSTREAM_DETAIL = "Upstream data source is temporarily unavailable"

#: Looks like an internal identifier plus a filesystem path, i.e. exactly what a
#: leaked exception string or a provider URL would expose.
SECRET_MARKER = "LEAK-MARKER-4b91:/root/private/vault.sqlite"

INSTRUMENT_KEYS = {"symbol", "name", "exchange", "instrument_type", "currency", "cik"}

SEC_ROWS = [
    Instrument(symbol="AAPL", name="Apple Inc.", exchange="NMS", currency="USD"),
    Instrument(symbol="MSFT", name="Microsoft Corp.", exchange="NMS", currency="USD"),
]
YAHOO_ROWS = [
    Instrument(symbol="AAPL", name="Apple Inc. (Yahoo)", exchange="NMS", currency="USD"),
    Instrument(symbol="7203.T", name="Toyota Motor", exchange="JPX", currency="JPY"),
]

LEAK_FRAGMENTS = (
    "Traceback (most recent call last)",
    'File "',
    "site-packages",
    ".venv/",
    "src/yowayowa/",
    "pydantic_core",
    "sqlalchemy.exc",
    SECRET_MARKER,
)


def _policy_error() -> ProviderPolicyError:
    return ProviderPolicyError(
        f"Provider 'yahoo-search' is not licensed for public redistribution {SECRET_MARKER}"
    )


def _general_error() -> RuntimeError:
    return RuntimeError(f"upstream returned HTTP 503 for https://example.test/?{SECRET_MARKER}")


@pytest.fixture(autouse=True)
def _forbid_outbound_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    inet_families = {socket.AF_INET, socket.AF_INET6}

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        if self.family in inet_families:
            raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> Any:
        if self.family in inet_families:
            raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")
        return real_connect_ex(self, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"outbound network is forbidden in this suite: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from yowayowa.config import get_settings

    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    get_settings.cache_clear()


def _patch_providers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sec: list[Instrument] | Exception,
    yahoo: list[Instrument] | Exception,
) -> dict[str, int]:
    """Replace both search providers and count how often each is consulted."""
    from yowayowa.api import routes

    calls = {"sec": 0, "yahoo": 0}

    class _StubSec:
        def search(self, query: str, limit: int) -> list[Instrument]:
            calls["sec"] += 1
            if isinstance(sec, Exception):
                raise sec
            return list(sec)[:limit]

    class _StubYahoo:
        def __init__(self, settings: Any) -> None:
            self._settings = settings

        def search(self, query: str, limit: int) -> list[Instrument]:
            calls["yahoo"] += 1
            if isinstance(yahoo, Exception):
                raise yahoo
            return list(yahoo)[:limit]

    monkeypatch.setattr(routes, "_sec", lambda settings: _StubSec())
    monkeypatch.setattr(routes, "YahooSearchProvider", _StubYahoo)
    return calls


def _assert_no_leak(response: Any) -> None:
    body = response.text
    for fragment in LEAK_FRAGMENTS:
        assert fragment not in body, f"leaked internals in body: {fragment!r}"


def _search(client: TestClient, **params: Any) -> Any:
    return client.get(SEARCH_PATH, params={"q": "a", **params})


# --------------------------------------------------------------------------- #
# refusal only -> 403 with the fixed detail
# --------------------------------------------------------------------------- #
def test_sec_empty_yahoo_refused_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_providers(monkeypatch, sec=[], yahoo=_policy_error())

    response = _search(client)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": POLICY_DETAIL}
    assert calls == {"sec": 1, "yahoo": 1}
    _assert_no_leak(response)


def test_sec_refused_yahoo_empty_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=_policy_error(), yahoo=[])

    response = _search(client)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": POLICY_DETAIL}


def test_both_providers_refused_returns_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=_policy_error(), yahoo=_policy_error())

    response = _search(client)

    assert response.status_code == 403, response.text
    assert response.json() == {"detail": POLICY_DETAIL}
    _assert_no_leak(response)


def test_refused_provider_is_not_retried_or_backfilled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_providers(monkeypatch, sec=[], yahoo=_policy_error())

    assert _search(client).status_code == 403
    assert _search(client).status_code == 403
    # one attempt per provider per request: no retry loop, no secondary fetch
    assert calls == {"sec": 2, "yahoo": 2}


# --------------------------------------------------------------------------- #
# genuinely empty -> 200 []
# --------------------------------------------------------------------------- #
def test_both_providers_empty_and_healthy_returns_200_empty_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=[], yahoo=[])

    response = _search(client)

    assert response.status_code == 200, response.text
    assert response.json() == []


# --------------------------------------------------------------------------- #
# partial success -> 200, refusal does not hide the surviving results
# --------------------------------------------------------------------------- #
def test_sec_rows_with_yahoo_refused_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=SEC_ROWS, yahoo=_policy_error())

    response = _search(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["symbol"] for row in body] == ["AAPL", "MSFT"]
    assert set(body[0]) == INSTRUMENT_KEYS
    _assert_no_leak(response)


def test_yahoo_rows_with_sec_refused_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=_policy_error(), yahoo=YAHOO_ROWS)

    response = _search(client)

    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in response.json()] == ["AAPL", "7203.T"]


def test_sec_rows_with_yahoo_generic_defect_returns_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=SEC_ROWS, yahoo=_general_error())

    response = _search(client)

    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in response.json()] == ["AAPL", "MSFT"]


# --------------------------------------------------------------------------- #
# generic defects keep the existing 502 contract (and win over a refusal)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("sec", "yahoo"),
    [
        (_general_error(), _policy_error()),
        (_policy_error(), _general_error()),
        (_general_error(), _general_error()),
    ],
    ids=["general_then_refused", "refused_then_general", "both_general"],
)
def test_generic_defect_returns_502_and_takes_precedence_over_refusal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, sec: Exception, yahoo: Exception
) -> None:
    _patch_providers(monkeypatch, sec=sec, yahoo=yahoo)

    response = _search(client)

    assert response.status_code == 502, response.text
    assert response.json() == {"detail": UPSTREAM_DETAIL}
    _assert_no_leak(response)


# --------------------------------------------------------------------------- #
# unchanged success shape: SEC-first duplicate merge, order, limit, keys
# --------------------------------------------------------------------------- #
def test_duplicate_symbol_merge_keeps_sec_values_and_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=SEC_ROWS, yahoo=YAHOO_ROWS)

    response = _search(client)

    assert response.status_code == 200, response.text
    body = response.json()
    # Yahoo order first, SEC-only symbols appended; the duplicate symbol is
    # emitted once and carries the SEC record.
    assert [row["symbol"] for row in body] == ["AAPL", "7203.T", "MSFT"]
    assert body[0]["name"] == "Apple Inc."
    assert body[0]["exchange"] == "NMS"
    assert body[0]["currency"] == "USD"
    assert set(body[0]) == INSTRUMENT_KEYS


def test_limit_truncates_merged_results(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_providers(monkeypatch, sec=SEC_ROWS, yahoo=YAHOO_ROWS)

    response = _search(client, limit=1)

    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in response.json()] == ["AAPL"]


def test_limit_is_forwarded_to_both_providers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, int]] = []

    class _RecordingSec:
        def search(self, query: str, limit: int) -> list[Instrument]:
            seen.append(("sec", limit))
            return list(SEC_ROWS)[:limit]

    class _RecordingYahoo:
        def __init__(self, settings: Any) -> None:
            self._settings = settings

        def search(self, query: str, limit: int) -> list[Instrument]:
            seen.append(("yahoo", limit))
            return list(YAHOO_ROWS)[:limit]

    from yowayowa.api import routes

    monkeypatch.setattr(routes, "_sec", lambda settings: _RecordingSec())
    monkeypatch.setattr(routes, "YahooSearchProvider", _RecordingYahoo)

    assert _search(client, limit=7).status_code == 200
    assert seen == [("sec", 7), ("yahoo", 7)]


# --------------------------------------------------------------------------- #
# unchanged request validation and authentication
# --------------------------------------------------------------------------- #
def test_missing_query_returns_422(client: TestClient) -> None:
    response = client.get(SEARCH_PATH)

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_query_too_long_returns_422(client: TestClient) -> None:
    response = client.get(SEARCH_PATH, params={"q": "A" * 101})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("limit", [0, 101], ids=["too_small", "too_large"])
def test_limit_out_of_range_returns_422(client: TestClient, limit: int) -> None:
    response = client.get(SEARCH_PATH, params={"q": "a", "limit": limit})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_unauthorized_returns_401_without_calling_providers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yowayowa.config import get_settings

    calls = _patch_providers(monkeypatch, sec=[], yahoo=[])
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "test-secret")
    get_settings.cache_clear()

    response = client.get(SEARCH_PATH, params={"q": "a"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API token"}
    assert calls == {"sec": 0, "yahoo": 0}


def test_authorized_request_still_succeeds_with_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yowayowa.config import get_settings

    _patch_providers(monkeypatch, sec=SEC_ROWS, yahoo=_policy_error())
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "test-secret")
    get_settings.cache_clear()

    response = client.get(
        SEARCH_PATH, params={"q": "a"}, headers={"Authorization": "Bearer test-secret"}
    )

    assert response.status_code == 200, response.text
    assert [row["symbol"] for row in response.json()] == ["AAPL", "MSFT"]
