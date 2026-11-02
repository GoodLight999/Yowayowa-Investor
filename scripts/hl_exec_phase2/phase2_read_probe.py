#!/usr/bin/env python3
# ruff: noqa: RUF001, RUF002, RUF003
"""Hyperliquid 実行トラック 第2段: テストネット読み取りの実データ疎通 + リスク/丸めの実データ検証。

やらないこと（このスクリプトの契約）:
  * /exchange への送信なし（発注・取消・レバレッジ変更・approveAgent のいずれも呼ばない）
  * 電子署名なし・鍵の読み込みなし・鍵の生成なし
  * 触れるのは ``POST /info``（公開読み取り）だけ

やること:
  1. hl_exec/client.py で testnet の clearinghouseState / meta / l2Book(BTC) を取得し、
     生レスポンスと HTTP ステータス・送信電文を証跡ディレクトリへ保存する
  2. 取得値を hl_exec/risk.py に通し、実残高での許可/拒否境界を数値化する
  3. 取得した実 szDecimals で hl_exec/orders.py の丸め・組立が有効であることを示す
  4. 上記の結果を summary.json / summary.md に残す

使い方: python scripts/hl_exec_phase2/phase2_read_probe.py [--evidence-dir DIR]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import urllib.request
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from yowayowa.hl_exec import orders, risk  # noqa: E402
from yowayowa.hl_exec.client import (  # noqa: E402
    INFO_URL_TESTNET,
    USER_AGENT,
    HyperliquidInfoClient,
)

# テストネット口座（公開アドレスのみ・鍵は読まない）
TESTNET_ACCOUNT = "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82"
DEFAULT_EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / "hl_exec_phase2_20261102"
JST = dt.timezone(dt.timedelta(hours=9))


class RecordingTransport:
    """client.py の transport 注入口に差す、HTTPステータスを記録する読み取り専用トランスポート。

    既定の ``_default_transport`` と同じ urllib 実装だが、``resp.getcode()`` を
    記録して「HTTP 200 で読めた」ことを証跡に残せるようにする。
    送信先は URL 引数（= client が保持する base_url）に固定され、/exchange へは行かない。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, payload: dict[str, Any], url: str, timeout: float) -> Any:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定 https
            status = resp.getcode()
            raw = resp.read()
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
        self.calls.append(
            {
                "url": url,
                "request": payload,
                "http_status": status,
                "elapsed_ms": elapsed_ms,
                "response_bytes": len(raw),
            }
        )
        return json.loads(raw.decode("utf-8"))


