#!/usr/bin/env python3
"""【項目20】集計・判定 — windows.json から差の大きさ・持続・反転・残差を出す（読み取りのみ）。

入力: data/s07_paper/xvenue_funding/windows.json（xvenue_funding_diff.py が生成）
出力: analysis.json, REPORT_XVENUE_FUNDING.md

判定の骨格（一次資料 §項目20 の物差し）:
  1. 差の大きさ      … |diff| の分布（bps/8h）と年率換算
  2. 持続時間        … 同符号の連続長（8h窓数）と、符号反転の回数/週
  3. 時間帯別・銘柄別 … UTC精算スロット(00/08/16)別、銘柄別
  4. 残差            … 両脚の手数料 + 証拠金分断コストを引いた後、週の大半で残るか
  5. ベーシスリスク  … 価格差の変動（手数料ではないが、往復の損益に直接乗る）

すべて「窓合計」で揃えている: [T, T+8h) に各場が実際に徴収/支払した額の合計。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

DAYS_PER_YEAR = 365.0
WINDOWS_PER_DAY = 3           # 8時間窓
WINDOWS_PER_WEEK = 21

FEE_BPS = {"hyperliquid_taker": 4.5, "binance_taker": 5.0, "bybit_taker": 5.5}
LEVERAGE = 3.0
BUFFER = 0.20
TRANSFER_USD_MONTH = 2.0
L_EFF = LEVERAGE / (1.0 + BUFFER)          # 2.5倍ぶんの建玉が資本1に対して立つ（片脚あたり）


def bps(x: float) -> float:
    return x * 10_000.0


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    idx = (len(s) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return s[int(idx)]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def run_lengths(signs):
    if not signs:
        return []
    out, cur = [], 1
    for a, b in zip(signs, signs[1:]):
        if a == b:
            cur += 1
        else:
            out.append(cur)
            cur = 1
    out.append(cur)
    return out


def fmt(v, nd=2, na="—"):
    if v is None:
        return na
    return f"{v:,.{nd}f}"


def _phase_sensitivity(root: Path, coins: list[str], key_hours=(-8, -4, 0)) -> dict:
    """窓の位相（HLのどの8時間をCEXの8時間精算と組にするか）を振って結論の安定性を見る。

    HL の fundingHistory は1時間ごと、Binance/Bybit は8時間ごと。位相は自明でないため、
    複数の位相で |diff| と週の符号を出し、結論が位相に依存しないことを示す。
    """
    import datetime as _dt
    raw = {}
    for coin in coins:
        raw[coin] = {}
        for v in ("hl", "bn"):
            p = root / "raw" / f"{v}_{coin}.json"
            if p.exists():
                raw[coin][v] = json.loads(p.read_text())
    wins_blob = json.loads((root / "windows.json").read_text())
    wins = sorted({w["t_ms"] for w in wins_blob["windows"]})
    H = 3_600_000

    def key(ms):
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = {"note": "offset = HL窓の開始を CEX精算時刻から何時間ずらすか。0 が本文の定義。",
           "offsets": {}}
    for k0 in key_hours:
        diffs, per_week = [], defaultdict(list)
        for t in wins:
            week = int((t - wins[0]) / (7 * 86_400_000)) + 1
            vals = []
            for coin in coins:
                hl, bn = raw[coin].get("hl"), raw[coin].get("bn")
                if not hl or not bn:
                    continue
                s = 0.0
                ok = True
                for i in range(8):
                    k = key(t + (k0 + i) * H)
                    if k not in hl:
                        ok = False
                        break
                    s += hl[k]
                if not ok or key(t) not in bn:
                    continue
                vals.append((s - bn[key(t)]) * 1e4)
            if vals:
                diffs.append(statistics.fmean(vals))
                per_week[week].append(statistics.fmean(vals))
        wk_sums = {w: sum(v) for w, v in per_week.items()}
        out["offsets"][str(k0)] = {
            "n_windows": len(diffs),
            "mean_abs_bps": statistics.fmean([abs(x) for x in diffs]) if diffs else None,
            "mean_bps": statistics.fmean(diffs) if diffs else None,
            "weeks_positive": sum(1 for v in wk_sums.values() if v > 0),
            "n_weeks": len(wk_sums),
        }
    return out


def analyze(path: Path) -> dict:
    blob = json.loads(path.read_text())
    meta, rows = blob["meta"], blob["windows"]
    coins = meta["coins"]

    # 期間（実観測できた範囲）
    t0 = min(r["t_ms"] for r in rows)
    t1 = max(r["t_ms"] for r in rows) + 8 * 3600_000
    days = (t1 - t0) / 86_400_000
    weeks = days / 7.0

    by_coin: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_coin[r["coin"]].append(r)
    for c in by_coin:
        by_coin[c].sort(key=lambda x: x["t_ms"])

    # ---------- 1. 差の大きさ（銘柄別） ----------
    per_coin = {}
    for coin in coins:
        rs = by_coin[coin]
        for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
            d = [r[key] for r in rs if r.get(key) is not None]
            if not d:
                continue
            db = [bps(x) for x in d]
            absdb = [abs(x) for x in db]
            signs = [1 if x >= 0 else -1 for x in db]
            gross_ann = sum(absdb) / 10_000.0 * (DAYS_PER_YEAR / days) * 100.0
            slot = defaultdict(list)
            for r in rs:
                if r.get(key) is not None:
                    slot[r["hour_utc"]].append(abs(bps(r[key])))
            per_coin.setdefault(coin, {})[pair] = {
                "n": len(d),
                "mean_bps": statistics.fmean(db),
                "median_bps": statistics.median(db),
                "mean_abs_bps": statistics.fmean(absdb),
                "median_abs_bps": statistics.median(absdb),
                "p10_bps": pct(db, 0.10), "p90_bps": pct(db, 0.90),
                "p90_abs_bps": pct(absdb, 0.90),
                "max_abs_bps": max(absdb),
                "share_positive": sum(1 for x in db if x > 0) / len(db),
                "gross_ann_pct_of_notional": gross_ann,
                "sign_flips_per_week": sum(1 for a, b in zip(signs, signs[1:]) if a != b) / weeks,
                "run_len_windows_median": statistics.median(run_lengths(signs)),
                "run_len_windows_max": max(run_lengths(signs)),
                "abs_by_utc_hour": {str(h): statistics.fmean(v) for h, v in sorted(slot.items())},
            }

    # ---------- 時間帯別（全銘柄まとめ） ----------
    slot_all = {"hl_bn": defaultdict(list), "hl_by": defaultdict(list)}
    for r in rows:
        for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
            if r.get(key) is not None:
                slot_all[pair][r["hour_utc"]].append(bps(r[key]))
    hour_band = {}
    for pair, d in slot_all.items():
        hour_band[pair] = {
            str(h): {"n": len(v), "mean_abs_bps": statistics.fmean([abs(x) for x in v]),
                     "mean_bps": statistics.fmean(v),
                     "share_positive": sum(1 for x in v if x > 0) / len(v)}
            for h, v in sorted(d.items())
        }

    # ---------- 2＋4. 週別・残差 ----------
    # 手数料: 往復 = (HL taker + CEX taker) × 2脚
    fee = {"hl_bn": (FEE_BPS["hyperliquid_taker"] + FEE_BPS["binance_taker"]) * 2,
           "hl_by": (FEE_BPS["hyperliquid_taker"] + FEE_BPS["bybit_taker"]) * 2}

    weekly = {}
    for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
        wk = []
        # 週は観測開始から7日刻み（UTC）
        start_ms = t0
        for w in range(int(math.floor(days / 7))):
            ws = start_ms + w * 7 * 86_400_000
            we = ws + 7 * 86_400_000
            block = [r for r in rows if ws <= r["t_ms"] < we]
            if not block:
                continue
            # 銘柄ごとではなく「等ウェイト8銘柄ポートフォリオ」で見る（1銘柄集中を避ける）
            coin_sum, coin_abs, n_by_coin = {}, {}, {}
            for r in block:
                if r.get(key) is None:
                    continue
                coin_sum.setdefault(r["coin"], []).append(bps(r[key]))
                coin_abs.setdefault(r["coin"], []).append(abs(bps(r[key])))
            if not coin_sum:
                continue
            s_mean = statistics.fmean([sum(v) for v in coin_sum.values()])
            a_mean = statistics.fmean([sum(v) for v in coin_abs.values()])
            wk.append({
                "week_index": w + 1,
                "from_utc": dt.datetime.fromtimestamp(ws / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                "to_utc": dt.datetime.fromtimestamp(we / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                "sum_diff_bps": s_mean,
                "sum_abs_diff_bps": a_mean,
                "dominant_sign": 1 if s_mean >= 0 else -1,
                "per_coin_sum_bps": {c: sum(v) for c, v in coin_sum.items()},
            })
        weekly[pair] = wk

    # 実行規則（すべて「見たことのある情報だけ」で方向を決める）
    rules = {}
    for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
        wk = weekly[pair]
        f = fee[pair]
        # R1: 方向を固定（HL short / CEX long。DEXのプレミアムが高いという事前の向き）
        r1 = [w["sum_diff_bps"] - f for w in wk]          # 週ごとに建て直す（週1往復）
        r1_total = sum(w["sum_diff_bps"] for w in wk) - f  # 8週連続保有（手数料は最初の1往復のみ）
        # R2: 方向は「前週の符号」で決める（先読みなし）。初週は事前の向き=HL short。
        r2, cur_dir, flips = [], None, 0
        for i, w in enumerate(wk):
            new_dir = 1 if (i == 0) else (1 if wk[i - 1]["sum_diff_bps"] >= 0 else -1)
            if cur_dir is None:
                cost = f
            elif new_dir != cur_dir:
                cost = f
                flips += 1
            else:
                cost = 0.0
            r2.append(new_dir * w["sum_diff_bps"] - cost)
            cur_dir = new_dir
        # R3: 各窓で方向を完璧に当てる（実行不能な上限）
        r3 = sum(w["sum_abs_diff_bps"] for w in wk) - f
        rules[pair] = {
            "fee_round_trip_bps": f,
            "R1_fixed_direction_weekly_net_bps": r1,
            "R1_fixed_direction_weeks_positive": sum(1 for x in r1 if x > 0),
            "R1_continuous_hold_total_net_bps": r1_total,
            "R2_trailing_sign_weekly_net_bps": r2,
            "R2_trailing_sign_weeks_positive": sum(1 for x in r2 if x > 0),
            "R2_trailing_sign_flips": flips,
            "R3_perfect_foresight_total_net_bps": r3,
            "R3_weeks_positive": len(wk) if r3 > 0 else 0,
            "R3_note": "実行不能な上限（各窓の符号を事前に知る必要がある）",
            "n_weeks": len(wk),
        }

    # ---------- 4b. 週の大半で残るか（連続保有・手数料を週割り） ----------
    # 実行可能な最も有利な形: 8週間ずっと同じ向きで持ち、手数料は最初と最後の1往復だけ。
    # その手数料を週に按分して、週ごとの純取り分がプラスかを数える。
    amortized = {}
    for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
        f = fee[pair]
        n = len(weekly[pair])
        wk_fee = f / n if n else f
        nets = [w["sum_diff_bps"] - wk_fee for w in weekly[pair]]
        amortized[pair] = {
            "assumption": f"{n}週連続保有・往復手数料{f}bpsを週に按分（{wk_fee:.2f}bps/週）",
            "weekly_net_bps": nets,
            "weeks_positive": sum(1 for x in nets if x > 0),
            "n_weeks": n,
            "total_net_bps": sum(nets),
            "ann_pct_of_notional": sum(nets) / 10_000.0 * (DAYS_PER_YEAR / days) * 100.0,
        }

    # ---------- 4c. 「200倍」の正体: 比ではなく絶対額で見る ----------
    ratio = {"hl_bn": [], "hl_by": []}
    for r in rows:
        for pair, key in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
            other = "bn" if pair == "hl_bn" else "by"
            a_, b_ = r.get("hl"), r.get(other)
            if a_ is None or b_ is None or abs(b_) < 1e-12:
                continue
            ratio[pair].append(abs(a_) / abs(b_))
    ratio_stats = {}
    for pair, v in ratio.items():
        n200 = sum(1 for x in v if x > 200)
        ratio_stats[pair] = {
            "n_windows_with_nonzero_cex": len(v),
            "median_ratio": statistics.median(v) if v else None,
            "p90_ratio": pct(v, 0.90),
            "max_ratio": max(v) if v else None,
            "windows_ratio_gt_200": n200,
            "share_gt_200_pct": (n200 / len(v) * 100) if v else None,
            "note": "比が大きい窓は CEX レートがほぼ0の窓。損益に効くのは比ではなく絶対bps差。",
        }

    # ---------- 6. 実現損益シミュレーション（4週間保有・ベーシス込み） ----------
    # 28日保有を、標本内の全開始点で回す（先読みなし: 方向は直前14日の平均差で決める）。
    # 損益 = 方向×ファンディング差の積み上げ − ベーシスの変化 − 往復手数料。
    # ベーシスは「HL約定終値 − Binance mark」。d=+1（HL short / CEX long）なら
    #   価格損益 = −(b_exit − b_entry)  … HLが上がると short が負けるため。
    rolling = {}
    for pair, key, other in (("hl_bn", "diff_bn", "bn"), ("hl_by", "diff_by", "by")):
        f = fee[pair]
        hold = 28 * 3                      # 28日 = 84窓
        trades = []
        for coin in coins:
            rs = by_coin[coin]
            n = len(rs)
            for i in range(n - hold):
                blk = rs[i:i + hold]
                if any(r.get(key) is None for r in blk):
                    continue
                # 方向: 直前14日の平均差（なければ固定 +1）。先読みなし。
                look = rs[max(0, i - 42):i]
                ds = [r[key] for r in look if r.get(key) is not None]
                d = 1 if (not ds or statistics.fmean(ds) >= 0) else -1
                fund = sum(bps(r[key]) for r in blk)
                b0, b1 = blk[0].get("basis_bps"), blk[-1].get("basis_bps")
                basis_pnl = None
                if b0 is not None and b1 is not None:
                    basis_pnl = -d * (b1 - b0)
                total = d * fund + (basis_pnl if basis_pnl is not None else 0.0) - f
                trades.append({
                    "coin": coin, "start": blk[0]["t_utc"], "dir": d,
                    "funding_bps": d * fund, "basis_pnl_bps": basis_pnl,
                    "net_bps": total,
                })
        if not trades:
            continue
        nets = [t["net_bps"] for t in trades]
        fund_only = [t["funding_bps"] - f for t in trades]
        # 滑り（往復4回の約定 × 2.0bps。既存 S-07 の約定モデルと同じ想定）を足した感度
        slip = 4 * 2.0
        nets_slip = [x - slip for x in nets]
        per_coin_hold = {}
        for coin in coins:
            cn = [t["net_bps"] for t in trades if t["coin"] == coin]
            if not cn:
                continue
            per_coin_hold[coin] = {
                "n": len(cn), "mean_bps": statistics.fmean(cn),
                "median_bps": statistics.median(cn), "sd_bps": statistics.pstdev(cn),
                "share_positive": sum(1 for x in cn if x > 0) / len(cn),
                "worst_bps": min(cn),
            }
        rolling[pair] = {
            "n_holds": len(trades),
            "hold_days": 28,
            "fee_bps": f,
            "net_mean_bps": statistics.fmean(nets),
            "net_median_bps": statistics.median(nets),
            "net_sd_bps": statistics.pstdev(nets),
            "net_p10_bps": pct(nets, 0.10),
            "net_p90_bps": pct(nets, 0.90),
            "net_worst_bps": min(nets),
            "net_best_bps": max(nets),
            "share_positive": sum(1 for x in nets if x > 0) / len(nets),
            "slippage_assumed_bps": slip,
            "net_mean_bps_with_slippage": statistics.fmean(nets_slip),
            "net_median_bps_with_slippage": statistics.median(nets_slip),
            "share_positive_with_slippage": sum(1 for x in nets_slip if x > 0) / len(nets_slip),
            "funding_only_mean_bps": statistics.fmean(fund_only),
            "funding_only_share_positive": sum(1 for x in fund_only if x > 0) / len(fund_only),
            "basis_pnl_mean_bps": statistics.fmean([t["basis_pnl_bps"] for t in trades
                                                    if t["basis_pnl_bps"] is not None]),
            "basis_pnl_sd_bps": statistics.pstdev([t["basis_pnl_bps"] for t in trades
                                                   if t["basis_pnl_bps"] is not None]),
            "per_coin": per_coin_hold,
            "note": "手数料控除後・ベーシス込みの実現損益。方向は直前14日平均差で決定（先読みなし）。"
                    "ベーシスは約定終値どうし（HL 1h close vs Binance 1h close）。",
        }

    # ---------- 7. 位相の感度（窓の切り方が結論を変えないか） ----------
    phase = _phase_sensitivity(path.parent, coins, key_hours=(-8, -4, 0))

    # ---------- 5. ベーシスリスク ----------
    basis = {}
    for coin in coins:
        b = [r["basis_bps"] for r in by_coin[coin] if r.get("basis_bps") is not None]
        if len(b) < 10:
            basis[coin] = {"n": len(b)}
            continue
        ch = [b[i] - b[i - 1] for i in range(1, len(b))]
        basis[coin] = {
            "n": len(b),
            "mean_bps": statistics.fmean(b),
            "stdev_bps": statistics.pstdev(b),
            "min_bps": min(b), "max_bps": max(b),
            "range_bps": max(b) - min(b),
            "abs_change_per_8h_mean_bps": statistics.fmean([abs(x) for x in ch]),
            "abs_change_per_8h_p90_bps": pct([abs(x) for x in ch], 0.90),
        }

    # ---------- 集計（8銘柄等ウェイトポートフォリオ） ----------
    agg = {}
    for pair, pkey in (("hl_bn", "diff_bn"), ("hl_by", "diff_by")):
        per_window = defaultdict(list)
        for r in rows:
            v = r.get(pkey)
            if v is not None:
                per_window[r["t_ms"]].append(bps(v))
        port = [statistics.fmean(per_window[t]) for t in sorted(per_window)]
        port_abs = [abs(x) for x in port]
        signs = [1 if x >= 0 else -1 for x in port]
        agg[pair] = {
            "n_windows": len(port),
            "port_mean_bps_per_8h": statistics.fmean(port),
            "port_mean_abs_bps_per_8h": statistics.fmean(port_abs),
            "port_median_abs_bps_per_8h": statistics.median(port_abs),
            "port_share_positive": sum(1 for x in port if x > 0) / len(port),
            "gross_ann_pct_of_notional": sum(port_abs) / 10_000.0 * (DAYS_PER_YEAR / days) * 100.0,
            "sign_flips_per_week": sum(1 for a, b in zip(signs, signs[1:]) if a != b) / weeks,
            "run_len_median": statistics.median(run_lengths(signs)),
            "run_len_max": max(run_lengths(signs)),
            "weeks_net_positive_R1": rules[pair]["R1_fixed_direction_weeks_positive"],
            "weeks_net_positive_R2": rules[pair]["R2_trailing_sign_weeks_positive"],
            "n_weeks": rules[pair]["n_weeks"],
        }

    # 証拠金分断コスト（資本あたり）
    frag = {
        "leverage_per_venue": LEVERAGE, "margin_buffer": BUFFER, "l_eff": L_EFF,
        "notional_per_unit_capital": 2 * L_EFF,
        "note": "2脚ぶんの建玉が資本1あたり 2×L_eff 立つ。片脚だけなら L_eff。"
                "同じ差でも資本あたりの取り分は片脚の 1/2 になる（これが分断コストの本体）。",
        "transfer_cost_table": {},
    }
    for notional_per_leg in (500, 1000, 5000, 10000):
        cap = 2 * notional_per_leg / L_EFF
        frag["transfer_cost_table"][f"N={notional_per_leg}"] = {
            "capital_usd": round(cap, 0),
            "transfer_drag_pct_per_year": round(TRANSFER_USD_MONTH * 12 / cap * 100, 3),
        }

    return {
        "meta": meta, "days": days, "weeks": weeks,
        "period": {"from_utc": dt.datetime.fromtimestamp(t0 / 1000, dt.timezone.utc).isoformat(),
                   "to_utc": dt.datetime.fromtimestamp(t1 / 1000, dt.timezone.utc).isoformat()},
        "per_coin": per_coin, "hour_band": hour_band,
        "weekly": weekly, "rules": rules, "basis": basis,
        "amortized_continuous": amortized, "ratio_stats": ratio_stats,
        "rolling_hold": rolling, "phase_sensitivity": phase,
        "aggregate": agg, "fragmentation": frag,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/root/current-work/yowayowa-investor/data/s07_paper/xvenue_funding")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    a = analyze(d / "windows.json")
    (d / "analysis.json").write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "days": round(a["days"], 2), "weeks": round(a["weeks"], 2),
        "hl_bn_port_mean_abs_bps": round(a["aggregate"]["hl_bn"]["port_mean_abs_bps_per_8h"], 3),
        "hl_bn_gross_ann_pct": round(a["aggregate"]["hl_bn"]["gross_ann_pct_of_notional"], 2),
        "hl_bn_flips_per_week": round(a["aggregate"]["hl_bn"]["sign_flips_per_week"], 2),
        "hl_bn_weeks_pos_R1": a["aggregate"]["hl_bn"]["weeks_net_positive_R1"],
        "hl_bn_weeks_pos_R2": a["aggregate"]["hl_bn"]["weeks_net_positive_R2"],
        "n_weeks": a["aggregate"]["hl_bn"]["n_weeks"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
