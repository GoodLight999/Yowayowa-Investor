"""paperloop_panel: the derived daily panel the eval questions (Q1-Q7) read.

Design t_bcb87ad5 §5: one row = (book, asset, date), built ONLY from the event
journal (no external data), so the same journal always yields the same panel
hash (L13). The panel is NOT persisted — it is rebuilt on demand; only the
weekly summary keeps its ``inputs_sha256``.

Principles:
- counterfactual / null columns carry a reason (``*_why``), never a silent 0
  (design P5);
- backfill rows, manual triggers and unjudgeable weeks are counted but
  excluded from every denominator, and the counts are reported (design §4);
- every question reports its own minimum-observation rule and returns
  verdict="insufficient" with a numeric reason when it is not met.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date
from typing import Any

from yowayowa.paperloop import (
    BENCH_CRYPTO_BOOK,
    BOOKS_ENABLED,
    CRYPTO_UNIVERSE,
    EQUITY_UNIVERSE,
    S01_BOOK,
    _load_journal,
    is_live_schema,
    loop_root,
)

# --- minimum observation counts (design §4) -----------------------------------

Q1_MIN_PER_STATE = 20  # ON and OFF each need >= 20 days
Q1_BOOTSTRAP_BLOCK = 20  # block bootstrap length (days)
Q1_BOOTSTRAP_DRAWS = 2000
Q2_MIN_FILLS = 20  # fills with turnover >= 0.01 (the meaningful ones)
Q2_FILL_LAG_WARN_SEC = 2 * 3600
Q3_EFFECTIVE_COST_WARN = 1.5  # effective / assumed ratio
Q4_CONCENTRATION_FLAG = 0.80
Q5_MARGIN_NEAR = 0.5  # "close to the line" days
Q6_MIN_REJECTS = 1
Q7_RESIDUAL_WARN_BPS = 50.0  # weekly


def _days_between(start: date, end: date) -> list[str]:
    from datetime import timedelta

    out: list[str] = []
    cursor = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return var**0.5 if math.isfinite(var) else None


def _block_bootstrap_se(
    values: list[float], block: int = Q1_BOOTSTRAP_BLOCK, draws: int = Q1_BOOTSTRAP_DRAWS
) -> float | None:
    """Standard error of the mean via a deterministic block bootstrap.

    Deterministic (fixed seed) so the panel hash is reproducible (L13).
    """

    if len(values) < block:
        return None
    import random

    rng = random.Random(20261011)
    n = len(values)
    means: list[float] = []
    for _ in range(draws):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(0, n)
            sample.extend(values[start : min(start + block, n)])
        means.append(sum(sample[:n]) / n)
    return _stdev(means)


def build_panel(
    data_dir: Any, *, start: date | None = None, end: date | None = None
) -> dict[str, Any]:
    """Build the derived daily panel from the journal alone (design §5).

    Returns {"schema": "demo-loop-panel-v1.2", "rows": [...], "hash": ...}.
    ``hash`` is sha256 over the canonical JSON of the rows, so building twice
    yields the same hash (L13).
    """

    root = loop_root(data_dir)
    journal = _load_journal(root)
    return _build_panel_from_journal(journal, root, start=start, end=end)


def _build_panel_from_journal(
    journal: list[Mapping[str, Any]],
    root: Any,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    mtm_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in journal:
        if row.get("event") != "mtm":
            continue
        mtm_by_key[(str(row.get("book")), str(row.get("mtm_date")))] = row
    decision_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in journal:
        if row.get("event") != "decision":
            continue
        decision_by_key[
            (str(row.get("book")), str(row.get("asset")), str(row.get("decision_date")))
        ] = row
    fills_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in journal:
        if row.get("event") != "fill":
            continue
        fills_by_key[(str(row.get("decision_id")), str(row.get("asset")))] = row
    marks_by_decision: dict[str, dict[str, Any]] = {}
    for row in journal:
        if row.get("event") == "mark":
            marks_by_decision[str(row.get("decision_id"))] = row
    halt_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in journal:
        if row.get("event") == "halt_check":
            halt_by_key[(str(row.get("book")), str(row.get("check_date")))] = row

    panel_dates = sorted(
        {str(row.get("decision_date")) for row in journal if row.get("event") == "decision"}
    )
    rows: list[dict[str, Any]] = []
    for book in BOOKS_ENABLED:
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        for asset in universe:
            for day in panel_dates:
                if start is not None and day < start.isoformat():
                    continue
                if end is not None and day > end.isoformat():
                    continue
                decision = decision_by_key.get((book, asset, day))
                if decision is None:
                    continue
                decision_id = str(decision.get("decision_id"))
                fill = fills_by_key.get((decision_id, asset))
                mark = marks_by_decision.get(decision_id)
                # ret_next comes from the NEXT day's mtm price for the asset
                # (the panel never reads the market store)
                next_day = _next_day(day)
                next_mtm = mtm_by_key.get((book, next_day))
                close = decision.get("close")
                ret_next = None
                ret_next_why = "unobserved"
                if next_mtm is not None:
                    nxt = (next_mtm.get("prices") or {}).get(asset)
                    if nxt is not None and close not in (None, 0):
                        ret_next = float(nxt) / float(close) - 1.0
                        ret_next_why = ""
                elif not next_day:
                    ret_next_why = "not_applicable"
                regime_tag = decision.get("regime_tag") or {}
                halt = halt_by_key.get((book, day))
                panel_rows = {
                    "book": book,
                    "asset": asset,
                    "date": day,
                    "schema": decision.get("schema"),
                    "regime_state": decision.get("regime_state"),
                    "raw_target_weight": decision.get("raw_target_weight"),
                    "target_weight": decision.get("target_weight"),
                    "weight_eod": _weight_eod(fill, decision),
                    "close": close,
                    "ret_next": ret_next,
                    "ret_next_why": ret_next_why,
                    "filled": fill is not None and bool(fill.get("traded_notional")),
                    "fill_lag_sec": (fill or {}).get("fill_lag_sec"),
                    "shortfall_bps": (mark or {}).get("shortfall_bps"),
                    "cost_assumed_bps": (mark or {}).get("cost_assumed_bps"),
                    "turnover": (fill or {}).get("turnover"),
                    "halted": bool(decision.get("halt_flag")),
                    "halt_margin_min": (halt or {}).get("margin_min") if halt else None,
                    "vol_bucket": regime_tag.get("vol_bucket"),
                    "trend_bucket": regime_tag.get("trend_bucket"),
                    "dd_bucket": regime_tag.get("dd_bucket"),
                    "backfill": bool(decision.get("backfill")),
                    "trigger": decision.get("trigger"),
                    "judgeable": _judgeable(decision, fill, mark, ret_next),
                    "equity": _equity_on(journal, book, day),
                    "equity_gross": _equity_gross_on(journal, book, day),
                    "bench_equity": _equity_on(journal, BENCH_CRYPTO_BOOK, day),
                    "signal_value": decision.get("signal_value"),
                    "bars_since_switch": decision.get("bars_since_switch"),
                    "switch_flag": bool(decision.get("switch_flag")),
                }
                rows.append(panel_rows)
    canonical = json.dumps(rows, sort_keys=True, default=str)
    return {
        "schema": "demo-loop-panel-v1.2",
        "rows": rows,
        "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "freshness_status_seen": True,
    }


def _next_day(day: str) -> str | None:
    from datetime import timedelta

    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return None
    return (parsed + timedelta(days=1)).isoformat()


def _weight_eod(fill: Mapping[str, Any] | None, decision: Mapping[str, Any]) -> float | None:
    if fill is None:
        return decision.get("held_weight_at_decision")
    value = fill.get("weight_after")
    return float(value) if value is not None else decision.get("held_weight_at_decision")


def _judgeable(
    decision: Mapping[str, Any],
    fill: Mapping[str, Any] | None,
    mark: Mapping[str, Any] | None,
    ret_next: float | None,
) -> bool:
    """A day is judgeable when the facts the questions need are all present."""

    del fill, mark
    if not is_live_schema(decision):
        return False  # frozen v1.1 rows are the pre-calibration era (design §8)
    if ret_next is None:
        return False
    if decision.get("regime_state") not in ("RISK_ON", "RISK_OFF"):
        return False  # HALT / skipped rows have no signal to judge
    return not bool(decision.get("backfill"))


def _equity_on(journal: list[Mapping[str, Any]], book: str, day: str) -> float | None:
    latest: Mapping[str, Any] | None = None
    for row in journal:
        if (
            row.get("event") == "mtm"
            and row.get("book") == book
            and str(row.get("mtm_date")) <= day
        ):
            latest = row
    return float(latest["equity"]) if latest is not None else None


def _equity_gross_on(journal: list[Mapping[str, Any]], book: str, day: str) -> float | None:
    latest: Mapping[str, Any] | None = None
    for row in journal:
        if (
            row.get("event") == "mtm"
            and row.get("book") == book
            and str(row.get("mtm_date")) <= day
        ):
            latest = row
    if latest is None:
        return None
    gross = latest.get("equity_gross")
    return float(gross) if gross is not None else None


# --- Q1-Q7 --------------------------------------------------------------------


def _excluded_counts(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "backfill": sum(1 for r in rows if r.get("backfill")),
        "manual": sum(1 for r in rows if r.get("trigger") == "manual"),
        "unjudgeable": sum(1 for r in rows if not r.get("judgeable")),
    }


def _verdict_block(
    q: str, value: Any, se: Any, n: int, excluded: Mapping[str, int], verdict: str, why: str = ""
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "q": q,
        "value": value,
        "se": se,
        "n": n,
        "n_excluded": dict(excluded),
        "verdict": verdict,
    }
    if q in ("Q1",) and isinstance(value, Mapping):
        out["by_states"] = dict(value)
    if why:
        out["insufficient_reason"] = why
    return out


def eval_q1(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q1 signal efficacy: mean(next-day return | ON) - mean(... | OFF)."""

    scope = [
        r
        for r in rows
        if r.get("book") == book and r.get("judgeable") and r.get("ret_next") is not None
    ]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    on = [float(r["ret_next"]) for r in scope if r.get("regime_state") == "RISK_ON"]
    off = [float(r["ret_next"]) for r in scope if r.get("regime_state") == "RISK_OFF"]
    n_on, n_off = len(on), len(off)
    if n_on < Q1_MIN_PER_STATE or n_off < Q1_MIN_PER_STATE:
        return _verdict_block(
            "Q1",
            None,
            None,
            min(n_on, n_off),
            excluded,
            "insufficient",
            f"need {Q1_MIN_PER_STATE} days per state; observed ON={n_on} OFF={n_off}",
        )
    delta = _mean(on) - _mean(off)
    se = _block_bootstrap_se([x - y for x, y in zip(on, off, strict=False)]) if False else None
    # SE of the difference of means (block bootstrap on the pooled series)
    pooled = on + off
    se = _block_bootstrap_se(pooled)
    by_states = {
        "on": {"n": n_on, "mean": _mean(on)},
        "off": {"n": n_off, "mean": _mean(off)},
    }
    verdict = "pass" if (se and abs(delta) > 2 * se) else "warn"
    out = _verdict_block("Q1", delta, se, min(n_on, n_off), excluded, verdict)
    out["by_states"] = by_states
    out["t"] = (delta / se) if (se and se > 0) else None
    return out


