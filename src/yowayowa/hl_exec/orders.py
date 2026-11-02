# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""注文の組立（生成のみ・どこにも送信しない）。

ワイヤ形式は公式 Exchange Endpoint 仕様（2026-11-02 再確認）に基づく:
  order = {"a": asset_index, "b": is_buy, "p": price_str, "s": size_str,
           "r": reduce_only, "t": {"limit": {"tif": "Alo"|"Ioc"|"Gtc"}}
                                 | {"trigger": {"isMarket": bool,
                                                "triggerPx": str,
                                                "tpsl": "tp"|"sl"}},
           "c": cloid(任意・128bit hex)}
  出典: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint

価格・サイズの丸めは公式 Tick and Lot Size（2026-11-02 再確認・設計書 §2.3 と一致）:
  * 価格は有効数字最大 5 桁、かつ小数は最大 (MAX_DECIMALS - szDecimals) 桁
    （perp は MAX_DECIMALS = 6、spot は 8）
  * 整数価格は有効数字に関係なく常に許可（例: 123456 は有効、12345.6 は無効）
  * サイズは当該資産の szDecimals に丸める
  * 丸め方向は切り捨て（truncate）。四捨五入で桁数が増えない
    （運用補足 2026-11-02: 78012.1 -> 78012、12345.6 -> 12345、
     0.1234567 -> 0.12345、1234.5 -> 1234.5 のまま）

本モジュールは辞書を組み立てて返すだけで、ネットワーク呼び出しを
一切行わない（送信・電子署名のコードは本段階で存在しない）。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from yowayowa.hl_exec.client import (
    TESTNET_API_URL,
    HyperliquidExchangeClient,
    HyperliquidInfoClient,
)
from yowayowa.hl_exec.journal import ExecutionJournal, verify_chain
from yowayowa.hl_exec.killswitch import KillSwitch
from yowayowa.hl_exec.risk import MIN_NOTIONAL_USDC, POSITION_NOTIONAL_CAP_USDC

PRICE_SIGNIFICANT_FIGURES = 5
MAX_DECIMALS_PERP = 6
MAX_DECIMALS_SPOT = 8
VALID_TIF = ("Alo", "Ioc", "Gtc")
VALID_TPSL = ("tp", "sl")

_CLOID_RE = re.compile(r"^0x[0-9a-f]{32}$")


class OrderAssemblyError(ValueError):
    """組立の契約違反（丸め後の名目額が範囲外・形式不正など）。"""


def _to_decimal(value: float | int | str | Decimal) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise OrderAssemblyError(f"not a decimal number: {value!r}") from exc
    if not d.is_finite():
        raise OrderAssemblyError(f"not a finite number: {value!r}")
    return d


def _require_sz_decimals(sz_decimals: int) -> int:
    if not isinstance(sz_decimals, int) or not (0 <= sz_decimals <= 8):
        raise OrderAssemblyError(f"szDecimals must be an int in [0, 8]: {sz_decimals!r}")
    return sz_decimals


# ------------------------------------------------------------------ 丸め
def round_price(
    px: float | int | str | Decimal,
    *,
    sz_decimals: int,
    max_decimals: int = MAX_DECIMALS_PERP,
) -> float:
    """価格を公式 tick 契約へ切り捨て丸めする。

    有効数字 5 桁への切り捨てと、小数 (max_decimals - szDecimals) 桁への
    切り捨てのうち厳しい方を採る。結果が整数になる場合は整数価格として
    常に有効（有効数字の制約を受けない）。
    """
    _require_sz_decimals(sz_decimals)
    if max_decimals < 0:
        raise OrderAssemblyError("max_decimals must be >= 0")
    d = _to_decimal(px)
    if d <= 0:
        raise OrderAssemblyError(f"price must be positive: {px!r}")
    frac_by_sig = PRICE_SIGNIFICANT_FIGURES - 1 - d.adjusted()
    frac_by_tick = max_decimals - sz_decimals
    frac = min(frac_by_sig, frac_by_tick)
    if frac < 0:
        frac = 0
    quant = Decimal(1).scaleb(-frac)
    rounded = d.quantize(quant, rounding=ROUND_DOWN)
    if rounded <= 0:
        raise OrderAssemblyError(
            f"price {px!r} truncates to zero at szDecimals={sz_decimals} (untradable)"
        )
    return float(rounded)


