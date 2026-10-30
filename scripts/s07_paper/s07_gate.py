#!/usr/bin/env python3
"""S-07 ペーパーゲート判定（90営業日ロール窓・機械判定・実弾0）。

設計書 `docs/CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md` §6 / §6.1 を唯一の正として、
「数値6項目＋強制ロスカット0回」をペーパー状態JSONから機械的に判定する。

判定は必ず 合格 / 不合格 / 未達 の3値で出す。

  * 未達   … 90営業日窓に満たない、または決済済み往復が4未満（判定保留）
  * 不合格 … 材料は揃っているが1項目でも基準を満たさない
  * 合格   … 全項目が基準内

§6.1 の実測知見に基づく扱い:
  * 項目4（PF/EV）… 決済済み往復 < 4 のときは「判定保留（Pending）」。
    低頻度戦略で取引0〜3件は市場環境による正常動作であり、不合格に倒さない。
  * 項目2（スリッページ）… 板実測（book_samples）が無い場合、live では不合格（未測定）、
    hist 名前空間（`--namespace hist*`）または `--hist` 指定時は「N/A（過去データのため免除）」。
    Hyperliquid API に過去のL2板データが存在しない制約による。
  * 基準値は設計書§6のみ。旧暫定値（PF 1.25 / 反転4回）の併記判定は廃止した。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")

# 設計書 CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md §6（数値基準）＋ §6.1（窓・最低往復）
GATE_DESIGN = {
    "window_days": 90,          # §6.1 判定窓（直近90営業日ロール窓）
    "min_days": 90,             # 総合判定に必要な営業日数
    "min_round_trips": 4,       # 項目4を判定する最低往復数（未満は判定保留）
    "infra_min_days": 20,       # Phase 2-A インフラ適格の最短営業日
    "midterm_days": 20,         # 中間判定（合格/継続/中止）を出す営業日
    "completion_rate_min_pct": 98.0,
    "slippage_max_bps": 2.0,    # 15.0 bps への緩和は却下（§6.1-3）
    "max_dd_max_pct": 4.0,
    "pf_min": 1.30,
    "ev_min_usdc": 0.0,
    "reversals_per_month_max": 6,
    "capacity_exceptions_max": 0,
    "liquidations_max": 0,
    # 参考表示のみ（合否には使わない。損益シミュレーション内部の悲観課金値）
    "stress_slippage_model_bps": 15.0,
}

# インフラ適格（Phase 2-A）で見る項目（§6.1-1）
INFRA_CHECK_NOS = (1, 2, 3, 6, "★")

STATUS_LABEL = {"pass": "合格", "fail": "不合格", "pending": "保留", "na": "N/A"}


def load_state(data_dir: Path, namespace: str) -> dict:
    p = data_dir / f"state_{namespace}.json"
    if not p.exists():
        raise SystemExit(f"状態ファイルが無い: {p}")
    return json.loads(p.read_text())


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    v = sorted(values)
    k = (len(v) - 1) * 0.95
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def slice_window(state: dict, gate: dict) -> tuple[list[dict], list[dict], bool]:
    """直近 window_days 営業日のロール窓に評価対象を切る。"""
    days_all = state.get("days") or []
    wd = int(gate["window_days"])
    if len(days_all) <= wd:
        return list(days_all), list(state.get("round_trips") or []), False
    days = list(days_all[-wd:])
    start = days[0]["day"]
    trips = [t for t in (state.get("round_trips") or []) if (t.get("exit_day") or "") >= start]
    return days, trips, True


def sample_day(b: dict) -> str | None:
    """book_sample の記録日（YYYY-MM-DD）。時刻が無い/壊れている場合は None。"""
    at = str(b.get("at") or "")
    return at[:10] if len(at) >= 10 and at[4] == "-" and at[7] == "-" else None


def window_book_samples(state: dict, days: list[dict]) -> tuple[list[float], int]:
    """項目2の実測サンプルを評価窓（days の先頭日以降）に限定して返す。

    窓外（90営業日より古い）板値を持ち込むと、窓内が健全でも p95 が汚染されて
    不合格になる（2026-10-30 の独立検収で再現）。recency の下限だけを切り、
    窓内の日付より後に計測したサンプルは「現在の板」として採用する。
    日時が判別できないサンプルは live を緩めないよう窓内扱いにする。
    """
    start = days[0]["day"] if days else None
    all_s = state.get("book_samples") or []
    kept: list[float] = []
    for b in all_s:
        if "half_spread_bps" not in b:
            continue
        sd = sample_day(b)
        if start is None or sd is None or sd >= start:
            kept.append(b["half_spread_bps"])
    return kept, len(all_s)


def window_max_dd(days: list[dict], equity0: float) -> float:
    """評価窓内のEquity系列から最大DDを再計算する（窓開始直前残高を初期ピーク）。

    保存済み dd_pct は全期間ピーク基準なので、窓外で付いたピークを持ち越すと
    窓内が右肩上がりでも不合格になる（同上の再現）。§6.1 は「評価期間中のピーク
    から測定」なので、窓の開始直前残高を起点に引き直す。
    """
    if not days:
        return 0.0
    peak = float(equity0)
    mdd = 0.0
    for d in days:
        eq = float(d["equity_close"])
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, (eq / peak - 1.0) * 100.0)
    return mdd


def count_reversals(days: list[dict]) -> int:
    """同一営業日に「決済→新規建て」が起きた回数（＝ドテン）。"""
    n = 0
    for d in days:
        acts = [str(e.get("action", "")) for e in (d.get("executed") or [])]
        if "exit" in acts and any(a.startswith("enter_") for a in acts):
            n += 1
    return n


def midterm(state: dict, gate: dict) -> dict:
    """20営業日時点の中間判定（合格 / 継続 / 中止 の3択）。

    * 中止 … 強制ロスカット1回以上・容量例外・DD>4%・EV≤0/PF<1.0（往復4件以上で判断）
    * 合格 … 20営業日到達かつ往復4件以上で PF≥1.30・EV>0（→ 90営業日判定へ継続運用）
    * 継続 … それ以外（営業日不足、往復4件未満、PF 1.00〜1.30）
    """
    days = list(state.get("days") or [])
    n = len(days)
    trips = list(state.get("round_trips") or [])
    counters = state.get("counters") or {}
    liq = int(counters.get("liquidations", 0))
    cap = int(counters.get("capacity_exceptions", 0))
    wins = [t for t in trips if t["net_pnl"] > 0]
    losses = [t for t in trips if t["net_pnl"] < 0]
    gw = sum(t["net_pnl"] for t in wins)
    gl = -sum(t["net_pnl"] for t in losses)
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    ev = (sum(t["net_pnl"] for t in trips) / len(trips)) if trips else 0.0
    eq0 = float(state.get("equity_start_usdc")
                or ((days[0]["equity_close"] - float(days[0].get("day_pnl") or 0.0)) if days else 0.0))
    mdd = window_max_dd(days, eq0) if days else 0.0
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    base = {
        "days": n,
        "target_days": gate["midterm_days"],
        "trades": len(trips),
        "pf": None if pf == float("inf") else round(pf, 4),
        "ev": round(ev, 6),
        "max_dd_pct": round(mdd, 4),
        "liquidations": liq,
    }
    if liq > gate["liquidations_max"]:
        return {**base, "verdict": "中止", "reason": f"強制ロスカット{liq}回（1回で即失格）"}
    if cap > gate["capacity_exceptions_max"]:
        return {**base, "verdict": "中止", "reason": f"容量例外{cap}件"}
    if n < gate["midterm_days"]:
        return {**base, "verdict": "継続",
                "reason": f"{n}/{gate['midterm_days']}営業日（中間判定は未到達）"}
    if abs(mdd) > gate["max_dd_max_pct"]:
        return {**base, "verdict": "中止",
                "reason": f"最大DD {mdd:.2f}% が基準 {gate['max_dd_max_pct']:.1f}% 超"}
    if len(trips) < gate["min_round_trips"]:
        return {**base, "verdict": "継続",
                "reason": f"往復{len(trips)}件<{gate['min_round_trips']}件（サンプル不足・判定保留）"}
    if ev <= gate["ev_min_usdc"] or pf < 1.0:
        return {**base, "verdict": "中止", "reason": f"EV {ev:+.4f} / PF {pf_txt}（負け越し）"}
    if pf >= gate["pf_min"]:
        return {**base, "verdict": "合格",
                "reason": f"EV {ev:+.4f} / PF {pf_txt}（基準 PF≥{gate['pf_min']:.2f}）→ 90営業日判定へ継続"}
    return {**base, "verdict": "継続",
            "reason": f"PF {pf_txt} が 1.00〜{gate['pf_min']:.2f}（合格水準に未達・中止でもない）"}


def compute(state: dict, gate: dict, hist_mode: bool = False) -> dict:
    days, trips, windowed = slice_window(state, gate)
    counters = state.get("counters") or {}
    if windowed:
        equity0 = days[0]["equity_close"] - float(days[0].get("day_pnl") or 0.0)
    else:
        equity0 = float(state["equity_start_usdc"])
    equity = days[-1]["equity_close"] if days else equity0

    # ① 運用完遂率
    if days:
        d0 = dt.date.fromisoformat(days[0]["day"])
        d1 = dt.date.fromisoformat(days[-1]["day"])
        expected = (d1 - d0).days + 1
        ok_days = sum(1 for d in days if d.get("status") == "ok")
        completion = ok_days / expected * 100.0
    else:
        expected, ok_days, completion = 0, 0, 0.0

    # ② 実効スリッページ（片側95%）— 評価窓（直近90営業日）内の実測のみ
    samples, n_book_all = window_book_samples(state, days)
    excluded = n_book_all - len(samples)
    sl_p95 = p95(samples)
    if sl_p95 is not None:
        sl_status = "pass" if sl_p95 <= gate["slippage_max_bps"] else "fail"
        sl_value = f"{sl_p95:.3f} bps"
        extra = f"・窓外{excluded}件は除外" if excluded else ""
        sl_basis = f"L2ブック半スプレッド実測 p95 (n={len(samples)}、評価窓内{extra})"
    elif hist_mode:
        sl_status = "na"
        sl_value = "N/A（過去データのため免除）"
        sl_basis = (
            "HL APIに過去L2板データが無いため未測定 → 設計書§6.1-3 により判定対象外"
            f"（参考: ストレステスト課金 {gate['stress_slippage_model_bps']:.1f} bps）"
        )
    else:
        sl_status = "fail"
        sl_value = f"未測定（ストレステスト課金 {gate['stress_slippage_model_bps']:.3f} bps）"
        sl_basis = "板実測サンプル無し → 実測していない項目を合格にはしない（live運用では毎日計測）"

    # ③ 最大DD（評価窓内のEquityと窓開始直前残高から再計算。全期間ピークは持ち越さない）
    max_dd = window_max_dd(days, equity0)

    # ④ EV / PF（往復4未満は判定保留）
    wins = [t for t in trips if t["net_pnl"] > 0]
    losses = [t for t in trips if t["net_pnl"] < 0]
    gw = sum(t["net_pnl"] for t in wins)
    gl = -sum(t["net_pnl"] for t in losses)
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    ev = (sum(t["net_pnl"] for t in trips) / len(trips)) if trips else 0.0
    pf_txt = "∞" if pf == float("inf") else f"{pf:.3f}"
    pf_ev_value = f"EV {ev:+.4f} USDC/取引, PF {pf_txt} ({len(trips)}往復)"
    if len(trips) < gate["min_round_trips"]:
        pf_status = "pending"
        pf_note = f"判定保留（決済済み {len(trips)}/{gate['min_round_trips']}往復・サンプル不足）"
    elif pf >= gate["pf_min"] and ev > gate["ev_min_usdc"]:
        pf_status, pf_note = "pass", ""
    else:
        pf_status, pf_note = "fail", ""

    # ⑤ 反転頻度（月間換算）
    if days:
        span_days = max(1, (dt.date.fromisoformat(days[-1]["day"]) - dt.date.fromisoformat(days[0]["day"])).days + 1)
    else:
        span_days = 1
    reversals = int(counters.get("reversals", 0)) if not windowed else count_reversals(days)
    rev_per_month = reversals / span_days * 30.0

    # ⑥ 容量例外（累積カウンタ）
    cap_ex = int(counters.get("capacity_exceptions", 0))

    # ★ 強制ロスカット（累積カウンタ。1回でも発生したら失格）
    liq = int(counters.get("liquidations", 0))

    checks = [
        {
            "no": 1,
            "name": "運用完遂率",
            "value": f"{completion:.2f}% ({ok_days}/{expected}日)",
            "threshold": f"≥ {gate['completion_rate_min_pct']}%",
            "status": "pass" if completion >= gate["completion_rate_min_pct"] else "fail",
        },
        {
            "no": 2,
            "name": "実効スリッページ(片側p95)",
            "value": sl_value,
            "threshold": f"≤ {gate['slippage_max_bps']} bps",
            "status": sl_status,
            "basis": sl_basis,
        },
        {
            "no": 3,
            "name": "最大ドローダウン",
            "value": f"{max_dd:.3f}%",
            "threshold": f"≤ {gate['max_dd_max_pct']}%",
            "status": "pass" if abs(max_dd) <= gate["max_dd_max_pct"] else "fail",
            "basis": f"評価窓内Equityから再計算（開始残高 {equity0:.4f} USDC を初期ピーク）",
        },
        {
            "no": 4,
            "name": "期待値・PF",
            "value": pf_ev_value,
            "threshold": f"EV > 0 かつ PF ≥ {gate['pf_min']:.2f}（最低{gate['min_round_trips']}往復）",
            "status": pf_status,
            "note": pf_note,
        },
        {
            "no": 5,
            "name": "反転頻度(月間)",
            "value": f"{rev_per_month:.2f} 回/月 (期間内{reversals}回/{span_days}日)",
            "threshold": f"≤ {gate['reversals_per_month_max']} 回/月",
            "status": "pass" if rev_per_month <= gate["reversals_per_month_max"] else "fail",
        },
        {
            "no": 6,
            "name": "発注容量・ロット整合性",
            "value": f"例外 {cap_ex} 件",
            "threshold": f"= {gate['capacity_exceptions_max']} 件",
            "status": "pass" if cap_ex <= gate["capacity_exceptions_max"] else "fail",
        },
        {
            "no": "★",
            "name": "強制ロスカット回数",
            "value": f"{liq} 回",
            "threshold": f"= {gate['liquidations_max']} 回",
            "status": "pass" if liq <= gate["liquidations_max"] else "fail",
        },
    ]
    for c in checks:  # 後方互換（旧consumerはpassだけ見ていた）
        c["pass"] = c["status"] == "pass"

    fails = [c for c in checks if c["status"] == "fail"]
    pending_reasons: list[str] = []
    if len(days) < gate["min_days"]:
        pending_reasons.append(f"営業日不足 {len(days)}/{gate['min_days']}日")
    if len(trips) < gate["min_round_trips"]:
        pending_reasons.append(f"取引数不足 {len(trips)}/{gate['min_round_trips']}往復（項目4は判定保留）")

    if liq > gate["liquidations_max"]:
        verdict = "不合格"  # §6 ★: 清算1回で即時失格（営業日不足でも不合格）
    elif pending_reasons:
        verdict = "未達"
    elif fails:
        verdict = "不合格"
    else:
        verdict = "合格"

    # Phase 2-A インフラ適格（§6.1-1）
    infra_items = [c for c in checks if c["no"] in INFRA_CHECK_NOS]
    if len(days) < gate["infra_min_days"]:
        infra_verdict = f"未判定（{len(days)}/{gate['infra_min_days']}営業日）"
    elif any(c["status"] == "fail" for c in infra_items):
        infra_verdict = "不適格"
    elif any(c["status"] == "na" for c in infra_items):
        infra_verdict = "判定保留（スリッページ未測定）"
    else:
        infra_verdict = "適格"

    return {
        "namespace": state.get("namespace"),
        "days": len(days),
        "period": [days[0]["day"], days[-1]["day"]] if days else None,
        "window": {
            "window_days": gate["window_days"],
            "windowed": windowed,
            "min_days": gate["min_days"],
            "min_round_trips": gate["min_round_trips"],
        },
        "hist_mode": hist_mode,
        "trades": len(trips),
        "equity": equity,
        "total_pnl": equity - equity0,
        "total_pnl_pct": (equity / equity0 - 1) * 100.0 if equity0 else 0.0,
        "checks": checks,
        "verdict": verdict,
        "pending_reasons": pending_reasons,
        "fails": [f"{c['no']} {c['name']}" for c in fails],
        "infra_verdict": infra_verdict,
        "liquidations": liq,
        "midterm": midterm(state, gate),
        "real_orders": 0,
    }


def render(result: dict, gate: dict) -> str:
    L: list[str] = []
    L.append(f"# S-07 ペーパーゲート判定（{result['namespace']}）")
    L.append("")
    L.append(f"- 判定日時: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if result["period"]:
        L.append(
            f"- 対象期間: {result['period'][0]} 〜 {result['period'][1]}"
            f"（{result['days']}営業日 / 暗号資産は24時間稼働なのでUTC日次バー=営業日）"
        )
    else:
        L.append("- 対象期間: データ無し")
    win = result["window"]
    L.append(
        f"- 判定窓: 直近{win['window_days']}営業日ロール窓"
        f"（{'窓内に切って評価' if win['windowed'] else '全期間（90営業日以内）'}）"
        f" / 総合判定は{win['min_days']}営業日到達後 / 項目4は最低{win['min_round_trips']}往復"
    )
    L.append(
        "- 窓の適用範囲: 項目1〜6すべて。項目2は窓内の板実測のみ、"
        "項目3は窓開始直前残高を初期ピークに窓内Equityから再計算（窓外の履歴を持ち込まない）"
    )
    if result["hist_mode"]:
        L.append("- モード: 過去データ（hist）→ 項目2（スリッページ）は N/A（免除）")
        L.append("- 注意: hist の合格は過去データへの当てはめであり、前方の成績ではない（実弾移行の承認ではない）")
    L.append(
        f"- 成績: Equity {result['equity']:.4f} USDC / 累計 {result['total_pnl']:+.4f} USDC "
        f"({result['total_pnl_pct']:+.3f}%) / 往復トレード {result['trades']} 件"
    )
    L.append("")
    L.append(f"## 判定結果: **{result['verdict']}**")
    if result["verdict"] == "未達":
        L.append("")
        L.append("未達理由（材料不足。不合格ではない）:")
        for r in result["pending_reasons"]:
            L.append(f"- {r}")
    L.append("")
    L.append("| # | 項目 | 実測値 | 合格基準 | 判定 |")
    L.append("|---|---|---|---|---|")
    for c in result["checks"]:
        L.append(f"| {c['no']} | {c['name']} | {c['value']} | {c['threshold']} | {STATUS_LABEL[c['status']]} |")
    L.append("")
    fails = [c for c in result["checks"] if c["status"] == "fail"]
    if fails:
        L.append("### 基準外の項目" + ("（現時点の参考。判定は未達）" if result["verdict"] == "未達" else ""))
        for c in fails:
            extra = f"（{c['basis']}）" if c.get("basis") else ""
            L.append(f"- **{c['name']}**: 実測 {c['value']} が基準 {c['threshold']} を満たさない{extra}")
        L.append("")
    pending = [c for c in result["checks"] if c["status"] == "pending"]
    if pending:
        L.append("### 判定保留の項目")
        for c in pending:
            L.append(f"- **{c['name']}**: {c.get('note') or 'サンプル不足'}")
        L.append("")
    mt = result.get("midterm") or {}
    L.append(
        f"### 20営業日 中間判定（合格/継続/中止）: **{mt.get('verdict', '-')}**"
        f" — {mt.get('reason', '')}"
    )
    L.append("")
    L.append(f"### Phase 2-A インフラ適格（最短20営業日・項目1/2/3/6/★）: **{result['infra_verdict']}**")
    L.append("")
    L.append("判定基準の出典: `docs/CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md` §6（数値6項目＋強制ロスカット0回）／§6.1（90営業日窓・最低4往復・hist例外）")
    L.append("")
    L.append("> 実弾0・注文0。公開APIの読み取りのみで、`/exchange` も署名も使っていない。")
    return "\n".join(L) + "\n"


def simple(result: dict) -> str:
    """1行サマリ（cron/Telegram向け）。"""
    tail = ""
    if result["verdict"] == "未達":
        tail = " / 未達: " + ", ".join(result["pending_reasons"])
    elif result["fails"]:
        tail = " / NG: " + ", ".join(result["fails"])
    period = f"{result['period'][0]}〜{result['period'][1]}" if result["period"] else "データ無し"
    mt = result.get("midterm") or {}
    return (
        f"S-07ゲート[{result['namespace']}] {result['verdict']} "
        f"({result['days']}日 {period} / {result['trades']}取引 {result['total_pnl_pct']:+.2f}%)"
        f"{tail} / ロスカット{result['liquidations']}回 / 中間:{mt.get('verdict', '-')}"
        f" / インフラ:{result['infra_verdict']} / 実弾0"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S-07 paper gate judgment (90-business-day window)")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--namespace", default="live")
    ap.add_argument("--hist", action="store_true",
                    help="過去データ扱い（namespace が hist で始まる場合も同じ）: 項目2を N/A 免除")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="1行サマリのみ")
    ap.add_argument("--no-write", action="store_true", help="レポートを書かない")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    state = load_state(data_dir, args.namespace)
    hist_mode = bool(args.hist or args.namespace.startswith("hist"))
    result = compute(state, GATE_DESIGN, hist_mode=hist_mode)

    if not args.no_write:
        out = data_dir / "reports" / f"S07_GATE_{args.namespace}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(result, GATE_DESIGN))
        result["report"] = str(out)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    elif args.quiet:
        print(simple(result))
    else:
        print(render(result, GATE_DESIGN))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
