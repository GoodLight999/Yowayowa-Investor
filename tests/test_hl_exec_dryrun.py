# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec の「完全ドライラン」結合テスト（発注0件・実ネットワーク0回の証明）。

SIGNAL → INTENT → ORDER の三段を、テスト用 transport だけで最後まで通す:
  1. 公開API（meta / l2Book / clearinghouseState）を読む  ← ここだけが「通信」
  2. リスクゲート（隔離証拠金・同時建玉・名目額・最小額）を通す
  3. 指値IOC ＋ 取引所側SLトリガの束を組み立てる
  4. 三段ログに追記し、ハッシュチェーンが健全であることを確認する

送信は一切行われない。テストは「transport に渡った payload の type が
読み取り専用の許可集合に収まっている」ことを毎回アサートする。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import pytest

from yowayowa.hl_exec.client import (
    INFO_URL_TESTNET,
    ExchangeWriteNotImplemented,
    HyperliquidInfoClient,
)
from yowayowa.hl_exec.journal import ExecutionJournal, VerifyResult, verify_chain
from yowayowa.hl_exec.orders import build_entry_bundle, price_is_valid, size_is_valid
from yowayowa.hl_exec.risk import evaluate_entry_gates

ADDRESS = "0x" + "b" * 40
FIXED_NOW = _dt.datetime(2026, 11, 2, 5, 0, 0, tzinfo=_dt.UTC)

# /info で許可される読み取り専用の type（送信系は 1 つも入らない）
READ_ONLY_INFO_TYPES = frozenset({"meta", "l2Book", "clearinghouseState", "openOrders"})


@pytest.fixture(autouse=True)
def _forbid_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """実ネットワークを物理的に塞ぐ（このモジュールの全テストに自動適用）。

    transport を注入しているので本来 urlopen には到達しない。到達したら
    即座に落とすことで「テスト中に実通信が0回」を実行時に証明する。
    """

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("real network access attempted during hl_exec dry-run tests")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    monkeypatch.setattr("socket.socket.connect", _boom)


class InfoOnlyTransport:
    """読み取り専用 /info のモック。許可外の type が来たら即座に失敗させる。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any], url: str, timeout: float) -> Any:
        assert payload.get("type") in READ_ONLY_INFO_TYPES, f"non read-only info type: {payload}"
        self.calls.append({"payload": payload, "url": url, "timeout": timeout})
        kind = payload["type"]
        if kind == "meta":
            return {
                "universe": [
                    {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
                    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
                ]
            }
        if kind == "l2Book":
            return {
                "coin": payload["coin"],
                "levels": [
                    [{"px": "78010", "sz": "3.5"}],
                    [{"px": "78014", "sz": "2.5"}],
                ],
            }
        if kind == "clearinghouseState":
            return {
                "marginSummary": {"accountValue": "200.0"},
                "withdrawable": "200.0",
                "assetPositions": [],
            }
        return []


def _build_client(transport: InfoOnlyTransport) -> HyperliquidInfoClient:
    return HyperliquidInfoClient(INFO_URL_TESTNET, transport=transport, sleeper=lambda _s: None)


def test_full_dry_run_pipeline_reaches_order_stage_without_sending(tmp_path: Path) -> None:
    transport = InfoOnlyTransport()
    client = _build_client(transport)

    # --- 1. 読み取り（テスト資金の残高照合と銘柄メタ） ---------------------
    state = client.clearinghouse_state(ADDRESS)
    assert state["marginSummary"]["accountValue"] == "200.0"
    meta = client.asset_meta("BTC")
    assert meta["szDecimals"] == 5
    assert meta["asset_index"] == 1
    top = client.top_of_book("BTC")
    assert top["bid"] == 78010.0 and top["ask"] == 78014.0

    # --- 2. リスクゲート -------------------------------------------------
    isolated_margin = 20.0  # 設計書 §2.4: 1建玉あたり上限
    size = 0.0007  # BTC szDecimals=5 の刻みに乗る数量
    entry_px = top["mid"]  # 78012.0（IOC指値の基準。丸めは orders 側で強制）
    notional = entry_px * size
    gates = evaluate_entry_gates(
        isolated_margin_usdc=isolated_margin,
        open_position_count=0,
        notional_usdc=notional,
        equity_usdc=200.0,
    )
    assert all(g.ok for g in gates), [g.reason for g in gates if not g.ok]

    # --- 3. 注文組立（生成のみ・送信なし） -------------------------------
    sl_trigger = round(entry_px * 0.94, 2)  # 1.8 x ATR14 相当の想定（約-6%）
    bundle = build_entry_bundle(
        asset_index=meta["asset_index"],
        coin="BTC",
        is_buy=True,
        size=size,
        price=entry_px,
        sz_decimals=meta["szDecimals"],
        sl_trigger_price=sl_trigger,
        tp_trigger_price=round(entry_px * 1.08, 2),
    )
    assert price_is_valid(bundle["entry"]["price"], sz_decimals=5)
    assert size_is_valid(bundle["entry"]["size"], sz_decimals=5)
    assert bundle["entry"]["wire"]["b"] is True
    assert bundle["sl"]["wire"]["r"] is True  # SLは reduce-only
    assert bundle["sl"]["wire"]["b"] is False  # 買い建てのSLは売り
    assert 10.0 <= bundle["entry"]["notional_usdc"] <= 60.0

    # --- 4. 三段ログ -----------------------------------------------------
    journal = ExecutionJournal(tmp_path / "hl_testnet_20261102.jsonl", clock=lambda: FIXED_NOW)
    decision = journal.log_decision(
        {
            "decision": "BUY",
            "coin": "BTC",
            "confirmed_bar_ohlcv": {"o": 77900.0, "h": 78200.0, "l": 77750.0, "c": 78012.0},
            "sma50": 76500.0,
            "ema10": 77900.0,
            "ema20": 77650.0,
            "atr14": 1200.0,
            "recommended_size": size,
        }
    )
    intent = journal.log_intent(
        decision.event_id,
        {
            "coin": "BTC",
            "side": "buy",
            "size": bundle["entry"]["size"],
            "limit_px": bundle["entry"]["price"],
            "tif": bundle["entry"]["tif"],
            "sl_trigger_px": bundle["sl"]["trigger_price"],
            "tp_trigger_px": bundle["tp"]["trigger_price"],
            "isolated_margin_usdc": isolated_margin,
            "notional_usdc": bundle["entry"]["notional_usdc"],
            "cloid": None,
        },
    )
    order = journal.log_order(
        intent.event_id,
        {
            "dry_run": True,
            "sent": False,
            "wire": bundle["entry"]["wire"],
            "sl_wire": bundle["sl"]["wire"],
            "tp_wire": bundle["tp"]["wire"],
            "response": None,
        },
    )
    assert order.parent_event_id == intent.event_id
    assert intent.parent_event_id == decision.event_id
    assert order.payload["sent"] is False

    check = verify_chain(journal._path)
    assert isinstance(check, VerifyResult)
    assert check.ok is True
    assert check.line_count == 3

    # --- 送信0件の証明 ---------------------------------------------------
    assert [c["payload"]["type"] for c in transport.calls] == [
        "clearinghouseState",
        "meta",
        "l2Book",
    ]
    assert all(c["url"] == INFO_URL_TESTNET for c in transport.calls)
    with pytest.raises(ExchangeWriteNotImplemented):
        client.submit_order(bundle["entry"]["wire"])
    # submit_order は例外を投げるだけで、transport を1回も増やさない
    assert len(transport.calls) == 3