def price_is_valid(
    px: float | int | str | Decimal,
    *,
    sz_decimals: int,
    max_decimals: int = MAX_DECIMALS_PERP,
) -> bool:
    """価格が公式 tick 契約を満たすか（丸め後の証明と事前検証の共通規約）。"""
    _require_sz_decimals(sz_decimals)
    try:
        d = _to_decimal(px)
    except OrderAssemblyError:
        return False
    if d <= 0:
        return False
    if d == d.to_integral_value():
        return True  # 整数価格は有効数字に関係なく常に許可（公式仕様）
    normalized = d.normalize()
    digits = len(normalized.as_tuple().digits)
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):  # inf/nan は _to_decimal で排除済み（型の絞り込み）
        return False
    frac = max(0, -exponent)
    return digits <= PRICE_SIGNIFICANT_FIGURES and frac <= max_decimals - sz_decimals


def round_size(sz: float | int | str | Decimal, *, sz_decimals: int) -> float:
    """サイズを szDecimals 桁へ切り捨て丸めする（過剰な数量を絶対に出さない）。"""
    _require_sz_decimals(sz_decimals)
    d = _to_decimal(sz)
    if d <= 0:
        raise OrderAssemblyError(f"size must be positive: {sz!r}")
    quant = Decimal(1).scaleb(-sz_decimals)
    rounded = d.quantize(quant, rounding=ROUND_DOWN)
    if rounded <= 0:
        raise OrderAssemblyError(
            f"size {sz!r} truncates to zero at szDecimals={sz_decimals} (untradable)"
        )
    return float(rounded)


def size_is_valid(sz: float | int | str | Decimal, *, sz_decimals: int) -> bool:
    """サイズが szDecimals 桁契約を満たすか（丸め後の証明用の共通規約）。"""
    _require_sz_decimals(sz_decimals)
    try:
        d = _to_decimal(sz)
    except OrderAssemblyError:
        return False
    if d <= 0:
        return False
    exponent = d.normalize().as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    frac = max(0, -exponent)
    return frac <= sz_decimals


def to_wire_str(value: float | int | str | Decimal) -> str:
    """wire の p/s 用の10進文字列（末尾の 0 を除去・指数表記を使わない）。

    公式の注意書きに従い、将来の電子署名実装でも必要になる
    「末尾0を除いた正準形」をここで統一して出す。
    """
    d = _to_decimal(value)
    return format(d.normalize(), "f")


# ------------------------------------------------------------------ 組立
def _validate_cloid(cloid: str | None) -> str | None:
    if cloid is None:
        return None
    if not isinstance(cloid, str) or not _CLOID_RE.match(cloid):
        raise OrderAssemblyError(
            f"cloid must be a 128-bit hex string like 0x + 32 hex chars: {cloid!r}"
        )
    return cloid


def _validate_notional(notional: Decimal) -> None:
    if notional < Decimal(str(MIN_NOTIONAL_USDC)):
        raise OrderAssemblyError(
            f"notional {notional} below min {MIN_NOTIONAL_USDC} USDC (reject before submit)"
        )
    if notional > Decimal(str(POSITION_NOTIONAL_CAP_USDC)):
        raise OrderAssemblyError(
            f"notional {notional} exceeds cap {POSITION_NOTIONAL_CAP_USDC} USDC "
            "(reject before submit)"
        )


