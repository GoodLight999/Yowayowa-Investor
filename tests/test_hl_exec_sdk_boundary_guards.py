# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""SDK境界3契約（cloid変換・不明注文の非再送照合）を本物SDKで検証する。

ネットワークはモジュール全体で遮断する（socket.connect/create_connection を
拒否し、実行時は ``--disable-socket`` も併用する）。本物の
``hyperliquid.exchange.Exchange`` の order/bulk_orders/wire 変換を通し、
署名関数と送信（``_post_action``）だけをテスト側で置き換える。
実注文・取消・鍵生成・実ネットワーク送信は行わない。
"""

from __future__ import annotations

import ast
import inspect
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from yowayowa.hl_exec.client import TESTNET_API_URL, HyperliquidExchangeClient
from yowayowa.hl_exec.guards import (
    CloidContractError,
    OrderReconciliation,
    UnknownOrderStateError,
    canonicalize_cloid,
    cloid_to_str,
    reconcile_unknown_order,
    send_order_once,
    to_sdk_cloid,
)

sdk_exchange = pytest.importorskip("hyperliquid.exchange")
sdk_types = pytest.importorskip("hyperliquid.utils.types")

ADDRESS = "0x" + "a" * 40
CANONICAL_CLOID = "0x" + "ab" * 16
UPPER_CLOID = "0x" + "AB" * 16


class NetworkAccessDenied(RuntimeError):
    """テスト中の実ネットワーク到達を即時失敗させる。"""


def _deny_network(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkAccessDenied("network access is denied during SDK boundary tests")


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)


def test_network_access_is_blocked_in_this_run() -> None:
    """遮断が実際に効いていることの positive control。"""
    with pytest.raises((NetworkAccessDenied, OSError)):
        socket.create_connection(("api.hyperliquid-testnet.xyz", 443), timeout=0.01)


# --------------------------------------------------------------- cloid 契約
def test_cloid_roundtrip_normalizes_hex_and_uses_official_type() -> None:
    sdk_cloid = to_sdk_cloid(UPPER_CLOID)
    assert isinstance(sdk_cloid, sdk_types.Cloid)
    assert str(sdk_cloid) == CANONICAL_CLOID
    assert cloid_to_str(sdk_cloid) == CANONICAL_CLOID
    assert canonicalize_cloid(UPPER_CLOID) == CANONICAL_CLOID


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "0x",
        "0x" + "a" * 31,
        "0x" + "a" * 33,
        "0x" + "g" * 32,
        "0X" + "a" * 32,
        " 0x" + "a" * 32,
        "0x" + "a" * 32 + "\n",
        "  " + CANONICAL_CLOID,
        None,
        123,
        True,
        b"0x" + b"a" * 32,
        sdk_types.Cloid(CANONICAL_CLOID),
    ],
)
def test_cloid_rejects_invalid_input_fail_closed(bad: Any) -> None:
    with pytest.raises(CloidContractError):
        to_sdk_cloid(bad)


def test_cloid_to_str_rejects_raw_strings() -> None:
    with pytest.raises(CloidContractError):
        cloid_to_str(CANONICAL_CLOID)


# ------------------------------------------------ 不明注文: 3値と非再送（fake）
class _FakeExchange:
    base_url = TESTNET_API_URL

    def __init__(self, result: Any = None, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result
        self.error = error

    def order(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.error is not None:
            raise self.error
        return self.result

    def cancel_by_cloid(self, coin: str, cloid: Any) -> dict[str, Any]:
        self.calls.append({"cancel_by_cloid": (coin, cloid)})
        return {"status": "ok"}


def _place(exchange: Any, cloid: str = UPPER_CLOID, **kwargs: Any) -> Any:
    return send_order_once(
        exchange,
        coin="BTC",
        is_buy=False,
        sz=0.0002,
        px=75000.0,
        order_type={"limit": {"tif": "Ioc"}},
        cloid=cloid,
        **kwargs,
    )


def test_timeout_is_reconciled_confirmed_and_never_resubmitted() -> None:
    exchange = _FakeExchange(error=TimeoutError("after possible acceptance"))
    attempt = _place(
        exchange,
        open_orders=lambda: [{"oid": 7, "cloid": CANONICAL_CLOID}],
        order_status=lambda: {"status": "unknownCloid"},
    )
    assert attempt.status is OrderReconciliation.CONFIRMED
    assert attempt.reconciled is True
    assert len(exchange.calls) == 1


def test_timeout_with_definitive_not_found_is_absent_without_resubmit() -> None:
    exchange = _FakeExchange(error=ConnectionError("reset"))
    attempt = _place(
        exchange,
        open_orders=lambda: [],
        order_status=lambda: {"status": "unknownCloid"},
    )
    assert attempt.status is OrderReconciliation.ABSENT
    assert len(exchange.calls) == 1


def test_timeout_with_failed_lookups_raises_unresolved_without_resubmit() -> None:
    exchange = _FakeExchange(error=TimeoutError("after possible acceptance"))

    def broken_lookup() -> Any:
        raise ConnectionError("lookup unavailable")

    with pytest.raises(UnknownOrderStateError) as raised:
        _place(exchange, open_orders=broken_lookup, order_status=broken_lookup)
    assert raised.value.attempt is not None
    assert raised.value.attempt.status is OrderReconciliation.UNRESOLVED
    assert len(exchange.calls) == 1


def test_unrecognized_submit_and_lookup_is_unresolved_not_absent() -> None:
    exchange = _FakeExchange(result={"status": "ok", "response": {"data": {}}})
    with pytest.raises(UnknownOrderStateError):
        _place(exchange, open_orders=lambda: [], order_status=lambda: {"status": "weird"})
    assert len(exchange.calls) == 1


def test_exchange_error_status_is_absent_and_skips_lookup() -> None:
    exchange = _FakeExchange(
        result={"status": "ok", "response": {"data": {"statuses": [{"error": "bad cloid"}]}}}
    )

    def must_not_be_called() -> Any:
        raise AssertionError("lookup must not run for a definitive rejection")

    attempt = _place(exchange, open_orders=must_not_be_called, order_status=must_not_be_called)
    assert attempt.status is OrderReconciliation.ABSENT
    assert len(exchange.calls) == 1


def test_cloid_passed_to_transport_is_official_type_not_str() -> None:
    exchange = _FakeExchange(
        result={
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": 1}}]}},
        }
    )
    _place(exchange)
    sent = exchange.calls[0]["kwargs"]["cloid"]
    assert isinstance(sent, sdk_types.Cloid)
    assert str(sent) == CANONICAL_CLOID


def test_reconcile_matches_oid_even_when_cloid_is_absent() -> None:
    attempt = reconcile_unknown_order(
        cloid=CANONICAL_CLOID,
        oid=42,
        open_orders=lambda: [{"oid": 42}],
        order_status=lambda: {"status": "unknownOid"},
    )
    assert attempt.status is OrderReconciliation.CONFIRMED


def test_invalid_cloid_never_reaches_transport() -> None:
    exchange = _FakeExchange(result={"status": "ok"})
    with pytest.raises(CloidContractError):
        _place(exchange, cloid="0x" + "z" * 32)
    assert exchange.calls == []


def test_send_order_once_has_no_retry_loop_and_single_transport_call() -> None:
    source_path = Path(inspect.getsourcefile(send_order_once) or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "send_order_once"
    )
    order_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "order"
    ]
    assert len(order_calls) == 1
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(function))
    cloid_kwarg = next(kw for kw in order_calls[0].keywords if kw.arg == "cloid")
    assert isinstance(cloid_kwarg.value, ast.Name)
    assert cloid_kwarg.value.id == "sdk_cloid"


# ------------------------------------------------- 公式SDKのwireまで通す試験
def _real_exchange(monkeypatch: pytest.MonkeyPatch) -> Any:
    exchange = sdk_exchange.Exchange.__new__(sdk_exchange.Exchange)
    exchange.base_url = TESTNET_API_URL
    exchange.info = SimpleNamespace(name_to_asset=lambda _coin: 0)
    exchange.wallet = None
    exchange.vault_address = None
    exchange.expires_after = None
    monkeypatch.setattr(sdk_exchange, "sign_l1_action", lambda *args, **kwargs: {})
    return exchange


def test_real_sdk_wire_receives_normalized_cloid(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = _real_exchange(monkeypatch)
    actions: list[dict[str, Any]] = []

    def capture(action: dict[str, Any], _signature: Any, _nonce: int) -> dict[str, str]:
        actions.append(action)
        return {"status": "ok"}

    monkeypatch.setattr(exchange, "_post_action", capture)
    client = HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange)
    client.order("BTC", False, 0.0002, 75000.0, {"limit": {"tif": "Ioc"}}, cloid=UPPER_CLOID)

    assert len(actions) == 1
    assert actions[0]["orders"][0]["c"] == CANONICAL_CLOID


def test_real_sdk_cancel_by_cloid_receives_official_cloid(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = _real_exchange(monkeypatch)
    actions: list[dict[str, Any]] = []

    def capture(action: dict[str, Any], _signature: Any, _nonce: int) -> dict[str, str]:
        actions.append(action)
        return {"status": "ok"}

    monkeypatch.setattr(exchange, "_post_action", capture)
    client = HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange)
    client.cancel_by_cloid("BTC", UPPER_CLOID)

    assert actions[0]["cancels"][0]["cloid"] == CANONICAL_CLOID


def test_real_sdk_timeout_is_reconciled_without_second_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _real_exchange(monkeypatch)
    sent: list[dict[str, Any]] = []

    def timeout(action: dict[str, Any], _signature: Any, _nonce: int) -> Any:
        sent.append(action)
        raise TimeoutError("simulated transport timeout after signing")

    monkeypatch.setattr(exchange, "_post_action", timeout)
    attempt = send_order_once(
        exchange,
        coin="BTC",
        is_buy=False,
        sz=0.0002,
        px=75000.0,
        order_type={"limit": {"tif": "Ioc"}},
        cloid=UPPER_CLOID,
        open_orders=lambda: [],
        order_status=lambda: {"status": "unknownCloid"},
    )
    assert attempt.status is OrderReconciliation.ABSENT
    assert len(sent) == 1


# ---------------------------------------------------- 既存adapterの是正確認
def test_client_accepts_normalized_trailing_slash_and_443() -> None:
    exchange = _FakeExchange(result={"status": "ok"})
    client = HyperliquidExchangeClient(
        ADDRESS, "unused", base_url=TESTNET_API_URL + ":443/", exchange=exchange
    )
    assert client.base_url == TESTNET_API_URL


def test_client_rejects_injected_exchange_without_base_url() -> None:
    class Bare:
        def order(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - not reached
            raise AssertionError("must not be used")

    with pytest.raises(ValueError, match="no base_url"):
        HyperliquidExchangeClient(ADDRESS, "unused", exchange=Bare())


def test_client_rejects_injected_mainnet_exchange() -> None:
    exchange = _FakeExchange(result={"status": "ok"})
    exchange.base_url = "https://api.hyperliquid.xyz"
    with pytest.raises(ValueError, match="injected exchange"):
        HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange)


def test_client_normalizes_cloid_before_transport() -> None:
    exchange = _FakeExchange(
        result={
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": 1}}]}},
        }
    )
    client = HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange)
    client.order("BTC", False, 0.0002, 75000.0, {"limit": {"tif": "Ioc"}}, cloid=UPPER_CLOID)
    sent = exchange.calls[0]["kwargs"]["cloid"]
    assert isinstance(sent, sdk_types.Cloid)
    assert str(sent) == CANONICAL_CLOID


def test_client_rejects_invalid_cloid_before_transport() -> None:
    exchange = _FakeExchange(result={"status": "ok"})
    client = HyperliquidExchangeClient(ADDRESS, "unused", exchange=exchange)
    with pytest.raises(CloidContractError):
        client.order(
            "BTC", False, 0.0002, 75000.0, {"limit": {"tif": "Ioc"}}, cloid="0x" + "g" * 32
        )
    assert exchange.calls == []
