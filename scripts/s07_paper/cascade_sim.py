#!/usr/bin/env python3
"""Cascade-receiving limit-order simulation (shadow measurement, 0 orders / 0 deposits).

Reads the B2 liquidation-observation candle store (+ public HL candles) and asks:
  for a cascade event, if I rest a bid D% below the reference price, d minutes after
  detection, for W minutes, and exit H minutes after the fill — what is the
  fee-inclusive expectancy, the worst-event loss, and is there adverse selection?

PROXY WARNINGS (always carry into reports)
  * The "liquidation" side is a PROXY: no public per-coin liquidation feed exists.
    Events are detected from price/volume only.
  * "Best bid" is proxied by the bar close (no historical order book is available).

Pre-registered definitions (fixed before looking at PnL):
  event   : 10-minute close-to-close drop <= -EVENT_DROP_PCT and
            10-minute volume >= EVENT_VOL_MULT x the trailing-24h median of the
            same 10-minute volume (24h sampled every 30 min). First trigger per
            coin, later triggers within EVENT_CLUSTER_MIN suppressed.
  order   : limit L = close(t_place) * (1 - depth/100), placed at t_detect + delay,
            resting window W. Fill only if some bar low < L (strict penetration ->
            no fill on a mere touch: conservative queue assumption).
  exit    : market at the close of the bar H minutes after the fill bar.
  costs   : FEE_BPS per side (4.5 bps = the repo's measured HL taker assumption),
            notional 1000 USD per trial.
"""
import argparse
import json
import math
import random
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# ---------------------------------------------------------------- pre-registered
EVENT_DROP_PCT = 2.0        # 10-minute decline
EVENT_VOL_MULT = 3.0        # vs trailing 24h median 10-minute volume
EVENT_CLUSTER_MIN = 120     # suppress repeat triggers on the same coin
EPISODE_GAP_MIN = 60        # events closer than this belong to the same market episode
DEPTHS = [0.5, 1.0, 2.0, 3.0]
DELAYS_MIN = [0, 5, 15, 30]
REST_MIN = 30               # resting window
HOLD_MIN = 60               # holding horizon after fill
FEE_BPS_PER_SIDE = 4.5      # repo standard (HL taker); maker entry = 1.5 bps
NOTIONAL = 1000.0
MAX_ZERO_VOL_SHARE = 0.25   # coin-level data quality gate
MIN_TRAILING_H = 12         # >=12h of history for the volume baseline
BASELINE_SAMPLE_MIN = 30


def load(name):
    return json.loads((DATA / name).read_text())


def series_to_arrays(rows):
    return {
        "t": [r["t"] for r in rows], "o": [r["o"] for r in rows], "h": [r["h"] for r in rows],
        "l": [r["l"] for r in rows], "c": [r["c"] for r in rows], "v": [r["v"] for r in rows],
    }


def zero_vol_share(rows):
    return sum(1 for r in rows if r["v"] == 0) / len(rows) if rows else 1.0