def validate_assembled(assembled: dict[str, Any], *, sz_decimals: int) -> None:
    """組立済み注文の最終検証（生成物が公式契約を満たすことの機械証明）。

    build_* は全て内部でこれを通す。丸め後の wire が常に有効であることを
    テストからも直接呼べるように公開している。
    """
    wire = assembled.get("wire")
    if not isinstance(wire, dict):
        raise OrderAssemblyError("assembled order has no wire dict")
    for key, typ in (("a", int), ("b", bool), ("p", str), ("s", str), ("r", bool)):
        if not isinstance(wire.get(key), typ):
            raise OrderAssemblyError(f"wire[{key!r}] missing or wrong type: {wire.get(key)!r}")
    if not price_is_valid(Decimal(wire["p"]), sz_decimals=sz_decimals):
        raise OrderAssemblyError(f"wire price violates tick contract: {wire['p']!r}")
    if not size_is_valid(Decimal(wire["s"]), sz_decimals=sz_decimals):
        raise OrderAssemblyError(f"wire size violates lot contract: {wire['s']!r}")
    t = wire.get("t")
    if not isinstance(t, dict) or len(t) != 1:
        raise OrderAssemblyError(f"wire order type must have exactly one entry: {t!r}")
    if "limit" in t:
        tif = t["limit"].get("tif")
        if tif not in VALID_TIF:
            raise OrderAssemblyError(f"invalid tif: {tif!r}")
    elif "trigger" in t:
        trig = t["trigger"]
        if not isinstance(trig, dict) or trig.get("tpsl") not in VALID_TPSL:
            raise OrderAssemblyError(f"invalid trigger structure: {trig!r}")
        if not isinstance(trig.get("isMarket"), bool) or not isinstance(trig.get("triggerPx"), str):
            raise OrderAssemblyError(f"invalid trigger fields: {trig!r}")
        if not price_is_valid(Decimal(trig["triggerPx"]), sz_decimals=sz_decimals):
            raise OrderAssemblyError(f"trigger price violates tick contract: {trig['triggerPx']!r}")
    else:
        raise OrderAssemblyError(f"unknown order type: {t!r}")
    notional = Decimal(wire["p"]) * Decimal(wire["s"])
    _validate_notional(notional)


def build_limit_order(
    *,
    asset_index: int,
    coin: str,
    is_buy: bool,
    size: float,
    price: float,
    sz_decimals: int,
    reduce_only: bool = False,
    tif: str = "Ioc",
    max_decimals: int = MAX_DECIMALS_PERP,
    cloid: str | None = None,
) -> dict[str, Any]:
    """指値注文を組み立てる（設計書 §2.3: 新規は指値IOC・手仕舞いは reduceOnly IOC）。

    送信はしない。返す辞書の ``wire`` は公式形式そのままだが、この関数の
    契約上どこにも送られない。既定の tif は Ioc（設計書 §2.3-1）。
    """
    if not isinstance(asset_index, int) or asset_index < 0:
        raise OrderAssemblyError(f"asset_index must be a non-negative int: {asset_index!r}")
    if not isinstance(coin, str) or not coin:
        raise OrderAssemblyError("coin must be a non-empty string")
    if tif not in VALID_TIF:
        raise OrderAssemblyError(f"invalid tif: {tif!r}")
    rounded_size = round_size(size, sz_decimals=sz_decimals)
    rounded_price = round_price(price, sz_decimals=sz_decimals, max_decimals=max_decimals)
    assembled: dict[str, Any] = {
        "wire": {
            "a": int(asset_index),
            "b": bool(is_buy),
            "p": to_wire_str(rounded_price),
            "s": to_wire_str(rounded_size),
            "r": bool(reduce_only),
            "t": {"limit": {"tif": tif}},
        },
        "coin": coin,
        "is_buy": bool(is_buy),
        "price": rounded_price,
        "size": rounded_size,
        "notional_usdc": rounded_price * rounded_size,
        "reduce_only": bool(reduce_only),
        "tif": tif,
    }
    validated_cloid = _validate_cloid(cloid)
    if validated_cloid is not None:
        assembled["wire"]["c"] = validated_cloid
        assembled["cloid"] = validated_cloid
    validate_assembled(assembled, sz_decimals=sz_decimals)
    return assembled


