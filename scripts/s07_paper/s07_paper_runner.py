#!/usr/bin/env python3
"""S-07 ペーパー日次ランナー（実弾0・注文0）。

やること
  1. 取得済みの日足/funding（HL公開API・読み取りのみ）を使う
  2. 未処理の「確定日」だけを時系列で処理し、JSON状態へ追記（同じ日を2回処理しない）
  3. 当日損益・累計・DD・勝率・PF・反転回数・約定数・スキップ理由をMarkdown 1枚に出す

約定モデル（設計書 §4.3 + タスク指示）
  * シグナルは当日終値(00:00 UTC確定足)で判定し、翌日 00:01 UTC の始値で約定
  * 手数料: taker 4.5 bps / 片道
  * スリッページ: 15 bps / 片道（保守仮定。実測ブックの半スプレッドは別途記録）
  * funding: 保有中の1時間ごとのfunding率 × 想定元本（日中決済時は半日ぶん）
  * 損切り: 1.8 × ATR14（S-07 rev.1）、日足の高安で到達判定（寄りギャップは寄り値で約定）
  * 日次サーキットブレーカー: 当日 -2.0% 到達で全決済・翌日まで新規停止

実口座・秘密鍵・署名は一切使わない（/exchange は呼ばない）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hl_client  # noqa: E402
import s07_strategy as st  # noqa: E402

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")
STRATEGY_REV = "S-07 rev.1 (SMA50 / EMA10-20 / BO10 / Stop1.8)"

FEE_BPS = 4.5            # Hyperliquid taker
SLIPPAGE_BPS = 15.0      # タスク指示の保守仮定
CIRCUIT_BREAKER_PCT = 0.020
USDJPY = 150.0           # 設計書の 30,000円 ≈ 200 USDC に合わせた固定換算


# --------------------------------------------------------------------------- util


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_data(data_dir: Path) -> tuple[list[dict], list[dict]]:
    cpath = data_dir / "candles_1d.json"
    fpath = data_dir / "funding_1h.json"
    if not cpath.exists():
        raise SystemExit(f"日足データが無い: {cpath}（先に fetch_s07_data.py を実行）")
    candles = json.loads(cpath.read_text())["candles"]
    funding = json.loads(fpath.read_text())["funding"] if fpath.exists() else {}
    cl = sorted(candles.values(), key=lambda c: c["t"])
    fl = sorted(funding.values(), key=lambda f: f["time"])
    return cl, fl


def closed_candles(candles: list[dict], now_ms: int) -> list[dict]:
    """期間が終わった足だけ（当日進行中の足をシグナルに使わない）。"""
    return [c for c in candles if c["T"] < now_ms]


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, path)


# --------------------------------------------------------------------------- state


def new_state(namespace: str, equity_start: float, cfg: dict) -> dict:
    return {
        "schema": "s07-paper/v1",
        "namespace": namespace,
        "strategy": "hl_perp_btc_trend_funding (S-07)",
        "strategy_rev": STRATEGY_REV,
        "created_at": _now_iso(),
        "config": {
            **cfg,
            "fee_bps_per_side": FEE_BPS,
            "slippage_bps_per_side": SLIPPAGE_BPS,
            "circuit_breaker_pct": CIRCUIT_BREAKER_PCT,
            "usdjpy": USDJPY,
        },
        "equity_start_usdc": equity_start,
        "cash": equity_start,
        "peak_equity": equity_start,
        "position": None,
        "pending": None,
        "last_processed_day": None,
        "days": [],
        "round_trips": [],
        "counters": {
            "reversals": 0,
            "entries": 0,
            "exits": 0,
            "capacity_exceptions": 0,
            "liquidations": 0,
            "circuit_breaker_locks": 0,
            "skips": {},
        },
        "fail_streak": 0,
        "last_run": None,
    }


def load_state(path: Path, namespace: str, equity_start: float, cfg: dict) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return new_state(namespace, equity_start, cfg)


def archive_state(state_path: Path, namespace: str, data_dir: Path) -> dict:
    """旧状態を `archive/` へ退避し、`reset_log.jsonl` に1行残す（旧成績は保全・削除しない）。"""
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    arch_dir = data_dir / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dest = arch_dir / f"state_{namespace}_{ts}.json"
    prev = json.loads(state_path.read_text())
    os.replace(state_path, dest)
    prev_days = prev.get("days") or []
    prev_trips = prev.get("round_trips") or []
    try:
        prev_total = round(stats(prev)["total_pnl"], 6)
    except Exception:  # noqa: BLE001 - 壊れた旧状態でも退避だけは必ず残す
        prev_total = None
    info = {
        "at": _now_iso(),
        "namespace": namespace,
        "strategy_rev": STRATEGY_REV,
        "archived_to": str(dest),
        "prev_days": len(prev_days),
        "prev_first_day": prev_days[0]["day"] if prev_days else None,
        "prev_last_day": prev.get("last_processed_day"),
        "prev_trades": len(prev_trips),
        "prev_total_pnl": prev_total,
        "prev_config": {
            k: (prev.get("config") or {}).get(k)
            for k in ("sma_window", "ema_fast", "ema_slow", "breakout_window", "stop_atr_mult")
        },
        "reason": "S-07 rev.1 適用に伴う起点リセット（過去の当てはめを成績に混ぜない・埋め戻しなし）",
    }
    with open(data_dir / "reset_log.jsonl", "a") as fh:
        fh.write(json.dumps(info, ensure_ascii=False) + "\n")
    print(
        f"RESET: {state_path.name} -> {dest} "
        f"（旧 {info['prev_days']}日 / {info['prev_trades']}往復 / 累計 {prev_total} USDC を保全）"
    )
    return info


def scoring_start(state: dict) -> str | None:
    """成績の起算日。初日が基準日（約定なし）ならその翌日から起算する。"""
    days = state.get("days") or []
    if not days:
        return state.get("start_day")
    d0 = days[0]
    if "baseline_no_fill" in (d0.get("skips") or []):
        day = dt.date.fromisoformat(d0["day"]) + dt.timedelta(days=1)
        return day.isoformat()
    return d0["day"]


def oneline(state: dict) -> str:
    """毎日出す1行成績表（累計損益・往復数・PF・DD・清算回数・当日のシグナル）。"""
    s = stats(state)
    start = scoring_start(state)
    base = (state.get("days") or [{}])[0].get("day") if state.get("days") else None
    base_note = f"(基準日{base}・約定なし)" if base and base != start else ""
    d = state["days"][-1] if state["days"] else None
    if d:
        dec = d["signal"]
        sig = []
        if dec.get("long_signal"):
            sig.append("LONG")
        if dec.get("short_signal"):
            sig.append("SHORT")
        if not sig:
            sig.append("なし")
        if dec.get("block_long"):
            sig.append("long遮断")
        if dec.get("block_short"):
            sig.append("short遮断")
        signal = (
            f"{d['day']} {'/'.join(sig)}"
            f"（close={d['close']:.0f} 建玉={d['position_after']} 翌日={d['next_action']}）"
        )
    else:
        signal = "データなし"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (
        f"S-07 rev.1 paper[{state['namespace']}] 起算{start or '-'}{base_note} "
        f"累計{s['total_pnl']:+.4f}USDC({s['total_pnl_pct']:+.2f}%) "
        f"往復{s['trades']}件({s['wins']}勝{s['losses']}敗) PF={pf} EV={s['ev']:+.4f} "
        f"DD={s['max_dd_pct']:.2f}% 清算{s['liquidation']}回 "
        f"当日シグナル:{signal} 実弾0・注文0"
    )


def append_oneline_log(data_dir: Path, line: str) -> None:
    log = data_dir / "reports" / "S07_ONELINE.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as fh:
        fh.write(f"{_now_iso()} {line}\n")


# --------------------------------------------------------------------------- engine


class Engine:
    def __init__(self, state: dict, strat: st.Strategy):
        self.s = state
        self.strat = strat
        self.cfg = strat.cfg

    # -- helpers ---------------------------------------------------------
    @property
    def equity(self) -> float:
        pos = self.s["position"]
        if not pos:
            return float(self.s["cash"])
        price = pos["last_price"]
        size = pos["size"]
        if pos["side"] == "long":
            return float(self.s["cash"]) + size * (price - pos["entry_price"])
        return float(self.s["cash"]) + size * (pos["entry_price"] - price)

    def _fee(self, notional: float) -> float:
        return notional * FEE_BPS / 10_000.0

    def _fill_price(self, price: float, side: str, opening: bool) -> float:
        """スリッページを不利方向に乗せる。"""
        slip = SLIPPAGE_BPS / 10_000.0
        buy = (side == "long") == opening  # long entry は買い、long exit は売り
        return price * (1.0 + slip) if buy else price * (1.0 - slip)

    def _skip(self, reason: str) -> None:
        skips = self.s["counters"]["skips"]
        skips[reason] = skips.get(reason, 0) + 1

    # -- position lifecycle ----------------------------------------------
    def open_position(self, day: dict, side: str, atr14: float | None, pending: dict) -> dict:
        equity = self.equity
        sizing = self.strat.size_position(equity, day["o"], atr14 or 0.0)
        if not sizing["ok"]:
            self._skip(sizing["reason"])
            if pending.get("action") in ("enter_long", "enter_short", "reverse"):
                self.s["counters"]["capacity_exceptions"] += 0  # 例外ではなくスキップ
            return {}
        price = self._fill_price(day["o"], side, opening=True)
        size = sizing["size"]
        notional = size * price
        fee = self._fee(notional)
        self.s["cash"] = float(self.s["cash"]) - fee
        stop = price - sizing["stop_dist"] if side == "long" else price + sizing["stop_dist"]
        # 分離証拠金・実効レバレッジ3.0倍上限での清算価格（設計書 §5.1）
        margin = notional / self.cfg["max_leverage"]
        mmr = 0.0125
        if side == "long":
            liq = (price - margin / size) / (1 - mmr)
        else:
            liq = (price + margin / size) / (1 + mmr)
        pos = {
            "side": side,
            "entry_day": _iso(day["t"]),
            "entry_price": price,
            "entry_ref_price": day["o"],
            "size": size,
            "notional": notional,
            "stop": stop,
            "margin": margin,
            "liquidation_price": liq,
            "leverage": notional / equity if equity else 0.0,
            "equity_at_entry": equity,
            "entry_fee": fee,
            "fees_paid": fee,
            "funding_paid": 0.0,
            "last_price": price,
            "bars": 0,
            "entry_reason": pending.get("reason", ""),
        }
        self.s["position"] = pos
        self.s["counters"]["entries"] += 1
        return {
            "action": f"enter_{side}",
            "price": price,
            "ref_price": day["o"],
            "size": size,
            "notional": notional,
            "fee": fee,
            "reason": pending.get("reason", ""),
            "stop": stop,
            "leverage": pos["leverage"],
        }

    def close_position(self, day: dict, price: float, reason: str, intraday: bool) -> dict:
        pos = self.s["position"]
        if not pos:
            return {}
        side = pos["side"]
        fill = self._fill_price(price, side, opening=False)
        size = pos["size"]
        gross = size * (fill - pos["entry_price"]) if side == "long" else size * (pos["entry_price"] - fill)
        notional_exit = size * fill
        fee = self._fee(notional_exit)
        self.s["cash"] = float(self.s["cash"]) + gross - fee
        pos["fees_paid"] += fee
        net = gross - pos["fees_paid"] - pos["funding_paid"]
        trip = {
            "side": side,
            "entry_day": pos["entry_day"],
            "exit_day": _iso(day["t"]),
            "entry_price": pos["entry_price"],
            "exit_price": fill,
            "size": size,
            "notional": pos["notional"],
            "gross_pnl": round(gross, 6),
            "fees": round(pos["fees_paid"], 6),
            "funding": round(pos["funding_paid"], 6),
            "net_pnl": round(net, 6),
            "exit_reason": reason,
            "bars_held": pos["bars"],
            "intraday_exit": intraday,
        }
        self.s["round_trips"].append(trip)
        self.s["counters"]["exits"] += 1
        self.s["position"] = None
        return {
            "action": "exit",
            "side": side,
            "price": fill,
            "ref_price": price,
            "size": size,
            "fee": fee,
            "gross_pnl": gross,
            "net_pnl": net,
            "reason": reason,
        }

    def apply_funding(self, day: dict, ratios: dict, fraction: float = 1.0) -> float:
        """その日に保有していた分のfundingを現金へ反映する。

        fraction=0.5 は「場中で決済したので半日ぶん」の近似。
        """
        pos = self.s["position"]
        if not pos:
            return 0.0
        start = day["t"]
        end = start + MS_PER_DAY
        total = 0.0
        for ts, rate in ratios.items():
            if start <= ts < end:
                total += rate
        total *= fraction
        if total == 0.0:
            return 0.0
        notional = pos["size"] * pos["last_price"]
        pay = total * notional
        if pos["side"] == "long":
            self.s["cash"] = float(self.s["cash"]) - pay
            pos["funding_paid"] += pay
        else:
            self.s["cash"] = float(self.s["cash"]) + pay
            pos["funding_paid"] -= pay  # 受け取りは正のキャッシュフロー
            pay = -pay
        return pay

    # -- one day ---------------------------------------------------------
    def run_day(self, candles: list[dict], j: int, ratios: dict) -> dict:
        day = candles[j]
        pending = self.s.get("pending") or {}
        prev_atr = (pending.get("decision") or {}).get("atr14")
        executed: list[dict] = []
        skips: list[str] = []
        realized = 0.0
        fees = 0.0

        # 1) 前日終値の判断を当日始値で約定
        action = pending.get("action", "none")
        if action != "none":
            if action in ("exit", "reverse") and self.s["position"]:
                r = self.close_position(day, day["o"], pending.get("reason", action), intraday=False)
                if r:
                    executed.append(r)
                    realized += r["net_pnl"]
                    fees += r["fee"]
                if action == "reverse" and pending.get("reverse_to"):
                    r2 = self.open_position(day, pending["reverse_to"], prev_atr, pending)
                    if r2:
                        executed.append(r2)
                        fees += r2["fee"]
                        self.s["counters"]["reversals"] += 1
            elif action in ("enter_long", "enter_short") and not self.s["position"]:
                side = "long" if action == "enter_long" else "short"
                r = self.open_position(day, side, prev_atr, pending)
                if r:
                    executed.append(r)
                    fees += r["fee"]
            elif action in ("enter_long", "enter_short"):
                skips.append("already_in_position")
                self._skip("already_in_position")

        # 2) funding（その日に保有した分。場中決済なら半日ぶんに減額）
        funding_paid = 0.0
        if self.s["position"]:
            funding_paid = self.apply_funding(day, ratios)

        # 3) 場中チェック（損切り・清算・サーキットブレーカー）
        pos = self.s["position"]
        if pos:
            pos["bars"] += 1
            side = pos["side"]
            stop = pos["stop"]
            # サーキットブレーカー価格（当日 -2%）
            eq0 = float(pos.get("equity_at_entry") or self.s["equity_start_usdc"])
            breaker_px = (
                pos["entry_price"] - (CIRCUIT_BREAKER_PCT * eq0) / pos["size"]
                if side == "long"
                else pos["entry_price"] + (CIRCUIT_BREAKER_PCT * eq0) / pos["size"]
            )
            liq = pos["liquidation_price"]

            if side == "long":
                triggers = [(stop, "stop_loss"), (breaker_px, "circuit_breaker")]
                if day["l"] <= liq:
                    hits = [t for t in triggers if t[0] >= liq]
                    px = liq
                    reason = "liquidation"
                else:
                    hits = [t for t in triggers if t[0] >= day["l"]]
                    px = max(hits, key=lambda t: t[0])[0] if hits else None
                    reason = max(hits, key=lambda t: t[0])[1] if hits else ""
                if px is not None:
                    if day["o"] <= px:  # 寄りで既に割っている（ギャップ）
                        px = day["o"]
                    r = self.close_position(day, px, reason, intraday=True)
                    if r:
                        executed.append(r)
                        realized += r["net_pnl"]
                        fees += r["fee"]
                        if reason == "liquidation":
                            self.s["counters"]["liquidations"] += 1
                        if reason == "circuit_breaker":
                            self.s["counters"]["circuit_breaker_locks"] += 1
            else:
                triggers = [(stop, "stop_loss"), (breaker_px, "circuit_breaker")]
                if day["h"] >= liq:
                    px, reason = liq, "liquidation"
                else:
                    hits = [t for t in triggers if t[0] <= day["h"]]
                    px = min(hits, key=lambda t: t[0])[0] if hits else None
                    reason = min(hits, key=lambda t: t[0])[1] if hits else ""
                if px is not None:
                    if day["o"] >= px:
                        px = day["o"]
                    r = self.close_position(day, px, reason, intraday=True)
                    if r:
                        executed.append(r)
                        realized += r["net_pnl"]
                        fees += r["fee"]
                        if reason == "liquidation":
                            self.s["counters"]["liquidations"] += 1
                        if reason == "circuit_breaker":
                            self.s["counters"]["circuit_breaker_locks"] += 1

        # 4) 場中で決済した場合は funding を半日ぶんへ調整
        if funding_paid and any(e["action"] == "exit" for e in executed):
            refund = funding_paid * 0.5
            self.s["cash"] = float(self.s["cash"]) + refund
            funding_paid -= refund
            pos_after = self.s["position"]
            if pos_after is not None:
                pos_after["funding_paid"] -= refund

        # 4) 当日の評価
        pos = self.s["position"]
        if pos:
            pos["last_price"] = day["c"]
        equity = self.equity
        peak = max(float(self.s["peak_equity"]), equity)
        self.s["peak_equity"] = peak
        dd = (equity - peak) / peak * 100.0 if peak else 0.0
        day_pnl = equity - (self.s["days"][-1]["equity_close"] if self.s["days"] else self.s["equity_start_usdc"])

        # 5) 当日終値での判断 → 翌日始値で執行する注文を作る
        dec = self.strat.decide(j)
        next_action, next_reason, reverse_to = self._decide_next(dec, skips)
        self.s["pending"] = {
            "action": next_action,
            "reason": next_reason,
            "reverse_to": reverse_to,
            "decision": {**dec.as_dict(), "day": dec.day},
            "from_day": dec.day,
        }

        row = {
            "day": dec.day,
            "open": day["o"],
            "high": day["h"],
            "low": day["l"],
            "close": day["c"],
            "executed": executed,
            "skips": skips,
            "position_after": self.s["position"]["side"] if self.s["position"] else "flat",
            "realized_pnl": round(realized, 6),
            "funding": round(funding_paid, 6),
            "fees": round(fees, 6),
            "equity_close": round(equity, 6),
            "peak_equity": round(peak, 6),
            "dd_pct": round(dd, 4),
            "day_pnl": round(day_pnl, 6),
            "day_pnl_jpy": round(day_pnl * USDJPY, 1),
            "signal": dec.as_dict(),
            "next_action": next_action,
            "next_reason": next_reason,
            "status": "ok",
        }
        self.s["days"].append(row)
        self.s["last_processed_day"] = dec.day
        return row

    def _decide_next(self, dec: st.DayDecision, skips: list[str]) -> tuple[str, str, str | None]:
        pos = self.s["position"]
        if pos:
            side = pos["side"]
            if side == "long" and dec.exit_long:
                return "exit", "exit_long_rule", None
            if side == "short" and dec.exit_short:
                return "exit", "exit_short_rule", None
            # 反対シグナルが出たらドテン（設計書: エグジット条件で中立化 → 新規は別判定）
            if side == "long" and dec.short_signal and not dec.block_short:
                return "reverse", "reverse_to_short", "short"
            if side == "short" and dec.long_signal and not dec.block_long:
                return "reverse", "reverse_to_long", "long"
            return "none", "hold", None

        if dec.long_signal:
            if dec.block_long:
                skips.append("funding_filter_long")
                self._skip("funding_filter_long")
                return "none", "funding_filter_long", None
            return "enter_long", "trend_breakout_long", None
        if dec.short_signal:
            if dec.block_short:
                skips.append("funding_filter_short")
                self._skip("funding_filter_short")
                return "none", "funding_filter_short", None
            return "enter_short", "trend_breakdown_short", None
        if not dec.warm:
            skips.append("warmup")
            self._skip("warmup")
        return "none", "no_signal", None


# --------------------------------------------------------------------------- report


def stats(state: dict) -> dict:
    days = state["days"]
    trips = state["round_trips"]
    wins = [t for t in trips if t["net_pnl"] > 0]
    losses = [t for t in trips if t["net_pnl"] < 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    ev = sum(t["net_pnl"] for t in trips) / len(trips) if trips else 0.0
    equity = days[-1]["equity_close"] if days else state["equity_start_usdc"]
    peak = max([state["equity_start_usdc"]] + [d["peak_equity"] for d in days]) if days else state["equity_start_usdc"]
    mdd = min([0.0] + [d["dd_pct"] for d in days]) if days else 0.0
    return {
        "days": len(days),
        "equity": equity,
        "total_pnl": equity - state["equity_start_usdc"],
        "total_pnl_pct": (equity / state["equity_start_usdc"] - 1) * 100.0,
        "max_dd_pct": mdd,
        "trades": len(trips),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trips) * 100.0) if trips else 0.0,
        "pf": pf,
        "ev": ev,
        "reversals": state["counters"]["reversals"],
        "fees": sum(t["fees"] for t in trips),
        "funding": sum(t["funding"] for t in trips),
        "liquidation": state["counters"]["liquidations"],
        "breaker": state["counters"]["circuit_breaker_locks"],
        "capacity_exceptions": state["counters"]["capacity_exceptions"],
        "fills": sum(len(d["executed"]) for d in days),
        "skips": state["counters"]["skips"],
    }


def render_report(state: dict, *, title: str, limit: int = 25) -> str:
    s = stats(state)
    cfg = state["config"]
    thr = cfg.get("gate", {})
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 生成: {_now_iso()} / 状態: `{state['namespace']}` / 最終処理日: {state['last_processed_day']}")
    lines.append(
        f"- 戦略: S-07 `hl_perp_btc_trend_funding`（BTC日足・00:00 UTC確定 → 翌日始値約定）"
    )
    lines.append(
        f"- 版: {state.get('strategy_rev') or STRATEGY_REV} / 起算日: {scoring_start(state) or '-'}"
        f"（これより前の当てはめはこの成績に含めない）"
    )
    lines.append(
        f"- コスト前提: 手数料 {cfg['fee_bps_per_side']}bps/片道・スリッページ {cfg['slippage_bps_per_side']}bps/片道・"
        f"funding実績。" f"初期資金 {state['equity_start_usdc']:.2f} USDC（¥{state['equity_start_usdc']*cfg['usdjpy']:,.0f}・1USD=¥{cfg['usdjpy']:.0f}）"
    )
    lines.append("- **実弾0・注文0**（公開API読み取りのみ／`/exchange` 未使用／鍵なし）")
    lines.append("")
    lines.append("## 1行成績表（毎日）")
    lines.append("")
    lines.append("```")
    lines.append(oneline(state))
    lines.append("```")
    lines.append("")
    lines.append("## 当日成績（最新日）")
    lines.append("")
    if state["days"]:
        d = state["days"][-1]
        lines.append("| 項目 | 値 |")
        lines.append("|---|---|")
        lines.append(f"| 対象日 (UTC) | {d['day']} |")
        lines.append(f"| 当日損益 | {d['day_pnl']:+.4f} USDC（¥{d['day_pnl_jpy']:+,.0f}） |")
        lines.append(f"| 累計損益 | {s['total_pnl']:+.4f} USDC（{s['total_pnl_pct']:+.3f}%） |")
        lines.append(f"| Equity | {s['equity']:.4f} USDC |")
        lines.append(f"| 最大DD | {s['max_dd_pct']:.3f}% |")
        lines.append(f"| 当日DD | {d['dd_pct']:.3f}% |")
        lines.append(f"| 建玉 | {d['position_after']} |")
        lines.append(f"| 約定数(当日) | {len(d['executed'])} |")
        lines.append(f"| スキップ理由 | {', '.join(d['skips']) if d['skips'] else 'なし'} |")
        lines.append(f"| funding(当日) | {d['funding']:+.6f} USDC |")
        lines.append(f"| 手数料(当日) | {d['fees']:.6f} USDC |")
        dec = d["signal"]
        n_sma, n_fast, n_slow = cfg["sma_window"], cfg["ema_fast"], cfg["ema_slow"]
        vals = (dec["sma200"], dec["ema20"], dec["ema50"], dec["atr14"])
        if all(v is not None for v in vals):
            lines.append(
                f"| シグナル | close={d['close']:.1f} / SMA{n_sma}={dec['sma200']:.1f} / "
                f"EMA{n_fast}={dec['ema20']:.1f} / EMA{n_slow}={dec['ema50']:.1f} / "
                f"ATR14={dec['atr14']:.1f} |"
            )
            lines.append(
                f"| 当日判定 | LONG={'○' if dec['long_signal'] else '×'} / "
                f"SHORT={'○' if dec['short_signal'] else '×'} / "
                f"exit_long={'○' if dec['exit_long'] else '×'} / "
                f"exit_short={'○' if dec['exit_short'] else '×'} |"
            )
        else:
            lines.append("| シグナル | ウォームアップ中 |")
        fa = dec["funding_ann_pct"]
        lines.append(
            f"| funding年率(24h) | {fa:+.2f}% |" if fa is not None else "| funding年率(24h) | n/a |"
        )
        lines.append(f"| 翌日への注文 | {d['next_action']} ({d['next_reason']}) |")
    else:
        lines.append("（まだ処理日がありません）")
    lines.append("")
    lines.append("## 累計サマリ")
    lines.append("")
    lines.append("| 指標 | 値 |")
    lines.append("|---|---|")
    lines.append(f"| 処理営業日数 | {s['days']} |")
    lines.append(f"| 約定数(累計) | {s['fills']} |")
    lines.append(f"| 往復トレード数 | {s['trades']} |")
    lines.append(f"| 勝率 | {s['win_rate']:.1f}%（{s['wins']}勝 {s['losses']}敗） |")
    pf = s["pf"]
    lines.append(f"| PF | {'∞' if pf == float('inf') else f'{pf:.3f}'} |")
    lines.append(f"| 期待値(EV) | {s['ev']:+.6f} USDC/トレード |")
    lines.append(f"| 反転回数 | {s['reversals']} |")
    lines.append(f"| 手数料累計 | {s['fees']:.4f} USDC |")
    lines.append(f"| funding累計 | {s['funding']:+.4f} USDC |")
    lines.append(f"| 強制ロスカット | {s['liquidation']} 回 |")
    lines.append(f"| サーキットブレーカー発動 | {s['breaker']} 回 |")
    lines.append(f"| 容量例外 | {s['capacity_exceptions']} 件 |")
    if s["skips"]:
        lines.append(f"| スキップ内訳 | {', '.join(f'{k}:{v}' for k, v in sorted(s['skips'].items()))} |")
    lines.append("")
    if state["round_trips"]:
        lines.append("## 往復トレード明細")
        lines.append("")
        lines.append("| # | 方向 | 参入日 | 決済日 | 参入 | 決済 | 数量 | 純損益(USDC) | 理由 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, t in enumerate(state["round_trips"][-limit:], 1):
            lines.append(
                f"| {i} | {t['side']} | {t['entry_day']} | {t['exit_day']} | {t['entry_price']:.1f} | "
                f"{t['exit_price']:.1f} | {t['size']:.5f} | {t['net_pnl']:+.4f} | {t['exit_reason']} |"
            )
        lines.append("")
    lines.append("## 日次推移")
    lines.append("")
    lines.append("| 日付(UTC) | close | 当日損益 | Equity | DD% | 建玉 | 約定 | スキップ |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in state["days"][-limit:]:
        lines.append(
            f"| {d['day']} | {d['close']:.0f} | {d['day_pnl']:+.4f} | {d['equity_close']:.4f} | "
            f"{d['dd_pct']:+.2f} | {d['position_after']} | {len(d['executed'])} | "
            f"{','.join(d['skips']) if d['skips'] else '-'} |"
        )
    lines.append("")
    lines.append(f"_gate thresholds: {json.dumps(thr, ensure_ascii=False)}_" if thr else "")
    lines.append("")
    lines.append("> 本ファイルはペーパー（紙）の記録であり、発注・送金は一切行っていない。")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S-07 paper daily runner (no real orders)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--namespace", default="live", help="状態名前空間 (live / hist など)")
    ap.add_argument("--equity", type=float, default=200.0, help="初期資金 USDC（既定200=¥30,000）")
    ap.add_argument("--start-day", default=None, help="処理開始日 YYYY-MM-DD（過去流し用）")
    ap.add_argument("--reset", action="store_true", help="状態を作り直す")
    ap.add_argument("--init-baseline", action="store_true",
                    help="初回は基準日だけ記録して約定しない（live用）")
    ap.add_argument("--measure-book", action="store_true",
                    help="実行時のL2ブック半スプレッドを実測して記録（live用・読み取りのみ）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    state_path = data_dir / f"state_{args.namespace}.json"
    report_path = data_dir / "reports" / (
        "S07_DAILY_SCORECARD.md" if args.namespace == "live" else f"S07_SCORECARD_{args.namespace}.md"
    )

    reset_info = None
    if args.reset and state_path.exists():
        reset_info = archive_state(state_path, args.namespace, data_dir)

    candles_all, funding = load_data(data_dir)
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    candles = closed_candles(candles_all, now_ms)
    if len(candles) < 260:
        raise SystemExit("確定日足が260本未満。データ取得が足りない")

    strat = st.Strategy(candles_all, funding)
    # 指標は全履歴で作る（ウォームアップを潰さない）。確定足の位置だけを使う。
    idx_of = {_iso(c["t"]): i for i, c in enumerate(candles_all)}
    closed_days = [_iso(c["t"]) for c in candles]
    closed_set = set(closed_days)
    ratios = {f["time"]: f["rate"] for f in funding}

    state = load_state(state_path, args.namespace, args.equity, strat.cfg)
    state.setdefault("strategy_rev", STRATEGY_REV)
    if reset_info:
        state["reset"] = reset_info

    # 開始位置
    if args.start_day:
        start_day = args.start_day
    elif state["days"] and state["last_processed_day"] in closed_set:
        nxt = closed_days.index(state["last_processed_day"]) + 1
        if nxt >= len(closed_days):
            start_day = None  # 新しい確定日がまだ無い
        else:
            start_day = closed_days[nxt]
    else:
        start_day = closed_days[-1]  # baseline 初日

    if start_day and not state.get("start_day"):
        state["start_day"] = start_day

    if start_day is None:
        # 処理対象なし（当日分がまだ確定していない）。状態はそのまま＝同じ日を2回処理しない。
        state["last_run"] = {"at": _now_iso(), "processed": [], "ok": True, "note": "no_new_closed_day"}
        state.setdefault("run_history", []).append(
            {"at": state["last_run"]["at"], "processed": 0, "ok": True, "note": "no_new_closed_day"}
        )
        state["run_history"] = state["run_history"][-400:]
        state["fail_streak"] = 0
        write_atomic(state_path, state)
        line = oneline(state)
        append_oneline_log(data_dir, line)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            render_report(state, title=f"S-07 ペーパー日次成績表（{args.namespace}）")
        )
        s = stats(state)
        out = {
            "ok": True,
            "namespace": args.namespace,
            "processed": [],
            "note": "no_new_closed_day",
            "days": s["days"],
            "equity": round(s["equity"], 6),
            "total_pnl": round(s["total_pnl"], 6),
            "total_pnl_jpy": round(s["total_pnl"] * state["config"]["usdjpy"], 1),
            "max_dd_pct": round(s["max_dd_pct"], 4),
            "win_rate": round(s["win_rate"], 2),
            "pf": None if s["pf"] == float("inf") else round(s["pf"], 4),
            "ev": round(s["ev"], 6),
            "trades": s["trades"],
            "fills": s["fills"],
            "liquidations": s["liquidation"],
            "oneline": line,
            "report": str(report_path),
            "state": str(state_path),
            "real_orders": 0,
            "real_equity_used": 0,
        }
        print(json.dumps(out, ensure_ascii=False) if args.json
              else f"新しい確定日なし（処理なし）\n{line}")
        return 0

    if start_day not in idx_of:
        raise SystemExit(f"start_day {start_day} が確定足に無い")

    start_idx = idx_of[start_day]
    first_run = not state["days"]
    if first_run:
        # 前日終値の判断を pending に入れてから開始
        prev_idx = max(0, start_idx - 1)
        dec = strat.decide(prev_idx)
        state["pending"] = {"action": "none", "reason": "init", "reverse_to": None,
                            "decision": {**dec.as_dict(), "day": dec.day}, "from_day": dec.day}

    engine = Engine(state, strat)
    processed: list[str] = []
    j = start_idx
    while j < len(candles_all):
        c = candles_all[j]
        if _iso(c["t"]) not in closed_set:
            break  # 当日進行中の足は触らない
        if engine.s["last_processed_day"] and _iso(c["t"]) <= engine.s["last_processed_day"]:
            j += 1
            continue
        if args.init_baseline and first_run:
            # 基準日: 約定はしないが、その日の判断は翌日執行の注文として引き継ぐ
            dec = strat.decide(j)
            na, nr, rev = engine._decide_next(dec, [])
            engine.s["pending"] = {"action": na, "reason": nr, "reverse_to": rev,
                                   "decision": {**dec.as_dict(), "day": dec.day}, "from_day": dec.day}
            row = {
                "day": dec.day, "open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"],
                "executed": [], "skips": ["baseline_no_fill"], "position_after": "flat",
                "realized_pnl": 0.0, "funding": 0.0, "fees": 0.0,
                "equity_close": state["equity_start_usdc"],
                "peak_equity": state["equity_start_usdc"], "dd_pct": 0.0,
                "day_pnl": 0.0, "day_pnl_jpy": 0.0, "signal": dec.as_dict(),
                "next_action": na, "next_reason": nr, "status": "ok",
            }
            engine.s["days"].append(row)
            engine.s["last_processed_day"] = dec.day
            first_run = False
            processed.append(dec.day)
            write_atomic(state_path, engine.s)
            break
        row = engine.run_day(candles_all, j, ratios)
        processed.append(row["day"])
        write_atomic(state_path, engine.s)  # 1日ごとに永続化（冪等の担保）
        j += 1

    # 実行時ブック計測（読み取りのみ）
    book = None
    if args.measure_book:
        try:
            b = hl_client.fetch_l2_book("BTC")
            book = {
                "bid": b["bid"], "ask": b["ask"], "mid": b["mid"],
                "spread_bps": round(b["spread_bps"], 4),
                "half_spread_bps": round(b["half_spread_bps"], 4),
                "ts": b["ts"],
            }
        except Exception as exc:  # noqa: BLE001
            book = {"error": str(exc)}

    state["last_run"] = {
        "at": _now_iso(),
        "processed": processed,
        "book": book,
        "ok": True,
    }
    # 実行履歴（完遂率の分母に使う）とブック実測サンプルを残す
    state.setdefault("run_history", []).append(
        {"at": state["last_run"]["at"], "processed": len(processed), "ok": True}
    )
    state["run_history"] = state["run_history"][-400:]
    if book and "half_spread_bps" in book:
        state.setdefault("book_samples", []).append(
            {"at": state["last_run"]["at"], "half_spread_bps": book["half_spread_bps"]}
        )
        state["book_samples"] = state["book_samples"][-400:]
    state["fail_streak"] = 0
    write_atomic(state_path, state)

    report = render_report(
        state,
        title=f"S-07 ペーパー日次成績表（{args.namespace}）",
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)

    s = stats(state)
    line = oneline(state)
    append_oneline_log(data_dir, line)
    summary = {
        "ok": True,
        "namespace": args.namespace,
        "processed": processed,
        "days": s["days"],
        "equity": round(s["equity"], 6),
        "total_pnl": round(s["total_pnl"], 6),
        "total_pnl_jpy": round(s["total_pnl"] * state["config"]["usdjpy"], 1),
        "max_dd_pct": round(s["max_dd_pct"], 4),
        "win_rate": round(s["win_rate"], 2),
        "pf": None if s["pf"] == float("inf") else round(s["pf"], 4),
        "ev": round(s["ev"], 6),
        "reversals": s["reversals"],
        "fills": s["fills"],
        "liquidations": s["liquidation"],
        "oneline": line,
        "report": str(report_path),
        "state": str(state_path),
        "book_half_spread_bps": (book or {}).get("half_spread_bps"),
        "real_orders": 0,
        "real_equity_used": 0,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"[{args.namespace}] 処理日: {', '.join(processed) if processed else 'なし（新規なし）'}")
        print(f"  Equity {s['equity']:.4f} USDC / 累計 {s['total_pnl']:+.4f} USDC "
              f"(¥{s['total_pnl']*state['config']['usdjpy']:+,.0f}) / 最大DD {s['max_dd_pct']:.3f}%")
        print(f"  約定 {s['fills']} / 往復 {s['trades']} / 勝率 {s['win_rate']:.1f}% / "
              f"PF {'∞' if s['pf']==float('inf') else f'{s["pf"]:.3f}'} / 反転 {s['reversals']} / 清算 {s['liquidation']}")
        if book:
            print(f"  ブック半スプレッド実測: {book.get('half_spread_bps')} bps")
        print(f"  成績表: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