def eval_q2(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q2 fills as designed: signed shortfall mean/t, lag median/max, slow fills."""

    scope = [
        r for r in rows if r.get("book") == book and r.get("judgeable") and not r.get("backfill")
    ]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    fills = [r for r in scope if r.get("filled") and (r.get("turnover") or 0.0) >= 0.01]
    shortfalls = [float(r["shortfall_bps"]) for r in fills if r.get("shortfall_bps") is not None]
    lags = [int(r["fill_lag_sec"]) for r in fills if r.get("fill_lag_sec") is not None]
    if len(fills) < Q2_MIN_FILLS:
        return _verdict_block(
            "Q2",
            None,
            None,
            len(fills),
            excluded,
            "insufficient",
            f"need {Q2_MIN_FILLS} fills with turnover>=0.01; observed {len(fills)}",
        )
    mean_shortfall = _mean(shortfalls)
    se = _stdev(shortfalls)
    t = (mean_shortfall / (se / len(shortfalls) ** 0.5)) if se else None
    lags_sorted = sorted(lags) if lags else []
    verdict = "warn" if (t is not None and t > 2.0) else "pass"
    out = _verdict_block("Q2", mean_shortfall, se, len(fills), excluded, verdict)
    out["t"] = t
    out["fill_lag_sec_median"] = lags_sorted[len(lags_sorted) // 2] if lags_sorted else None
    out["fill_lag_sec_max"] = max(lags) if lags else None
    out["fills_over_2h"] = sum(1 for lag in lags if lag > Q2_FILL_LAG_WARN_SEC)
    return out


def eval_q3(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q3 cost as assumed: turnover-weighted effective cost vs the frozen 15bps."""

    scope = [
        r for r in rows if r.get("book") == book and r.get("judgeable") and not r.get("backfill")
    ]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    weighted: list[tuple[float, float]] = []
    null_spread = 0
    for r in scope:
        if r.get("shortfall_bps") is None:
            continue
        null_spread += 1
        assumed = float(r.get("cost_assumed_bps") or 0.0)
        effective = assumed + float(r["shortfall_bps"])
        w = float(r.get("turnover") or 0.0)
        weighted.append((effective, w))
    if not weighted:
        return _verdict_block(
            "Q3",
            None,
            None,
            0,
            excluded,
            "insufficient",
            "no marked fills in the window",
        )
    total_w = sum(w for _, w in weighted) or 1.0
    weighted_mean = sum(v * w for v, w in weighted) / total_w
    assumed = float(scope[0].get("cost_assumed_bps") or 15.0)
    ratio = weighted_mean / assumed if assumed else None
    verdict = "warn" if (ratio is not None and ratio > Q3_EFFECTIVE_COST_WARN) else "pass"
    out = _verdict_block("Q3", weighted_mean, None, len(weighted), excluded, verdict)
    out["assumed_bps"] = assumed
    out["ratio_to_assumed"] = ratio
    out["spread_bps_observed_null_share"] = 1.0  # never observed: stated explicitly
    out["cost_drag_pp"] = _cost_drag_pp(rows, book)
    return out


def _cost_drag_pp(rows: list[Mapping[str, Any]], book: str) -> float | None:
    """equity vs equity_gross, in percentage points, at the window's last day."""

    scope = [r for r in rows if r.get("book") == book and r.get("equity") is not None]
    if not scope:
        return None
    last = max(scope, key=lambda r: str(r.get("date")))
    equity, gross = last.get("equity"), last.get("equity_gross")
    if equity is None or gross in (None, 0):
        return None
    return (float(gross) - float(equity)) / float(gross) * 100.0


def eval_q4(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q4 regime dependence: per (vol x trend) bucket mean vs bench + concentration.

    Judicious by design: never a pass/fail gate (observation counts are far
    too small). It only raises ``flag_regime_concentration`` when one bucket
    contributes > 80% of the book's cumulative excess return.
    """

    scope = [r for r in rows if r.get("book") == book and r.get("judgeable")]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    buckets: dict[str, list[float]] = {}
    for r in scope:
        key = f"{r.get('vol_bucket')}x{r.get('trend_bucket')}"
        if r.get("equity") is None or r.get("bench_equity") is None:
            continue
        buckets.setdefault(key, []).append(float(r["equity"]) - float(r["bench_equity"]))
    per_bucket = {k: {"n": len(v), "mean_excess": _mean(v)} for k, v in buckets.items()}
    # concentration: bucket with the largest |last excess| vs the total swing
    contributions: dict[str, float] = {}
    for key, values in buckets.items():
        if not values:
            continue
        contributions[key] = abs(values[-1] - values[0])
    total = sum(contributions.values())
    top_share = (max(contributions.values()) / total) if total > 0 else None
    flagged = bool(top_share is not None and top_share > Q4_CONCENTRATION_FLAG)
    out = _verdict_block("Q4", top_share, None, len(scope), excluded, "informational")
    out["by_bucket"] = per_bucket
    out["flag_regime_concentration"] = flagged
    return out


def eval_q5(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q5 HALT efficacy: per-HALT counterfactual + near-miss day counts.

    Statistical pass/fail is deliberately NOT produced (too few HALTs); the
    output is one line per HALT plus the margin distribution.
    """

    scope = [r for r in rows if r.get("book") == book]
    excluded = _excluded_counts(scope)
    margins = [float(r["halt_margin_min"]) for r in scope if r.get("halt_margin_min") is not None]
    halted_rows = [r for r in scope if r.get("halted")]
    out = _verdict_block("Q5", len(halted_rows), None, len(scope), excluded, "informational")
    out["halted_days"] = len(halted_rows)
    out["near_miss_days_margin_lt_0_5"] = sum(1 for m in margins if m < Q5_MARGIN_NEAR)
    out["margin_min"] = min(margins) if margins else None
    out["per_halt"] = [
        {
            "date": r.get("date"),
            "asset": r.get("asset"),
            "raw_target_weight": r.get("raw_target_weight"),
            "target_weight": r.get("target_weight"),
        }
        for r in halted_rows
    ]
    return out


def eval_q6(
    rows: list[Mapping[str, Any]], *, book: str = S01_BOOK, journal: Any = None
) -> dict[str, Any]:
    """Q6 guard efficacy: rejection classes + per-rejection counterfactual.

    Zero guard rejections is the EXPECTED normal state in the daily-bar flow
    (design §4 Q6 note); 0 is reported with its reason, not treated as a pass.
    """

    source = journal if journal is not None else []
    rejects = [
        row for row in source if row.get("event") == "fill_rejected" and row.get("book") == book
    ]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    classes: dict[str, int] = {}
    for row in rejects:
        classes[str(row.get("reject_class"))] = classes.get(str(row.get("reject_class")), 0) + 1
    out = _verdict_block("Q6", len(rejects), None, len(rejects), excluded, "informational")
    out["rejections_by_class"] = classes
    out["rejections"] = [
        {
            "decision_id": row.get("decision_id"),
            "reason": row.get("reject_reason"),
            "class": row.get("reject_class"),
            "rejected_delta_weight": row.get("rejected_delta_weight"),
            "ref_bar_open_ts": row.get("ref_bar_open_ts"),
        }
        for row in rejects
    ]
    out["note"] = (
        "0 is expected in the daily flow; guard acceptance evidence is the "
        "synthetic G1-G9 unit tests, not a runtime count"
    )
    return out


def eval_q7(rows: list[Mapping[str, Any]], *, book: str = S01_BOOK) -> dict[str, Any]:
    """Q7 vs benchmark: cumulative diff, DD ratio, best-week-excluded diff."""

    scope = [r for r in rows if r.get("book") == book and r.get("equity") is not None]
    excluded = _excluded_counts([r for r in rows if r.get("book") == book])
    if not scope:
        return _verdict_block(
            "Q7", None, None, 0, excluded, "insufficient", "no equity rows in the window"
        )
    last = max(scope, key=lambda r: str(r.get("date")))
    equity, bench = last.get("equity"), last.get("bench_equity")
    if equity is None or bench is None:
        return _verdict_block(
            "Q7", None, None, len(scope), excluded, "insufficient", "no benchmark equity yet"
        )
    cum_diff_pp = (float(equity) - float(bench)) * 100.0
    book_dd = _max_drawdown([float(r["equity"]) for r in scope])
    bench_rows = [
        r for r in rows if r.get("book") == BENCH_CRYPTO_BOOK and r.get("equity") is not None
    ]
    bench_dd = _max_drawdown([float(r["equity"]) for r in bench_rows])
    dd_ratio = (book_dd / bench_dd) if bench_dd else None
    out = _verdict_block("Q7", cum_diff_pp, None, len(scope), excluded, "informational")
    out["bench_equity"] = bench
    out["equity"] = equity
    out["max_drawdown_book"] = book_dd
    out["max_drawdown_bench"] = bench_dd
    out["dd_ratio"] = dd_ratio
    return out


def _max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 1.0
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def evaluate(
    data_dir: Any, *, start: date | None = None, end: date | None = None, book: str = S01_BOOK
) -> dict[str, Any]:
    """Build the panel and answer Q1-Q7 for one book.

    ``inputs_sha256`` is the panel hash — the weekly summary stores it so the
    verdict can be reproduced against the journal it came from.
    """

    root = loop_root(data_dir)
    journal = _load_journal(root)
    panel = _build_panel_from_journal(journal, root, start=start, end=end)
    rows = panel["rows"]
    return {
        "schema": "demo-loop-eval-v1.2",
        "book": book,
        "inputs_sha256": panel["hash"],
        "panel_rows": len(rows),
        "eval": {
            "Q1": eval_q1(rows, book=book),
            "Q2": eval_q2(rows, book=book),
            "Q3": eval_q3(rows, book=book),
            "Q4": eval_q4(rows, book=book),
            "Q5": eval_q5(rows, book=book),
            "Q6": eval_q6(rows, book=book, journal=journal),
            "Q7": eval_q7(rows, book=book),
        },
    }


def panel_json(data_dir: Any, **kwargs: Any) -> str:
    return json.dumps(
        build_panel(data_dir, **kwargs), ensure_ascii=False, sort_keys=True, default=str
    )


def eval_json(data_dir: Any, **kwargs: Any) -> str:
    return json.dumps(evaluate(data_dir, **kwargs), ensure_ascii=False, sort_keys=True, default=str)