def build_trigger_order(
    *,
    asset_index: int,
    coin: str,
    is_buy: bool,
    size: float,
    trigger_price: float,
    sz_decimals: int,
    tpsl: str,
    is_market: bool = True,
    reduce_only: bool = True,
    max_decimals: int = MAX_DECIMALS_PERP,
    cloid: str | None = None,
) -> dict[str, Any]:
    """トリガー注文（TP/SL）を組み立てる（設計書 §2.3: SLは取引所側Stop-Market）。

    SLを取引所側トリガーに置くのは、VPS停止時でもプロトコル上で損切りを
    確実に執行させるバックストップ（バックストップ無しの運用は禁止）。
    wire の ``p`` は必須フィールドのため triggerPx と同値を入れる
    （is_market=True のとき取引所は参照しない。公式SDKと同一の扱い）。
    """
    if tpsl not in VALID_TPSL:
        raise OrderAssemblyError(f"tpsl must be one of {VALID_TPSL}: {tpsl!r}")
    if not isinstance(asset_index, int) or asset_index < 0:
        raise OrderAssemblyError(f"asset_index must be a non-negative int: {asset_index!r}")
    if not isinstance(coin, str) or not coin:
        raise OrderAssemblyError("coin must be a non-empty string")
    rounded_size = round_size(size, sz_decimals=sz_decimals)
    rounded_trigger = round_price(trigger_price, sz_decimals=sz_decimals, max_decimals=max_decimals)
    assembled: dict[str, Any] = {
        "wire": {
            "a": int(asset_index),
            "b": bool(is_buy),
            "p": to_wire_str(rounded_trigger),
            "s": to_wire_str(rounded_size),
            "r": bool(reduce_only),
            "t": {
                "trigger": {
                    "isMarket": bool(is_market),
                    "triggerPx": to_wire_str(rounded_trigger),
                    "tpsl": tpsl,
                }
            },
        },
        "coin": coin,
        "is_buy": bool(is_buy),
        "trigger_price": rounded_trigger,
        "size": rounded_size,
        "notional_usdc": rounded_trigger * rounded_size,
        "reduce_only": bool(reduce_only),
        "tpsl": tpsl,
    }
    validated_cloid = _validate_cloid(cloid)
    if validated_cloid is not None:
        assembled["wire"]["c"] = validated_cloid
        assembled["cloid"] = validated_cloid
    validate_assembled(assembled, sz_decimals=sz_decimals)
    return assembled


def build_entry_bundle(
    *,
    asset_index: int,
    coin: str,
    is_buy: bool,
    size: float,
    price: float,
    sz_decimals: int,
    sl_trigger_price: float,
    tp_trigger_price: float | None = None,
    tif: str = "Ioc",
    max_decimals: int = MAX_DECIMALS_PERP,
    cloid: str | None = None,
) -> dict[str, Any]:
    """エントリー指値IOC ＋ 取引所側SLトリガ（＋TPトリガ任意）の1束を組立（送信なし）。

    SLは必ずエントリーの逆サイド・reduce_only=True で組む
    （意図せぬ逆サイド建玉の新規保有をプロトコルレベルで防止）。
    送信時の grouping（normalTpsl 等）は送信系を実装する後段カードの契約。
    """
    entry = build_limit_order(
        asset_index=asset_index,
        coin=coin,
        is_buy=is_buy,
        size=size,
        price=price,
        sz_decimals=sz_decimals,
        reduce_only=False,
        tif=tif,
        max_decimals=max_decimals,
        cloid=cloid,
    )
    sl = build_trigger_order(
        asset_index=asset_index,
        coin=coin,
        is_buy=not is_buy,
        size=entry["size"],
        trigger_price=sl_trigger_price,
        sz_decimals=sz_decimals,
        tpsl="sl",
        is_market=True,
        reduce_only=True,
        max_decimals=max_decimals,
    )
    tp: dict[str, Any] | None = None
    if tp_trigger_price is not None:
        tp = build_trigger_order(
            asset_index=asset_index,
            coin=coin,
            is_buy=not is_buy,
            size=entry["size"],
            trigger_price=tp_trigger_price,
            sz_decimals=sz_decimals,
            tpsl="tp",
            is_market=True,
            reduce_only=True,
            max_decimals=max_decimals,
        )
    return {"entry": entry, "sl": sl, "tp": tp}


