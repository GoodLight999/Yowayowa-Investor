# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.client の単体テスト（transport 注入のみ・実ネットワークに触れない）。"""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from yowayowa.hl_exec.client import (
    INFO_URL_MAINNET,
    INFO_URL_TESTNET,
    ExchangeWriteNotImplemented,
    HLError,
    HyperliquidInfoClient,
)


class RecordingTransport:
    """呼び出しを記録し、用意した応答（または例外）を順に返すモック。"""

    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = list(responses)

    def __call__(self, payload: dict[str, Any], url: str, timeout: float) -> Any:
        self.calls.append({"payload": payload, "url": url, "timeout": timeout})
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def make_client(transport: RecordingTransport, **kwargs: Any) -> HyperliquidInfoClient:
    return HyperliquidInfoClient(transport=transport, sleeper=lambda _s: None, **kwargs)


ADDRESS = "0x" + "a" * 40


def test_clearinghouse_state_sends_expected_payload() -> None:
    transport = RecordingTransport([{"marginSummary": {"accountValue": "200.0"}}])
    client = make_client(transport)
    out = client.clearinghouse_state(ADDRESS)
    assert out == {"marginSummary": {"accountValue": "200.0"}}
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["payload"] == {"type": "clearinghouseState", "user": ADDRESS}
    assert call["url"] == INFO_URL_MAINNET


def test_l2_book_sends_expected_payload() -> None:
    transport = RecordingTransport([{"coin": "BTC", "levels": [[], []]}])
    make_client(transport).l2_book("BTC")
    assert transport.calls[0]["payload"] == {"type": "l2Book", "coin": "BTC"}


def test_open_orders_sends_expected_payload() -> None:
    transport = RecordingTransport([[]])
    make_client(transport).open_orders(ADDRESS)
    assert transport.calls[0]["payload"] == {"type": "openOrders", "user": ADDRESS}


def test_meta_sends_expected_payload() -> None:
    transport = RecordingTransport([{"universe": []}])
    make_client(transport).meta()
    assert transport.calls[0]["payload"] == {"type": "meta"}


def test_asset_meta_extracts_index_and_szdecimals() -> None:
    universe = {
        "universe": [
            {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
            {"name": "BTC", "szDecimals": 5, "maxLeverage": 40, "onlyIsolated": False},
        ]
    }
    transport = RecordingTransport([universe])
    meta = make_client(transport).asset_meta("BTC")
    assert meta == {
        "coin": "BTC",
        "asset_index": 1,
        "szDecimals": 5,
        "maxLeverage": 40,
        "onlyIsolated": False,
    }


def test_asset_meta_raises_for_unknown_coin() -> None:
    transport = RecordingTransport([{"universe": [{"name": "ETH", "szDecimals": 4}]}])
    with pytest.raises(HLError, match="not found"):
        make_client(transport).asset_meta("NOPE")


def test_top_of_book_normalizes_levels() -> None:
    book = {"levels": [[{"px": "78010", "sz": "1.0"}], [{"px": "78012", "sz": "2.0"}]]}
    transport = RecordingTransport([book])
    top = make_client(transport).top_of_book("BTC")
    assert top["bid"] == 78010.0
    assert top["ask"] == 78012.0
    assert top["mid"] == 78011.0
    assert top["spread_bps"] == pytest.approx(2.0 / 78011.0 * 10_000.0)


def test_empty_book_raises() -> None:
    transport = RecordingTransport([{"levels": [[], []]}])
    with pytest.raises(HLError, match="empty"):
        make_client(transport).top_of_book("BTC")


def test_invalid_address_is_rejected_before_any_network_call() -> None:
    transport = RecordingTransport([])
    client = make_client(transport)
    with pytest.raises(ValueError, match="invalid address"):
        client.clearinghouse_state("0x123")
    with pytest.raises(ValueError, match="invalid address"):
        client.open_orders("not-an-address")
    assert transport.calls == []


def test_transport_error_retries_then_raises() -> None:
    transport = RecordingTransport([urllib.error.URLError("down")] * 3)
    with pytest.raises(HLError, match="after 3 tries"):
        make_client(transport, retries=3).l2_book("BTC")
    assert len(transport.calls) == 3


def test_http_4xx_fails_fast_without_retry() -> None:
    err = urllib.error.HTTPError(
        INFO_URL_MAINNET, 422, "Unprocessable", None, io.BytesIO(b"bad request")
    )
    transport = RecordingTransport([err])
    with pytest.raises(HLError, match="HTTP 422"):
        make_client(transport, retries=3).l2_book("BTC")
    assert len(transport.calls) == 1


def test_http_5xx_is_retried() -> None:
    err = urllib.error.HTTPError(INFO_URL_MAINNET, 503, "Busy", None, io.BytesIO(b"busy"))
    transport = RecordingTransport([err, err, err])
    with pytest.raises(HLError, match="HTTP 503"):
        make_client(transport, retries=3).l2_book("BTC")
    assert len(transport.calls) == 3


def test_testnet_endpoint_is_physically_separate() -> None:
    transport = RecordingTransport([{}])
    client = HyperliquidInfoClient(INFO_URL_TESTNET, transport=transport, sleeper=lambda _s: None)
    client.meta()
    assert transport.calls[0]["url"] == INFO_URL_TESTNET
    assert INFO_URL_MAINNET != INFO_URL_TESTNET


def test_submit_order_is_not_implemented_by_contract() -> None:
    transport = RecordingTransport([])
    with pytest.raises(ExchangeWriteNotImplemented):
        make_client(transport).submit_order({"a": 0})
    assert transport.calls == []


def test_cancel_orders_is_not_implemented_by_contract() -> None:
    transport = RecordingTransport([])
    with pytest.raises(ExchangeWriteNotImplemented):
        make_client(transport).cancel_orders([{"a": 0, "o": 1}])
    assert transport.calls == []
