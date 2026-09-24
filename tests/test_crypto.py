"""Crypto daily-OHLCV tests (P4-E phase 1).

Normal, failing, and boundary paths per the task rules:
- normal: both providers return complete provenance records;
- failure: HTTP 40x -> LookupError, transport error -> TransportError,
  missing days are skipped (never zero-filled);
- store: append/read round-trip, idempotent re-fetch, corrupt-line safety,
  every persisted JSON line valid with full provenance;
- CLI + /v1/crypto routes incl. public-mode 404 fail-closed;
- licensing registry entries for coingecko/binance (PERSONAL_ONLY).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from yowayowa.config import get_settings
from yowayowa.crypto_acquisition import CryptoOhlcvStore, default_store, fetch_crypto_ohlcv
from yowayowa.crypto_models import (
    BINANCE_SYMBOLS,
    COINGECKO_COIN_IDS,
    CryptoOhlcvRecord,
    normalize_crypto_symbol,
)
from yowayowa.domain import LicenseClass
from yowayowa.providers.binance import BinanceKlinesProvider, BinanceTransportError
from yowayowa.providers.coingecko import CoinGeckoOhlcProvider, CoinGeckoTransportError
from yowayowa.services.licensing import source_policy

# ------------------------------------------------------------------- fixtures


def _canned(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "https://stub.invalid"),
    )


def _days(count: int, start: datetime | None = None) -> list[datetime]:
    start = start or datetime(2026, 9, 20, tzinfo=UTC)
    return [start + timedelta(days=offset) for offset in range(count)]


def _coingecko_payload(days: int = 6) -> list[list[float]]:
    rows = []
    for index, ts in enumerate(_days(days)):
        rows.append(
            [
                ts.timestamp() * 1000.0,
                100.0 + index,
                102.0 + index,
                99.0 + index,
                101.0 + index,
            ]
        )
    return rows


def _binance_payload(days: int = 6) -> list[list[float]]:
    rows = []
    for index, ts in enumerate(_days(days)):
        rows.append(
            [
                ts.timestamp() * 1000.0,
                200.0 + index,
                205.0 + index,
                198.0 + index,
                203.0 + index,
                12.5 + index,
                (ts + timedelta(days=1)).timestamp() * 1000.0,
                2540.0 + index * 20.0,
                0,
                0,
                0,
            ]
        )
    forming = _days(days)[-1] + timedelta(days=1)
    rows.append(  # currently-forming bar, must be dropped
        [
            forming.timestamp() * 1000.0,
            210.0,
            211.0,
            209.0,
            210.5,
            1.0,
            (forming + timedelta(days=1)).timestamp() * 1000.0,
            210.5,
            0,
            0,
            0,
        ]
    )
    return rows


def _stub_provider(provider_cls: type, payload: Any, *, status_code: int = 200):
    settings = get_settings()
    provider = provider_cls(settings)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    # Keep the provider's own client so its base_url wins; only the transport
    # is swapped. A client without base_url breaks relative paths because
    # httpx (0.28) does not merge relative URLs against an empty base.
    provider.client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        lambda _request: _canned(body, status_code)
    )
    return provider


def _record(symbol: str, provider: str, as_of: datetime, close: float) -> CryptoOhlcvRecord:
    return CryptoOhlcvRecord(
        symbol=symbol,
        provider=provider,
        currency="USD" if provider == "coingecko" else "USDT",
        source_url=f"https://stub.invalid/{provider}",
        license_class=LicenseClass.PERSONAL_ONLY.value,
        retrieved_at=datetime.now(UTC),
        as_of=as_of,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
    )


# ------------------------------------------------------------- normalization


def test_normalize_crypto_symbol_accepts_supported() -> None:
    assert normalize_crypto_symbol("btc") == "BTC"
    assert normalize_crypto_symbol(" ETH ") == "ETH"


def test_normalize_crypto_symbol_rejects_unsupported() -> None:
    from yowayowa.symbols import InputValidationError

    with pytest.raises(InputValidationError):
        normalize_crypto_symbol("SOL")
    with pytest.raises(InputValidationError):
        normalize_crypto_symbol("BTCUSD")
    with pytest.raises(InputValidationError):
        normalize_crypto_symbol("")


def test_symbol_maps_match_supported_assets() -> None:
    assert set(COINGECKO_COIN_IDS) == set(BINANCE_SYMBOLS) == {"BTC", "ETH"}


# ------------------------------------------------------------- coingecko


def test_coingecko_ohlcv_returns_records_with_provenance() -> None:
    provider = _stub_provider(CoinGeckoOhlcProvider, _coingecko_payload())
    records = provider.ohlcv("BTC", days=30)
    assert len(records) == 6
    first = records[0]
    assert isinstance(first, CryptoOhlcvRecord)
    assert first.provider == "coingecko"
    assert first.license_class == LicenseClass.PERSONAL_ONLY.value
    assert first.source_url == "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
    assert first.notes and "NOT an official reference rate" in first.notes[0]
    assert first.currency == "USD"
    assert first.close == 101.0
    assert first.volume is None  # CoinGecko public OHLC has no volume: stays None


def test_coingecko_ohlcv_skips_missing_and_zero_bars() -> None:
    payload = _coingecko_payload()
    payload[2] = [payload[2][0], 0, 0, 0, 0]  # one day's bar is all zeros
    payload.append(["not-a-ts", "x", "y", "z", None])  # type: ignore[list-item]  # malformed row
    provider = _stub_provider(CoinGeckoOhlcProvider, payload)
    records = provider.ohlcv("BTC", days=30)
    assert len(records) == 5  # zero bar and malformed row dropped, no zero-fill
    assert all(record.open > 0 for record in records)


def test_coingecko_retains_gap_as_gap() -> None:
    payload = _coingecko_payload()
    del payload[3]  # one whole day absent from the source
    provider = _stub_provider(CoinGeckoOhlcProvider, payload)
    records = provider.ohlcv("BTC", days=30)
    assert len(records) == 5  # the gap stays a gap: no zero/fwd fill


def test_coingecko_http_404_is_lookup_error() -> None:
    provider = _stub_provider(CoinGeckoOhlcProvider, {"error": "nf"}, status_code=404)
    with pytest.raises(LookupError):
        provider.ohlcv("BTC", days=30)


def test_coingecko_http_500_is_transport_error() -> None:
    provider = _stub_provider(CoinGeckoOhlcProvider, b"", status_code=500)
    with pytest.raises(CoinGeckoTransportError):
        provider.ohlcv("BTC", days=30)


def test_coingecko_empty_payload_is_lookup_error() -> None:
    provider = _stub_provider(CoinGeckoOhlcProvider, [])
    with pytest.raises(LookupError):
        provider.ohlcv("ETH", days=30)


# ------------------------------------------------------------- binance


def test_binance_klines_returns_records_drops_forming_bar() -> None:
    provider = _stub_provider(BinanceKlinesProvider, _binance_payload())
    records = provider.ohlcv("BTC", days=30)
    assert len(records) == 6
    first = records[0]
    assert first.provider == "binance"
    assert first.currency == "USDT"
    assert first.volume == 12.5
    assert first.quote_volume == 2540.0
    assert first.license_class == LicenseClass.PERSONAL_ONLY.value
    assert BINANCE_SYMBOLS["BTC"] == "BTCUSDT"


def test_binance_zero_bar_is_skipped() -> None:
    payload = _binance_payload()
    payload[2] = [payload[2][0], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    provider = _stub_provider(BinanceKlinesProvider, payload)
    records = provider.ohlcv("BTC", days=30)
    assert len(records) == 5
    assert all(record.close > 0 for record in records)


def test_binance_http_400_is_lookup_error() -> None:
    provider = _stub_provider(BinanceKlinesProvider, {"code": -1121}, status_code=400)
    with pytest.raises(LookupError):
        provider.ohlcv("BTC", days=30)


def test_binance_connection_error_is_transport_error() -> None:
    settings = get_settings()
    provider = BinanceKlinesProvider(settings)
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: (_ for _ in ()).throw(httpx.ConnectError("boom"))
        )
    )
    with pytest.raises(BinanceTransportError):
        provider.ohlcv("BTC", days=30)


# ------------------------------------------------------------- public mode


def test_crypto_providers_refuse_public_mode() -> None:
    from yowayowa.config import Settings
    from yowayowa.providers.base import ProviderPolicyError

    settings = Settings(mode="public", api_token="test-public-token")
    assert settings.mode == "public"
    with pytest.raises(ProviderPolicyError):
        CoinGeckoOhlcProvider(settings)
    with pytest.raises(ProviderPolicyError):
        BinanceKlinesProvider(settings)


# ------------------------------------------------------------- store


def test_store_append_read_roundtrip(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    day = _days(1)[0]
    written = store.append(
        "BTC",
        [
            _record("BTC", "coingecko", day, 101.0),
            _record("BTC", "binance", day, 203.0),
        ],
    )
    assert written == 2
    rows = store.read("BTC")
    assert len(rows) == 2
    by_provider = {row["provider"]: row for row in rows}
    assert by_provider["coingecko"]["close"] == 101.0
    assert by_provider["binance"]["currency"] == "USDT"
    # both sources coexist per-row for the same day: never merged
    assert by_provider["coingecko"]["as_of"] == by_provider["binance"]["as_of"]


def test_store_append_is_idempotent_per_source_day(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    records = [_record("BTC", "binance", _days(1)[0], 203.0)]
    assert store.append("BTC", records) == 1
    assert store.append("BTC", list(records)) == 0
    assert len(store.read("BTC")) == 1


def test_store_missing_provider_rows_stay_missing(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    store.append("ETH", [_record("ETH", "coingecko", _days(1)[0], 3000.0)])
    assert store.read("ETH", provider="binance") == []  # absent rows stay absent


def test_store_skips_corrupt_lines_but_reads_valid(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    store.append("BTC", [_record("BTC", "coingecko", _days(1)[0], 101.0)])
    path = tmp_path / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{broken json line\n")
    rows = store.read("BTC")
    assert len(rows) == 1


def test_store_every_line_is_valid_json_with_provenance(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    day = _days(1)[0]
    store.append(
        "BTC",
        [
            _record("BTC", "coingecko", day, 101.0),
            _record("BTC", "binance", day, 203.0),
        ],
    )
    path = tmp_path / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl"
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


def test_default_store_path_is_fixed() -> None:
    store = default_store("/tmp/yw-test-data")
    assert str(store.root) == "/tmp/yw-test-data/crypto-ohlcv"


# ------------------------------------------------------------- orchestration


def test_fetch_crypto_ohlcv_isolates_source_failures(tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    day = _days(1)[0]

    class _Ok:
        def ohlcv(self, symbol: str, *, days: int = 30):
            return [_record(symbol, "binance", day, 203.0)]

    class _Fail:
        def ohlcv(self, symbol: str, *, days: int = 30):
            raise CoinGeckoTransportError("down")

    summary = fetch_crypto_ohlcv({"binance": _Ok(), "coingecko": _Fail()}, store, ["BTC"])
    assert summary["BTC"]["binance"] == {"persisted": 1, "error": None}
    assert summary["BTC"]["coingecko"]["persisted"] == 0
    assert "down" in summary["BTC"]["coingecko"]["error"]
    assert len(store.read("BTC")) == 1


def test_fetch_crypto_ohlcv_rejects_unsupported_symbol(tmp_path) -> None:
    from yowayowa.symbols import InputValidationError

    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    with pytest.raises(InputValidationError):  # type: ignore[arg-type]
        fetch_crypto_ohlcv({"binance": None}, store, ["SOL"])  # type: ignore[dict-item]


# ------------------------------------------------------------- API routes


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(
        "yowayowa.api.crypto_routes.default_store",
        lambda data_dir="./data": CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv"),
    )
    monkeypatch.setattr("yowayowa.api.crypto_routes.get_settings", lambda: settings)
    from yowayowa.api.app import app

    return TestClient(app)


def test_api_crypto_ohlcv_serves_persisted_rows(api_client: TestClient, tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    store.append("BTC", [_record("BTC", "binance", _days(1)[0], 203.0)])
    response = api_client.get("/v1/crypto/ohlcv/BTC")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["provider"] == "binance"
    assert {"as_of", "open", "high", "low", "close", "provider", "source_url"} <= set(rows[0])


def test_api_crypto_ohlcv_provider_filter_and_csv(api_client: TestClient, tmp_path) -> None:
    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    day = _days(1)[0]
    store.append(
        "BTC",
        [
            _record("BTC", "coingecko", day, 101.0),
            _record("BTC", "binance", day, 203.0),
        ],
    )
    rows = api_client.get("/v1/crypto/ohlcv/BTC", params={"provider": "binance"}).json()
    assert all(row["provider"] == "binance" for row in rows)
    csv_response = api_client.get(
        "/v1/crypto/ohlcv/BTC", params={"provider": "binance", "format": "csv"}
    )
    assert csv_response.status_code == 200
    assert "provider" in csv_response.text and "close" in csv_response.text
    assert csv_response.headers["content-type"].startswith("text/csv")


def test_api_crypto_ohlcv_404_when_nothing_persisted(api_client: TestClient) -> None:
    assert api_client.get("/v1/crypto/ohlcv/BTC").status_code == 404


def test_api_crypto_ohlcv_422_on_bad_symbol(api_client: TestClient) -> None:
    assert api_client.get("/v1/crypto/ohlcv/SOL").status_code == 422


def test_api_crypto_ohlcv_404_in_public_mode(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from yowayowa.api.app import app
    from yowayowa.config import Settings

    public_settings = Settings(mode="public", api_token="test-public-token")
    assert public_settings.mode == "public"
    monkeypatch.setattr("yowayowa.api.crypto_routes.get_settings", lambda: public_settings)
    monkeypatch.setattr(
        "yowayowa.api.crypto_routes.default_store",
        lambda data_dir="./data": CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv"),
    )
    response = TestClient(app).get("/v1/crypto/ohlcv/BTC")
    assert response.status_code == 404


# ------------------------------------------------------------- CLI


def test_cli_crypto_commands_registered() -> None:
    from typer.testing import CliRunner

    from yowayowa.cli_entry import app as entry_app

    result = CliRunner().invoke(entry_app, ["--help"])
    assert result.exit_code == 0
    assert "crypto-fetch" in result.output
    assert "crypto-ohlcv" in result.output


def test_cli_crypto_ohlcv_prints_table(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import crypto_cli
    from yowayowa.cli_entry import app as entry_app

    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    day = _days(1)[0]
    store.append(
        "BTC",
        [
            _record("BTC", "coingecko", day, 101.0),
            _record("BTC", "binance", day, 203.0),
        ],
    )
    monkeypatch.setattr(crypto_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["crypto-ohlcv", "BTC"])
    assert result.exit_code == 0, result.output
    assert "coingecko" in result.output and "binance" in result.output


def test_cli_crypto_ohlcv_exit_1_when_empty(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import crypto_cli
    from yowayowa.cli_entry import app as entry_app

    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    monkeypatch.setattr(crypto_cli, "default_store", lambda data_dir="./data": store)
    result = CliRunner().invoke(entry_app, ["crypto-ohlcv", "BTC"])
    assert result.exit_code == 1


def test_cli_crypto_fetch_persists(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from yowayowa import crypto_cli
    from yowayowa.cli_entry import app as entry_app

    store = CryptoOhlcvStore(root=tmp_path / "crypto-ohlcv")
    monkeypatch.setattr(crypto_cli, "default_store", lambda data_dir="./data": store)
    monkeypatch.setattr(crypto_cli, "_providers", lambda: {"stub": _StubOkProvider()})
    result = CliRunner().invoke(entry_app, ["crypto-fetch", "BTC"])
    assert result.exit_code == 0, result.output
    assert "persisted 1 rows" in result.output
    assert len(store.read("BTC")) == 1


class _StubOkProvider:
    def ohlcv(self, symbol: str, *, days: int = 30):
        return [_record(symbol, "stub", _days(1)[0], 123.0)]


# ------------------------------------------------------------- licensing


def test_coingecko_policy_is_personal_only_not_official() -> None:
    policy = source_policy("coingecko")
    assert policy is not None
    assert policy.license_class is LicenseClass.PERSONAL_ONLY
    assert policy.commercial_use is False
    assert policy.public_api is False
    assert any("NOT an official reference rate" in note for note in policy.notes)


def test_binance_policy_is_personal_only_not_official() -> None:
    policy = source_policy("binance")
    assert policy is not None
    assert policy.license_class is LicenseClass.PERSONAL_ONLY
    assert policy.commercial_use is False
    assert policy.public_api is False
    assert any("NOT an official reference rate" in note for note in policy.notes)
