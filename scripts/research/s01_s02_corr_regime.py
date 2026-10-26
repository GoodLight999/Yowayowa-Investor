#!/usr/bin/env python3
"""Phase A: S-01 x S-02 return correlation and regime-trigger validation (t_3478ed58).

Research script. Read-only w.r.t. tracked JSONs and data/; strategy math is
delegated to the existing engine (yowayowa.services.backtest). Deterministic:
fixed seed 20260930, no timestamps in outputs, sorted JSON keys.
Writes: research/s01_s02_corr_regime_phaseA.json,
        research/s01_s02_regime_states_phaseA.jsonl,
        docs/RESEARCH_S01_S02_CORR_REGIME.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import correlation, fmean, geometric_mean, stdev

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from yowayowa.backtest_models import BacktestRunRequest, BacktestStrategyDefinition  # noqa: E402
from yowayowa.crypto_acquisition import default_store as default_crypto_store  # noqa: E402
from yowayowa.services.backtest import _target_weights, run_backtest  # noqa: E402
from yowayowa.stock_acquisition import default_store as default_stock_store  # noqa: E402

SEED = 20260930
COST_RATE = 0.0015  # 10 bps commission + 5 bps slippage, same as engine runners
CRYPTO_SYMBOLS = ["BTC", "ETH"]
STOCK_SYMBOLS = ["SPY", "IWM", "JNJ", "PG"]
CRYPTO_JSON = REPO / "crypto_backtest_results.json"
STOCK_JSON = REPO / "backtest_results_s02.json"
FRED = REPO / "data" / "macro-observations" / "fred.jsonl"


# ------------------------------------------------------------------ utilities
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_tree_hashes() -> dict:
    out = {}
    for sub, symbols in (("crypto-ohlcv", CRYPTO_SYMBOLS), ("stock-ohlcv", STOCK_SYMBOLS)):
        for sym in symbols:
            for f in sorted((REPO / "data" / sub / sym).iterdir()):
                out[str(f.relative_to(REPO))] = sha256_file(f)
    out["data/macro-observations/fred.jsonl"] = sha256_file(FRED)
    return out


def pearson(xs, ys) -> float:
    return correlation(list(xs), list(ys))


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        v = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = v
        i = j + 1
    return r


def spearman(xs, ys) -> float:
    return pearson(ranks(list(xs)), ranks(list(ys)))


def fisher_z_ci(r, n: int):
    if r is None or n < 4 or not math.isfinite(r):
        return None
    r = max(min(r, 0.999999), -0.999999)
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1 / math.sqrt(n - 3)
    return [math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se)]


def rng_for(tag: str) -> random.Random:
    return random.Random(f"{SEED}:{tag}")


def stationary_bootstrap_indices(n: int, mean_block: int, rng: random.Random) -> list:
    p = 1.0 / mean_block
    idx = []
    i = rng.randrange(n)
    for _ in range(n):
        idx.append(i)
        if rng.random() < p:
            i = rng.randrange(n)
        else:
            i = (i + 1) % n
    return idx


def boot_ci(xs, ys, stat, mean_block: int, tag: str, samples: int = 2000):
    xs, ys = list(xs), list(ys)
    n = len(xs)
    if n < 10:
        return None
    rng = rng_for(tag)
    vals = []
    for _ in range(samples):
        idx = stationary_bootstrap_indices(n, mean_block, rng)
        v = stat([xs[i] for i in idx], [ys[i] for i in idx])
        if v is not None and math.isfinite(v):
            vals.append(v)
    if len(vals) < samples // 2:
        return None
    vals.sort()
    return [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]


def max_drawdown(returns) -> float:
    peak = equity = 1.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
    return min(1.0, equity / peak - 1) if peak > 0 else -1.0


def running_max_drawdown(levels) -> float:
    peak = None
    dd = 0.0
    for e in levels:
        peak = e if peak is None else max(peak, e)
        dd = min(dd, e / peak - 1)
    return dd


def qtl(sorted_vals, q):
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def corr_stats(xs, ys, tag):
    xs, ys = list(xs), list(ys)
    n = len(xs)
    if n < 3:
        return {"n": n, "pearson": None, "spearman": None}
    r = pearson(xs, ys)
    return {
        "n": n,
        "pearson": r,
        "spearman": spearman(xs, ys),
        "pearson_stationary_bootstrap_ci": boot_ci(xs, ys, pearson, 4, tag + "|pear"),
        "pearson_fisher_z_ci": fisher_z_ci(r, n),
    }


# ------------------------------------------------------------------ loading
def load_histories():
    cstore = default_crypto_store(REPO / "data")
    crypto = {s: cstore.read(s, provider="binance", limit=10_000) for s in CRYPTO_SYMBOLS}
    sstore = default_stock_store(REPO / "data")
    stock = {s: sstore.read(s, provider="alpaca", limit=10_000) for s in STOCK_SYMBOLS}
    return crypto, stock


def crypto_definitions():
    out = {}
    for sid, reb, lookback in (("crypto_trend_dual_ma", "daily", 200), ("crypto_equal_weight", "monthly", None)):
        kw = dict(
            id=sid, name=sid, description="phase A reproduction", signal=sid,
            universe=CRYPTO_SYMBOLS, rebalance=reb, max_positions=2, weighting="equal",
        )
        if lookback:
            kw["lookback"] = lookback
        out[sid] = BacktestStrategyDefinition(**kw)
    return out


def stock_definitions():
    from run_backtests_vol_target import definitions

    return {d.id: d for d in definitions()}


def run_engine(strategy, histories, start, end, annualization, provider):
    req = BacktestRunRequest(
        strategy_id=strategy.id, start=start, end=end,
        commission_bps=10, slippage_bps=5, bootstrap_samples=0, bootstrap_seed=SEED,
        provider=provider, annualization_days=annualization,
    )
    return run_backtest(req, strategy, histories, _include_oos=False)


def map_rows(rows, end: date) -> dict:
    out = {}
    for row in rows:
        day = date.fromisoformat(str(row["as_of"])[:10])
        if day > end:
            continue
        close = float(row["close"])
        if math.isfinite(close) and close > 0:
            out[day] = row
    return out


def load_dgs10() -> dict:
    rates = {}
    for line in open(FRED, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if str(row.get("series_id")) != "DGS10":
            continue
        try:
            v = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0:
            rates[date.fromisoformat(str(row["date"])[:10])] = v / 100.0
    return rates


def daily_rate_fn(rates: dict):
    keys = sorted(rates)

    def rate(day: date) -> float:
        if day < keys[0]:
            return rates[keys[0]] / 365.0
        lo, hi = 0, len(keys) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if keys[mid] <= day:
                lo = mid
            else:
                hi = mid - 1
        return rates[keys[lo]] / 365.0

    return rate


def book_series(result, rate):
    dates = [p.date for p in result.equity_curve]
    raw = [p.daily_return for p in result.equity_curve]
    cash_prev = [0.0] + [p.cash / p.equity for p in result.equity_curve[:-1]]
    adj, exc, lvl = [], [], []
    eq = 1.0
    for d, r, c in zip(dates, raw, cash_prev):
        rf = rate(d)
        a = r + c * rf
        eq *= 1 + a
        adj.append(a)
        exc.append(a - rf)
        lvl.append(eq)
    return {"dates": dates, "raw": raw, "adj": adj, "exc": exc, "lvl": lvl,
            "cash_prev": cash_prev}


# ------------------------------------------------------------------ weekly grid
def iso_week_key(day: date):
    iso = day.isocalendar()
    return (iso[0], iso[1])


def build_week_rows(s1, s2, ac, ae, active_by_day, w_by_date, first_trade, last_stock):
    sat_by_week, fri_by_week = {}, {}
    for d in s1["dates"]:
        if d.weekday() == 5:
            sat_by_week[iso_week_key(d)] = d
    for d in s2["dates"]:
        k = iso_week_key(d)
        if k not in fri_by_week or d > fri_by_week[k]:
            fri_by_week[k] = d
    i1 = {d: i for i, d in enumerate(s1["dates"])}
    i2 = {d: i for i, d in enumerate(s2["dates"])}
    iac = {d: i for i, d in enumerate(ac["dates"])}
    iae = {d: i for i, d in enumerate(ae["dates"])}
    raw_weeks = []
    for k in sorted(set(sat_by_week) & set(fri_by_week)):
        fd, sd = fri_by_week[k], sat_by_week[k]
        if fd < first_trade or fd > last_stock or sd not in i1:
            continue
        raw_weeks.append({"week": k, "fri": fd, "sat": sd})
    wk = []
    for prev, cur in zip(raw_weeks, raw_weeks[1:]):
        a1, b1 = i1[prev["sat"]], i1[cur["sat"]]
        a2, b2 = i2[prev["fri"]], i2[cur["fri"]]
        aa, ba = iac[prev["sat"]], iac[cur["sat"]]
        ae_, be = iae[prev["fri"]], iae[cur["fri"]]

        def comp(rets, a, b):
            p = 1.0
            for k in range(a + 1, b + 1):
                p *= 1 + rets[k]
            return p - 1

        p = w_by_date[cur["fri"]]
        wk.append({
            "week": list(cur["week"]), "sat": cur["sat"].isoformat(), "fri": cur["fri"].isoformat(),
            "s_c_raw": comp(s1["raw"], a1, b1), "s_c_adj": comp(s1["adj"], a1, b1), "s_c_exc": comp(s1["exc"], a1, b1),
            "s_e_raw": comp(s2["raw"], a2, b2), "s_e_adj": comp(s2["adj"], a2, b2), "s_e_exc": comp(s2["exc"], a2, b2),
            "a_c": ac["lvl"][ba] / ac["lvl"][aa] - 1, "a_e": ae["lvl"][be] / ae["lvl"][ae_] - 1,
            "n_active": len(active_by_day[cur["sat"]]),
            "w": p["held_target_w"], "spy_sigma": p["spy_sigma_annualized"],
        })
    return wk


# ------------------------------------------------------------------ sim (engine-faithful)
def sim_book(gates, opens_c, closes_c, days, targets_fn=None, cost=COST_RATE):
    """Mirror of the engine daily loop: overnight on drifted old weights,
    rebalance at the open, intraday on new target weights, drift again."""
    eq = 1.0
    weights = {s: 0.0 for s in CRYPTO_SYMBOLS}
    curve = []
    for i, d in enumerate(days):
        if i == 0:
            curve.append(eq)
            continue
        prev = days[i - 1]
        old_w = dict(weights)
        ovn = {s: opens_c[s][d] / closes_c[s][prev] - 1 for s in CRYPTO_SYMBOLS}
        overnight = sum(old_w[s] * ovn[s] for s in CRYPTO_SYMBOLS)
        eq *= 1 + overnight
        open_w = (
            {s: old_w[s] * (1 + ovn[s]) / (1 + overnight) for s in CRYPTO_SYMBOLS}
            if 1 + overnight > 0 else dict(old_w)
        )
        if targets_fn is not None:
            tgt = targets_fn(prev, open_w)
        else:
            n_act = sum(1 for s in CRYPTO_SYMBOLS if gates[s].get(prev, False))
            tgt = {s: (1 / n_act if gates[s].get(prev, False) else 0.0) for s in CRYPTO_SYMBOLS}
        turnover = sum(abs(tgt[s] - open_w[s]) for s in CRYPTO_SYMBOLS)
        eq *= 1 - turnover * cost
        intra = {s: closes_c[s][d] / opens_c[s][d] - 1 for s in CRYPTO_SYMBOLS}
        intraday = sum(tgt[s] * intra[s] for s in CRYPTO_SYMBOLS)
        eq *= 1 + intraday
        weights = (
            {s: tgt[s] * (1 + intra[s]) / (1 + intraday) for s in CRYPTO_SYMBOLS}
            if 1 + intraday > 0 else dict(tgt)
        )
        curve.append(eq)
    return curve


def curve_stats(curve, per_year: int):
    rets = [c / p - 1 for p, c in zip([1.0] + curve[:-1], curve)]
    yrs = len(rets) / per_year
    return {
        "cagr": curve[-1] ** (1 / yrs) - 1 if curve[-1] > 0 else None,
        "maxdd": max_drawdown(rets),
        "ann_vol": stdev(rets) * math.sqrt(per_year),
    }


# ------------------------------------------------------------------ E sections
def e_sections(wk):
    out = {}
    e1 = {}
    for name, kx, ky in (
        ("strategy_books_raw", "s_c_raw", "s_e_raw"),
        ("strategy_books_excess", "s_c_exc", "s_e_exc"),
        ("strategy_books_with_interest", "s_c_adj", "s_e_adj"),
        ("asset_sleeves", "a_c", "a_e"),
    ):
        e1[name] = corr_stats([r[kx] for r in wk], [r[ky] for r in wk], "E1|" + name)
    out["E1_full_period"] = e1

    def s01_bucket(n):
        return {0: "all_cash", 1: "one_asset", 2: "both_assets"}[n]

    def s02_bucket(w):
        if w >= 1 - 1e-9:
            return "w_eq_1"
        return "0_5_le_w_lt_1" if w >= 0.5 else "w_lt_0_5"

    cells = {}
    for r in wk:
        cells.setdefault(s01_bucket(r["n_active"]) + "|" + s02_bucket(r["w"]), []).append(r)
    e2 = {}
    for key, rows in sorted(cells.items()):
        cell = {
            "weeks": len(rows),
            "mean_s_c_with_interest": fmean(x["s_c_adj"] for x in rows),
            "mean_s_e_with_interest": fmean(x["s_e_adj"] for x in rows),
        }
        if len(rows) >= 10:
            cell["corr_s_c_s_e_with_interest"] = corr_stats(
                [x["s_c_adj"] for x in rows], [x["s_e_adj"] for x in rows], "E2|" + key)
        e2[key] = cell
    out["E2_state_table"] = e2

    sub = [r for r in wk if r["n_active"] >= 1 and r["w"] >= 0.5]
    out["E3_both_invested_weeks"] = {
        "definition": "S-01 active >=1 asset AND S-02 w >= 0.5",
        "weeks": len(sub),
        "corr_a_c_a_e": corr_stats([r["a_c"] for r in sub], [r["a_e"] for r in sub], "E3"),
    }

    ac_all = sorted(r["a_c"] for r in wk)
    ae_all = sorted(r["a_e"] for r in wk)
    e4 = {}
    for q in (0.10, 0.20):
        tc, te = qtl(ac_all, q), qtl(ae_all, q)
        uc, ue = qtl(ac_all, 1 - q), qtl(ae_all, 1 - q)
        lo = [r for r in wk if r["a_c"] <= tc and r["a_e"] <= te]
        hi = [r for r in wk if r["a_c"] >= uc and r["a_e"] >= ue]
        entry = {}
        for nm, rows in (("lower", lo), ("upper", hi)):
            entry[nm] = {
                "weeks": len(rows),
                "pearson": pearson([r["a_c"] for r in rows], [r["a_e"] for r in rows]) if len(rows) >= 3 else None,
            }
        entry["lower"]["reference_only"] = True
        e4[f"q{int(q * 100)}"] = entry
    out["E4_exceedance"] = e4

    def joint_loss(kloss, kother):
        rows = sorted(wk, key=lambda r: r[kloss])[: max(1, int(0.10 * len(wk)))]
        neg = sum(1 for r in rows if r[kother] < 0)
        return {"weeks": len(rows), "other_negative": neg, "share": neg / len(rows)}

    out["E5_joint_losses"] = {
        "worst10pct_s_e_weeks_where_s_c_negative": joint_loss("s_e_adj", "s_c_adj"),
        "worst10pct_s_c_weeks_where_s_e_negative": joint_loss("s_c_adj", "s_e_adj"),
        "independence_expectation_approx": 0.5,
    }

    roll = []
    for i in range(26, len(wk) + 1):
        window = wk[i - 26:i]
        roll.append({
            "week_end_fri": window[-1]["fri"],
            "corr_s_c_s_e_with_interest": pearson([r["s_c_adj"] for r in window], [r["s_e_adj"] for r in window]),
            "spy_sigma_ann": window[-1]["spy_sigma"],
        })
    out["E6_rolling_26w"] = {
        "series": roll,
        "max_corr_point": max(roll, key=lambda x: x["corr_s_c_s_e_with_interest"]),
        "min_corr_point": min(roll, key=lambda x: x["corr_s_c_s_e_with_interest"]),
    }

    vol_c = stdev([r["s_c_adj"] for r in wk]) * math.sqrt(52)
    vol_e = stdev([r["s_e_adj"] for r in wk]) * math.sqrt(52)
    vol_mix = stdev([0.5 * r["s_c_adj"] + 0.5 * r["s_e_adj"] for r in wk]) * math.sqrt(52)
    out["E7_diversification"] = {
        "vol_s_c_ann": vol_c, "vol_s_e_ann": vol_e,
        "vol_50_50_mix_ann": vol_mix, "weighted_avg_vols": 0.5 * vol_c + 0.5 * vol_e,
        "ratio_mix_over_weighted_avg": vol_mix / (0.5 * vol_c + 0.5 * vol_e),
        "note": "diagnostic only; no allocation proposal",
    }
    out["weekly_table"] = [
        {k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()} for r in wk
    ]
    return out


# ------------------------------------------------------------------ triggers
def t1_gate(crypto_rows, active_by_day, s1_dates, closes_c, opens_c):
    gates = {s: {d: (s in active_by_day[d]) for d in s1_dates} for s in CRYPTO_SYMBOLS}
    days = list(s1_dates)
    assets = {}
    for s in CRYPTO_SYMBOLS:
        pos = {d: i for i, d in enumerate(days)}
        ret_on = {b: closes_c[s][b] / closes_c[s][a] - 1 for a, b in zip(days, days[1:])}
        fwd = {}
        for k in (1, 5, 20):
            groups = {"ON": [], "OFF": []}
            for d in days:
                i = pos[d]
                if i + k >= len(days):
                    continue
                r = closes_c[s][days[i + k]] / closes_c[s][d] - 1
                groups["ON" if gates[s][d] else "OFF"].append(r)
            g = {}
            for st, rs in groups.items():
                if not rs:
                    continue
                g[st] = {
                    "n": len(rs),
                    "mean": fmean(rs),
                    "ann_vol": stdev(rs) * math.sqrt(365 / k) if len(rs) > 1 else None,
                    "neg_share": sum(1 for x in rs if x < 0) / len(rs),
                }
            diff = None
            if groups["ON"] and groups["OFF"]:
                on, off = groups["ON"], groups["OFF"]
                pooled = [(x, "ON") for x in on] + [(x, "OFF") for x in off]
                rng = rng_for(f"T1|{s}|{k}")
                vals = []
                for _ in range(2000):
                    idx = stationary_bootstrap_indices(len(pooled), 10, rng)
                    o = [pooled[i][0] for i in idx if pooled[i][1] == "ON"]
                    f = [pooled[i][0] for i in idx if pooled[i][1] == "OFF"]
                    if o and f:
                        vals.append(fmean(o) - fmean(f))
                vals.sort()
                diff = {
                    "observed": fmean(on) - fmean(off),
                    "stationary_bootstrap_ci": [vals[int(0.025 * (len(vals) - 1))],
                                                vals[int(0.975 * (len(vals) - 1))]] if vals else None,
                }
            fwd[str(k)] = {"by_state": g, "diff_ON_minus_OFF": diff}
        state_days = {"ON": [d for d in days if gates[s][d]], "OFF": [d for d in days if not gates[s][d]]}
        state_stats = {}
        for st, ds in state_days.items():
            rs = [ret_on[d] for d in ds if d in ret_on]
            state_stats[st] = {
                "days": len(ds),
                "daily_mean": fmean(rs) if rs else None,
                "ann_vol": stdev(rs) * math.sqrt(365) if len(rs) > 1 else None,
                "neg_share": sum(1 for x in rs if x < 0) / len(rs) if rs else None,
                "maxdd_state_stream": max_drawdown(rs) if rs else None,
            }
        switches = [b for a, b in zip(days, days[1:]) if gates[s][a] != gates[s][b]]
        assets[s] = {"forward": fwd, "state_stream_stats": state_stats,
                     "switches_total": len(switches),
                     "switch_dates": [d.isoformat() for d in switches]}
    return gates, {"assets": assets}


def t1_placebo(gates, closes_c, opens_c, days):
    def run_lengths(series):
        out, cur, n = [], series[0], 1
        for v in series[1:]:
            if v == cur:
                n += 1
            else:
                out.append((cur, n))
                cur, n = v, 1
        out.append((cur, n))
        return out

    real_curve = sim_book(gates, opens_c, closes_c, days)
    real_dd = max_drawdown([c / p - 1 for p, c in zip([1.0] + real_curve[:-1], real_curve)])
    rng = rng_for("T1|placebo")
    placebo_dds = []
    for _ in range(2000):
        gates_p = {}
        for s in CRYPTO_SYMBOLS:
            blocks = run_lengths([gates[s][d] for d in days])
            rng.shuffle(blocks)
            rebuilt = []
            for val, ln in blocks:
                rebuilt.extend([val] * ln)
            gates_p[s] = {d: rebuilt[i] for i, d in enumerate(days)}
        pc = sim_book(gates_p, opens_c, closes_c, days)
        placebo_dds.append(max_drawdown([c / p - 1 for p, c in zip([1.0] + pc[:-1], pc)]))
    base_curve = sim_book({s: {d: True for d in days} for s in CRYPTO_SYMBOLS}, opens_c, closes_c, days)
    base_dd = max_drawdown([c / p - 1 for p, c in zip([1.0] + base_curve[:-1], base_curve)])
    placebo_dds.sort()
    return {
        "method": "block-order shuffle preserving ON/OFF run lengths; 2000 reps; same-cost engine-faithful sim",
        "sim_maxdd_real_gates": real_dd,
        "baseline_1n_maxdd_sim": base_dd,
        "dd_reduction_vs_baseline": base_dd - real_dd,
        "placebo_dd_percentile_of_real": sum(1 for x in placebo_dds if x <= real_dd) / len(placebo_dds),
        "placebo_dd_p05_p50_p95": [qtl(placebo_dds, 0.05), qtl(placebo_dds, 0.50), qtl(placebo_dds, 0.95)],
        "interpretation_threshold": "meaningful timing iff percentile > 0.95; else shallow DD explained by time out of market",
    }, real_curve, real_dd


def t1_whipsaw(gates, opens_c, days):
    out = {}
    for s in CRYPTO_SYMBOLS:
        trips = []
        state = None
        sell_d = None
        for d in days:
            g = gates[s][d]
            if state is None:
                state = g
                continue
            if state and not g:
                sell_d = d
            elif (not state) and g and sell_d is not None:
                sell, buy = opens_c[s][sell_d], opens_c[s][d]
                gross = sell / buy - 1
                trips.append({
                    "sell_date": sell_d.isoformat(), "buy_date": d.isoformat(),
                    "days_off": (d - sell_d).days, "gross_sell_over_rebuy": gross,
                    "net_after_2x15bps": (1 + gross) * (1 - 2 * COST_RATE) - 1,
                    "whipsaw_le_10d": (d - sell_d).days <= 10,
                })
                sell_d = None
            state = g
        n_wip = sum(1 for t in trips if t["whipsaw_le_10d"])
        out[s] = {
            "off_on_round_trips": len(trips),
            "whipsaw_count_le_10d": n_wip,
            "whipsaw_rate": n_wip / len(trips) if trips else None,
            "whipsaw_loss_sum_net": sum(t["net_after_2x15bps"] for t in trips if t["whipsaw_le_10d"]),
            "trades": trips,
        }
    return out


def t1_one_day_late(gates, closes_c, opens_c, days, real_curve, real_dd):
    lag = {s: {} for s in CRYPTO_SYMBOLS}
    for s in CRYPTO_SYMBOLS:
        prev = False
        for d in days:
            lag[s][d] = prev
            prev = gates[s][d]
    lc = sim_book(lag, opens_c, closes_c, days)
    lrets = [c / p - 1 for p, c in zip([1.0] + lc[:-1], lc)]
    yrs = len(days) / 365
    return {
        "reference_only": True,
        "cagr_base": real_curve[-1] ** (1 / yrs) - 1,
        "cagr_1day_late": lc[-1] ** (1 / yrs) - 1,
        "maxdd_base": real_dd,
        "maxdd_1day_late": max_drawdown(lrets),
    }


def t2_sigma21(crypto_hist, stock_hist, crypto_end, stock_end):
    assets = {}
    sigma_maps = {}
    for s, rows_map in (
        ("SPY", map_rows(stock_hist["SPY"], stock_end)),
        ("BTC", map_rows(crypto_hist["BTC"], crypto_end)),
        ("ETH", map_rows(crypto_hist["ETH"], crypto_end)),
    ):
        closes = {d: float(r["close"]) for d, r in rows_map.items()}
        ddays = sorted(closes)
        rets = {b: closes[b] / closes[a] - 1 for a, b in zip(ddays, ddays[1:])}
        rdays = sorted(rets)
        sig = {}
        for i, d in enumerate(rdays):
            if i >= 21:
                sig[d] = stdev([rets[rdays[j]] for j in range(i - 21, i)])
        sigma_maps[s] = sig
        pairs = []
        for i, d in enumerate(rdays):
            if i + 20 >= len(rdays) or d not in sig:
                continue
            fw = [rets[rdays[j]] for j in range(i, i + 21)]
            pairs.append((sig[d], stdev(fw), geometric_mean([1 + x for x in fw]) - 1))
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        n = len(xs)
        mx = fmean(xs)
        slope = (sum((x - mx) * (y - fmean(ys)) for x, y in pairs)
                 / sum((x - mx) ** 2 for x in xs))
        sp = sorted(pairs, key=lambda p: p[0])
        k = n // 5
        quint = {}
        for qi in range(5):
            sel = sp[qi * k:(qi + 1) * k] if qi < 4 else sp[4 * k:]
            if not sel:
                continue
            mv = fmean(p[2] for p in sel)
            vv = fmean(p[1] for p in sel)
            quint[f"q{qi + 1}"] = {
                "sigma_lo": sel[0][0], "sigma_hi": sel[-1][0], "n": len(sel),
                "fwd21_mean_ret": mv, "fwd21_mean_vol": vv,
                "ret_over_vol": mv / vv if vv else None,
            }
        assets[s] = {
            "n": n, "slope_sigma_vs_fwd21_vol": slope,
            "r2_pearson_sq": max(0.0, pearson(xs, ys)) ** 2,
            "spearman_sigma_vs_fwd_vol": spearman(xs, ys),
            "sigma_quintiles": quint,
        }
    return assets, sigma_maps


def t2_book_b(gates, closes_c, opens_c, days, sigma_maps):
    def cap_for(s, d):
        sig = sigma_maps[s].get(d)
        if sig is None or sig <= 0:
            return 0.0
        return min(1.0, (0.40 / math.sqrt(252)) / sig)

    def targets_book_b(prev, _open_w):
        n_act = sum(1 for s in CRYPTO_SYMBOLS if gates[s].get(prev, False))
        tgt = {}
        for s in CRYPTO_SYMBOLS:
            if not gates[s].get(prev, False) or n_act == 0:
                tgt[s] = 0.0
            else:
                tgt[s] = cap_for(s, prev) / n_act
        return tgt

    bb_curve = sim_book(gates, opens_c, closes_c, days, targets_fn=targets_book_b)
    ws = []
    for d in days:
        n_act = sum(1 for s in CRYPTO_SYMBOLS if gates[s].get(d, False))
        tot = sum(cap_for(s, d) / n_act for s in CRYPTO_SYMBOLS if gates[s].get(d, False))
        ws.append(tot)
    frac = fmean(ws)

    def targets_fixed(prev, _open_w):
        n_act = sum(1 for s in CRYPTO_SYMBOLS if gates[s].get(prev, False))
        return {s: (frac / n_act if gates[s].get(prev, False) and n_act else 0.0) for s in CRYPTO_SYMBOLS}

    fx_curve = sim_book(gates, opens_c, closes_c, days, targets_fn=targets_fixed)
    return {
        "research_sim_note": "shadow books simulated with the engine gate function and the same cost model; not engine strategies",
        "book_B_target_vol_ann": 0.40,
        "book_B": {**curve_stats(bb_curve, 365), "avg_total_weight": frac},
        "fixed_fraction_matched_avg_weight": {**curve_stats(fx_curve, 365), "avg_total_weight": frac},
    }


def t3_halt(crypto_hist, stock_hist, crypto_end, stock_end, closes_c):
    out = {}
    for s in ("BTC", "ETH", "SPY", "IWM", "JNJ", "PG"):
        rows_map = (map_rows(stock_hist[s], stock_end) if s in STOCK_SYMBOLS
                    else map_rows(crypto_hist[s], crypto_end))
        closes = {d: float(r["close"]) for d, r in rows_map.items()}
        ddays = sorted(closes)
        rets = {b: closes[b] / closes[a] - 1 for a, b in zip(ddays, ddays[1:])}
        rlist = sorted(rets)
        pos = {d: i for i, d in enumerate(ddays)}
        fires, fires_buggy = [], []
        for i, d in enumerate(rlist):
            r = rets[d]
            if i >= 21:
                sig = stdev([rets[rlist[j]] for j in range(i - 21, i)])
                if sig > 0 and abs(r) > 5 * sig:
                    fires.append({"date": d.isoformat(), "r": r, "sigma21_prior": sig,
                                  "multiple": abs(r) / sig})
            if i >= 20:
                sig_b = stdev([rets[rlist[j]] for j in range(i - 20, i + 1)])
                if sig_b > 0 and abs(r) > 5 * sig_b:
                    fires_buggy.append(d.isoformat())

        def fwd(k):
            vals = []
            for f in fires:
                d_fire = date.fromisoformat(f["date"])
                i0 = pos[d_fire]
                if i0 + k < len(ddays):
                    vals.append(closes[ddays[i0 + k]] / closes[d_fire] - 1)
            return {"n": len(vals), "mean": fmean(vals) if vals else None}

        years_span = (ddays[-1] - ddays[0]).days / 365.25
        out[s] = {
            "window": [ddays[0].isoformat(), ddays[-1].isoformat()],
            "fires_correct": fires,
            "fires_per_year": len(fires) / years_span,
            "fires_buggy_d1_count": len(fires_buggy),
            "fires_buggy_d1_dates": fires_buggy,
            "fwd5_after_fire": fwd(5),
            "fwd20_after_fire": fwd(20),
            "tariff_window_2025_04_03_09_fires": [f for f in fires if "2025-04-03" <= f["date"] <= "2025-04-09"],
        }
    top5 = {}
    for s in ("BTC", "ETH"):
        ddays = sorted(closes_c[s])
        worst = sorted(((closes_c[s][b] / closes_c[s][a] - 1, b) for a, b in zip(ddays, ddays[1:])),
                       key=lambda x: x[0])[:5]
        fired = {f["date"] for f in out[s]["fires_correct"]}
        top5[s] = [{"date": d.isoformat(), "r": r, "fired_5sigma": d.isoformat() in fired}
                   for r, d in worst]
    out["_crypto_top5_down_days_reference"] = top5
    return out


def tracked_engine_maxdd(json_path: Path, sid: str, id_key: str) -> float | None:
    # Reference value from the tracked backtest JSON (engine metrics, no cash interest).
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    for r in data["results"]:
        key = r["strategy"]["id"] if id_key == "strategy" else r[id_key]
        if key == sid:
            m = r["metrics"]["max_drawdown"]
            return m["value"] if isinstance(m, dict) else float(m)
    return None


def t4_dd_line(s1, s2):
    def analyze(dates, levels):
        peak, dd_series, breach = None, [], None
        for d, e in zip(dates, levels):
            peak = e if peak is None else max(peak, e)
            dd = e / peak - 1
            dd_series.append(dd)
            if breach is None and dd <= -0.20:
                breach = d
        out = {"maxdd": min(dd_series),
               "first_breach_date": breach.isoformat() if breach else None}
        n = len(dates)
        idx_by_date = {d: i for i, d in enumerate(dates)}
        if breach:
            i = idx_by_date[breach]
            out["weeks_from_start_to_breach"] = round((breach - dates[0]).days / 7, 1)
            j = next((k for k in range(i, n) if (dates[k] - breach).days >= 182), None)
            if j is not None:
                out["if_stopped_at_breach"] = {"pnl_to_26w_later": 0.0,
                                               "equity_frozen": levels[i]}
                out["if_not_stopped"] = {
                    "pnl_to_26w_later": levels[j] / levels[i] - 1,
                    "maxdd_breach_to_26w": running_max_drawdown(levels[i:j + 1]),
                }
        hits = total = 0
        for i in range(n):
            j = next((k for k in range(i, n) if (dates[k] - dates[i]).days >= 182), None)
            if j is None:
                j = n
            if j - i < 2:
                continue
            peak_w = None
            dipped = False
            for e in levels[i:j]:
                peak_w = e if peak_w is None else max(peak_w, e)
                if e / peak_w - 1 <= -0.20:
                    dipped = True
                    break
            hits += dipped
            total += 1
        out["window_26w_dip_below_minus20_share"] = hits / total
        out["windows_count"] = total
        share = out["window_26w_dip_below_minus20_share"]
        if share > 0.20:
            out["judgement"] = "line_too_strict_for_book; re-thresholding required (master decision; no new value proposed here)"
        elif share < 0.05:
            out["judgement"] = "keep line as is"
        else:
            out["judgement"] = "keep line; disclose false-stop rate at the top of the report"
        return out

    return {
        "rule": "HALT absolute line: drawdown from book equity high <= -20%",
        "S01_book_A": analyze(s1["dates"], s1["lvl"]),
        "S02_book_C": analyze(s2["dates"], s2["lvl"]),
        "tracked_engine_maxdd_reference": {
            "crypto_trend_dual_ma": tracked_engine_maxdd(CRYPTO_JSON, "crypto_trend_dual_ma", "strategy"),
            "s02_vol_target_15": tracked_engine_maxdd(STOCK_JSON, "s02_vol_target_15", "strategy_id"),
        },
        "note": "S-01 large DD sits in IS 2023-2025; full period replayed, not OOS only",
    }


def running_max_drawdown_from_returns(rets):
    eq = 1.0
    levels = []
    for r in rets:
        eq *= 1 + r
        levels.append(eq)
    return running_max_drawdown(levels)


def t5_gate_12w(wk, switch_days_A, switch_days_C, rate_A, rate_C):
    def run(book_key, bench_key, switch_days, rate):
        expected = rate * 12 / 52
        rows = []
        for i in range(len(wk) - 12):
            window = wk[i:i + 12]
            book = [w[book_key] for w in window]
            bench = [w[bench_key] for w in window]
            d0 = date.fromisoformat(window[0]["sat"])
            d1 = date.fromisoformat(window[-1]["fri"])
            n_sw = sum(1 for d in switch_days if d0 <= d <= d1)
            ddb = running_max_drawdown_from_returns(book)
            ddm = running_max_drawdown_from_returns(bench)
            cum = 1.0
            for r in book:
                cum *= 1 + r
            cum -= 1
            row = {"window": [d0.isoformat(), d1.isoformat()], "book_maxdd": ddb,
                   "bench_maxdd": ddm, "cum": cum, "switches": n_sw}
            if abs(ddm) < 0.10:
                row["verdict"] = "no_judgment_bench_dd_lt_10pct"
            else:
                a = ddb <= ddm * 0.6
                b = cum >= -0.10
                c = n_sw <= 2 * expected
                row.update({"a_dd_gate": a, "b_cum_gate": b, "c_switch_gate": c})
                row["verdict"] = "continue" if (a and b and c) else ("change" if (a and b) else "halt")
            rows.append(row)
        cnt = Counter(r["verdict"] for r in rows)
        return {
            "annual_switch_rate_used": rate,
            "expected_switches_12w": expected,
            "windows": len(rows),
            "verdict_counts": dict(sorted(cnt.items())),
            "verdict_share": {k: v / len(rows) for k, v in sorted(cnt.items())},
            "warn_switch_ge5_share": sum(1 for r in rows if r["switches"] >= 5) / len(rows),
            "rows": rows,
        }

    return {
        "S01_book_A": run("s_c_adj", "a_c", switch_days_A, rate_A),
        "S02_book_C": run("s_e_adj", "a_e", switch_days_C, rate_C),
    }


def t6_global_risk_off(s1, s2, ac, ae, active_by_day, w_by_date):
    i1 = {d: i for i, d in enumerate(s1["dates"])}
    i2 = {d: i for i, d in enumerate(s2["dates"])}
    iac = {d: i for i, d in enumerate(ac["dates"])}
    iae = {d: i for i, d in enumerate(ae["dates"])}
    sdates = s2["dates"]
    events = []
    for idx, d in enumerate(sdates):
        if d not in active_by_day or active_by_day[d]:
            continue
        p = w_by_date.get(d)
        if p is None or p["held_target_w"] >= 0.5 or d not in i1:
            continue
        j = idx + 20
        if j >= len(sdates):
            continue
        f = sdates[j]
        if f not in iac or f not in i1:
            continue
        events.append({
            "date": d.isoformat(), "date_plus_20_sessions": f.isoformat(),
            "s_c_20d": s1["lvl"][i1[f]] / s1["lvl"][i1[d]] - 1,
            "s_e_20d": s2["lvl"][i2[f]] / s2["lvl"][i2[d]] - 1,
            "a_c_20d": ac["lvl"][iac[f]] / ac["lvl"][iac[d]] - 1,
            "a_e_20d": ae["lvl"][iae[f]] / ae["lvl"][iae[d]] - 1,
        })
    runs = []
    for d in [date.fromisoformat(e["date"]) for e in events]:
        if runs and (d - runs[-1][-1]).days <= 3:
            runs[-1].append(d)
        else:
            runs.append([d])
    spans = [{"start": r[0].isoformat(), "end": r[-1].isoformat(), "days": len(r)} for r in runs]
    return {
        "condition": "book A all assets RISK_OFF AND book C held w < 0.5",
        "days": len(events),
        "consecutive_spans_approx": spans[:100],
        "fwd20_mean_book_A": fmean(e["s_c_20d"] for e in events) if events else None,
        "fwd20_mean_book_C": fmean(e["s_e_20d"] for e in events) if events else None,
        "verdict": "indeterminate_fewer_than_5_events" if len(events) < 5 else "recorded_only",
        "note": "recording-only signal (design 2-3); never used for trading",
        "events": events,
    }


def t7_states(s1, active_by_day, sma_by_day, w_by_date, fires_by_symbol, states_path):
    n = 0
    with open(states_path, "w", encoding="utf-8") as fh:
        for d in s1["dates"]:
            n_act = len(active_by_day[d])
            for s in CRYPTO_SYMBOLS:
                g = s in active_by_day[d]
                row = {
                    "date": d.isoformat(), "book": "A", "asset": s,
                    "regime_state": "RISK_ON" if g else "RISK_OFF",
                    "close": None, "sma200": sma_by_day[s][d],
                    "sigma21": None,
                    "target_weight": (0.5 if n_act == 2 else (1.0 if g else 0.0)),
                    "halt_5sigma_flag": d.isoformat() in fires_by_symbol[s],
                    "engine_gate_function": "yowayowa.services.backtest._target_weights:crypto_trend_dual_ma",
                }
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                n += 1
        for d in sorted(w_by_date):
            p = w_by_date[d]
            row = {
                "date": d.isoformat(), "book": "C", "asset": "PORTFOLIO",
                "regime_state": "RISK_ON" if p["held_target_w"] >= 1 - 1e-9 else "RISK_REDUCED",
                "close": p.get("spy_close"), "sma200": None,
                "sigma21": p["spy_sigma_daily"],
                "target_weight": p["daily_signal_w"],
                "halt_5sigma_flag": d.isoformat() in fires_by_symbol["SPY"],
                "engine_source": "tracked backtest_results_s02.json diagnostics exposure_path",
            }
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return {"jsonl": states_path.name, "rows": n,
            "golden_data_note": "diff against paperloop records when implemented"}


# ------------------------------------------------------------------ report
def render_report(out) -> str:
    t4 = out["triggers"]["T4_dd_minus20_line"]
    a4, c4 = t4["S01_book_A"], t4["S02_book_C"]
    e1 = out["correlation"]["E1_full_period"]
    h = out["hypotheses"]
    al = out["alignment"]
    t5 = out["triggers"]["T5_12w_gate"]
    t1p = out["triggers"]["T1_placebo"]
    fmt = lambda v, p=4: ("N/A" if v is None else f"{v:.{p}f}")
    pctf = lambda v: ("N/A" if v is None else f"{v * 100:.2f}%")
    lines = [
        "# S-01 × S-02 相関・局面切替トリガ検証 — フェーズA（研究）",
        "",
        "個人研究バックテスト。投資助言ではない。数値はすべて research/s01_s02_corr_regime_phaseA.json から機械転記している。",
        "",
        "## 結論（3行）",
        "",
        f"1. T4（最優先）: 帳簿Aの全開始点26週窓でDD-20%を割る割合は {pctf(a4['window_26w_dip_below_minus20_share'])}（{a4['windows_count']}窓）→ 判定: {a4['judgement']}。帳簿Cは {pctf(c4['window_26w_dip_below_minus20_share'])}。",
        f"2. H1（戦略相関 < 資産相関）: corr(s,e)={fmt(e1['strategy_books_raw']['pearson'])} vs corr(a_c,a_e)={fmt(e1['asset_sleeves']['pearson'])}、差のブートストラップ95%区間上限 {fmt((h['H1_strategy_corr_below_asset_corr']['diff_bootstrap_ci'] or [None, None])[1])} → {('支持' if h['H1_strategy_corr_below_asset_corr']['supported'] else '不支持')}。H3: {('支持' if h['H3_w_low_coherent_riskoff']['supported'] else '不支持')}（w<0.5の週のうちS-01現金寄り {pctf(h['H3_w_low_coherent_riskoff']['share_s01_cash_or_one_asset'])}）。",
        f"3. T1 プラセボ: S-01 の MaxDD は区間並べ替え対照の {pctf(t1p['placebo_dd_percentile_of_real'])} パーセンタイル（しきい値95%）→ {('時期の選び方に意味がある水準' if t1p['placebo_dd_percentile_of_real'] > 0.95 else '時期選びの効果とは言えず、投資日数が少ないことの寄与が残る')}。",
        "",
        "## 精度の限界（必読）",
        "",
        f"- 週次 n={al['weeks']}、相関の標準誤差 ≈ {fmt(al['weekly_corr_se_approx'], 3)}、95%区間の半幅 ≈ ±{fmt(al['weekly_corr_ci_halfwidth_approx'], 2)}。0.2 と 0.5 は区別できるが、0.3 と 0.4 は区別できない。細かい差を根拠に配分を決めない。",
        "- 暗号データは 2023-01-01 開始のため、2020-03 急落と 2022 下落局面が不在。裾の相関（E4下側）、H2、T3 の暗号側判定は参考値。判定はフェーズB（2019-06 まで遡り取得）で行う。",
        f"- 現金利: fred.jsonl に DGS3MO/DGS1 が無いため {out['data_limits']['rate_series_detail']} を両帳簿共通で日割り付与（設計1-3の代替、明記済み）。",
        "- H2 と裾の数値、T1 の1日遅れ診断、帳簿B（影）は参考値であり、判定には使わない。パラメータは1つも変えていない。",
        "",
        "## T4: 中止の絶対線 DD -20%（最優先）",
        "",
        "| 帳簿 | 全期間MaxDD | 最初の-20%達成日 | 開始から週数 | 26週窓で-20%を割る割合 | 窓数 | 判定 |",
        "|---|---:|---|---:|---:|---:|---|",
        (f"| 帳簿A（S-01 利付き） | {pctf(a4['maxdd'])} | {a4['first_breach_date']} | {a4.get('weeks_from_start_to_breach')} | {pctf(a4['window_26w_dip_below_minus20_share'])} | {a4['windows_count']} | {a4['judgement']} |"),
        (f"| 帳簿C（S-02 利付き） | {pctf(c4['maxdd'])} | {c4['first_breach_date']} | {c4.get('weeks_from_start_to_breach')} | {pctf(c4['window_26w_dip_below_minus20_share'])} | {c4['windows_count']} | {c4['judgement']} |"),
        "",
        f"- 注: 表のMaxDDは設計T4どおり利付き曲線の再計算値（帳簿A {pctf(a4['maxdd'])}／帳簿C {pctf(c4['maxdd'])}）。設計書・S-01報告書の -39.75% は追跡JSONのエンジン値（利付なし）: 帳簿A {pctf(t4['tracked_engine_maxdd_reference']['crypto_trend_dual_ma'])}、帳簿C {pctf(t4['tracked_engine_maxdd_reference']['s02_vol_target_15'])}。基準が違うだけで数値は整合。",
        "",
    ]
    if a4.get("if_not_stopped"):
        lines += [
            f"- 帳簿Aの最初の-20%達成日に止まっていた場合の26週後損益: 0%（凍結）。止めなかった場合: {pctf(a4['if_not_stopped']['pnl_to_26w_later'])}、区間MaxDD {pctf(a4['if_not_stopped']['maxdd_breach_to_26w'])}。",
            "",
        ]
    lines += [
        "## 1. 準拠と一致（受入条件1）",
        "",
        "| 系列 | 完全一致 |",
        "|---|---|",
    ]
    for k, v in out["equity_match"].items():
        lines.append(f"| {k} | {v['match']} |")
    lines += [
        "",
        f"- 比較方法: crypto は equity_curve の日付と equity を追跡JSONと要素一致、metrics 辞書一致。S-02 は daily_return（exposure_path）と metrics 一致。すべて {all(v['match'] for v in out['equity_match'].values())}。",
        f"- 戦略計算はエンジン既存関数（run_backtest / _target_weights）を直接呼んだ。再実装はない。data/ と追跡JSONのSHA256は実行前後で一致（受入条件3）。設計書コミット {out['hypotheses']['registration']['repo_head_commit']}、設計書SHA256 {str(out['hypotheses']['registration']['design_doc_sha256'])[:16]}…。",
        "",
        "## 2. 相関（E1〜E7、週次・主）",
        "",
        f"- 共通期間: {al['common_period'][0]} 〜 {al['common_period'][1]}（{al['weeks']}週）。S-01初回発火 {al['s01_first_trade']}。金曜休場で直前営業日に置き換えた週: {al['weeks_where_friday_replaced_by_prior_session']}。",
        "",
        "| E1 系列 | n | Pearson | Spearman | ブートストラップ95%CI |",
        "|---|---:|---:|---:|---|",
    ]
    for name, v in e1.items():
        ci = v.get("pearson_stationary_bootstrap_ci")
        lines.append(
            f"| {name} | {v['n']} | {fmt(v['pearson'])} | {fmt(v['spearman'])} | [{fmt((ci or [None, None])[0])}, {fmt((ci or [None, None])[1])}] |")
    lines += [
        "",
        "### E2 状態別 3×3（週数・平均リターン・マス内相関、10週以上のマスのみ相関）",
        "",
        "| S-01 \\ S-02 | マス | 週数 | 平均s_c | 平均s_e | 相関 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for key, v in out["correlation"]["E2_state_table"].items():
        c1, c2 = key.split("|")
        cc = v.get("corr_s_c_s_e_with_interest")
        lines.append(f"| {c1} | {c2} | {v['weeks']} | {fmt(v['mean_s_c_with_interest'])} | {fmt(v['mean_s_e_with_interest'])} | {fmt((cc or {}).get('pearson'))} |")
    e3 = out["correlation"]["E3_both_invested_weeks"]
    e4 = out["correlation"]["E4_exceedance"]
    e5 = out["correlation"]["E5_joint_losses"]
    e7 = out["correlation"]["E7_diversification"]
    e6 = out["correlation"]["E6_rolling_26w"]
    lines += [
        "",
        f"### E3 両方投資中の週: {e3['weeks']}週、corr(a_c,a_e)={fmt((e3['corr_a_c_a_e'] or {}).get('pearson'))}",
        "",
        f"### E4 裾の超過相関（参考値）: 下側10% {fmt(e4['q10']['lower']['pearson'])}（{e4['q10']['lower']['weeks']}週）/ 上側10% {fmt(e4['q10']['upper']['pearson'])}（{e4['q10']['upper']['weeks']}週）、下側20% {fmt(e4['q20']['lower']['pearson'])} / 上側20% {fmt(e4['q20']['upper']['pearson'])}",
        "",
        f"### E5 同時損失: s_e最悪10%の週でs_cが負の割合 {pctf(e5['worst10pct_s_e_weeks_where_s_c_negative']['share'])}、s_c最悪10%でs_eが負 {pctf(e5['worst10pct_s_c_weeks_where_s_e_negative']['share'])}（独立なら約50%）",
        "",
        f"### E7 分散の効き目（診断）: 50/50ミックスボラ ÷ 加重平均 = {fmt(e7['ratio_mix_over_weighted_avg'])}（1に近いほど効き目なし）",
        "",
        f"E6 の26週移動相関の全系列は JSON correlation.E6_rolling_26w.series（最大 {fmt(e6['max_corr_point']['corr_s_c_s_e_with_interest'])} @ {e6['max_corr_point']['week_end_fri']}、最小 {fmt(e6['min_corr_point']['corr_s_c_s_e_with_interest'])} @ {e6['min_corr_point']['week_end_fri']}）。SPY σ21 水準との並記は同 series の spy_sigma_ann。",
        "",
        "## 3. 仮説（事前登録・受入条件2）",
        "",
        f"- H1: corr差 = {fmt(h['H1_strategy_corr_below_asset_corr']['diff'])}、ブートストラップ95%CI [{fmt((h['H1_strategy_corr_below_asset_corr']['diff_bootstrap_ci'] or [None, None])[0])}, {fmt((h['H1_strategy_corr_below_asset_corr']['diff_bootstrap_ci'] or [None, None])[1])}] → {('支持（区間上限<0）' if h['H1_strategy_corr_below_asset_corr']['supported'] else '不支持')}。判定基準は登録のまま（区間上限<0で支持）。",
        f"- H2: 参考値のみ（フェーズBで判定）。下側10%超過相関 {fmt(e4['q10']['lower']['pearson'])} vs 全期間 {fmt(e1['asset_sleeves']['pearson'])}。",
        f"- H3: w<0.5 の週 {h['H3_w_low_coherent_riskoff']['weeks_w_lt_0_5']}週のうち S-01 が全額現金または1資産のみ {pctf(h['H3_w_low_coherent_riskoff']['share_s01_cash_or_one_asset'])} → {('支持（>50%）' if h['H3_w_low_coherent_riskoff']['supported'] else '不支持（50%以下）')}。不支持の場合、設計2-3の「全体リスクオフ」記録は v2 に格上げしない目安。",
        "",
        "## 4. トリガ（T1〜T7）",
        "",
        "### T1 SMA200 入り切り",
        "",
        "| 資産 | 切替回数 | ON日数 | OFF日数 | fwd20 平均 ON−OFF | MaxDD（状態ストリーム ON/OFF） |",
        "|---|---:|---:|---:|---:|---|",
    ]
    t1 = out["triggers"]["T1_sma200_gate"]["assets"]
    for s, v in t1.items():
        d20 = v["forward"]["20"]["diff_ON_minus_OFF"]
        st = v["state_stream_stats"]
        lines.append(
            f"| {s} | {v['switches_total']} | {st['ON']['days']} | {st['OFF']['days']} | {fmt((d20 or {}).get('observed'))}（CI [{fmt(((d20 or {}).get('stationary_bootstrap_ci') or [None, None])[0])}, {fmt(((d20 or {}).get('stationary_bootstrap_ci') or [None, None])[1])}]） | {pctf(st['ON']['maxdd_state_stream'])} / {pctf(st['OFF']['maxdd_state_stream'])} |")
    wh = out["triggers"]["T1_whipsaw"]
    lines += [
        "",
        f"- プラセボ（区間長保存の並べ替え2000回）: 実MaxDD {pctf(t1p['sim_maxdd_real_gates'])} vs 1/N基線 {pctf(t1p['baseline_1n_maxdd_sim'])}、削減幅 {pctf(t1p['dd_reduction_vs_baseline'])}、実DD の対照分布内パーセンタイル {pctf(t1p['placebo_dd_percentile_of_real'])}（>95% で時期選びに意味）。",
        f"- だまし（10日以内に買い戻し）: BTC {wh['BTC']['whipsaw_count_le_10d']}/{wh['BTC']['off_on_round_trips']}回（率 {pctf(wh['BTC']['whipsaw_rate'])}、損失合計 {fmt(wh['BTC']['whipsaw_loss_sum_net'])}）、ETH {wh['ETH']['whipsaw_count_le_10d']}/{wh['ETH']['off_on_round_trips']}回（率 {pctf(wh['ETH']['whipsaw_rate'])}、損失合計 {fmt(wh['ETH']['whipsaw_loss_sum_net'])}）。",
        f"- 1日遅れ診断（参考値）: CAGR {pctf(out['triggers']['T1_one_day_late_diagnostic']['cagr_base'])} → {pctf(out['triggers']['T1_one_day_late_diagnostic']['cagr_1day_late'])}、MaxDD {pctf(out['triggers']['T1_one_day_late_diagnostic']['maxdd_base'])} → {pctf(out['triggers']['T1_one_day_late_diagnostic']['maxdd_1day_late'])}。",
        "",
        "### T2 σ21 による量の調整",
        "",
        "| 資産 | n | σ→先21日ボラ 傾き | R² | 順位相関 | 五分位 ret/vol（q1→q5） |",
        "|---|---:|---:|---:|---:|---|",
    ]
    t2 = out["triggers"]["T2_sigma21_sizing"]["assets"]
    for s, v in t2.items():
        q = v["sigma_quintiles"]
        seq = " → ".join(fmt(q[k]["ret_over_vol"]) for k in ("q1", "q2", "q3", "q4", "q5") if k in q)
        lines.append(f"| {s} | {v['n']} | {fmt(v['slope_sigma_vs_fwd21_vol'])} | {fmt(v['r2_pearson_sq'])} | {fmt(v['spearman_sigma_vs_fwd_vol'])} | {seq} |")
    bb = out["triggers"]["T2_book_B_shadow"]
    lines += [
        "",
        f"- 帳簿B（影・目標ボラ40%）: CAGR {pctf(bb['book_B']['cagr'])}、MaxDD {pctf(bb['book_B']['maxdd'])}、平均投資比率 {pctf(bb['book_B']['avg_total_weight'])}。固定比率（同平均比率）: CAGR {pctf(bb['fixed_fraction_matched_avg_weight']['cagr'])}、MaxDD {pctf(bb['fixed_fraction_matched_avg_weight']['maxdd'])}。研究用模擬（エンジンのゲート関数と同一コスト）であり、量の調整の妥当性はこの平均比率を揃えた比較で見る。",
        "",
        "### T3 HALT 5σ（|r| > 5σ21、σ21は前日まで21本）",
        "",
        "| 資産 | 発火数 | 件数/年 | D1誤σ発火数 | 翌日から5日平均 | 2025-04-03〜09発火 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    t3 = out["triggers"]["T3_halt_5sigma"]
    for s in ("BTC", "ETH", "SPY", "IWM", "JNJ", "PG"):
        v = t3[s]
        lines.append(
            f"| {s} | {len(v['fires_correct'])} | {fmt(v['fires_per_year'], 2)} | {v['fires_buggy_d1_count']} | {fmt((v['fwd5_after_fire'] or {}).get('mean'))} | {len(v['tariff_window_2025_04_03_09_fires'])} |")
    lines += [
        "",
        "- 発火頻度が資産あたり年2回を超えるもの: " + ("なし" if all(t3[s]["fires_per_year"] <= 2 for s in ("BTC", "ETH", "SPY", "IWM", "JNJ", "PG")) else "あり（HALTが実質的に売買シグナル化。設計の想定と食い違いとして記録）") + "。",
        "- D1（当日リターンを窓に含める誤り）の比較: 誤った σ での発火数は各資産の fires_buggy_d1_count。正しい実装での発火数との差が、D1 が発火を消していた量の実測。",
        "- 暗号最大下位5日（参考値）: " + "; ".join(f"{s} " + ", ".join(f"{x['date']}(発火={x['fired_5sigma']})" for x in t3["_crypto_top5_down_days_reference"][s]) for s in ("BTC", "ETH")) + "。",
        "",
        "### T5 12週判定ゲート",
        "",
        "| 帳簿 | 窓数 | 続行 | 変更 | 中止 | 判定なし(ベンチDD<10%) | 切替≥5回の窓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, v in t5.items():
        shr = v["verdict_share"]
        lines.append(
            f"| {key} | {v['windows']} | {pctf(shr.get('continue'))} | {pctf(shr.get('change'))} | {pctf(shr.get('halt'))} | {pctf(shr.get('no_judgment_bench_dd_lt_10pct'))} | {pctf(v['warn_switch_ge5_share'])} |")
    lines += [
        "",
        f"- 凍結戦略をバックテストどおりに動けても「中止」になる窓の割合（誤って止める率の目安）: 帳簿A {pctf(t5['S01_book_A']['verdict_counts'].get('halt', 0) / t5['S01_book_A']['windows'])}、帳簿C {pctf(t5['S02_book_C']['verdict_counts'].get('halt', 0) / t5['S02_book_C']['windows'])}。",
        "",
        "### T6 全体リスクオフ（記録専用）",
        "",
        f"- 同時成立日数: {out['triggers']['T6_global_risk_off']['days']}日 → {out['triggers']['T6_global_risk_off']['verdict']}。成立日の20営業日後平均: 帳簿A {pctf(out['triggers']['T6_global_risk_off']['fwd20_mean_book_A'])}、帳簿C {pctf(out['triggers']['T6_global_risk_off']['fwd20_mean_book_C'])}。",
        "",
        "### T7 状態の記録（正解データ）",
        "",
        f"- research/s01_s02_regime_states_phaseA.jsonl に {out['triggers']['T7_state_recording']['rows']}行（帳簿A日次×資産＋帳簿C日次）。paperloop 実装後の再計算一致検査の正解データとして使う。",
        "",
        "## 5. データ制限と留意",
        "",
    ]
    for k, v in out["data_limits"].items():
        if isinstance(v, str):
            lines.append(f"- {k}: {v}")
    lines += [
        "",
        "## 6. 生成環境",
        "",
        f"- repo HEAD: {out['hypotheses']['registration']['repo_head_commit']} / 設計書SHA256: {str(out['hypotheses']['registration']['design_doc_sha256'])[:16]}… / seed: {out['hypotheses']['registration']['seed']}（同じseedで2回実行すると出力JSONはバイト単位で一致する）",
        f"- トラック済みJSONのSHA256（実行時）: crypto_backtest_results.json {out['data_integrity']['tracked_json_sha256_before']['crypto_backtest_results.json'][:16]}… / backtest_results_s02.json {out['data_integrity']['tracked_json_sha256_before']['backtest_results_s02.json'][:16]}…",
        "- 本スクリプトは research/ と docs/ と自身（scripts/research/）にのみ書き込む。エンジン本体・追跡JSON・data/ は不変。",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, default=REPO / "research" / "s01_s02_corr_regime_phaseA.json")
    ap.add_argument("--report", type=Path, default=REPO / "docs" / "RESEARCH_S01_S02_CORR_REGIME.md")
    ap.add_argument("--states-jsonl", type=Path, default=REPO / "research" / "s01_s02_regime_states_phaseA.jsonl")
    ap.add_argument("--design-doc", type=Path, required=True)
    ap.parse_args()
    args = ap.parse_args()

    hashes_before = data_tree_hashes()
    crypto_hist, stock_hist = load_histories()
    crypto_defs, stock_defs = crypto_definitions(), stock_definitions()
    tracked_crypto = json.loads(CRYPTO_JSON.read_text(encoding="utf-8"))
    tracked_stock = json.loads(STOCK_JSON.read_text(encoding="utf-8"))

    crypto_end = max(date.fromisoformat(str(r["as_of"])[:10]) for r in crypto_hist["BTC"])
    stock_end = max(date.fromisoformat(str(r["as_of"])[:10]) for r in stock_hist["SPY"])

    runs = {
        "crypto_trend_dual_ma": run_engine(crypto_defs["crypto_trend_dual_ma"], crypto_hist, date(2023, 1, 1), crypto_end, 365, "binance"),
        "crypto_equal_weight": run_engine(crypto_defs["crypto_equal_weight"], crypto_hist, date(2023, 1, 1), crypto_end, 365, "binance"),
        "s02_vol_target_15": run_engine(stock_defs["s02_vol_target_15"], stock_hist, date(2021, 1, 4), stock_end, 252, "alpaca"),
        "s02_baseline_ew_weekly": run_engine(stock_defs["s02_baseline_ew_weekly"], stock_hist, date(2021, 1, 4), stock_end, 252, "alpaca"),
    }

    # ---------------------------------------------------------- equity_match
    def metrics_values(metrics):
        return {k: v["value"] for k, v in metrics.items()
                if isinstance(v, dict) and v.get("value") is not None}

    equity_match = {}
    for sid in ("crypto_trend_dual_ma", "crypto_equal_weight"):
        tracked = [r for r in tracked_crypto["results"] if r["strategy"]["id"] == sid][0]
        ours = runs[sid]
        eq_ok = [p.equity for p in ours.equity_curve] == [p["equity"] for p in tracked["equity_curve"]]
        dates_ok = [p.date.isoformat() for p in ours.equity_curve] == [p["date"] for p in tracked["equity_curve"]]
        tm = metrics_values(tracked["metrics"])
        om = metrics_values(ours.metrics.model_dump(mode="json"))
        equity_match[sid] = {
            "equity_curve_exact": eq_ok, "dates_exact": dates_ok, "metrics_exact": tm == om,
            "match": bool(eq_ok and dates_ok and tm == om),
        }
    expath = tracked_stock["diagnostics"]["s02_vol_target_15"]["full"]["exposure_path"]
    t15 = [r for r in tracked_stock["results"] if r["strategy_id"] == "s02_vol_target_15"][0]
    ours15 = runs["s02_vol_target_15"]
    dr_ok = [p.daily_return for p in ours15.equity_curve] == [p["daily_return"] for p in expath]
    dates_ok = [p.date.isoformat() for p in ours15.equity_curve] == [p["date"] for p in expath]
    tm = metrics_values(t15["metrics"])
    om = metrics_values(ours15.metrics.model_dump(mode="json"))
    equity_match["s02_vol_target_15"] = {
        "daily_returns_exact_vs_tracked_diagnostics": dr_ok, "dates_exact": dates_ok,
        "metrics_exact": tm == om, "match": bool(dr_ok and dates_ok and tm == om),
    }
    tew = [r for r in tracked_stock["results"] if r["strategy_id"] == "s02_baseline_ew_weekly"][0]
    om = metrics_values(runs["s02_baseline_ew_weekly"].metrics.model_dump(mode="json"))
    equity_match["s02_baseline_ew_weekly"] = {
        "metrics_exact": metrics_values(tew["metrics"]) == om,
        "match": metrics_values(tew["metrics"]) == om,
    }
    if not all(v["match"] for v in equity_match.values()):
        print(json.dumps({"equity_match": equity_match}, indent=2))
        raise SystemExit("FATAL: equity_match failed; aborting before any further computation")

    # ---------------------------------------------------------- hypothesis registration
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        head = None
    git_status_before = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                                       capture_output=True, text=True).stdout.splitlines()
    hypotheses = {
        "registration": {
            "repo_head_commit": head,
            "design_doc_path": str(args.design_doc),
            "design_doc_sha256": sha256_file(args.design_doc),
            "registered_before_computation": True,
            "seed": SEED,
            "decision_rules": {
                "H1": "supported iff bootstrap 95% CI upper bound of corr(s_c,s_e)-corr(a_c,a_e) < 0",
                "H2": "not decided in Phase A (reference only); decided in Phase B after data backfill",
                "H3": "supported iff share of w<0.5 weeks with S-01 all-cash-or-one-asset > 0.5",
                "T4": "dip share > 20% -> line too strict, report only (no new value proposed); < 5% -> keep; else keep and disclose false-stop rate",
            },
        },
    }

    # ---------------------------------------------------------- daily series
    rate = daily_rate_fn(load_dgs10())
    s1 = book_series(runs["crypto_trend_dual_ma"], rate)
    ac = book_series(runs["crypto_equal_weight"], rate)
    s2 = book_series(runs["s02_vol_target_15"], rate)
    ae = book_series(runs["s02_baseline_ew_weekly"], rate)
    first_trade = runs["crypto_trend_dual_ma"].trades[0].date

    crypto_rows = {s: map_rows(crypto_hist[s], crypto_end) for s in CRYPTO_SYMBOLS}
    strat1 = crypto_defs["crypto_trend_dual_ma"]
    active_by_day, sma_by_day = {}, {s: {} for s in CRYPTO_SYMBOLS}
    for d in s1["dates"]:
        tgt = _target_weights(strat1, crypto_rows, d)
        active_by_day[d] = sorted(s for s, w in tgt.items() if w > 0)
        for s in CRYPTO_SYMBOLS:
            hist = sorted(day for day in crypto_rows[s] if day < d)
            closes = [float(crypto_rows[s][day]["close"]) for day in hist]
            sma_by_day[s][d] = fmean(closes[-200:]) if len(closes) >= 200 else None
    w_by_date = {}
    spy_close = {date.fromisoformat(str(r["as_of"])[:10]): float(r["close"]) for r in stock_hist["SPY"]}
    for p in expath:
        d = date.fromisoformat(p["date"])
        w_by_date[d] = {**p, "spy_close": spy_close.get(d)}

    # ---------------------------------------------------------- weekly grid + E
    wk = build_week_rows(s1, s2, ac, ae, active_by_day, w_by_date, first_trade,
                         min(stock_end, date(2026, 9, 28)))
    n_weeks = len(wk)
    se_weekly = 1 / math.sqrt(n_weeks)
    correlation = e_sections(wk)
    holiday_fri = sum(1 for r in wk if date.fromisoformat(r["fri"]).weekday() != 4)

    # H1
    diff_ci = boot_ci(
        wk, wk,
        lambda A, B: (pearson([x["s_c_raw"] for x in A], [x["s_e_raw"] for x in A])
                      - pearson([x["a_c"] for x in A], [x["a_e"] for x in A])),
        4, "H1|diff",
    )
    corr_s = correlation["E1_full_period"]["strategy_books_raw"]
    corr_a = correlation["E1_full_period"]["asset_sleeves"]
    diff = corr_s["pearson"] - corr_a["pearson"]
    exc_diff_ci = boot_ci(
        wk, wk,
        lambda A, B: (pearson([x["s_c_exc"] for x in A], [x["s_e_exc"] for x in A])
                      - pearson([x["a_c"] for x in A], [x["a_e"] for x in A])),
        4, "H1|diff_exc",
    )
    hypotheses["H1_strategy_corr_below_asset_corr"] = {
        "basis": "raw strategy returns (engine daily returns); excess variant also recorded",
        "corr_s_c_s_e_raw": corr_s["pearson"],
        "corr_s_c_s_e_excess": correlation["E1_full_period"]["strategy_books_excess"]["pearson"],
        "corr_a_c_a_e": corr_a["pearson"],
        "diff": diff,
        "diff_bootstrap_ci": diff_ci,
        "diff_excess_bootstrap_ci": exc_diff_ci,
        "supported": bool(diff_ci and diff_ci[1] < 0),
    }
    hypotheses["H2_downside_exceedance_above_full"] = {
        "lower10_exceedance_pearson": correlation["E4_exceedance"]["q10"]["lower"]["pearson"],
        "full_asset_pearson": corr_a["pearson"],
        "status": "reference_only_phase_A",
    }
    sub = [r for r in wk if r["w"] < 0.5]
    share = (sum(1 for r in sub if r["n_active"] <= 1) / len(sub)) if sub else None
    hypotheses["H3_w_low_coherent_riskoff"] = {
        "weeks_w_lt_0_5": len(sub),
        "share_s01_cash_or_one_asset": share,
        "supported": bool(share is not None and share > 0.5),
    }

    # ---------------------------------------------------------- triggers
    triggers = {}
    closes_c = {s: {d: float(r["close"]) for d, r in crypto_rows[s].items()} for s in CRYPTO_SYMBOLS}
    opens_c = {s: {d: float(r.get("open", r["close"])) for d, r in crypto_rows[s].items()} for s in CRYPTO_SYMBOLS}
    gates, t1_assets = t1_gate(crypto_rows, active_by_day, s1["dates"], closes_c, opens_c)
    triggers["T1_sma200_gate"] = t1_assets
    t1p, real_curve, real_dd = t1_placebo(gates, closes_c, opens_c, s1["dates"])
    sim_curve = sim_book(gates, opens_c, closes_c, s1["dates"])
    t1p["sim_validation_vs_engine"] = {
        "max_abs_equity_diff": max(abs(a - b) for a, b in zip(sim_curve, [p.equity for p in runs["crypto_trend_dual_ma"].equity_curve])),
        "engine_maxdd": runs["crypto_trend_dual_ma"].metrics.max_drawdown.value,
        "sim_maxdd": max_drawdown([c / p - 1 for p, c in zip([1.0] + sim_curve[:-1], sim_curve)]),
    }
    triggers["T1_placebo"] = t1p
    triggers["T1_whipsaw"] = t1_whipsaw(gates, opens_c, s1["dates"])
    triggers["T1_one_day_late_diagnostic"] = t1_one_day_late(gates, closes_c, opens_c, s1["dates"], real_curve, real_dd)

    t2_assets, sigma_maps = t2_sigma21(crypto_hist, stock_hist, crypto_end, stock_end)
    triggers["T2_sigma21_sizing"] = {"assets": t2_assets}
    triggers["T2_book_B_shadow"] = t2_book_b(gates, closes_c, opens_c, s1["dates"], sigma_maps)

    t3 = t3_halt(crypto_hist, stock_hist, crypto_end, stock_end, closes_c)
    triggers["T3_halt_5sigma"] = t3
    fires_by_symbol = {s: {f["date"] for f in t3[s]["fires_correct"]} for s in ("BTC", "ETH", "SPY")}

    triggers["T4_dd_minus20_line"] = t4_dd_line(s1, s2)

    switch_days_A = [b for a, b in zip(s1["dates"], s1["dates"][1:])
                     if active_by_day[a] != active_by_day[b]]
    rate_A = len(switch_days_A) / ((s1["dates"][-1] - s1["dates"][0]).days / 365.25)
    switch_days_C, prev_w = [], None
    for d in sorted(w_by_date):
        p = w_by_date[d]
        if p["rebalance"]:
            if prev_w is not None and abs(p["daily_signal_w"] - prev_w) > 1e-9:
                switch_days_C.append(d)
            prev_w = p["daily_signal_w"]
    rate_C = len(switch_days_C) / ((sorted(w_by_date)[-1] - sorted(w_by_date)[0]).days / 365.25)
    triggers["T5_12w_gate"] = t5_gate_12w(wk, switch_days_A, switch_days_C, rate_A, rate_C)

    triggers["T6_global_risk_off"] = t6_global_risk_off(s1, s2, ac, ae, active_by_day, w_by_date)

    triggers["T7_state_recording"] = t7_states(
        s1, active_by_day, sma_by_day, w_by_date, fires_by_symbol, args.states_jsonl)

    # ---------------------------------------------------------- assemble + write
    hashes_after = data_tree_hashes()
    alignment = {
        "weekly_primary_rule": "stock Fri-close-to-Fri-close vs crypto Sat-00:00UTC bar, paired by ISO week; holiday Friday replaced by the week's last stock session",
        "daily_auxiliary": "not computed in Phase A (weekly is primary per design 1-2)",
        "common_period": [wk[0]["sat"], wk[-1]["fri"]],
        "weeks": n_weeks,
        "s01_first_trade": first_trade.isoformat(),
        "crypto_coverage": [s1["dates"][0].isoformat(), s1["dates"][-1].isoformat()],
        "stock_coverage": [s2["dates"][0].isoformat(), s2["dates"][-1].isoformat()],
        "weeks_where_friday_replaced_by_prior_session": holiday_fri,
        "weekly_corr_se_approx": se_weekly,
        "weekly_corr_ci_halfwidth_approx": 1.96 * se_weekly,
        "precision_limit": "95% CI half width about +/-0.15; do not decide on small differences",
    }
    data_limits = {
        "crypto_history_start": "2023-01-01: no 2020-03 crash and no 2022 crypto bear in sample; tail corr and HALT efficacy are reference-only until Phase B backfill",
        "rate_series": "DGS3MO and DGS1 absent from data/macro-observations/fred.jsonl; DGS10 used as the common cash rate for both books (design 1-3 fallback, recorded)",
        "rate_series_detail": "FRED DGS10 (10-year Treasury constant maturity, fred.jsonl, official_public)",
        "correlation_precision": f"weekly n={n_weeks}, SE~{se_weekly:.3f}, CI half width ~+/-{1.96 * se_weekly:.2f}",
        "h2_and_lower_tail": "reference values only; decision deferred to Phase B",
        "t1_placebo_caveat": "block shuffle preserves run lengths but not seasonality/vol clustering; indication, not proof",
        "book_b_and_1day_late": "shadow/diagnostic results, never adoption criteria",
        "no_parameter_changed": True,
    }
    out = {
        "alignment": alignment,
        "equity_match": equity_match,
        "correlation": correlation,
        "hypotheses": hypotheses,
        "triggers": triggers,
        "data_limits": data_limits,
        "data_integrity": {
            "tracked_json_sha256_before": {
                CRYPTO_JSON.name: sha256_file(CRYPTO_JSON),
                STOCK_JSON.name: sha256_file(STOCK_JSON),
            },
            "data_tree_unchanged": hashes_before == hashes_after,
            "git_status_porcelain_before_run": git_status_before,
        },
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.states_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    args.report.write_text(render_report(out), encoding="utf-8")
    print(json.dumps({
        "saved_json": str(args.json),
        "saved_report": str(args.report),
        "equity_match_all_true": all(v["match"] for v in equity_match.values()),
        "data_tree_unchanged": hashes_before == hashes_after,
        "weeks": n_weeks,
        "H1_supported": hypotheses["H1_strategy_corr_below_asset_corr"]["supported"],
        "H3_supported": hypotheses["H3_w_low_coherent_riskoff"]["supported"],
        "T4_S01": triggers["T4_dd_minus20_line"]["S01_book_A"]["window_26w_dip_below_minus20_share"],
        "T4_S02": triggers["T4_dd_minus20_line"]["S02_book_C"]["window_26w_dip_below_minus20_share"],
        "T4_S01_judgement": triggers["T4_dd_minus20_line"]["S01_book_A"]["judgement"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