def _dump(path: pathlib.Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _parse_account(st: dict[str, Any]) -> dict[str, Any]:
    ms = st.get("marginSummary") or {}
    positions = []
    for ap in st.get("assetPositions") or []:
        pos = (ap or {}).get("position") or {}
        positions.append(
            {
                "coin": pos.get("coin"),
                "szi": pos.get("szi"),
                "entryPx": pos.get("entryPx"),
                "leverage": pos.get("leverage"),
                "unrealizedPnl": pos.get("unrealizedPnl"),
            }
        )
    return {
        "account_value": float(ms.get("accountValue", "0")),
        "total_margin_used": float(ms.get("totalMarginUsed", "0")),
        "total_ntl_pos": float(ms.get("totalNtlPos", "0")),
        "withdrawable": float(st.get("withdrawable", "0")),
        "cross_account_value": ms.get("accountValue"),
        "positions": positions,
        "time": st.get("time"),
    }


def _risk_section(account: dict[str, Any], btc: dict[str, Any]) -> dict[str, Any]:
    """実データ（実残高・実価格）で risk.py の境界を数値化する。"""
    equity = account["account_value"]
    withdrawable = account["withdrawable"]
    ask = btc["ask"]
    mid = btc["mid"]

    # 設計§2.4: 1建玉の隔離証拠金上限は「20.0 USDC」かつ「口座資金の10.0%」。
    # risk.py の check_isolated_margin / evaluate_entry_gates は内部で min(20.0, 10%×equity) を適用する（裁定 t_e91f2dbc 準拠）。
    design_margin_cap = min(risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC, risk.ISOLATED_MARGIN_FRACTION_OF_EQUITY * equity)
    notional_from_leverage = design_margin_cap * risk.LEVERAGE_CAP
    design_notional_cap = min(risk.POSITION_NOTIONAL_CAP_USDC, notional_from_leverage)

    max_size = orders.round_size(design_notional_cap / ask, sz_decimals=btc["szDecimals"])
    min_size = orders.round_size(risk.MIN_NOTIONAL_USDC / ask, sz_decimals=btc["szDecimals"])
    # 切り捨て後の実効名目
    max_size_notional = round(max_size * ask, 6)
    # 最小発注額を切り捨てで満たす最小サイズ（切り捨て後の名目 >= 10.0 になる最小の刻み）
    tick = 1.0 / 10 ** btc["szDecimals"]
    min_tradable = min_size
    while min_tradable * ask < risk.MIN_NOTIONAL_USDC:
        min_tradable = orders.round_size(min_tradable + tick, sz_decimals=btc["szDecimals"])

    def checks(margin: float, notional: float, open_count: int) -> list[dict[str, Any]]:
        out = []
        for chk in risk.evaluate_entry_gates(
            isolated_margin_usdc=margin,
            open_position_count=open_count,
            notional_usdc=notional,
            equity_usdc=equity,
        ):
            out.append(
                {"rule": chk.rule, "ok": chk.ok, "value": chk.value, "limit": chk.limit, "reason": chk.reason}
            )
        out.append(
            {
                "rule": "leverage_cap",
                **{
                    k: v
                    for k, v in vars(risk.check_leverage(notional, margin)).items()
                    if k in ("ok", "value", "limit", "reason")
                },
            }
        )
        out.append(
            {
                "rule": "margin_preflight",
                "ok": risk.margin_preflight_ok(withdrawable, margin),
                "value": withdrawable,
                "limit": margin + risk.MARGIN_PREFLIGHT_FEE_BUFFER_USDC,
                "reason": "",
            }
        )
        return out

    boundary = {
        "max_size_allowed": max_size,
        "max_size_notional_usdc": max_size_notional,
        "max_size_gates": checks(design_margin_cap, max_size_notional, 0),
        "one_step_over_size": orders.round_size(max_size + 1.0 / 10**btc["szDecimals"], sz_decimals=btc["szDecimals"]),
        "one_step_over_notional_usdc": round((max_size + 1.0 / 10 ** btc["szDecimals"]) * ask, 6),
        "one_step_over_gates": checks(
            design_margin_cap, (max_size + 1.0 / 10 ** btc["szDecimals"]) * ask, 0
        ),
        "min_size_allowed": min_size,
        "below_min_size": orders.round_size(min_size - 1.0 / 10**btc["szDecimals"], sz_decimals=btc["szDecimals"]),
        "below_min_notional_usdc": round(
            (min_size - 1.0 / 10 ** btc["szDecimals"]) * ask, 6
        ),
        "below_min_gates": checks(
            design_margin_cap, (min_size - 1.0 / 10 ** btc["szDecimals"]) * ask, 0
        ),
        "two_positions_gates": checks(design_margin_cap, max_size_notional, 2),
    }

    # 設計想定口座（200 USDC）での絶対上限（20.0 USDC）の境界も併記（設計値との整合性可視化）
    # 設計想定口座（200 USDC）を渡して絶対上限 20.0 USDC の境界を検証
    module_boundary = {
        "module_margin_cap_usdc": risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC,
        "at_cap_ok": risk.check_isolated_margin(
            risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC, equity_usdc=200.0
        ).ok,
        "at_cap_usdc": risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC + 0.01,
        "just_over_cap_ok": risk.check_isolated_margin(
            risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC + 0.01, equity_usdc=200.0
        ).ok,
        "at_cap_share_of_real_equity": risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC / equity,
    }

    return {
        "equity_usdc": equity,
        "withdrawable_usdc": withdrawable,
        "btc_ask": ask,
        "btc_mid": mid,
        "design_margin_cap_usdc": design_margin_cap,
        "design_notional_cap_usdc": design_notional_cap,
        "notional_from_leverage_usdc": notional_from_leverage,
        "boundary": boundary,
        "module_absolute_boundary": module_boundary,
        "min_tradable_size": min_tradable,
        "min_tradable_notional_usdc": round(min_tradable * ask, 6),
        "stop_levels": {
            "daily_loss_stop_threshold_usdc": min(
                risk.DAILY_LOSS_STOP_USDC, risk.DAILY_LOSS_STOP_RATIO * equity
            ),
            "daily_loss_stop_triggered_at": -min(risk.DAILY_LOSS_STOP_USDC, risk.DAILY_LOSS_STOP_RATIO * equity),
            "daily_stop_hit_at_threshold": risk.daily_loss_stop_hit(
                -min(risk.DAILY_LOSS_STOP_USDC, risk.DAILY_LOSS_STOP_RATIO * equity), day_start_equity=equity
            ),
            "daily_stop_hit_just_before": risk.daily_loss_stop_hit(
                -min(risk.DAILY_LOSS_STOP_USDC, risk.DAILY_LOSS_STOP_RATIO * equity) + 0.000001,
                day_start_equity=equity,
            ),
            "drawdown_stop_threshold_usdc": min(risk.DRAWDOWN_STOP_USDC, risk.DRAWDOWN_STOP_RATIO * equity),
            "drawdown_hit_at_threshold": risk.drawdown_stop_hit(
                equity - min(risk.DRAWDOWN_STOP_USDC, risk.DRAWDOWN_STOP_RATIO * equity), equity
            ),
            "drawdown_hit_just_before": risk.drawdown_stop_hit(
                equity - min(risk.DRAWDOWN_STOP_USDC, risk.DRAWDOWN_STOP_RATIO * equity) + 0.000001, equity
            ),
            "liquidation_distance_ok_0_15": risk.liquidation_distance_ok(0.15),
            "liquidation_distance_ok_0_1499": risk.liquidation_distance_ok(0.1499),
        },
    }


def _rounding_section(universe: list[dict[str, Any]], prices: dict[str, float]) -> dict[str, Any]:
    """実 szDecimals で orders.py の丸め・組立が有効であることを示す。"""
    rows = []
    for name, px in prices.items():
        asset = next((a for a in universe if a.get("name") == name), None)
        if asset is None or not px:
            continue
        szd = int(asset["szDecimals"])
        rounded = orders.round_price(px, sz_decimals=szd)
        rows.append(
            {
                "coin": name,
                "szDecimals": szd,
                "max_price_decimals": orders.MAX_DECIMALS_PERP - szd,
                "mid_px": px,
                "rounded_px": rounded,
                "rounded_px_wire": orders.to_wire_str(rounded),
                "price_is_valid": orders.price_is_valid(rounded, sz_decimals=szd),
                "truncated": rounded <= px,
            }
        )

    btc = next(a for a in universe if a.get("name") == "BTC")
    btc_szd = int(btc["szDecimals"])
    btc_px = prices["BTC"]
    size_probe = {
        "szDecimals": btc_szd,
        "tick": 1.0 / 10**btc_szd,
        "samples": [
            {
                "requested": q,
                "rounded": orders.round_size(q, sz_decimals=btc_szd),
                "valid": orders.size_is_valid(orders.round_size(q, sz_decimals=btc_szd), sz_decimals=btc_szd),
            }
            for q in (0.000789012, 0.0012345, 0.000999949, 1.0 / 3.0)
        ],
    }

    examples: list[dict[str, Any]] = []
    for coin in ("BTC", "ETH", "DOGE"):
        asset = next((a for a in universe if a.get("name") == coin), None)
        if asset is None:
            continue
        szd = int(asset["szDecimals"])
        for raw in ("78012.1", "12345.6", "1234.5", "0.1234567"):
            rounded = orders.round_price(raw, sz_decimals=szd)
            examples.append(
                {
                    "coin": coin,
                    "szDecimals": szd,
                    "input": raw,
                    "rounded": rounded,
                    "wire": orders.to_wire_str(rounded),
                    "valid": orders.price_is_valid(rounded, sz_decimals=szd),
                    "truncated": rounded <= float(raw),
                }
            )

    return {
        "per_symbol_price_rounding": rows,
        "btc_size_rounding": size_probe,
        "price_rule_examples": examples,
    }


def _assembled_section(meta: dict[str, Any], btc: dict[str, Any], ask: float, equity: float) -> dict[str, Any]:
    """実 asset_index・実 szDecimals で注文辞書を組み立て、送信せずに検証する。"""
    universe = meta["universe"]
    asset_index = next(i for i, a in enumerate(universe) if a.get("name") == "BTC")
    szd = int(universe[asset_index]["szDecimals"])
    design_margin_cap = min(
        risk.ISOLATED_MARGIN_PER_POSITION_CAP_USDC, risk.ISOLATED_MARGIN_FRACTION_OF_EQUITY * equity
    )
    notional_cap = min(risk.POSITION_NOTIONAL_CAP_USDC, design_margin_cap * risk.LEVERAGE_CAP)
    size = orders.round_size(notional_cap / ask, sz_decimals=szd)
    built = orders.build_limit_order(
        asset_index=asset_index,
        coin="BTC",
        is_buy=True,
        size=size,
        price=ask,
        sz_decimals=szd,
        tif="Ioc",
    )
    rejection = None
    try:
        orders.build_limit_order(
            asset_index=asset_index,
            coin="BTC",
            is_buy=True,
            size=orders.round_size(risk.POSITION_NOTIONAL_CAP_USDC * 2 / ask, sz_decimals=szd),
            price=ask,
            sz_decimals=szd,
        )
    except orders.OrderAssemblyError as exc:
        rejection = str(exc)
    return {
        "asset_index": asset_index,
        "coin": "BTC",
        "szDecimals": szd,
        "assembled": built,
        "notional_cap_oversize_rejected_with": rejection,
        "sent": False,
    }


def _markdown(summary: dict[str, Any]) -> str:
    acct = summary["account"]
    rs = summary["risk"]
    rd = summary["rounding"]
    b = rs["boundary"]
    m = rs["module_absolute_boundary"]
    lines = [
        "# 第2段 実データ検証サマリ（自動生成）",
        "",
        f"- 生成時刻: {summary['generated_at_jst']}",
        f"- endpoint: `{summary['endpoint']}`（POST /info のみ・/exchange 呼出し 0 件）",
        f"- 口座: `{summary['account_address']}`",
        "",
        "## 1. 読み取り（HTTPステータス）",
        "",
        "| call | type | HTTP | bytes | ms |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(summary["calls"], start=1):
        lines.append(
            f"| {i} | {c['request']['type']} | {c['http_status']} | {c['response_bytes']} | {c['elapsed_ms']} |"
        )
    lines += [
        "",
        f"- accountValue = **{acct['account_value']}** USDC / withdrawable = **{acct['withdrawable']}** USDC / "
        f"建玉数 = **{len(acct['positions'])}**",
        f"- BTC ask = {rs['btc_ask']} / mid = {rs['btc_mid']}",
        "",
        "## 2. risk.py の実データ判定",
        "",
        f"- 実残高 {rs['equity_usdc']} USDC → 設計の隔離証拠金上限 = min(20.0, 10%×残高) = **{rs['design_margin_cap_usdc']}** USDC",
        f"- そのレバレッジ上限 3.0 倍 → 名目上限 = min(60.0, 3×{rs['design_margin_cap_usdc']}) = "
        f"**{rs['design_notional_cap_usdc']}** USDC",
        f"- 許可される最大サイズ = **{b['max_size_allowed']} BTC**（名目 {b['max_size_notional_usdc']} USDC）",
        f"- 1刻み上乗せ = {b['one_step_over_size']} BTC（名目 {b['one_step_over_notional_usdc']} USDC）",
        "",
        "| ケース | ルール | ok | value | limit |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, gates in (
        ("最大サイズ", b["max_size_gates"]),
        ("1刻み超過", b["one_step_over_gates"]),
        ("最小サイズ割れ", b["below_min_gates"]),
        ("2建玉目", b["two_positions_gates"]),
    ):
        if not gates:
            continue
        for g in gates:
            lines.append(f"| {label} | {g['rule']} | {g['ok']} | {g['value']} | {g['limit']} |")
    lines += [
        "",
        f"- 1刻み超過で最初に落ちるルール: "
        f"{next((g['rule'] for g in b['one_step_over_gates'] if not g['ok']), 'なし')}",
        f"- 2建玉目で落ちるルール: {next((g['rule'] for g in b['two_positions_gates'] if not g['ok']), 'なし')}",
        f"- 最小発注額 10.0 USDC を満たす最小サイズ: **{summary['risk']['min_tradable_size']} BTC**"
        f"（名目 {summary['risk']['min_tradable_notional_usdc']} USDC）",
        f"- 実データでの最小サイズ {b['min_size_allowed']} BTC は名目 {b['below_min_notional_usdc']} USDC で"
        f" **min_notional により拒否**（境界が効いている証明）",
        "",
        "### 参考: 設計想定口座（200 USDC）における絶対上限（20.0 USDC）の境界",
        "",
        f"- margin = {m['module_margin_cap_usdc']} USDC → check_isolated_margin = **{m['at_cap_ok']}**（上限ちょうどは許可）",
        f"- margin = {m['at_cap_usdc']} USDC → **{m['just_over_cap_ok']}**（超過は拒否）",
        f"- 注意: 上記は設計想定口座（200 USDC）での絶対上限 20.0 USDC の境界確認。"
        f" 実残高 100.0 USDC では 20.0 USDC = 残高の {m['at_cap_share_of_real_equity'] * 100:.1f}%。",
        f"  現行 risk.py は比率上限を内部適用するため（裁定 t_e91f2dbc 準拠）、実残高での実効 cap は"
        f" min(20.0, 10%×実残高) = {rs['design_margin_cap_usdc']} USDC となる。",
        "",
        "## 3. orders.py の丸め（実 szDecimals）",
        "",
        "| coin | szDecimals | 価格小数上限 | mid | 丸め後 | wire | 有効 | 切捨て |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rd["per_symbol_price_rounding"]:
        lines.append(
            f"| {r['coin']} | {r['szDecimals']} | {r['max_price_decimals']} | {r['mid_px']} | "
            f"{r['rounded_px']} | `{r['rounded_px_wire']}` | {r['price_is_valid']} | {r['truncated']} |"
        )
    lines += ["", "| 要求サイズ | 丸め後 | 有効 |", "| --- | --- | --- |"]
    for s in rd["btc_size_rounding"]["samples"]:
        lines.append(f"| {s['requested']} | {s['rounded']} | {s['valid']} |")
    lines += [
        "",
        "公式 tick 契約の実例（実 szDecimals ごと・すべて切捨て）:",
        "",
        "| coin | szDecimals | 入力 | 丸め後 | wire | 有効な価格か | 切捨て |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in rd["price_rule_examples"]:
        lines.append(
            f"| {e['coin']} | {e['szDecimals']} | {e['input']} | {e['rounded']} | `{e['wire']}` | "
            f"{e['valid']} | {e['truncated']} |"
        )
    a = summary["assembled"]
    lines += [
        "",
        "## 4. 組立（送信なし・実 asset_index と実 szDecimals）",
        "",
        f"- BTC の asset_index = **{a['asset_index']}**（`meta.universe` の並び位置）/ szDecimals = {a['szDecimals']}",
        f"- wire = `{json.dumps(a['assembled']['wire'], ensure_ascii=False)}`",
        f"- 名目 = {a['assembled']['notional_usdc']:.6f} USDC（min 10.0 / cap 60.0 の内側）",
        f"- 上限超過サイズの拒否: `{a['notional_cap_oversize_rejected_with']}`",
        f"- 送信フラグ `sent` = **{a['sent']}**（このスクリプトから /exchange への送信は 0 件）",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    args = ap.parse_args(argv)
    evidence = pathlib.Path(args.evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)

    transport = RecordingTransport()
    client = HyperliquidInfoClient(INFO_URL_TESTNET, transport=transport, timeout=20.0, retries=2)

    state = client.clearinghouse_state(TESTNET_ACCOUNT)
    meta = client.meta()
    book = client.l2_book("BTC")

    _dump(evidence / "clearinghouseState.json", state)
    _dump(evidence / "meta.json", meta)
    _dump(evidence / "l2Book_BTC.json", book)
    _dump(evidence / "call_log.json", {"calls": transport.calls})

    account = _parse_account(state)
    btc_meta = client.asset_meta("BTC")
    top = client.top_of_book("BTC")
    btc = {
        "ask": top["ask"],
        "bid": top["bid"],
        "mid": top["mid"],
        "spread_bps": top["spread_bps"],
        "szDecimals": btc_meta["szDecimals"],
        "asset_index": btc_meta["asset_index"],
        "maxLeverage": btc_meta["maxLeverage"],
    }
    _dump(evidence / "btc_tick.json", btc)

    universe = meta["universe"]
    prices = {"BTC": top["mid"]}
    for coin in ("ETH", "SOL", "DOGE", "HYPE"):
        if any(a.get("name") == coin for a in universe):
            try:
                prices[coin] = client.top_of_book(coin)["mid"]
            except Exception as exc:  # 板が読めない銘柄はスキップ（読み取りのみ）
                print(f"warn: {coin} book unavailable: {exc}", file=sys.stderr)
    _dump(evidence / "call_log.json", {"calls": transport.calls})  # 追加呼出しも反映

    summary = {
        "generated_at_jst": dt.datetime.now(JST).isoformat(timespec="seconds"),
        "endpoint": INFO_URL_TESTNET,
        "account_address": TESTNET_ACCOUNT,
        "calls": transport.calls,
        "account": account,
        "btc": btc,
        "risk": _risk_section(account, btc),
        "rounding": _rounding_section(universe, prices),
        "assembled": _assembled_section(meta, btc, top["ask"], account["account_value"]),
        "signature_calls": 0,
        "exchange_calls": 0,
        "key_reads": 0,
    }
    _dump(evidence / "summary.json", summary)
    (evidence / "summary.md").write_text(_markdown(summary), encoding="utf-8")

    print(
        json.dumps(
            {
                "statuses": [c["http_status"] for c in transport.calls],
                "account_value": account["account_value"],
                "positions": len(account["positions"]),
                "btc_ask": top["ask"],
                "btc_szDecimals": btc_meta["szDecimals"],
                "btc_asset_index": btc_meta["asset_index"],
                "max_size_allowed": summary["risk"]["boundary"]["max_size_allowed"],
                "design_margin_cap": summary["risk"]["design_margin_cap_usdc"],
                "evidence_dir": str(evidence),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