def find_events(coin, rows, interval_min, drop_pct, vol_mult):
    if len(rows) < 30:
        return []
    a = series_to_arrays(rows)
    n = len(a["t"])
    k = max(1, 10 // interval_min)
    step = 86_400_000 // interval_min
    sample_step = max(1, BASELINE_SAMPLE_MIN // interval_min)
    min_trail = max(k + 2, int(MIN_TRAILING_H * 60 / interval_min))
    if n <= min_trail + k:
        return []
    pv = [0.0] * (n + 1)
    for i, x in enumerate(a["v"]):
        pv[i + 1] = pv[i] + x

    def vol10(i):
        return pv[i + 1] - pv[i + 1 - k]

    out, last_t = [], None
    for i in range(max(k, min_trail), n - k):
        c0, c1 = a["c"][i - k], a["c"][i]
        if not c0 or not c1:
            continue
        drop = (c1 / c0 - 1.0) * 100.0
        if drop > -drop_pct:
            continue
        lo = max(0, i - step)
        vols = [vol10(j) for j in range(lo, max(lo + 1, i - k), sample_step)]
        vols = [x for x in vols if x > 0]
        if len(vols) < 8:
            continue
        base = statistics.median(vols)
        if base <= 0:
            continue
        v10 = vol10(i)
        ratio = v10 / base
        if ratio < vol_mult:
            continue
        t_close = a["t"][i] + interval_min * 60_000
        if last_t is not None and (t_close - last_t) < EVENT_CLUSTER_MIN * 60_000:
            continue
        last_t = t_close
        out.append({"coin": coin, "bar_index": i, "t_close_ms": t_close,
                    "drop_pct": round(drop, 4), "vol_ratio": round(ratio, 3),
                    "vol10": round(v10, 3), "baseline10": round(base, 3),
                    "ref_close": c1, "bar_low": a["l"][i]})
    return out


def simulate_event(a, ev, interval_min, depth, delay_min, rest_min, hold_min):
    n = len(a["t"])
    i = ev["bar_index"]
    ib = delay_min // interval_min
    ip = i + ib
    wbars, hbars = rest_min // interval_min, hold_min // interval_min
    if ip >= n or ip + wbars + hbars >= n:
        return None
    ref = a["c"][ip]
    if not ref or ref <= 0:
        return None
    limit = ref * (1.0 - depth / 100.0)
    fill_j = None
    for j in range(ip + 1, min(n, ip + wbars) + 1):
        if a["l"][j] < limit:
            fill_j = j
            break
    ie = ip + hbars
    r_ref = (a["c"][ie] / ref - 1.0) * 100.0 if a["c"][ie] else None
    rec = {"coin": ev["coin"], "t_close_ms": ev["t_close_ms"], "depth": depth, "delay": delay_min,
           "ref": ref, "limit": limit, "r_ref_pct": r_ref, "filled": fill_j is not None,
           "pnl_usd": 0.0, "ret_net_pct": 0.0, "fill_min_after_place": None,
           "fill_dv_usd": None, "fill_bar_range_pct": None}
    if fill_j is not None:
        jx = fill_j + hold_min // interval_min
        if jx >= n:
            return None
        exit_px = a["c"][jx]
        gross = (exit_px / limit - 1.0) * 100.0
        net = gross - 2.0 * FEE_BPS_PER_SIDE / 100.0
        rec.update({"exit_px": exit_px, "gross_pct": round(gross, 4),
                    "ret_net_pct": round(net, 4), "pnl_usd": round(NOTIONAL * net / 100.0, 4),
                    "fill_min_after_place": (fill_j - ip) * interval_min,
                    "fill_dv_usd": round(a["v"][fill_j] * a["c"][fill_j], 2),
                    "fill_bar_range_pct": round((a["h"][fill_j] - a["l"][fill_j]) / limit * 100, 3)})
    return rec


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def boot_ci(values, seed=20261107, iters=2000):
    if len(values) < 5:
        return None
    rnd = random.Random(seed)
    n = len(values)
    m = sorted(sum(values[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return [round(m[int(iters * 0.025)], 4), round(m[int(iters * 0.975)], 4)]


def quintile_fill_rates(sub):
    """Fill rate per quintile of the fixed-horizon reference return (the selection curve)."""
    pts = [t for t in sub if t["r_ref_pct"] is not None]
    if len(pts) < 25:
        return None
    pts.sort(key=lambda t: t["r_ref_pct"])
    out, n = [], len(pts)
    for q in range(5):
        lo, hi = int(n * q / 5), int(n * (q + 1) / 5)
        chunk = pts[lo:hi]
        if not chunk:
            continue
        out.append({"q": q + 1, "n": len(chunk),
                    "r_ref_from_pct": round(chunk[0]["r_ref_pct"], 3),
                    "r_ref_to_pct": round(chunk[-1]["r_ref_pct"], 3),
                    "fill_rate": round(sum(1 for t in chunk if t["filled"]) / len(chunk), 4)})
    return out


def pointbiserial(sub):
    """Correlation between 'filled' and the reference return (selection strength)."""
    pts = [t for t in sub if t["r_ref_pct"] is not None]
    if len(pts) < 20:
        return None
    xs = [t["r_ref_pct"] for t in pts]
    ys = [1.0 if t["filled"] else 0.0 for t in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    return round(cov / (sx * sy), 4)


def mann_whitney_p(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    combined = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    ranks = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k2 in range(i, j + 1):
            ranks[k2] = avg
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, combined) if g == 0)
    n1, n2 = len(a), len(b)
    u = r1 - n1 * (n1 + 1) / 2.0
    mu, sd = n1 * n2 / 2.0, (n1 * n2 * (n1 + n2 + 1) / 12.0) ** 0.5
    if sd == 0:
        return None
    z = (u - mu) / sd
    return round(2 * (1 - 0.5 * (1 + math.erf(abs(z) / 2 ** 0.5))), 6)


def main():
    global EVENT_DROP_PCT, EVENT_VOL_MULT, REST_MIN, HOLD_MIN
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="1m", choices=["1m", "5m"])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--drop", type=float, default=EVENT_DROP_PCT)
    ap.add_argument("--volmult", type=float, default=EVENT_VOL_MULT)
    ap.add_argument("--rest", type=int, default=REST_MIN)
    ap.add_argument("--hold", type=int, default=HOLD_MIN)
    ap.add_argument("--min-dv-mult", type=float, default=0.0,
                    help="require fill-bar dollar volume >= mult x notional (liquidity gate)")
    ap.add_argument("--max-zero-vol", type=float, default=MAX_ZERO_VOL_SHARE,
                    help="coin-level data quality gate (share of zero-volume bars)")
    ap.add_argument("--only-coins", default=None, help="comma-separated allowlist")
    args = ap.parse_args()

    interval_min = 1 if args.interval == "1m" else 5
    src = "candles_1m_merged.json" if interval_min == 1 else "candles_5m.json"
    tag = args.tag or args.interval
    REST_MIN, HOLD_MIN = args.rest, args.hold
    EVENT_DROP_PCT, EVENT_VOL_MULT = args.drop, args.volmult
    coins = load(src)
    allow = {c.strip() for c in args.only_coins.split(",")} if args.only_coins else None

    excluded, events, arrays = {}, [], {}
    for coin, rows in coins.items():
        zs = zero_vol_share(rows)
        if (allow is not None and coin not in allow) or zs > args.max_zero_vol or len(rows) < 100:
            excluded[coin] = {"zero_vol_share": round(zs, 3), "n": len(rows)}
            continue
        arrays[coin] = series_to_arrays(rows)
        events.extend(find_events(coin, rows, interval_min, args.drop, args.volmult))
    events.sort(key=lambda e: e["t_close_ms"])

    # episodes: events separated by > EPISODE_GAP_MIN are different market episodes
    ep, ep_start = -1, None
    for e in events:
        if ep_start is None or (e["t_close_ms"] - ep_start) > EPISODE_GAP_MIN * 60_000:
            ep += 1
            ep_start = e["t_close_ms"]
        e["episode"] = ep

    trials, censored = [], 0
    for ev in events:
        for d in DEPTHS:
            for dl in DELAYS_MIN:
                tr = simulate_event(arrays[ev["coin"]], ev, interval_min, d, dl, REST_MIN, HOLD_MIN)
                if tr is None:
                    censored += 1
                else:
                    tr["event_id"] = f'{ev["coin"]}@{ev["t_close_ms"]}'
                    tr["episode"] = ev["episode"]
                    trials.append(tr)
    if args.min_dv_mult > 0:
        gate = args.min_dv_mult * NOTIONAL
        for t in trials:
            if t["filled"] and (t["fill_dv_usd"] or 0) < gate:
                t["filled_before_gate"] = True
                t["filled"] = False
                t["pnl_usd"] = 0.0

    cells = []
    for d in DEPTHS:
        for dl in DELAYS_MIN:
            sub = [t for t in trials if t["depth"] == d and t["delay"] == dl]
            filled = [t for t in sub if t["filled"]]
            pn_all = [t["pnl_usd"] for t in sub]
            pn_f = [t["pnl_usd"] for t in filled]
            top5 = sorted(pn_f, reverse=True)[:5]
            tot = sum(pn_f)
            ep_pnl = {}
            for t in filled:
                ep_pnl[t["episode"]] = ep_pnl.get(t["episode"], 0.0) + t["pnl_usd"]
            cells.append({
                "depth_pct": d, "delay_min": dl,
                "n_trials": len(sub), "n_filled": len(filled),
                "fill_rate": round(len(filled) / len(sub), 4) if sub else None,
                "ev_per_trial_usd": round(sum(pn_all) / len(pn_all), 4) if pn_all else None,
                "ev_per_fill_usd": round(tot / len(pn_f), 4) if pn_f else None,
                "total_usd": round(tot, 4) if pn_f else None,
                "median_usd": round(statistics.median(pn_f), 4) if pn_f else None,
                "q25_usd": round(pct(pn_f, 0.25), 4) if pn_f else None,
                "q75_usd": round(pct(pn_f, 0.75), 4) if pn_f else None,
                "worst_fill_usd": round(min(pn_f), 4) if pn_f else None,
                "best_fill_usd": round(max(pn_f), 4) if pn_f else None,
                "win_rate": round(sum(1 for x in pn_f if x > 0) / len(pn_f), 4) if pn_f else None,
                "ev_ci95_per_fill": boot_ci(pn_f),
                "top5_share_of_total": round(sum(top5) / tot, 4) if tot and tot > 0 else None,
                "ev_per_fill_ex_top5_usd": round((tot - sum(top5)) / max(1, len(pn_f) - 5), 4)
                if len(pn_f) > 5 and tot and tot > 0 else None,
                "median_r_ref_filled_pct": round(statistics.median(
                    [t["r_ref_pct"] for t in filled]), 4) if filled else None,
                "median_fill_min_after_place": round(statistics.median(
                    [t["fill_min_after_place"] for t in filled]), 2) if filled else None,
                "n_episodes_with_fills": len(ep_pnl),
                "worst_episode_pnl_usd": round(min(ep_pnl.values()), 4) if ep_pnl else None,
                "best_episode_pnl_usd": round(max(ep_pnl.values()), 4) if ep_pnl else None,
                "mean_episode_pnl_usd": round(sum(ep_pnl.values()) / len(ep_pnl), 4) if ep_pnl else None,
                "max_concurrent_fills": max((sum(1 for t in filled if t["episode"] == k)
                                             for k in ep_pnl), default=0),
                # post-fill markout from OUR entry price L: negative = adverse selection wins
                "mean_gross_pct_fill": round(sum(t["gross_pct"] for t in filled) / len(filled), 4)
                if filled else None,
                "median_gross_pct_fill": round(statistics.median(
                    [t["gross_pct"] for t in filled]), 4) if filled else None,
                "share_negative_gross": round(sum(1 for t in filled if t["gross_pct"] < 0)
                                              / len(filled), 4) if filled else None,
                "fill_rate_by_r_ref_quintile": quintile_fill_rates(sub),
                "pointbiserial_fill_vs_rref": pointbiserial(sub),
            })

    adv = []
    for d in DEPTHS:
        for dl in DELAYS_MIN:
            sub = [t for t in trials if t["depth"] == d and t["delay"] == dl
                   and t["r_ref_pct"] is not None]
            f = [t["r_ref_pct"] for t in sub if t["filled"]]
            u = [t["r_ref_pct"] for t in sub if not t["filled"]]
            adv.append({
                "depth_pct": d, "delay_min": dl, "n_filled": len(f), "n_unfilled": len(u),
                "mean_r_ref_filled_pct": round(sum(f) / len(f), 4) if f else None,
                "mean_r_ref_unfilled_pct": round(sum(u) / len(u), 4) if u else None,
                "median_r_ref_filled_pct": round(statistics.median(f), 4) if f else None,
                "median_r_ref_unfilled_pct": round(statistics.median(u), 4) if u else None,
                "diff_filled_minus_unfilled_pp": round(sum(f) / len(f) - sum(u) / len(u), 4)
                if f and u else None,
                "mwu_p": mann_whitney_p(f, u),
            })

    per_event = {}
    for t in trials:
        rec = per_event.setdefault(t["event_id"], {"coin": t["coin"], "t": t["t_close_ms"],
                                                   "worst_pnl": 0.0, "n_filled": 0})
        rec["worst_pnl"] = min(rec["worst_pnl"], t["pnl_usd"])
        rec["n_filled"] += 1 if t["filled"] else 0

    by_day = {}
    for t in trials:
        if not t["filled"]:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(t["t_close_ms"] / 1000))
        by_day.setdefault(day, {"n": 0, "pnl": 0.0})
        by_day[day]["n"] += 1
        by_day[day]["pnl"] = round(by_day[day]["pnl"] + t["pnl_usd"], 2)

    result = {
        "tag": tag, "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_file": src, "interval_min": interval_min,
        "proxy_warning": "清算はPROXY（価格・出来高からの代理検知であり実清算値ではない）／最良bidは足の終値で代理",
        "assumptions": {"event_drop_pct": args.drop, "event_vol_mult": args.volmult,
                        "event_cluster_min": EVENT_CLUSTER_MIN, "depths_pct": DEPTHS,
                        "delays_min": DELAYS_MIN, "rest_min": REST_MIN, "hold_min": HOLD_MIN,
                        "fee_bps_per_side": FEE_BPS_PER_SIDE, "round_trip_bps": 2 * FEE_BPS_PER_SIDE,
                        "notional_usd": NOTIONAL, "min_dv_gate_mult": args.min_dv_mult,
                        "fill_rule": "bar low < limit (strict penetration)",
                        "exit_rule": "close of the bar HOLD_MIN after the fill bar",
                        "zero_vol_gate": MAX_ZERO_VOL_SHARE, "min_trailing_h": MIN_TRAILING_H},
        "universe": {"coins": len(coins), "usable": len(coins) - len(excluded), "excluded": excluded},
        "n_events": len(events), "n_trials": len(trials), "n_censored": censored,
        "n_episodes": (max((e["episode"] for e in events), default=-1) + 1),
        "cells": cells, "adverse_selection": adv,
        "adverse_selection_note": (
            "adverse_selection の filled vs unfilled 比較は、約定の定義上（下落しなければ約定しない）"
            "必ず負になる機械的な部分を含む。経済的に意味があるのは cells 側の "
            "mean_gross_pct_fill / median_gross_pct_fill（約定価格Lから保有後の値動き＝逆選択そのもの）と、"
            "fill_rate_by_r_ref_quintile（どの値動き帯で約定が増えるか）である。"),
        "by_day_filled": {k: v for k, v in sorted(by_day.items())},
        "per_event": sorted(per_event.values(), key=lambda r: r["worst_pnl"])[:15],
        "events": events,
    }
    out = DATA / f"results_{tag}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"[{tag}] events={len(events)} trials={len(trials)} censored={censored} -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
