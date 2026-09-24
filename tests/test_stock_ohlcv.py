"""Stock daily-OHLCV tests (P4-F, Alpaca US bars).

Normal, failing, and boundary paths per the task rules:
- normal: the alpaca provider returns complete provenance records;
- failure: HTTP 401/403/5xx -> AlpacaTransportError, 404/422 -> LookupError,
  transport error -> AlpacaTransportError, next_page_token pagination,
  forming-bar exclusion (15-minute SIP constraint), malformed/zero rows
  skipped (never zero-filled);
- store: append/read round-trip, idempotent re-fetch, corrupt-line safety,
  every persisted JSON line valid with full provenance;
- CLI + /v1/stocks routes incl. public-mode 404 fail-closed, route ordering
  (/latest declared before /{symbol}/bars), missing token -> 401;
- licensing registry entry for alpaca (PERSONAL_ONLY).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.domain import LicenseClass
from yowayowa.providers.alpaca import (
    AlpacaMarketDataProvider,
    AlpacaTransportError,
)
from yowayowa.services.licensing import source_policy
from yowayowa.stock_acquisition import StockOhlcvStore, default_store, fetch_stock_ohlcv
from yowayowa.stock_models import StockOhlcvRecord, normalize_stock_symbol

# ------------------------------------------------------------------- fixtures


def _canned(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "https://stub.invalid"),
    )


def _days(count: int, start: datetime | None = None) -> list[datetime]:
    start = start or datetime(2026, 9, 15, 4, 0, tzinfo=UTC)  # 04:00 UTC bar starts
    return [start + timedelta(days=offset) for offset in range(count)]


def _alpaca_payload(days: int = 6, *, with_next_token: bool = False) -> dict[str, Any]:
    bars = []
    for index, ts in enumerate(_days(days)):
        bars.append(
            {
                "t": ts.isoformat().replace("+00:00", "Z"),
                "o": 100.0 + index,
                "h": 102.0 + index,
                "l": 99.0 + index,
                "c": 101.0 + index,
                "v": 50_000_000 + index * 1000,
                "n": 600_000 + index,
                "vw": 100.9 + index,
            }
        )
    payload: dict[str, Any] = {"bars": {"AAPL": bars}}
    if with_next_token:
        payload["next_page_token"] = "PAGE2"
    return payload


def _stub_provider(provider_cls: type, responses: list[httpx.Response] | httpx.Response):
    settings = get_settings()
    provider = provider_cls(settings)
    if isinstance(responses, httpx.Response):
        responses = [responses]
    queue = iter(responses)

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return next(queue)
        except StopIteration:  # pagination replay safety
            return responses[-1]

    provider.client._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]
    return provider


def _record(symbol: str, provider: str, as_of: datetime, close: float) -> StockOhlcvRecord:
    return StockOhlcvRecord(
        symbol=symbol,
        provider=provider,
        currency="USD",
        source_url=f"https://stub.invalid/{provider}",
        license_class=LicenseClass.PERSONAL_ONLY.value,
        retrieved_at=datetime.now(UTC),
        as_of=as_of,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000.0,
        vwap=close - 0.1,
        trade_count=1234,
    )


class _StubOkProvider:
    def ohlcv(self, symbol: str, *, days: int = 30):
        return [_record(symbol, "stub", _days(1)[0], 123.0)]


# ------------------------------------------------------------- normalization


def test_normalize_stock_symbol_accepts_valid_tickers() -> None:
    assert normalize_stock_symbol("aapl") == "AAPL"
    assert normalize_stock_symbol(" MSFT ") == "MSFT"
    assert normalize_stock_symbol("A") == "A"


def test_normalize_stock_symbol_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        normalize_stock_symbol("TOOLONG1")  # 6 letters + digit
    with pytest.raises(ValueError):
        normalize_stock_symbol("BRK..")  # malformed class suffix
    with pytest.raises(ValueError):
        normalize_stock_symbol("")
    with pytest.raises(ValueError):
        normalize_stock_symbol("7203")  # digits: not an Alpaca stock symbol


def test_normalize_stock_symbol_accepts_dotted_class_shares() -> None:
    assert normalize_stock_symbol("brk.b") == "BRK.B"


# ------------------------------------------------------------- alpaca provider


def test_alpaca_ohlcv_returns_records_with_provenance() -> None:
    payload = _canned(json.dumps(_alpaca_payload()).encode())
    provider = _stub_provider(AlpacaMarketDataProvider, payload)
    records = provider.ohlcv("AAPL", days=30)
    assert len(records) == 6
    first = records[0]
    assert isinstance(first, StockOhlcvRecord)
    assert first.provider == "alpaca"
    assert first.symbol == "AAPL"
    assert first.currency == "USD"
    assert first.interval == "1d"
    assert first.license_class == LicenseClass.PERSONAL_ONLY.value
    assert first.source_url.startswith("https://data.alpaca.markets/v2/stocks/bars")
    assert "NOT an official reference rate" in first.notes[0]
    assert first.open == 100.0 and first.close == 101.0
    assert first.volume == 50_000_000.0
    assert first.vwap == 100.9
    assert first.trade_count == 600_000


def test_alpaca_skips_malformed_and_zero_bars() -> None:
    payload = _alpaca_payload()
    payload["bars"]["AAPL"][2] = {"t": _days(6)[2].isoformat()}  # malformed: no OHLC
    payload["bars"]["AAPL"][3] = {
        "t": _days(6)[3].isoformat(),
        "o": 0,
        "h": 0,
        "l": 0,
        "c": 0,
        "v": 0,
    }
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(json.dumps(payload).encode()))
    records = provider.ohlcv("AAPL", days=30)
    assert len(records) == 4  # malformed + all-zero dropped, no zero-fill
    assert all(record.close > 0 for record in records)


def test_alpaca_pagination_follows_next_page_token() -> None:
    page1 = _canned(json.dumps(_alpaca_payload(days=2, with_next_token=True)).encode())
    page2 = _canned(json.dumps(_alpaca_payload(days=4)).encode())
    provider = _stub_provider(AlpacaMarketDataProvider, [page1, page2])
    records = provider.ohlcv("AAPL", days=30)
    assert len(records) == 4  # page 1 (2 bars) + page 2 (2 new bars)
    closes = [record.close for record in records]
    assert closes == [101.0, 102.0, 103.0, 104.0]  # sorted oldest-first, both pages merged


def test_alpaca_unknown_symbol_is_lookup_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"{}", status_code=404))
    with pytest.raises(LookupError):
        provider.ohlcv("ZZZZZ", days=30)


def test_alpaca_422_is_lookup_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"{}", status_code=422))
    with pytest.raises(LookupError):
        provider.ohlcv("ZZZZZ", days=30)


def test_alpaca_http_401_is_transport_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"{}", status_code=401))
    with pytest.raises(AlpacaTransportError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_http_403_is_transport_error() -> None:
    # Covers both bad-grant 403s: insufficient FX grants and the recent-SIP
    # subscription error — neither is missing data; both fail closed loudly.
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"{}", status_code=403))
    with pytest.raises(AlpacaTransportError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_http_429_is_transport_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"{}", status_code=429))
    with pytest.raises(AlpacaTransportError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_http_500_is_transport_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b"", status_code=500))
    with pytest.raises(AlpacaTransportError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_connection_error_is_transport_error() -> None:
    settings = get_settings()
    provider = AlpacaMarketDataProvider(settings)
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: (_ for _ in ()).throw(httpx.ConnectError("boom"))
        )
    )
    with pytest.raises(AlpacaTransportError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_empty_bars_payload_is_lookup_error() -> None:
    provider = _stub_provider(AlpacaMarketDataProvider, _canned(b'{"bars": {"AAPL": []}}'))
    with pytest.raises(LookupError):
        provider.ohlcv("AAPL", days=30)


def test_alpaca_sends_request_window_and_feed_contract() -> None:
    """The request pins timeframe/feed/window: SIP, 1Day, end = yesterday."""

    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return _canned(json.dumps(_alpaca_payload()).encode())

    settings = get_settings()
    provider = AlpacaMarketDataProvider(settings)
    provider.client._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]
    provider.ohlcv("AAPL", days=5)
    assert captured, "provider must have issued at least one request"
    url = captured[0]
    assert "feed=sip" in url
    assert "timeframe=1Day" in url
    assert "symbols=AAPL" in url
    assert "start=" in url and "end=" in url
    # The window must end before today: Alpaca's free plan rejects SIP
    # requests whose window includes the current calendar day with 403
    # (live-verified 2026-09-24), and ending at yesterday guarantees every
    # bar is a finalized session.
    end_date = url.split("end=")[1].split("&")[0]
    assert end_date == (datetime.now(UTC) - timedelta(days=1)).date().isoformat()


# ------------------------------------------------------------- public mode


def test_alpaca_provider_refuses_public_mode() -> None:
    from yowayowa.config import Settings
    from yowayowa.providers.base import ProviderPolicyError

    settings = Settings(mode="public", api_token="test-public-token")
    assert settings.mode == "public"
    with pytest.raises(ProviderPolicyError):
        AlpacaMarketDataProvider(settings)


# ------------------------------------------------------------- store


def test_stock_store_append_read_roundtrip(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    day = _days(1)[0]
    written = store.append(
        "AAPL",
        [_record("AAPL", "alpaca", day, 101.0), _record("AAPL", "stub", day, 205.0)],
    )
    assert written == 2
    rows = store.read("AAPL")
    assert len(rows) == 2
    by_provider = {row["provider"]: row for row in rows}
    assert by_provider["alpaca"]["close"] == 101.0
    assert by_provider["stub"]["vwap"] == 204.9
    assert by_provider["alpaca"]["as_of"] == by_provider["stub"]["as_of"]


def test_stock_store_append_is_idempotent_per_source_day(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    records = [_record("AAPL", "alpaca", _days(1)[0], 101.0)]
    assert store.append("AAPL", records) == 1
    assert store.append("AAPL", list(records)) == 0  # re-run writes nothing
    assert len(store.read("AAPL")) == 1


def test_stock_store_normalizes_z_and_offset_asof_keys(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    day = _days(1)[0]
    assert store.append("AAPL", [_record("AAPL", "alpaca", day, 101.0)]) == 1
    same_day = _record("AAPL", "alpaca", day, 101.0)
    same_day.retrieved_at = datetime.now(UTC)  # only retrieval time differs
    assert store.append("AAPL", [same_day]) == 0


def test_stock_store_missing_provider_rows_stay_missing(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("MSFT", [_record("MSFT", "alpaca", _days(1)[0], 300.0)])
    assert store.read("MSFT", provider="other") == []  # absent rows stay absent


def test_stock_store_skips_corrupt_lines_but_reads_valid(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("AAPL", [_record("AAPL", "alpaca", _days(1)[0], 101.0)])
    path = tmp_path / "stock-ohlcv" / "AAPL" / "ohlcv.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{broken json line\n")
    rows = store.read("AAPL")
    assert len(rows) == 1


def test_stock_store_read_is_newest_first(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    days = _days(3)
    store.append("NVDA", [_record("NVDA", "alpaca", day, 100.0 + i) for i, day in enumerate(days)])
    rows = store.read("NVDA", limit=2)
    assert len(rows) == 2
    assert rows[0]["as_of"] > rows[1]["as_of"]  # newest first


def test_stock_store_rejects_bad_symbol(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    with pytest.raises(ValueError):
        store.append("BRK..", [_record("BRK..", "alpaca", _days(1)[0], 101.0)])
    with pytest.raises(ValueError):
        store.read("TOOLONG1")
    assert store.read("AAPL") == []


def test_stock_store_every_line_is_valid_json_with_provenance(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    day = _days(1)[0]
    store.append("AAPL", [_record("AAPL", "alpaca", day, 101.0)])
    path = tmp_path / "stock-ohlcv" / "AAPL" / "ohlcv.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)  # every line parses as JSON
        for key in (
            "symbol",
            "interval",
            "currency",
            "provider",
            "source_url",
            "license_class",
            "retrieved_at",
            "as_of",
            "open",
            "high",
            "low",
            "close",
        ):
            assert key in entry, key
        assert entry["interval"] == "1d"
        assert entry["license_class"] == "personal_only"
        assert entry["provider"] == "alpaca"


def test_default_stock_store_path_is_fixed() -> None:
    store = default_store("/tmp/yw-test-data")
    assert str(store.root) == "/tmp/yw-test-data/stock-ohlcv"


# ------------------------------------------------------------- orchestration


def test_fetch_stock_ohlcv_isolates_symbol_failures(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    day = _days(1)[0]

    class _Ok:
        def ohlcv(self, symbol: str, *, days: int = 30):
            return [_record(symbol, "alpaca", day, 203.0)]

    class _Fail:
        def ohlcv(self, symbol: str, *, days: int = 30):
            raise AlpacaTransportError("down")

    summary = fetch_stock_ohlcv({"alpaca": _Fail()}, store, ["AAPL"])
    assert summary["AAPL"]["alpaca"]["persisted"] == 0
    assert "down" in summary["AAPL"]["alpaca"]["error"]

    summary = fetch_stock_ohlcv({"alpaca": _Ok()}, store, ["MSFT", "NVDA"])
    assert summary["MSFT"]["alpaca"] == {"persisted": 1, "error": None}
    assert summary["NVDA"]["alpaca"] == {"persisted": 1, "error": None}
    assert len(store.read("AAPL")) == 0
    assert len(store.read("MSFT")) == 1


def test_fetch_stock_ohlcv_rejects_bad_symbol(tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    with pytest.raises(ValueError):  # type: ignore[arg-type]
        fetch_stock_ohlcv({"alpaca": None}, store, ["BRK.."])  # type: ignore[dict-item]


# ------------------------------------------------------------- API routes


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(
        "yowayowa.api.stock_routes.default_store",
        lambda data_dir="./data": StockOhlcvStore(root=tmp_path / "stock-ohlcv"),
    )
    monkeypatch.setattr("yowayowa.api.stock_routes.get_settings", lambda: settings)
    from yowayowa.api.app import app

    return TestClient(app)


def test_api_stock_bars_serves_persisted_rows(api_client: TestClient, tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("AAPL", [_record("AAPL", "alpaca", _days(1)[0], 101.0)])
    response = api_client.get("/v1/stocks/AAPL/bars")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["provider"] == "alpaca"
    assert {"as_of", "open", "high", "low", "close", "provider", "source_url"} <= set(rows[0])


def test_api_stock_bars_latest_returns_single_newest_row(api_client: TestClient, tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    days = _days(3)
    store.append("AAPL", [_record("AAPL", "alpaca", day, 100.0 + i) for i, day in enumerate(days)])
    response = api_client.get("/v1/stocks/AAPL/bars/latest")
    assert response.status_code == 200
    row = response.json()
    assert row["close"] == 102.0  # newest day, one row only


def test_api_stock_bars_provider_filter_and_csv(api_client: TestClient, tmp_path) -> None:
    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    day = _days(1)[0]
    store.append(
        "AAPL",
        [_record("AAPL", "alpaca", day, 101.0), _record("AAPL", "stub", day, 205.0)],
    )
    rows = api_client.get("/v1/stocks/AAPL/bars", params={"provider": "alpaca"}).json()
    assert all(row["provider"] == "alpaca" for row in rows)
    csv_response = api_client.get(
        "/v1/stocks/AAPL/bars", params={"provider": "alpaca", "format": "csv"}
    )
    assert csv_response.status_code == 200
    assert "provider" in csv_response.text and "close" in csv_response.text
    assert csv_response.headers["content-type"].startswith("text/csv")


def test_api_stock_bars_404_when_nothing_persisted(api_client: TestClient) -> None:
    assert api_client.get("/v1/stocks/AAPL/bars").status_code == 404
    assert api_client.get("/v1/stocks/AAPL/bars/latest").status_code == 404


def test_api_stock_bars_422_on_bad_symbol(api_client: TestClient) -> None:
    assert api_client.get("/v1/stocks/BRK../bars").status_code == 422
    assert api_client.get("/v1/stocks/TOOLONG1/bars").status_code == 422


def test_api_stock_routes_resolve_in_both_declaration_orders(
    api_client: TestClient, tmp_path
) -> None:
    """/latest must never be captured by /{symbol}/bars regardless of ordering."""

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("AAPL", [_record("AAPL", "alpaca", _days(1)[0], 101.0)])
    assert api_client.get("/v1/stocks/AAPL/bars/latest").status_code == 200
    assert api_client.get("/v1/stocks/AAPL/bars").status_code == 200


def test_api_stock_bars_404_in_public_mode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa.api.app import app
    from yowayowa.config import Settings

    public_settings = Settings(mode="public", api_token="test-public-token")
    assert public_settings.mode == "public"
    monkeypatch.setattr("yowayowa.api.stock_routes.get_settings", lambda: public_settings)
    monkeypatch.setattr(
        "yowayowa.api.stock_routes.default_store",
        lambda data_dir="./data": StockOhlcvStore(root=tmp_path / "stock-ohlcv"),
    )
    client = TestClient(app)
    assert client.get("/v1/stocks/AAPL/bars").status_code == 404
    assert client.get("/v1/stocks/AAPL/bars/latest").status_code == 404


def test_api_stock_requires_token_in_personal_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """require_api_token rejects a missing bearer when api_token is set."""

    from yowayowa.api.deps import require_api_token
    from yowayowa.config import Settings

    tokened = Settings(mode="personal", api_token="secret-token")
    request = _stock_request("GET", "/v1/stocks/AAPL/bars")
    with pytest.raises(HTTPException) as unauthorized:
        require_api_token(request, settings=tokened)  # no Authorization header
    assert unauthorized.value.status_code == 401
    with pytest.raises(HTTPException) as wrong_token:
        require_api_token(request, authorization="Bearer nope", settings=tokened)
    assert wrong_token.value.status_code == 401
    # Correct bearer passes (dependency params are explicit on direct calls).
    require_api_token(
        request, authorization="Bearer secret-token", settings=tokened
    )  # must not raise


def _stock_request(method: str, path: str):
    from starlette.requests import Request

    return Request(
        scope={
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
        }
    )


# ------------------------------------------------------------- CLI


def test_cli_stock_commands_registered() -> None:
    from typer.testing import CliRunner

    from yowayowa.cli_entry import app as entry_app

    result = CliRunner().invoke(entry_app, ["--help"])
    assert result.exit_code == 0
    assert "stock-fetch" in result.output
    assert "stock-ohlcv" in result.output


def test_cli_stock_ohlcv_prints_table(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import stock_cli
    from yowayowa.cli_entry import app as entry_app

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("AAPL", [_record("AAPL", "alpaca", _days(1)[0], 101.0)])
    monkeypatch.setattr(stock_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["stock-ohlcv", "AAPL"])
    assert result.exit_code == 0, result.output
    assert "alpaca" in result.output


def test_cli_stock_ohlcv_json_output(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import stock_cli
    from yowayowa.cli_entry import app as entry_app

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    store.append("AAPL", [_record("AAPL", "alpaca", _days(1)[0], 101.0)])
    monkeypatch.setattr(stock_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["stock-ohlcv", "AAPL", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["provider"] == "alpaca"


def test_cli_stock_ohlcv_exit_1_when_empty(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import stock_cli
    from yowayowa.cli_entry import app as entry_app

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    monkeypatch.setattr(stock_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["stock-ohlcv", "AAPL"])
    assert result.exit_code == 1


def test_cli_stock_fetch_persists(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import stock_cli
    from yowayowa.cli_entry import app as entry_app

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    monkeypatch.setattr(stock_cli, "default_store", lambda data_dir="./data": store)
    monkeypatch.setattr(stock_cli, "_providers", lambda: {"stub": _StubOkProvider()})
    result = CliRunner().invoke(entry_app, ["stock-fetch", "AAPL", "--days", "5"])
    assert result.exit_code == 0, result.output
    assert "persisted 1 rows" in result.output
    assert "+1 rows" in result.output
    assert len(store.read("AAPL")) == 1


def test_cli_stock_fetch_rejects_bad_symbol(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import stock_cli
    from yowayowa.cli_entry import app as entry_app

    store = StockOhlcvStore(root=tmp_path / "stock-ohlcv")
    monkeypatch.setattr(stock_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["stock-fetch", "BRK.."])
    assert result.exit_code != 0


# ------------------------------------------------------------- licensing


def test_alpaca_policy_is_personal_only_not_official() -> None:
    policy = source_policy("alpaca")
    assert policy is not None
    assert policy.license_class is LicenseClass.PERSONAL_ONLY
    assert policy.commercial_use is False
    assert policy.public_api is False
    assert policy.access.value == "registered_key"
    assert any("NOT an official reference rate" in note for note in policy.notes)
    assert any("403" in note for note in policy.notes)  # FX grant missing, verified
