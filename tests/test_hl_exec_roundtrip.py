# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.orders.run_order_roundtrip の単体・結合テスト（モック注入で実通信0回）。

受入条件の機械検証:
  1. BTC 買い指値（サイズ 0.0002・価格=最良買値×0.99切捨て）で oid 受領
  2. 発注直後の照会で 1件・oid一致
  3. 取消を実行して success、取消後未約定0件
  4. 前後の clearinghouseState で accountValue 維持・建玉0・新規約定0
  5. 三段ログ（SIGNAL/INTENT/ORDER）のハッシュチェーン完全性
  6. キルスイッチの疎通（発注前ロック取得、正常終了解放）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yowayowa.hl_exec.client import (
    HyperliquidExchangeClient,
    HyperliquidInfoClient,
)
from yowayowa.hl_exec.journal import verify_chain
from yowayowa.hl_exec.killswitch import KillSwitch
from yowayowa.hl_exec.orders import run_order_roundtrip

TEST_ACCT = "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"
FAKE_KEY = "0x" + "1" * 64


class MockExchange:
    """Hyperliquid 公式 SDK Exchange のモック。"""

    base_url = "https://api.hyperliquid-testnet.xyz"

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        self.next_oid = 64295258088

    def order(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        px: float,
        order_type: dict[str, Any],
        reduce_only: bool = False,
        cloid: str | None = None,
    ) -> dict[str, Any]:
        self.orders.append(
            {
                "coin": coin,
                "is_buy": is_buy,
                "sz": sz,
                "px": px,
                "order_type": order_type,
                "cloid": cloid,
            }
        )
        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [{"resting": {"oid": self.next_oid}}],
                },
            },
        }

    def cancel(self, coin: str, oid: int) -> dict[str, Any]:
        self.cancels.append({"coin": coin, "oid": oid})
        return {
            "status": "ok",
            "response": {
                "type": "cancel",
                "data": {"statuses": ["success"]},
            },
        }


class MockInfoTransport:
    """HyperliquidInfoClient 用の読み取りモック。"""

    def __init__(self, oid: int = 64295258088) -> None:
        self.oid = oid
        self.open_orders_query_count = 0

    def __call__(self, payload: dict[str, Any], _url: str, _timeout: float) -> Any:
        typ = payload.get("type")
        if typ == "meta":
            return {
                "universe": [
                    {"name": "ETH", "szDecimals": 4, "maxLeverage": 50},
                    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
                ]
            }
        if typ == "l2Book":
            return {
                "coin": "BTC",
                "levels": [
                    [{"px": "77541.0", "sz": "1.0"}],
                    [{"px": "77695.0", "sz": "1.0"}],
                ],
            }
        if typ == "clearinghouseState":
            return {
                "marginSummary": {"accountValue": "100.0"},
                "assetPositions": [],
            }
        if typ == "userFills":
            return []
        if typ == "openOrders":
            self.open_orders_query_count += 1
            if self.open_orders_query_count == 1:
                # 照会1回目: 発注直後（1件・oid一致）
                return [
                    {
                        "oid": self.oid,
                        "coin": "BTC",
                        "side": "B",
                        "sz": "0.0002",
                        "limitPx": "76765.0",
                    }
                ]
            # 照会2回目: 取消後（0件）
            return []
        raise ValueError(f"Unexpected mock query type: {typ}")


def test_roundtrip_full_lifecycle(tmp_path: Path) -> None:
    mock_ex = MockExchange()
    mock_transport = MockInfoTransport(oid=mock_ex.next_oid)

    ks_file = tmp_path / "HL_KILL_SWITCH"
    j_file = tmp_path / "journal.jsonl"
    raw_file = tmp_path / "roundtrip_raw.jsonl"

    info_client = HyperliquidInfoClient(
        "https://api.hyperliquid-testnet.xyz/info",
        transport=mock_transport,
        sleeper=lambda _s: None,
    )
    ex_client = HyperliquidExchangeClient(
        TEST_ACCT,
        FAKE_KEY,
        base_url="https://api.hyperliquid-testnet.xyz",
        exchange=mock_ex,
        killswitch=KillSwitch(ks_file),
    )

    res = run_order_roundtrip(
        account_address=TEST_ACCT,
        agent_key_path=FAKE_KEY,
        coin="BTC",
        size=0.0002,
        price_ratio=0.99,
        journal_path=j_file,
        raw_log_path=raw_file,
        killswitch_lock_path=ks_file,
        exchange_client=ex_client,
        info_client=info_client,
        sleep_fn=lambda _s: None,
    )

    # 1. 発注結果の検証
    assert res["status"] == "ok"
    assert res["oid"] == mock_ex.next_oid
    assert res["pre_account_value"] == "100.0"
    assert res["post_account_value"] == "100.0"
    assert res["pre_positions"] == 0
    assert res["post_positions"] == 0
    assert res["new_fills"] == 0

    # 2. 注文パラメータ検証（手動値 76765 = bid 77541 の 0.99 切捨て）
    assert len(mock_ex.orders) == 1
    placed = mock_ex.orders[0]
    assert placed["coin"] == "BTC"
    assert placed["is_buy"] is True
    assert placed["sz"] == 0.0002
    assert placed["px"] == 76765.0  # 77541 * 0.99 = 76765.59 -> 76765.0

    # 3. 取消検証
    assert len(mock_ex.cancels) == 1
    assert mock_ex.cancels[0]["oid"] == mock_ex.next_oid

    # 4. キルスイッチ解放検証
    assert not ks_file.exists()
    assert not (tmp_path / "HL_KILL_SWITCH_ROUNDTRIP_EXEC").exists()

    # 5. 三段ログのハッシュチェーン検証
    chain_check = verify_chain(j_file)
    assert chain_check.ok is True
    # SIGNAL + INTENT + ORDER(place) + ORDER(query) + ORDER(cancel) + ORDER(post)
    assert chain_check.line_count >= 5

    # 6. 生ログファイルの存在と照会ログの limitPx 検証
    assert raw_file.exists()
    raw_lines = [json.loads(line) for line in raw_file.read_text(encoding="utf-8").splitlines()]
    query_log = next(r for r in raw_lines if r.get("stage") == "query_open_orders")
    assert query_log["count"] == 1
    assert query_log["orders"][0]["limitPx"] == "76765.0"


def test_roundtrip_forbids_mainnet(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Mainnet endpoint is strictly forbidden"):
        run_order_roundtrip(
            account_address=TEST_ACCT,
            agent_key_path=FAKE_KEY,
            base_url="https://api.hyperliquid.xyz",
            killswitch_lock_path=tmp_path / "HL_KILL_SWITCH",
        )


def test_roundtrip_blocks_when_killswitch_engaged(tmp_path: Path) -> None:
    ks_file = tmp_path / "HL_KILL_SWITCH"
    ks = KillSwitch(ks_file)
    ks.engage(reason="active block")

    mock_ex = MockExchange()
    mock_transport = MockInfoTransport()
    info_client = HyperliquidInfoClient(
        "https://api.hyperliquid-testnet.xyz/info",
        transport=mock_transport,
        sleeper=lambda _s: None,
    )
    ex_client = HyperliquidExchangeClient(
        TEST_ACCT,
        FAKE_KEY,
        exchange=mock_ex,
        killswitch=ks,
    )

    with pytest.raises(RuntimeError, match="Failed to acquire kill switch lock before trade"):
        run_order_roundtrip(
            account_address=TEST_ACCT,
            agent_key_path=FAKE_KEY,
            killswitch_lock_path=ks_file,
            exchange_client=ex_client,
            info_client=info_client,
        )