# ------------------------------------------------------------------ 注文往復（第4段）
def run_order_roundtrip(
    *,
    account_address: str,
    agent_key_path: str | Path,
    coin: str = "BTC",
    size: float = 0.0002,
    price_ratio: float = 0.99,
    tif: str = "Gtc",
    base_url: str = TESTNET_API_URL,
    journal_path: str | Path | None = None,
    raw_log_path: str | Path | None = None,
    killswitch_lock_path: str | Path | None = None,
    exchange_client: HyperliquidExchangeClient | None = None,
    info_client: HyperliquidInfoClient | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """テストネット最小注文の往復（発注→照会→取消→照合）を完全自動実行する。

    受入条件:
      1. BTC 買い指値（サイズ 0.0002・価格=最良買値×0.99切捨て）を発注し oid を受領。
      2. 発注直後の照会（openOrders）で 1件・oid一致 を確認。
      3. 取消を実行して success を受領し、取消後 未約定注文0件 を確認。
      4. 前後の clearinghouseState で accountValue が同一維持・建玉0・新規約定0 を証明。
      5. 三段ログ（SIGNAL/INTENT/ORDER）が journal.py 経由で JSONL に改ざん不能チェーンで記録。
      6. キルスイッチ疎通: 発注前にロック取得、正常終了で解放をログで実証。
    """
    if "api.hyperliquid.xyz" in base_url and "testnet" not in base_url:
        raise ValueError(f"Mainnet endpoint is strictly forbidden: {base_url}")

    default_ks = Path("/root/.secrets/HL_KILL_SWITCH")
    ks_path = Path(killswitch_lock_path) if killswitch_lock_path else default_ks
    ks = KillSwitch(ks_path)
    info = info_client if info_client is not None else HyperliquidInfoClient(f"{base_url}/info")
    ex = (
        exchange_client
        if exchange_client is not None
        else HyperliquidExchangeClient(
            account_address=account_address,
            agent_key=agent_key_path,
            base_url=base_url,
            killswitch=ks,
        )
    )

    default_j = Path("data/execution_logs/hl_testnet_roundtrip.jsonl")
    j_path = Path(journal_path) if journal_path else default_j
    journal = ExecutionJournal(j_path)
    raw_events: list[dict[str, Any]] = []

    def _record_raw(stage: str, **kwargs: Any) -> None:
        ev = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime()),
            "stage": stage,
            **kwargs,
        }
        raw_events.append(ev)

    # 1. キルスイッチ経路の疎通（受入条件6: 発注前にロック取得、正常終了で解放）
    # 1a. キルスイッチ自体のロック取得と解放の疎通テスト
    ks_lock_ok = ks.acquire_lock(reason="pre-trade-connectivity-test")
    if not ks_lock_ok:
        raise RuntimeError(f"Failed to acquire kill switch lock before trade: {ks.lock_path}")
    if not ks.is_engaged():
        raise RuntimeError(
            f"Kill switch lock reported acquired but is_engaged() is False: {ks.lock_path}"
        )
    _record_raw("killswitch_lock_acquired", lock_path=str(ks.lock_path), engaged=True)

    ks_released = ks.release_lock()
    if not ks_released:
        raise RuntimeError(f"Failed to release kill switch lock: {ks.lock_path}")
    if ks.is_engaged():
        raise RuntimeError(
            f"Kill switch lock reported released but is_engaged() is True: {ks.lock_path}"
        )
    _record_raw("killswitch_lock_released", lock_path=str(ks.lock_path), engaged=False)

    # 1b. 実行中排他ロックを取得（fail-closed: 異常停止時にロックが残り次回発注を阻止）
    trade_lock_path = ks.lock_path.with_name(ks.lock_path.name + "_ROUNDTRIP_EXEC")
    trade_lock = KillSwitch(trade_lock_path)
    if not trade_lock.acquire_lock(reason="roundtrip_in_progress"):
        raise RuntimeError(f"Execution lock already held: {trade_lock_path}")
    _record_raw("execution_lock_acquired", lock_path=str(trade_lock_path))

    try:
        # 2. 事前状態確認（Pre-state）
        pre_state = info.clearinghouse_state(account_address)
        pre_account_value = str(pre_state["marginSummary"]["accountValue"])
        pre_positions = len(pre_state.get("assetPositions", []))
        tob = info.top_of_book(coin)
        bid = float(tob["bid"])
        ask = float(tob["ask"])
        pre_fills = info.user_fills(account_address)
        initial_fills_count = len(pre_fills)

        _record_raw(
            "pre_state",
            accountValue=pre_account_value,
            openPositions=pre_positions,
            bid=bid,
            ask=ask,
        )

        signal_entry = journal.log_decision({
            "action": "ORDER_ROUNDTRIP_SIGNAL",
            "stage": "pre_state",
            "account_address": account_address,
            "coin": coin,
            "accountValue": pre_account_value,
            "openPositions": pre_positions,
            "bid": bid,
            "ask": ask,
            "initial_fills_count": initial_fills_count,
        })

        # 3. 価格・サイズ丸めと注文組立
        meta = info.asset_meta(coin)
        sz_decimals = int(meta["szDecimals"])
        asset_index = int(meta["asset_index"])
        rounded_px = round_price(bid * price_ratio, sz_decimals=sz_decimals)
        rounded_sz = round_size(size, sz_decimals=sz_decimals)

        assembled = build_limit_order(
            asset_index=asset_index,
            coin=coin,
            is_buy=True,
            size=rounded_sz,
            price=rounded_px,
            sz_decimals=sz_decimals,
            tif=tif,
        )

        intent_entry = journal.log_intent(
            signal_entry.event_id,
            {
                "action": "ORDER_INTENT",
                "stage": "intent",
                "coin": coin,
                "is_buy": True,
                "price": rounded_px,
                "size": rounded_sz,
                "tif": tif,
                "wire": assembled["wire"],
            },
        )

        # 4. 発注（受入条件1: oid を受領）
        ks.ensure_may_trade()
        place_resp = ex.order(
            coin,
            is_buy=True,
            sz=rounded_sz,
            px=rounded_px,
            order_type={"limit": {"tif": tif}},
        )
        _record_raw("place", resp=place_resp)

        statuses = place_resp.get("response", {}).get("data", {}).get("statuses") or [{}]
        resting = statuses[0].get("resting")
        if not resting or "oid" not in resting:
            raise RuntimeError(f"Order placement did not return resting oid: {place_resp}")
        oid = int(resting["oid"])

        journal.log_order(
            intent_entry.event_id,
            {
                "substage": "place",
                "oid": oid,
                "resp": place_resp,
            },
        )

        # 5. 発注直後の照会（受入条件2: 1件・oid一致）
        sleep_fn(2.0)
        open_orders = info.open_orders(account_address)
        _record_raw(
            "query_open_orders",
            count=len(open_orders),
            orders=[
                {
                    "oid": o.get("oid"),
                    "coin": o.get("coin"),
                    "side": o.get("side"),
                    "sz": o.get("sz"),
                    "px": o.get("px"),
                }
                for o in open_orders
            ],
        )

        if len(open_orders) != 1:
            raise RuntimeError(
                f"Expected exactly 1 open order, found {len(open_orders)}: {open_orders}"
            )
        matched_order = open_orders[0]
        if int(matched_order.get("oid", -1)) != oid:
            raise RuntimeError(
                f"Open order oid mismatch: expected {oid}, got {matched_order.get('oid')}"
            )
        if matched_order.get("coin") != coin or matched_order.get("side") != "B":
            raise RuntimeError(f"Open order attributes mismatch: {matched_order}")

        journal.log_order(
            intent_entry.event_id,
            {
                "substage": "query_open_orders",
                "count": len(open_orders),
                "matched_oid": oid,
                "orders": open_orders,
            },
        )

        # 6. 取消（受入条件3: success を受領し、取消後 未約定注文0件）
        cancel_resp = ex.cancel(coin, oid)
        _record_raw("cancel", oid=oid, resp=cancel_resp)

        cancel_statuses = cancel_resp.get("response", {}).get("data", {}).get("statuses") or []
        if not cancel_statuses or cancel_statuses[0] != "success":
            raise RuntimeError(f"Cancel request failed or unexpected response: {cancel_resp}")

        sleep_fn(2.0)
        post_cancel_orders = info.open_orders(account_address)
        _record_raw("post_cancel_open_orders", count=len(post_cancel_orders))

        if len(post_cancel_orders) != 0:
            raise RuntimeError(
                f"Expected 0 open orders after cancel, found {len(post_cancel_orders)}"
            )

        journal.log_order(
            intent_entry.event_id,
            {
                "substage": "cancel",
                "oid": oid,
                "resp": cancel_resp,
                "post_cancel_open_orders_count": len(post_cancel_orders),
            },
        )

        # 7. 事後照合（受入条件4: 前後の clearinghouseState で accountValue 維持・建玉0・新規約定0）
        post_state = info.clearinghouse_state(account_address)
        post_account_value = str(post_state["marginSummary"]["accountValue"])
        post_positions = len(post_state.get("assetPositions", []))
        post_fills = info.user_fills(account_address)
        new_fills_count = len(post_fills) - initial_fills_count

        _record_raw(
            "post_state",
            accountValue=post_account_value,
            openPositions=post_positions,
            fills=new_fills_count,
        )

        if post_account_value != pre_account_value:
            raise RuntimeError(
                f"accountValue changed: pre={pre_account_value}, post={post_account_value}"
            )
        if post_positions != 0:
            raise RuntimeError(f"Expected 0 open positions, found {post_positions}")
        if new_fills_count != 0:
            raise RuntimeError(f"Expected 0 new fills, found {new_fills_count}")

        journal.log_order(
            intent_entry.event_id,
            {
                "substage": "post_state",
                "accountValue": post_account_value,
                "openPositions": post_positions,
                "new_fills": new_fills_count,
            },
        )

    finally:
        # 8. 正常終了・異常終了にかかわらず実行排他ロックの解放
        trade_lock.release_lock()
        _record_raw("execution_lock_released", lock_path=str(trade_lock_path))

    # 9. 三段ログのハッシュチェーン検証（受入条件5）
    chain_result = verify_chain(j_path)
    if not chain_result.ok:
        raise RuntimeError(f"Journal hash chain verification failed: {chain_result.reason}")

    # 10. raw_log_path が指定されていれば生ログ（JSONL）も保存
    if raw_log_path:
        out_p = Path(raw_log_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as fh:
            for ev in raw_events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return {
        "status": "ok",
        "oid": oid,
        "pre_account_value": pre_account_value,
        "post_account_value": post_account_value,
        "pre_positions": pre_positions,
        "post_positions": post_positions,
        "new_fills": new_fills_count,
        "journal_path": str(j_path),
        "journal_line_count": chain_result.line_count,
        "raw_events": raw_events,
    }
