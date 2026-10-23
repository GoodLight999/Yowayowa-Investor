"""Demo (paper) forward-operation loop v1.1 — frozen books, event journal.

Spec: t_fcd37354 ``DEMO_LOOP_V1_IMPLEMENTATION_SPEC.md`` (implementation
contract, D1-D12 fixes), design t_421158aa, falsification t_c998951d.

Key contracts:
- Single event journal ``data/demo-loop/journal.jsonl`` (append-only, hash
  chain, seq numbering). State is ALWAYS rebuilt from the journal
  (``rebuild_state``); no state files.
- decision_id = "{book}:{asset}:{decision_date}" is the primary key:
  decide is idempotent (existing decision rows are never re-written, and a
  re-run can never un-fill a filled row — D4).
- commit-before-fill: ``decision`` rows carry ``recorded_at`` (taken inside
  the function, never an argument — D5) and ``inputs_last_bar_ts`` of the
  actually-read bar; ``fill`` rows may only use a bar whose open time is
  strictly after the decision's recorded_at (assert, C2/C3).
- ``mtm`` rows are written EVERY day per book (D3) so the equity chain, DD
  and the -20% stop line work on hold-only days.
- apply_fill trades the DELTA notional |target - held| * equity (D2), at
  the paper fill price, charging the frozen 15 bps one-way cost.
- sigma21 for the 5-sigma HALT is computed on the PRIOR 21 returns only (D1;
  a window with the return itself caps z at ~4.36 and never fires).
- Anti-scalp guard (``yowayowa.paperloop_guard.check_trade``) evaluated at
  the fill instant on journal-derived state (D6).
- Benchmark CRYPTO_1N buys 1/N on the first decision and never trades
  again (D7); recompute audits apply to strategy books only.
- shortfall_bps is SIGNED: s*(paper/bt - 1)*1e4, positive = worse than
  backtest (D8).
- ``inputs_last_bar_ts`` is the newest as_of actually READ for the check,
  and each decision freezes its inputs to
  ``data/demo-loop/inputs/{date}/{book}.json`` with input_sha256 (D9/F5).
- Journal loads raise ChainError on corrupt lines (D10); only the 1h tape
  cache may skip bad rows.
- Zero order path: no broker/MT5/order-endpoint references (M6, grep test).

Note: equity books (C, EQUITY_EW) carry the same schema as the crypto books
(v1.3 rows; BOOK_C_ENABLED gates the book C set).

v1.6 addition (C-02, docs/PAPERLOOP_MONEY_REVIEW_20261023.md): the weekly row's
``week_return`` is the 7-day window's change rate (last mark before
``window_start`` → last mark on/before ``week_end``), not the cumulative return
from inception. The cumulative figure keeps its value under ``cum_return``.
``week_return_base`` / ``week_return_as_of`` / ``week_return_reason`` make the
window audit-able, and the summary row is now ``demo-loop-summary-v1.6`` because
rows already written to the append-only weekly file carry the old meaning of
that key (they are not rewritten).

v1.4 additions (t_dddae39e, revenue verification):
- EQUITY_EW is ENABLED: the equity 1/N benchmark book was preregistered and
  coded when Book C shipped but never added to BOOKS_ENABLED, so Book C had
  no comparison book and its own hypothesis ("controls drawdown vs EW") was
  unreadable forward. It buys 1/N once and holds (D7, same as CRYPTO_1N).
- each strategy book's weekly row carries its own comparison line
  (benchmark_book / cum_pnl / max_drawdown / benchmark_cum_pnl /
  benchmark_max_drawdown / vs_benchmark_pp_cum / dd_vs_benchmark_ratio).
  Missing data stays None; nothing is zero-filled and no gate is auto-passed.
- v1.4 review round 2 (t_dddae39e): the comparison line is measured from a
  SHARED baseline date — the first mtm date that both books carry on/after the
  benchmark's first fill day (comparison_start). Until the benchmark has a fill
  and such a shared mark exists, every comparison number is None with
  comparison_reason set. A benchmark sitting in cash is never reported as a
  0% comparison.

v1.2 additions (eval logging, design t_bcb87ad5):
- new rows carry schema="demo-loop-v1.2"; v1.1 rows are never rewritten.
- B1 fix: a decision is finished when ANY fill/fill_rejected row exists for
  its decision_id (event accumulation, never the frozen fill_status column).
- B2 fix: mtm is written at most once per (book, mtm_date); the row records
  the price bar date it used (price_bar_date).
- B4 fix: every appended timestamp is tz-aware UTC; naive stamps are refused.
- B5 fix: every row carries code_rev; decision rows carry spec_hash.
- B6 fix: the daily anchor is one row per anchored date (dedupe by date).
- new events: halt_check (daily, per book, per-trigger margins) and run
  (start/end per job). loop_run is gone.
- regime_spec is preregistered before the first v1.2 decision row.

v1.3 additions (reconciliation t_69d006ad / cards t_554c40f6, t_2202c100):
- new rows carry schema="demo-loop-v1.3"; v1.1/v1.2 rows are never rewritten.
- the -20% drawdown line (HALT_DRAWDOWN) is DEMOTED from a stop to a
  WARNING: mtm/halt_check rows record ``drawdown_warning`` and the
  halt_check action becomes "warn"; nothing stops or halts on it any more
  (t_554c40f6: 55.9% hit rate over the 26w window). Historical ``stopped``
  flags on frozen rows stay recorded but no longer propagate a stop.
- bench-over-DD auto-stop (weekly, max-DD ratio, bench DD >= 10%, multiplier
  k): HOOK ONLY — ``BENCH_DD_STOP`` config + the pure ``bench_dd_stop_fired``
  predicate exist but stay disabled until the master freezes k.
- C6: a newer decision for the same (book, asset) closes older pendings with
  fill_rejected(reject_reason="superseded") at resolve time.
- MILESTONE_RULES[26] cost gate replaced by the frozen "signed shortfall_bps
  mean 95% upper bound <= 15 bps" (shortfall_bps_mean_upper95_max).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import subprocess
import sys
import urllib.request
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from yowayowa.backtest_models import VOLATILITY_TARGET_SYMBOL, BacktestStrategyDefinition
from yowayowa.paperloop_guard import GuardInput, check_trade

# --- frozen v1 parameters (never tuned retroactively) ------------------------

S01_BOOK = "A"
BOOK_C_BOOK = "C"
BENCH_CRYPTO_BOOK = "CRYPTO_1N"
BENCH_EQUITY_BOOK = "EQUITY_EW"
STRATEGY_BOOKS = (S01_BOOK, BOOK_C_BOOK)
# v1.4 (t_dddae39e): the equity benchmark book was preregistered and coded
# (BOOK_SPEC_HASHES / _benchmark_weights / the equity branches below) when
# Book C was deployed, but it was never added here — so Book C had no
# comparison book and its own frozen hypothesis ("S-02 controls drawdown vs
# EW") could not be read forward. Enabled from the next decision date; it buys
# 1/N once and never trades again, exactly like CRYPTO_1N (D7).
BOOKS_ENABLED = (S01_BOOK, BENCH_CRYPTO_BOOK, BOOK_C_BOOK, BENCH_EQUITY_BOOK)
# Each strategy book's own comparison book (read by weekly_summary). A uses the
# crypto 1/N book, C uses the equity EW book it was adopted against.
BOOK_BENCHMARK = {S01_BOOK: BENCH_CRYPTO_BOOK, BOOK_C_BOOK: BENCH_EQUITY_BOOK}
CRYPTO_UNIVERSE = ("BTC", "ETH")
EQUITY_UNIVERSE = (VOLATILITY_TARGET_SYMBOL, "IWM", "JNJ", "PG")
SMA_LOOKBACK = 200
VOL_WINDOW = 21
CRYPTO_ANNUALIZATION = 365
EQUITY_ANNUALIZATION = 252
COST_RATE = 0.0015
COST_RATE_BPS = 15.0
BOOK_C_TARGET_VOL = 0.15
# v1.3 (t_2202c100): WARNING line, not a stop — mtm/halt_check rows record
# drawdown_warning; nothing halts or stops on this any more. The constant
# keeps its name (frozen v1.1 exports) but only labels the warning threshold.
HALT_DRAWDOWN = -0.20  # warning line: t_554c40f6 fired it 55.9% of 26w windows
HALT_FRESHNESS_CRYPTO = timedelta(hours=26)
HALT_FRESHNESS_EQUITY_DAYS = 2
HALT_SIGMA_MOVE = 5.0
FLOOR_SIGMA_DAILY = 0.001  # degenerate-window floor: 0.1%/day (~1.9% ann.)
GUARD_MIN_HOLD_HOURS = 4.0
GUARD_MAX_FILLS_PER_DAY = 2
BINANCE_TAPE = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
SCHEMA = "demo-loop-v1.1"
SCHEMA_V12 = "demo-loop-v1.2"
SCHEMA_V13 = "demo-loop-v1.3"  # t_2202c100: DD warning, C6 superseded, R2/R3 split
LIVE_SCHEMAS = (SCHEMA_V12, SCHEMA_V13)  # "current" rows; anything else is frozen legacy
BOOK_C_ENABLED = True
# Weekly benchmark-excess-DD auto-stop (t_2202c100): HOOK ONLY. The loop must
# not consult it until the master freezes the multiplier k — until then the
# gate below stays disabled and ``bench_dd_stop_fired`` answers False.
BENCH_DD_STOP: dict[str, Any] = {
    "enabled": False,  # k is undecided (master's call) — do not flip by hand
    "bench_dd_min": 0.10,  # the benchmark DD must be at least this deep
    "k": None,  # fires when book_dd <= -k * |bench_dd|; None = undecided
}
# v1.5 (t_e2c98d2b): the registered edge-lifetime monitor (yowayowa.cusum_watch,
# the weekly vital in ops/demo-loop/weekly.sh) appends its verdict to
# cusum_A.jsonl with ``action: "PAUSE" | "continue"``. Until this card nothing
# read that file: the preregistered PAUSE action was recorded and had no effect
# on the next decision, so the loop was open between judgment and next signal.
# The gate below makes the recorded verdict an INPUT to run_daily_decide for the
# monitored book only. It never recomputes z_w/S_w and never writes to the
# monitor's log (the monitor stays the single source of truth).
CUSUM_LOG_NAME = "cusum_A.jsonl"
CUSUM_GATED_BOOK = S01_BOOK  # the monitor is registered for book A only
CUSUM_ACTIONS = ("continue", "PAUSE")
MILESTONE_RULES = {  # design §4, frozen numeric gates
    4: {"loop_success_rate": 0.95, "observed_cost_bps_max": 30.0},
    12: {"dd_vs_benchmark": 0.6, "cum_pnl_min": -0.10, "switches_multiple": 2.0},
    # 26w cost gate (t_2202c100): the old "observed cost <= 1.5x assumed"
    # mixed shortfall with the assumed fee (consultant t_69d006ad, F3). The
    # frozen replacement is the one-sided 95% upper bound of the SIGNED
    # shortfall_bps mean <= 15 bps.
    26: {"dd_vs_benchmark": 0.6, "shortfall_bps_mean_upper95_max": 15.0},
}
SHORTFALL_UPPER95_Z = 1.6449  # one-sided 95% normal quantile (frozen)

# clock injection point (single, for tests) — D5
CLOCK = datetime.now


def is_live_schema(row: Mapping[str, Any]) -> bool:
    """True for rows written by live (non-frozen) code: v1.2 or v1.3.

    The verifier treats everything else as frozen legacy (design §8-1): the
    pre-v1.2 rows are never rewritten, so their violations are listed, not
    failed. v1.3 (t_2202c100) joins v1.2 on the live side.
    """

    return row.get("schema") in LIVE_SCHEMAS


# --- environment facts (B5: code_rev, host) ----------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def code_rev() -> str:
    """HEAD's first 12 hex chars, plus "+dirty" when the tree is dirty.

    Never raises: a missing git or a detached/no-repo run yields "unknown".
    """

    root = _repo_root()
    try:
        head = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if len(head) < 12:
        return "unknown"
    rev = head[:12]
    try:
        status = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        status = ""
    return rev + ("+dirty" if status else "")


def host_name() -> str:
    """The executing host's name (run events, §3-6)."""

    return os.uname().nodename if hasattr(os, "uname") else "unknown"


def _require_utc(value: Any, field: str) -> datetime:
    """P4: a timestamp without a timezone is REFUSED, never assumed."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: missing timestamp")
    parsed = _parse_ts(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}: timestamp {value!r} has no timezone (UTC required)")
    return parsed.astimezone(UTC)


def _utc_now_text(now: datetime | None = None) -> str:
    """P4: every appended stamp is tz-aware UTC and normalised to +00:00."""

    stamp = now if now is not None else CLOCK(UTC)
    if stamp.tzinfo is None:
        raise ValueError("naive datetime: recorded_at must carry a UTC timezone")
    return stamp.astimezone(UTC).isoformat()


class ChainError(RuntimeError):
    """A journal line is corrupt or the hash chain is broken (D10)."""


# --- filesystem contracts ---------------------------------------------------


def loop_root(data_dir: Path) -> Path:
    return data_dir / "demo-loop"


def journal_path(root: Path) -> Path:
    return root / "journal.jsonl"


def anchors_path(root: Path) -> Path:
    return root / "anchors.jsonl"


def inputs_dir(root: Path) -> Path:
    return root / "inputs"


def tape_dir(root: Path) -> Path:
    return root / "tape" / "1h"


def summary_path(root: Path) -> Path:
    return root / "weekly_summary.jsonl"


def prereg_path(root: Path) -> Path:
    return root / "prereg.jsonl"


def verify_dir(root: Path) -> Path:
    return root / "verify"


# --- hashing / journal primitives -------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_ts(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed


def _parse_ts_assume_utc(value: Any) -> datetime:
    """Parsing that ASSUMES UTC for naive stamps (legacy use only)."""

    parsed = _parse_ts(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _rows_from_jsonl(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    """Read a JSONL file. Journal reads are STRICT: a corrupt line raises
    ChainError (D10). Only non-journal caches (1h tape) may skip bad rows."""

    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except ValueError as exc:
                if strict:
                    raise ChainError(f"{path.name}:{index}: invalid JSON ({exc})") from exc
                continue
            if isinstance(entry, dict):
                out.append(entry)
    return out


def _load_journal(root: Path) -> list[dict[str, Any]]:
    rows = _rows_from_jsonl(journal_path(root), strict=True)
    prev = ""
    for seq, row in enumerate(rows, start=1):
        if int(row.get("seq", -1)) != seq:
            raise ChainError(f"journal seq gap at line {seq}: {row.get('seq')}")
        if str(row.get("prev_hash", "")) != prev:
            raise ChainError(f"journal prev_hash mismatch at line {seq}")
        recorded = row.get("row_hash")
        expected = _sha256(
            json.dumps(
                {k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str
            )
        )
        if recorded != expected:
            raise ChainError(f"journal row_hash mismatch at line {seq}")
        prev = str(recorded)
    return rows


def _append_row(root: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    """The ONLY journal writer: seq numbering + prev-hash chain + fsync.

    v1.2: every row carries code_rev (B5), and a naive/non-UTC recorded_at is
    refused (B4/P4). v1.1 rows are appended by the legacy writer below and
    are never re-stamped. v1.3 (t_2202c100): new rows carry
    schema="demo-loop-v1.3"; v1.1/v1.2 rows are never re-stamped either.
    """

    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_journal(root) if path.exists() else []
    seq = len(prior) + 1
    out = dict(row)
    out["schema"] = SCHEMA_V13
    out["seq"] = seq
    out["code_rev"] = code_rev()
    _require_utc(out.get("recorded_at"), "recorded_at")  # B4/P4: refuse naive
    out["recorded_at"] = _require_utc(out.get("recorded_at"), "recorded_at").isoformat()
    out["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    out["row_hash"] = _sha256(
        json.dumps({k: v for k, v in out.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return out


def _append_row_v11(root: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    """The v1.1 journal writer (frozen). Appends schema="demo-loop-v1.1".

    Kept so a downgrade/rollback path can still write historically-stamped
    rows; the live v1.2 jobs never call it.
    """

    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = _load_journal(root) if path.exists() else []
    seq = len(prior) + 1
    out = dict(row)
    out["schema"] = SCHEMA
    out["seq"] = seq
    out["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    out["row_hash"] = _sha256(
        json.dumps({k: v for k, v in out.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return out


def _write_anchor(root: Path, anchored_date: date | None = None) -> dict[str, Any] | None:
    """Daily anchor: last seq + hash (chain-cut detection), ONE per day (B6).

    ``anchored_date`` defaults to UTC today; re-anchoring the same date is a
    no-op so a retry can never grow duplicate rows (v1.1 wrote one per call).
    """

    rows = _load_journal(root)
    if not rows:
        return None
    day = anchored_date if anchored_date is not None else CLOCK(UTC).date()
    anchors = _rows_from_jsonl(anchors_path(root))
    if any(str(a.get("anchor_date")) == day.isoformat() for a in anchors):
        return None
    last = rows[-1]
    out = {
        "seq": last["seq"],
        "row_hash": last["row_hash"],
        "anchor_date": day.isoformat(),
        "anchored_at": _utc_now_text(),
    }
    with anchors_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return out


def _anchors_ok(root: Path) -> bool:
    anchors = _rows_from_jsonl(anchors_path(root))
    rows = _load_journal(root)
    hashes = {str(row["row_hash"]) for row in rows}
    return all(str(a.get("row_hash")) in hashes for a in anchors)


# --- market data ------------------------------------------------------------


def _daily_map(root: Path, symbol: str) -> dict[date, dict[str, float]]:
    """Finalized daily bars from the local OHLCV store, oldest first."""

    source = "binance" if symbol in BINANCE_TAPE else "alpaca"
    market_root = root.parent / ("crypto-ohlcv" if symbol in BINANCE_TAPE else "stock-ohlcv")
    path = market_root / symbol.upper() / "ohlcv.jsonl"
    out: dict[date, dict[str, float]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # the daily store is a vendor cache: skip, never crash
            if not isinstance(entry, dict) or entry.get("provider") != source:
                continue
            try:
                opened = float(entry["open"])
                close = float(entry["close"])
                session = _parse_ts(entry["as_of"]).date()
            except (KeyError, TypeError, ValueError):
                continue
            if not all(math.isfinite(price) and price > 0 for price in (opened, close)):
                continue
            if session in out:
                raise ChainError(f"duplicate daily session for {symbol} on {session}")
            out[session] = {"open": opened, "close": close}
    return out


def _fetch_crypto_1h(symbol: str, *, limit: int = 48) -> list[dict[str, Any]]:
    """Realized 1h bars from Binance public klines (still-forming excluded)."""

    market_symbol = BINANCE_TAPE[symbol.upper()]
    url = f"https://api.binance.com/api/v3/klines?symbol={market_symbol}&interval=1h&limit={limit}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    bars: list[dict[str, Any]] = []
    for candle in payload[:-1]:
        if not isinstance(candle, list) or len(candle) < 5:
            continue
        opened, close = float(candle[1]), float(candle[4])
        open_ts = datetime.fromtimestamp(int(candle[0]) / 1000, tz=UTC)
        if math.isfinite(opened) and math.isfinite(close) and opened > 0 and close > 0:
            bars.append({"open_time": open_ts.isoformat(), "open": opened, "close": close})
    return bars


def _forming_candle_open(symbol: str) -> dict[str, Any] | None:
    """The current (still-forming) 1h candle's open, straight from the API.

    The open price is fixed at the candle's start and is usable as an
    execution price while the candle forms; forming candles are never
    persisted into the tape cache."""

    market_symbol = BINANCE_TAPE[symbol.upper()]
    url = f"https://api.binance.com/api/v3/klines?symbol={market_symbol}&interval=1h&limit=1"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candle = (
        payload[-1]
        if isinstance(payload, list) and payload and isinstance(payload[-1], list)
        else None
    )
    if candle is None or len(candle) < 5:
        return None
    opened = float(candle[1])
    open_ts = datetime.fromtimestamp(int(candle[0]) / 1000, tz=UTC)
    if not math.isfinite(opened) or opened <= 0:
        return None
    return {"open_time": open_ts.isoformat(), "open": opened}


def _append_intraday(root: Path, symbol: str, bars: list[dict[str, Any]]) -> None:
    """Idempotent 1h tape cache append (book-shared, corrupt rows skippable)."""

    path = tape_dir(root) / f"{symbol.upper()}.jsonl"
    existing: set[str] = set()
    if path.exists():
        for entry in _rows_from_jsonl(path):
            existing.add(str(entry.get("open_time")))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for bar in bars:
            if str(bar["open_time"]) in existing:
                continue
            handle.write(json.dumps(dict(bar), ensure_ascii=False, default=str) + "\n")


def _first_realized_open(path: Path, symbol: str, after: datetime) -> dict[str, Any] | None:
    full = path / f"{symbol.upper()}.jsonl"
    if not full.exists():
        return None
    best: dict[str, Any] | None = None
    for entry in _rows_from_jsonl(full):
        try:
            open_ts = _parse_ts(entry["open_time"])
            opened = float(entry["open"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            open_ts > after
            and math.isfinite(opened)
            and opened > 0
            and (best is None or open_ts < _parse_ts(best["open_time"]))
        ):
            best = {"open_time": entry["open_time"], "open": opened}
    return best


def _first_fill_candidate(root: Path, symbol: str, after: datetime) -> dict[str, Any] | None:
    """First execution candidate open strictly after ``after``: the tape's
    first realized bar, else the current forming candle's open (network)."""

    realized = _first_realized_open(tape_dir(root), symbol, after)
    forming: dict[str, Any] | None = None
    try:
        forming = _forming_candle_open(symbol)
    except (OSError, ValueError, KeyError) as exc:
        print(f"forming-candle fetch failed for {symbol}: {exc}", file=sys.stderr)
    if forming is not None and _parse_ts(forming["open_time"]) <= after:
        forming = None
    candidates = [bar for bar in (realized, forming) if bar is not None]
    if not candidates and symbol in EQUITY_UNIVERSE:
        daily = _daily_map(root, symbol)
        for session, bar in sorted(daily.items()):
            session_open = datetime(session.year, session.month, session.day, 13, 30, tzinfo=UTC)
            if session_open > after:
                candidates.append(
                    {
                        "open_time": session_open.isoformat(),
                        "open": bar["open"],
                        "close": bar["close"],
                    }
                )
                break
    if not candidates:
        return None
    return min(candidates, key=lambda bar: _parse_ts(bar["open_time"]))


# --- indicators -------------------------------------------------------------


def _sma200(closes: list[float]) -> float | None:
    if len(closes) < SMA_LOOKBACK:
        return None
    total = sum(closes[-SMA_LOOKBACK:])
    return total / SMA_LOOKBACK if math.isfinite(total) else None


def _sigma21(closes: list[float], annualization: int) -> float | None:
    """21-day realized volatility of the LAST 21 returns (annualized)."""

    if len(closes) < VOL_WINDOW + 1:
        return None
    window = closes[-(VOL_WINDOW + 1) :]
    returns = [current / prior - 1 for prior, current in itertools.pairwise(window) if prior > 0]
    if len(returns) != VOL_WINDOW:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    sigma = variance**0.5
    return sigma * annualization**0.5 if math.isfinite(sigma) else None


def _sigma21_prior(closes: list[float], annualization: int) -> float | None:
    """21-day realized volatility EXCLUDING the latest return (D1 fix).

    Used for the 5-sigma HALT: a window with the judged return caps z at
    ~4.36 (Samuelson), so a surprise day can never fire.
    """

    if len(closes) < VOL_WINDOW + 2:
        return None
    window = closes[-(VOL_WINDOW + 2) : -1]
    returns = [current / prior - 1 for prior, current in itertools.pairwise(window) if prior > 0]
    if len(returns) != VOL_WINDOW:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    sigma = variance**0.5
    return sigma * annualization**0.5 if math.isfinite(sigma) else None


# --- engine calls ------------------------------------------------------------


def _engine_target(
    book: str,
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, Any]]],
    decision_date: date,
) -> dict[str, float]:
    """One frozen book's target weights via the SAME P2 engine function."""

    from yowayowa.services.backtest import _target_weights

    if book == S01_BOOK:
        strategy = BacktestStrategyDefinition(
            id="s01-a",
            name="crypto_trend_dual_ma",
            description=(
                "Paper book A: frozen crypto_trend_dual_ma strategy from the "
                "P2 crypto backtests (SMA200, next-open execution, equal weight)."
            ),
            signal="crypto_trend_dual_ma",
            universe=list(CRYPTO_UNIVERSE),
            weighting="equal",
        )
    elif book == BOOK_C_BOOK:
        strategy = BacktestStrategyDefinition(
            id="s02-c",
            name="vol_target_defensive",
            description=(
                "Paper book C: frozen vol_target_defensive (S-02, 15% target, "
                "weekly) from the P2 US-equity backtests."
            ),
            signal="vol_target_defensive",
            universe=list(EQUITY_UNIVERSE),
            weighting="equal",
            lookback=VOL_WINDOW,
            target_volatility=BOOK_C_TARGET_VOL,
        )
    else:
        raise ValueError(f"unsupported book for the engine: {book!r}")
    target = _target_weights(strategy, rows_by_symbol, decision_date)
    assert all(math.isfinite(weight) for weight in target.values()), "non-finite target weight"
    return target


def _benchmark_weights(book: str) -> dict[str, float]:
    if book == BENCH_CRYPTO_BOOK:
        return {symbol: 1 / len(CRYPTO_UNIVERSE) for symbol in CRYPTO_UNIVERSE}
    if book == BENCH_EQUITY_BOOK:
        return {symbol: 1 / len(EQUITY_UNIVERSE) for symbol in EQUITY_UNIVERSE}
    raise ValueError(f"unsupported benchmark book: {book!r}")


BOOK_SPEC_HASHES: dict[str, str] = {
    S01_BOOK: "primary:s01:crypto_trend_dual_ma:BTCETH:200:daily:0.0015",
    BENCH_CRYPTO_BOOK: "benchmark:crypto_1n:buy_and_hold:BTCETH",
    BOOK_C_BOOK: "primary:s02:vol_target_defensive:SPY_IWM_JNJ_PG:15:weekly:0.0015",
    BENCH_EQUITY_BOOK: "benchmark:equity_ew:buy_and_hold:SPY_IWM_JNJ_PG",
}

# --- regime_spec (§3-4): preregistered market-regime boundaries ---------------
# The regime label is built from quantities the strategy does NOT use, so
# "ON happened to coincide with an up-trend" cannot be the finding by
# construction. Boundaries are frozen here and preregistered before any v1.2
# decision row (design §3-4; changing them requires a new prereg row).

REGIME_VOL_WINDOW = 365  # trailing window for the vol-rank terciles
REGIME_R63_FLAT = 0.05  # |r63| below this is "flat"
REGIME_DD_DEEP = -0.20  # trailing 365d drawdown at/below this is "deep"
REGIME_SPEC: dict[str, Any] = {
    "version": 1,
    "vol_bucket": {
        "window_days": REGIME_VOL_WINDOW,
        "quantiles": (1 / 3, 2 / 3),
        "labels": ("low", "mid", "high"),
    },
    "trend_bucket": {"window_days": 63, "flat_abs_return": REGIME_R63_FLAT},
    "dd_bucket": {"window_days": REGIME_VOL_WINDOW, "deep_drawdown": REGIME_DD_DEEP},
}


def regime_spec_hash() -> str:
    return _sha256(json.dumps(REGIME_SPEC, sort_keys=True))


def _tercile_bucket(value: float, low_cut: float, high_cut: float) -> str:
    if value < low_cut:
        return "low"
    if value < high_cut:
        return "mid"
    return "high"


def _regime_tag(
    symbol: str, daily: Mapping[date, Mapping[str, float]], as_of: date
) -> dict[str, Any]:
    """Market regime label from frozen inputs only (design §3-4).

    Three independent buckets (vol / trend / drawdown), none of which is the
    SMA200 gate the strategy trades on.
    """

    days = sorted(day for day in daily if day <= as_of)
    closes = [float(daily[d]["close"]) for d in days if math.isfinite(float(daily[d]["close"]))]
    if len(closes) < 63:
        return {
            "vol_bucket": None,
            "trend_bucket": None,
            "dd_bucket": None,
            "why": "insufficient_history",
        }
    window = closes[-(REGIME_VOL_WINDOW + 1) :] if len(closes) > REGIME_VOL_WINDOW else closes
    rets = [b / a - 1.0 for a, b in itertools.pairwise(window) if a > 0]
    if len(rets) >= 21:
        mean = sum(rets[-21:]) / 21.0
        var = sum((r - mean) ** 2 for r in rets[-21:]) / 20.0
        sigma21 = var**0.5
    else:
        sigma21 = None
    if sigma21 is None or len(window) < REGIME_VOL_WINDOW:
        vol_bucket = None
    else:
        historical: list[float] = []
        for i in range(20, len(window)):
            block = [
                b / a - 1.0 for a, b in itertools.pairwise(window[max(0, i - 20) : i + 1]) if a > 0
            ]
            if len(block) == 20:
                m = sum(block) / 20.0
                historical.append((sum((x - m) ** 2 for x in block) / 19.0) ** 0.5)
        historical.append(sigma21)
        historical_sorted = sorted(historical)
        third = max(1, len(historical_sorted) // 3)
        low_cut, high_cut = historical_sorted[third], historical_sorted[-third - 1]
        vol_bucket = _tercile_bucket(sigma21, low_cut, high_cut)
    r63 = closes[-1] / closes[-63] - 1.0 if len(closes) >= 63 else None
    if r63 is None:
        trend_bucket = None
    elif abs(r63) < REGIME_R63_FLAT:
        trend_bucket = "flat"
    else:
        trend_bucket = "up" if r63 > 0 else "down"
    peak = max(window)
    dd = closes[-1] / peak - 1.0 if peak > 0 else 0.0
    dd_bucket = "deep" if dd <= REGIME_DD_DEEP else "shallow"
    return {
        "vol_bucket": vol_bucket,
        "trend_bucket": trend_bucket,
        "dd_bucket": dd_bucket,
        "sigma21": sigma21,
        "ret63": r63,
        "drawdown_365d": dd,
    }


# --- journal-derived state ---------------------------------------------------


def _decision_rows(root: Path) -> dict[str, dict[str, Any]]:
    """decision_id -> the decision row (decision events only; D4 primary key)."""

    out: dict[str, dict[str, Any]] = {}
    for row in _load_journal(root):
        if row.get("event") == "decision":
            out[str(row["decision_id"])] = row
    return out


def _decision_seq(root: Path) -> dict[str, int]:
    """decision_id -> the seq of its LATEST decision row (dedupe marker)."""

    seqs: dict[str, int] = {}
    for row in _load_journal(root):
        if row.get("event") == "decision":
            seqs[str(row["decision_id"])] = int(row["seq"])
    return seqs


def rebuild_state(root: Path, book: str) -> dict[str, Any]:
    """Rebuild a book's units/cash/equity/peak/halt state from journal rows."""

    units: dict[str, float] = {}
    cash = 1.0
    equity = 1.0
    peak = 1.0
    halted = False
    stopped = False
    for row in _load_journal(root):
        if row.get("event") == "fill" and row.get("book") == book:
            units[str(row["asset"])] = float(row["units_after"])
            cash = float(row["cash_after"])
        elif row.get("event") == "mtm" and row.get("book") == book:
            equity = float(row["equity"])
            peak = max(peak, float(row.get("peak_equity", peak)))
            halted = bool(row.get("halted", halted))
            stopped = stopped or bool(row.get("stopped", False))
    return {
        "units": units,
        "cash": cash,
        "equity": equity,
        "peak": peak,
        "halted": halted,
        "stopped": stopped,
    }


def rebuild_guard_input(
    root: Path, book: str, asset: str, now: datetime, bar_seconds: int = 86400
) -> GuardInput:
    """Guard inputs from the journal: last fill ts/side, open time, count."""

    last_ts: datetime | None = None
    last_side: str | None = None
    fills_today = 0
    opened_ts: datetime | None = None
    for row in _load_journal(root):
        if row.get("event") != "fill" or row.get("book") != book or row.get("asset") != asset:
            continue
        fill_ts = _parse_ts(row.get("recorded_at"))
        if fill_ts.date() == now.date():
            fills_today += 1
        if last_ts is None or fill_ts > last_ts:
            last_ts, last_side = fill_ts, str(row.get("side"))
        if float(row.get("units_before", 0.0)) == 0.0:
            opened_ts = fill_ts
    asset_class = "crypto" if asset in BINANCE_TAPE else "equity"
    return GuardInput(
        asset_class=asset_class,  # type: ignore[arg-type]
        side="buy",  # replaced by resolve_fills with the actual side
        now=now,
        last_fill_ts=last_ts,
        last_fill_side=last_side,
        position_opened_ts=opened_ts,
        fills_today=fills_today,
        decision_bar_seconds=bar_seconds,
    )


# --- fill math (D2: delta notional) -------------------------------------------


def apply_fill(
    units: dict[str, float],
    cash: float,
    asset: str,
    side: str,
    fill_price: float,
    delta_notional: float,
) -> tuple[float, float, float]:
    """Apply one paper fill: DELTA notional trade. Returns
    (units_after, cash_after, traded_notional)."""

    if side == "buy":
        cost = delta_notional * COST_RATE
        quantity = (delta_notional - cost) / fill_price
        units[asset] = units.get(asset, 0.0) + quantity
        cash -= delta_notional
        return units[asset], cash, delta_notional
    if side == "sell":
        held_value = units.get(asset, 0.0) * fill_price
        traded = min(abs(delta_notional), held_value)
        proceeds = traded * (1 - COST_RATE)
        units[asset] = units.get(asset, 0.0) - traded / fill_price
        cash += proceeds
        return units[asset], cash, traded
    return units.get(asset, 0.0), cash, 0.0


# --- HALT evaluation ---------------------------------------------------------


def _halt_triggers_asset(
    rows: Mapping[date, Mapping[str, float]],
    closes: list[float],
    utc_now: datetime,
    annualization: int,
    is_equity: bool,
) -> list[dict[str, Any]]:
    """Per-asset trigger dicts: {name, value, threshold, margin, tripped}.

    ``margin`` is the distance to the threshold, positive = slack (design
    §3-5). A trigger that cannot be evaluated (missing input) is written as
    ``value: None, why: "unobserved"`` and never silently passes (P5).
    """

    out: list[dict[str, Any]] = []

    def add(name: str, value: float | None, threshold: float, tripped: bool, why: str = "") -> None:
        margin = None if value is None else value - threshold
        entry: dict[str, Any] = {
            "name": name,
            "value": value,
            "threshold": threshold,
            "margin": margin,
            "tripped": tripped,
        }
        if why:
            entry["why"] = why
        out.append(entry)

    latest = max(rows, default=None)
    if is_equity:
        lag_days = None if latest is None else float((utc_now.date() - latest).days)
        add(
            "stale_hours",
            None if lag_days is None else lag_days * 24.0,
            float(HALT_FRESHNESS_EQUITY_DAYS + 1) * 24.0,
            latest is None
            or (utc_now.date() - latest) > timedelta(days=HALT_FRESHNESS_EQUITY_DAYS + 1),
            "" if latest is not None else "unobserved",
        )
    else:
        # crypto freshness: now - (last bar date + 1 day 00:00 UTC) > 26h (H-a)
        if latest is None:
            add(
                "stale_hours",
                None,
                HALT_FRESHNESS_CRYPTO.total_seconds() / 3600.0,
                True,
                "unobserved",
            )
        else:
            last_close_utc = datetime.combine(
                latest + timedelta(days=1), datetime.min.time(), tzinfo=UTC
            )
            lag_h = (utc_now - last_close_utc).total_seconds() / 3600.0
            add(
                "stale_hours",
                lag_h,
                HALT_FRESHNESS_CRYPTO.total_seconds() / 3600.0,
                lag_h > HALT_FRESHNESS_CRYPTO.total_seconds() / 3600.0,
            )
    sigma_prior = _sigma21_prior(closes, annualization)
    if sigma_prior is None:
        add("big_move_z", None, HALT_SIGMA_MOVE, False, "unobserved")
    else:
        daily_sigma = max(sigma_prior / annualization**0.5, FLOOR_SIGMA_DAILY)
        last_return = abs(closes[-1] / closes[-2] - 1.0) if len(closes) >= 2 else None
        z = None if last_return is None else last_return / daily_sigma
        add(
            "big_move_z",
            z,
            HALT_SIGMA_MOVE,
            z is not None and z > HALT_SIGMA_MOVE,
            "" if last_return is not None else "unobserved",
        )
    add(
        "nonfinite",
        0.0 if (closes and math.isfinite(closes[-1])) else 1.0,
        0.5,
        bool(closes) and not math.isfinite(closes[-1]),
    )
    add("integrity", 0.0, 0.5, False)
    return out


def _halt_reasons_asset(
    rows: Mapping[date, Mapping[str, float]],
    closes: list[float],
    utc_now: datetime,
    annualization: int,
    is_equity: bool,
) -> list[str]:
    """Trigger names that fired for one asset (kept for back-compat callers)."""

    return [
        str(entry["name"])
        for entry in _halt_triggers_asset(rows, closes, utc_now, annualization, is_equity)
        if entry["tripped"]
    ]


def evaluate_halt(
    book: str,
    rows_by_symbol: Mapping[str, Mapping[date, Mapping[str, float]]],
    closes_by_symbol: Mapping[str, list[float]],
    utc_now: datetime,
    annualization: int,
    state: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Per-book HALT evaluation: per-asset triggers merged; DD is book scope."""

    results: dict[str, list[str]] = {}
    is_equity = book in (BOOK_C_BOOK, BENCH_EQUITY_BOOK)
    for symbol in rows_by_symbol:
        results[symbol] = _halt_reasons_asset(
            rows_by_symbol[symbol], closes_by_symbol[symbol], utc_now, annualization, is_equity
        )
    dd = state["equity"] / state["peak"] - 1.0 if state["peak"] > 0 else 0.0
    # v1.3 (t_2202c100): the -20% DD line is a WARNING — the trigger name is
    # recorded for callers but nothing downstream halts or stops on it.
    if dd <= HALT_DRAWDOWN and not state.get("stopped"):
        for symbol in results:
            results[symbol].append("drawdown_warning")
    if state.get("halted"):
        for symbol in results:
            results[symbol].append("halt_carryover")
    return results


# --- prereg (book registry, F6) ------------------------------------------------


def ensure_prereg(
    root: Path,
    *,
    run_id: str | None = None,
    trigger: str = "cron",
    phase: str | None = None,
    utc_now: datetime | None = None,
) -> None:
    """Register enabled books + the regime_spec (hash-chained prereg ledger).

    v1.2 (B4): ``registered_at`` is tz-aware UTC, so L9 can compare it against
    the first decision's ``recorded_at`` mechanically. The regime_spec row is
    appended before any v1.2 decision row may be written. ``utc_now`` is the
    job's clock (injected in tests) so a registered_at can never land after
    the decisions of the same run (L9).
    """

    path = prereg_path(root)
    existing: dict[str, str] = {}
    if path.exists():
        for entry in _rows_from_jsonl(path, strict=True):
            existing[str(entry.get("book_id"))] = str(entry.get("spec_hash"))
    for book in BOOKS_ENABLED:
        spec_hash = _sha256(BOOK_SPEC_HASHES[book])
        if existing.get(book) == spec_hash:
            continue
        if existing.get(book) is not None:
            raise ChainError(f"book {book} is preregistered with a different spec")
        prev = ""
        if path.exists():
            prior = _rows_from_jsonl(path, strict=True)
            prev = str(prior[-1]["row_hash"]) if prior else ""
        row = {
            "book_id": book,
            "spec_hash": spec_hash,
            "hypothesis": (
                "S-01 (frozen crypto SMA200 daily gate) reduces drawdown vs 1/N"
                if book == S01_BOOK
                else (
                    "S-02 (frozen US equity vol target 15% weekly) controls drawdown vs EW"
                    if book == BOOK_C_BOOK
                    else (
                        "Benchmark: US Equity 4-asset equal weight buy-and-hold"
                        if book == BENCH_EQUITY_BOOK
                        else "Benchmark: BTC/ETH 1/N buy-and-hold"
                    )
                )
            ),
            "judgment_rule": (
                "design §4 milestones at 4/12/26 weeks"
                if book in (BOOK_C_BOOK, BENCH_EQUITY_BOOK)
                else "Q1 signal efficacy on the SMA200 state (design §4)"
            ),
            "registered_at": _utc_now_text(utc_now),
            # v1.4 (t_dddae39e): EQUITY_EW belongs to the Book C deployment
            # (t_feb3aa55), not to the v1.1 crypto books — register it under
            # the task that actually enabled the equity side.
            "registered_by": (
                "worker-agy:t_feb3aa55"
                if book == BOOK_C_BOOK
                else (
                    "worker-go:t_dddae39e"
                    if book == BENCH_EQUITY_BOOK
                    else "worker-flash:t_d9a25dd2"
                )
            ),
        }
        row["prev_hash"] = prev
        row["row_hash"] = _sha256(
            json.dumps(
                {k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        existing[book] = spec_hash
    _ensure_regime_prereg(root, existing, utc_now)
    if phase == "start" and run_id:
        _open_run(root, run_id, trigger=trigger)


def _ensure_regime_prereg(
    root: Path, existing: Mapping[str, str], utc_now: datetime | None = None
) -> None:
    """Append the regime_spec registration once (design §3-4 / B4)."""

    if existing.get("__regime_spec__") == regime_spec_hash():
        return
    path = prereg_path(root)
    prior = _rows_from_jsonl(path, strict=True) if path.exists() else []
    prev = str(prior[-1]["row_hash"]) if prior else ""
    row = {
        "book_id": "__regime_spec__",
        "spec_hash": regime_spec_hash(),
        "hypothesis": (
            "Regime label is independent of the traded signal (vol/trend/dd "
            "buckets, none of which is SMA200)"
        ),
        "judgment_rule": "Q4 regime concentration flag only; not a pass/fail gate",
        "registered_at": _utc_now_text(utc_now),
        "registered_by": "free-worker3:t_08eb947a",
        "regime_spec": REGIME_SPEC,
    }
    row["prev_hash"] = prev
    row["row_hash"] = _sha256(
        json.dumps({k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def check_prereg(root: Path, book: str) -> None:
    """decide refuses unregistered/altered books (§6-2)."""

    path = prereg_path(root)
    spec_hash = _sha256(BOOK_SPEC_HASHES[book])
    for entry in _rows_from_jsonl(path, strict=True):
        if str(entry.get("book_id")) == book:
            if str(entry.get("spec_hash")) != spec_hash:
                raise ChainError(
                    f"book {book}: prereg spec_hash mismatch — parameters changed post-registration"
                )
            return
    raise ChainError(f"book {book} is not registered in prereg.jsonl")


# --- mocked-network-free core: decision / fill / mtm / mark -------------------


def _freeze_payload(
    book: str,
    decision_date: date,
    closes_by_symbol: Mapping[str, list[float]],
    last_bar_ts_by_symbol: Mapping[str, date],
) -> dict[str, Any]:
    """The frozen-inputs payload (identical bytes for write and recompute)."""

    return {
        "book": book,
        "decision_date": decision_date.isoformat(),
        "inputs": {
            symbol: {
                "closes": closes[-(SMA_LOOKBACK + 1) :],
                "last_bar_as_of": last_bar_ts_by_symbol[symbol].isoformat(),
            }
            for symbol, closes in closes_by_symbol.items()
        },
    }


def _freeze_payload_hash(payload: Mapping[str, Any]) -> str:
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _freeze_inputs(
    root: Path,
    book: str,
    decision_date: date,
    closes_by_symbol: Mapping[str, list[float]],
    last_bar_ts_by_symbol: Mapping[str, date],
) -> str:
    """Write frozen decision inputs (per book/date) and return input_sha256."""

    payload = _freeze_payload(book, decision_date, closes_by_symbol, last_bar_ts_by_symbol)
    path = inputs_dir(root) / decision_date.isoformat() / f"{book}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return _sha256(path.read_text(encoding="utf-8"))


def store_freeze_inputs(root: Path, book: str, decision_date: date) -> str:
    """Rebuild the freeze payload from the CURRENT store and hash it (R3).

    Both the closes and ``last_bar_as_of`` are restricted to days strictly
    before ``decision_date`` — the window a same-day decide could see. (The
    unfiltered store max would false-positive every decision once later days
    land; validated against the live journal before freezing this rule,
    card t_2202c100.) A hash that differs from the decision's frozen
    ``input_sha256`` means the store was revised afterwards (data_revised).
    """

    inputs: dict[str, Any] = {}
    for symbol in CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE:
        rows = _daily_map(root, symbol)
        prior = [day for day in sorted(rows) if day < decision_date]
        inputs[symbol] = {
            "closes": [rows[day]["close"] for day in prior][-(SMA_LOOKBACK + 1) :],
            "last_bar_as_of": prior[-1].isoformat() if prior else None,
        }
    payload = {"book": book, "decision_date": decision_date.isoformat(), "inputs": inputs}
    return _freeze_payload_hash(payload)


def run_daily_mtm(root: Path, utc_now: datetime | None = None) -> int:
    """Write one mtm row per enabled book (D3): equity chain + DD warning.

    v1.2 (B2): (book, mtm_date) is the primary key — a second run on the same
    date writes nothing. ``mtm_date`` is the date of the close prices actually
    used (``price_bar_date``), not the wall-clock date, and the cumulative
    cost/turnover columns are recorded so the panel can separate cost drag.
    v1.3 (t_2202c100): a drawdown at or below HALT_DRAWDOWN sets the row's
    ``drawdown_warning`` and nothing else — it never stops the book.
    """

    now = utc_now if utc_now is not None else CLOCK(UTC)
    mtm_day = now.date()
    written = 0
    for book in BOOKS_ENABLED:
        if any(
            row.get("event") == "mtm"
            and row.get("book") == book
            and str(row.get("mtm_date")) == mtm_day.isoformat()
            for row in _load_journal(root)
        ):
            continue  # B2: one mtm per (book, date)
        state = rebuild_state(root, book)
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        prices: dict[str, float | None] = {}
        price_bar_date: str | None = None
        for symbol in universe:
            daily = _daily_map(root, symbol)
            # L6: the mtms price_bar_date must be strictly BEFORE the mtms
            # recorded day, so only FINALIZED bars (as_of < today) price it.
            finalized_days = [day for day in daily if day < mtm_day]
            latest = max(finalized_days, default=None)
            if latest is None:
                prices[symbol] = None
                continue  # no price yet for this asset: state is carried as-is
            prices[symbol] = daily[latest]["close"]
            if price_bar_date is None or latest.isoformat() > str(price_bar_date):
                price_bar_date = latest.isoformat()
        units_value = sum(units * prices.get(asset, 0.0) for asset, units in state["units"].items())
        equity = state["cash"] + units_value
        peak = max(state["peak"], equity)
        drawdown = equity / peak - 1.0 if peak > 0 else 0.0
        # v1.3 (t_2202c100): the -20% line is a WARNING — the stopped flag is
        # only carried forward from frozen pre-v1.3 rows, never newly set.
        stopped = bool(state.get("stopped"))
        cum_cost, cum_turnover, equity_gross = _cost_accumulators(root, book, equity)
        _append_row(
            root,
            {
                "event": "mtm",
                "book": book,
                "mtm_date": mtm_day.isoformat(),
                "recorded_at": _utc_now_text(now),
                "price_bar_date": price_bar_date,
                "prices": {asset: prices.get(asset) for asset in universe},
                "units": dict(state["units"]),
                "cash": state["cash"],
                "equity": equity,
                "peak_equity": peak,
                "drawdown": drawdown,
                "drawdown_warning": drawdown <= HALT_DRAWDOWN,
                "halted": bool(state.get("halted")),
                "stopped": stopped,
                "cost_assumed_bps": COST_RATE_BPS,
                "cum_cost_assumed": cum_cost,
                "cum_turnover": cum_turnover,
                "equity_gross": equity_gross,
            },
        )
        written += 1
    return written


def _cost_accumulators(root: Path, book: str, equity: float) -> tuple[float, float, float]:
    """(cum cost in equity units, cum turnover, equity_gross) for one book.

    Deterministic from the journal (panel rule P2). ``equity_gross`` is the
    equity the book would show at this moment had the assumed cost never been
    charged (Q3's cost isolation): the daily chain replays the SAME
    day-over-day equity changes observed on the net ``equity`` series, and on
    each step adds back the costs charged between the two mtm rows (journal
    order), so market returns compound identically on both sides and only the
    cost leg differs. The chain starts at the inception capital of 1.0; costs
    charged before the first mtm row are added back at face value on the first
    step (daily resolution cannot reconstruct their intra-day growth).
    Algebraically ``gross/equity == product(1 + cost_t / equity_t)`` over the
    steps, so ``cost_drag_pp`` reads the actually incurred cost share. The
    cost share is stated against the CURRENT equity.
    """

    cum_cost = 0.0
    cum_turnover = 0.0
    equity_gross = 1.0
    prev_equity = 1.0  # inception capital anchor
    cost_since_prev_mtm = 0.0
    for row in _load_journal(root):
        if row.get("book") != book:
            continue
        if row.get("event") == "fill":
            cost = float(row.get("cost_assumed") or 0.0)
            cum_cost += cost
            cum_turnover += abs(float(row.get("traded_notional") or 0.0))
            cost_since_prev_mtm += cost
        elif row.get("event") == "mtm":
            mtm_equity = float(row.get("equity") or 0.0)
            if prev_equity > 0:
                equity_gross *= (mtm_equity + cost_since_prev_mtm) / prev_equity
            prev_equity = mtm_equity
            cost_since_prev_mtm = 0.0
    # The step for the mtm row now being written (its equity is the argument):
    # fills recorded after the last existing mtm row (the daily fill pass runs
    # after the daily mtm) are added back here, so tomorrow's row chains from
    # a gross that already carries today's costs.
    if prev_equity > 0:
        equity_gross *= (float(equity) + cost_since_prev_mtm) / prev_equity
    return cum_cost, cum_turnover, equity_gross


# --- run events (§3-6: loop_run replaced by start/end pairs) ----------------


def _new_run_id() -> str:
    return f"{CLOCK(UTC).strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"


def _open_run(root: Path, run_id: str, *, job: str = "daily", trigger: str = "cron") -> None:
    """Write the run start row (idempotent per run_id)."""

    if any(
        row.get("event") == "run" and row.get("run_id") == run_id and row.get("phase") == "start"
        for row in _load_journal(root)
    ):
        return
    _append_row(
        root,
        {
            "event": "run",
            "phase": "start",
            "job": job,
            "trigger": trigger,
            "run_id": run_id,
            "recorded_at": _utc_now_text(),
            "host": host_name(),
        },
    )


def _close_run(
    root: Path,
    run_id: str | None,
    now: datetime,
    rows_written: int,
    trigger: str = "cron",
    *,
    job: str = "daily",
    ok: bool = True,
    error_class: str | None = None,
) -> None:
    """Write the run end row. A start without an end = crashed run (L4)."""

    if not run_id:
        return
    if any(
        row.get("event") == "run" and row.get("run_id") == run_id and row.get("phase") == "end"
        for row in _load_journal(root)
    ):
        return
    _append_row(
        root,
        {
            "event": "run",
            "phase": "end",
            "job": job,
            "trigger": trigger,
            "run_id": run_id,
            "recorded_at": _utc_now_text(now),
            "host": host_name(),
            "rows_written": rows_written,
            "ok": ok,
            "error_class": error_class,
        },
    )


def _freshness_status(
    rows: Mapping[date, Mapping[str, float]], utc_now: date, is_equity: bool = False
) -> dict[str, Any]:
    """t_022e607d-style judgment on the daily store, frozen per decision.

    ``status`` is fresh / stale / unknown; ``lag_days`` is the observed gap
    between UTC today and the newest bar actually READ (P5: null with why).
    """

    latest = max(rows, default=None)
    if latest is None:
        return {"status": "unknown", "lag_days": None, "why": "unobserved"}
    lag = (utc_now - latest).days
    max_fresh = 3 if (is_equity and utc_now.weekday() == 0) else (1 if not is_equity else 2)
    return {"status": "fresh" if lag <= max_fresh else "stale", "lag_days": lag}


def _regime_state_from(
    *,
    book: str,
    symbol: str,
    closes: list[float],
    sma: float | None,
    gate_price: float,
    prev_regime: str | None,
) -> tuple[str, bool]:
    """RISK_ON / RISK_OFF / HALT for one decision (bench keeps a state, P6: one
    place per fact — the label lives only in the decision row)."""

    if sma is None:
        return "HALT", False  # fail closed on SMA
    regime = "RISK_ON" if gate_price > sma else "RISK_OFF"
    switch = prev_regime is not None and prev_regime != regime and regime != "HALT"
    return regime, switch


def _bars_since_switch(root: Path, book: str, symbol: str, decision_date: date) -> int | None:
    """Bars since the regime last flipped (Q1's false-breakout rate input)."""

    rows = [
        row
        for row in _load_journal(root)
        if row.get("event") == "decision" and row.get("book") == book and row.get("asset") == symbol
    ]
    count = 0
    for row in rows:
        count += 1
        if row.get("switch_flag"):
            return count - 1
    return None


def run_daily_decide(
    root: Path,
    utc_now: datetime | None = None,
    *,
    run_id: str | None = None,
    trigger: str = "cron",
) -> int:
    """Daily decide phase (D4 idempotent, D5 internal clock, D6 guard at fill,
    D7 benchmark buys 1/N once, D9 frozen inputs, first-hit HALT) + the D3
    daily mtm row per book.

    v1.2: every decision row also records the counterfactual inputs the eval
    needs (§3-1): raw_target_weight (no HALT/guard), signal_value (distance
    from the gate), bars_since_switch, input_bar_dates, freshness_status,
    regime_tag (preregistered boundaries) and spec_hash. A daily ``halt_check``
    row per book records every trigger's margin; a ``run`` row pair brackets
    the job so a crashed run is visible (L4).
    """

    now = utc_now if utc_now is not None else CLOCK(UTC)
    ensure_prereg(root, run_id=run_id, trigger=trigger, phase="start", utc_now=now)
    decision_date = now.date()
    written = 0
    regime_hash = regime_spec_hash()
    for book in BOOKS_ENABLED:
        # Weekly cadence gate for Book C (S-02 frozen backtest rebalance=weekly;
        # Friday close judged, Monday open filled per design §2-2). On non-rebalance
        # days (Tue-Sun), Book C does not generate trade decisions and only MTM runs.
        # v1.4 (t_dddae39e): the equity EW benchmark book rides the SAME weekly
        # cadence, otherwise its comparison line would be a different strategy
        # (daily decisions) than the book it is measuring.
        if book in (BOOK_C_BOOK, BENCH_EQUITY_BOOK) and decision_date.weekday() != 0:
            continue
        check_prereg(root, book)
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        rows_by_symbol = {symbol: _daily_map(root, symbol) for symbol in universe}
        closes_by_symbol: dict[str, list[float]] = {
            symbol: [rows[day]["close"] for day in sorted(rows) if day < decision_date]
            for symbol, rows in rows_by_symbol.items()
        }
        is_equity = book in (BOOK_C_BOOK, BENCH_EQUITY_BOOK)
        annualization = (
            CRYPTO_ANNUALIZATION if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_ANNUALIZATION
        )
        state = rebuild_state(root, book)
        dd = state["equity"] / state["peak"] - 1.0 if state["peak"] > 0 else 0.0
        trigger_details: list[dict[str, Any]] = []
        for symbol in universe:
            trigger_details.extend(
                _halt_triggers_asset(
                    rows_by_symbol[symbol],
                    closes_by_symbol[symbol],
                    now,
                    annualization,
                    is_equity,
                )
            )
        # v1.5 (t_e2c98d2b): the recorded edge-lifetime verdict gates the next
        # decision for the monitored book. A PAUSE (or an untrusted verdict,
        # fail-closed) joins the trigger list, so the existing machinery writes
        # a halted halt_check action and skipped HALT-regime decision rows with
        # the reason in halt_reason. Nothing else changes for other books.
        cusum_verdict: dict[str, Any] | None = (
            cusum_edge_verdict(root) if book == CUSUM_GATED_BOOK else None
        )
        if cusum_verdict is not None and cusum_verdict["status"] in ("pause", "unreadable"):
            trigger_details.append(_cusum_gate_trigger(cusum_verdict))
        halted_by_trigger = bool(state.get("halted")) or bool(
            [e for e in trigger_details if e["tripped"]]
        )
        # v1.3 (t_2202c100): the -20% DD line no longer halts decisions — it
        # is recorded as drawdown_warning on the halt_check row (t_554c40f6:
        # 55.9% of 26w windows fired it, far too blunt for a stop).
        drawdown_warning = dd <= HALT_DRAWDOWN
        halted_before = bool(state.get("halted"))
        halted_after = halted_by_trigger
        # §3-5: one halt_check row per book per day (L11)
        if not any(
            row.get("event") == "halt_check"
            and row.get("book") == book
            and str(row.get("check_date")) == decision_date.isoformat()
            for row in _load_journal(root)
        ):
            margins = [e["margin"] for e in trigger_details if e.get("margin") is not None]
            _append_row(
                root,
                {
                    "event": "halt_check",
                    "book": book,
                    "check_date": decision_date.isoformat(),
                    "recorded_at": _utc_now_text(now),
                    "triggers": trigger_details,
                    "drawdown": dd,
                    "drawdown_threshold": HALT_DRAWDOWN,
                    "drawdown_margin": dd - HALT_DRAWDOWN,
                    "drawdown_warning": drawdown_warning,
                    "margin_min": min(margins) if margins else None,
                    "halted_before": halted_before,
                    "halted_after": halted_after,
                    "action": _halt_action(halted_before, halted_after, dd, state, trigger_details),
                    # v1.5 (t_e2c98d2b): which edge-lifetime verdict was read
                    # for this decision (None = the book has no monitor gate).
                    "edge_lifetime": cusum_verdict,
                    "regime_spec_hash": regime_hash,
                },
            )
        equity = float(state["equity"])
        for symbol in universe:
            decision_id = f"{book}:{symbol}:{decision_date.isoformat()}"
            if decision_id in _decision_seq(root):
                continue  # D4: idempotent — a decision is written at most once
            closes = closes_by_symbol[symbol]
            halted = halted_by_trigger
            closes_ok = len(closes) >= SMA_LOOKBACK + 1
            sma = _sma200(closes)
            sigma = _sigma21(closes, annualization)
            backfill = decision_date < now.date()  # C4: auto (never an argument)
            last_bar = max(rows_by_symbol[symbol], default=None)
            first_bar = min(rows_by_symbol[symbol], default=None)
            input_bar_dates = (
                [first_bar.isoformat(), last_bar.isoformat()] if last_bar is not None else None
            )
            last_bars_by_symbol = {
                other: max(rows_by_symbol[other]) for other in universe if rows_by_symbol[other]
            }
            freshness = _freshness_status(rows_by_symbol[symbol], decision_date, is_equity)
            prev_regime: str | None = None
            prior_rows = [
                row
                for row in _load_journal(root)
                if row.get("event") == "decision"
                and row.get("asset") == symbol
                and row.get("book") == book
            ]
            if prior_rows:
                prev_regime = prior_rows[-1].get("regime_state")
            if not closes_ok or halted:
                # decision row + regime + skip (no pending fill, D10 chain kept)
                _append_row(
                    root,
                    {
                        "event": "decision",
                        "book": book,
                        "recorded_at": _utc_now_text(now),
                        "decision_id": decision_id,
                        "decision_date": decision_date.isoformat(),
                        "asset": symbol,
                        "backfill": backfill or decision_date < now.date(),
                        "spec_hash": _sha256(BOOK_SPEC_HASHES[book]),
                        "inputs_last_bar_ts": last_bar.isoformat() if last_bar else None,
                        "input_bar_dates": input_bar_dates,  # D9 range form
                        "input_sha256": _freeze_inputs(
                            root, book, decision_date, closes_by_symbol, last_bars_by_symbol
                        )
                        if last_bars_by_symbol
                        else None,
                        "close": closes[-1] if closes else None,
                        "sma200": sma,
                        "sigma21": sigma,
                        "sigma21_prior": _sigma21_prior(closes, annualization),
                        "last_return": closes[-1] / closes[-2] - 1.0 if len(closes) >= 2 else None,
                        "regime_state": "HALT",
                        "prev_regime_state": prev_regime,
                        "switch_flag": False,
                        "switch_reason": "",
                        "signal_value": None,
                        "signal_value_why": "unobserved",
                        "bars_since_switch": None,
                        "raw_target_weight": None,
                        "raw_target_weight_why": "halted",
                        "target_weight": 0.0,
                        "held_weight_at_decision": 0.0,
                        "side": "hold",
                        "delta_weight": 0.0,
                        "halt_flag": True,
                        "halt_reason": _halt_reason_text(trigger_details, closes_ok),
                        "fill_status": "skipped",
                        "freshness_status": freshness,
                        "regime_tag": _regime_tag(
                            symbol, rows_by_symbol[symbol], last_bar or decision_date
                        ),
                        "regime_spec_hash": regime_hash,
                        "guard_applied": False,
                        "trigger": trigger,
                        "run_id": run_id,
                    },
                )
                written += 1
                continue
            benchmark_bought = False
            if book in (S01_BOOK, BOOK_C_BOOK):
                target_map = _engine_target(
                    book, {other: rows_by_symbol[other] for other in universe}, decision_date
                )
            elif book in (BENCH_CRYPTO_BOOK, BENCH_EQUITY_BOOK):
                # D7: buy 1/N on the first decision; afterwards hold only.
                benchmark_bought = any(
                    row.get("event") == "fill" and row.get("book") == book
                    for row in _load_journal(root)
                )
                target_map = _benchmark_weights(book) if not benchmark_bought else {}
            else:
                target_map = _benchmark_weights(book)  # pragma: no cover
            # raw target: the strategy with NO halt and NO guard applied
            # (counterfactual column; target_weight below stays as traded)
            if book in (S01_BOOK, BOOK_C_BOOK):
                raw_map = _engine_target(
                    book, {other: rows_by_symbol[other] for other in universe}, decision_date
                )
            elif book in (BENCH_CRYPTO_BOOK, BENCH_EQUITY_BOOK):
                raw_map = _benchmark_weights(book) if not benchmark_bought else {}
            else:
                raw_map = _benchmark_weights(book)  # pragma: no cover
            target = float(target_map.get(symbol, 0.0))
            raw_target = float(raw_map.get(symbol, 0.0))
            held_units = float(state["units"].get(symbol, 0.0))
            gate_price = closes[-1]
            held_weight = (held_units * gate_price / equity) if equity > 0 else 0.0
            delta = target - held_weight
            if benchmark_bought:
                # D7 (C-01, t_cad2ff49): the benchmark never trades again after
                # buy-in. The exemption named CRYPTO_1N only, so EQUITY_EW read
                # its post-buy-in empty target_map as "target 0%" and liquidated
                # the whole 1/N position on the 2nd Monday.
                delta = 0.0
            side = "hold" if abs(delta) < 1e-6 else ("buy" if delta > 0 else "sell")
            regime, switch = _regime_state_from(
                book=book,
                symbol=symbol,
                closes=closes,
                sma=sma,
                gate_price=gate_price,
                prev_regime=prev_regime,
            )
            if book in (BENCH_CRYPTO_BOOK, BENCH_EQUITY_BOOK):
                # benchmark books carry no strategy state (L14: not_applicable)
                regime = "not_applicable"
                switch = False
            signal_value = (gate_price / sma - 1.0) if sma is not None else None
            freeze_sha = _freeze_inputs(
                root,
                book,
                decision_date,
                closes_by_symbol,
                {other: max(rows_by_symbol[other]) for other in universe if rows_by_symbol[other]},
            )
            _append_row(
                root,
                {
                    "event": "decision",
                    "book": book,
                    "recorded_at": _utc_now_text(now),
                    "decision_id": decision_id,
                    "decision_date": decision_date.isoformat(),
                    "asset": symbol,
                    "backfill": backfill,
                    "spec_hash": _sha256(BOOK_SPEC_HASHES[book]),
                    "inputs_last_bar_ts": last_bar.isoformat() if last_bar else None,
                    "input_bar_dates": input_bar_dates,  # D9 range form
                    "input_sha256": freeze_sha,
                    "close": gate_price,
                    "sma200": sma,
                    "sigma21": sigma,
                    "sigma21_prior": _sigma21_prior(closes, annualization),
                    "last_return": closes[-1] / closes[-2] - 1.0 if len(closes) >= 2 else None,
                    "regime_state": regime,
                    "prev_regime_state": prev_regime,
                    "switch_flag": switch,
                    "switch_reason": (
                        f"close={gate_price:.2f};sma200={sma:.2f};cross="
                        + ("up" if regime == "RISK_ON" else "down")
                        if switch
                        else ""
                    ),
                    "signal_value": signal_value,
                    "signal_value_why": "unobserved" if signal_value is None else "",
                    "bars_since_switch": _bars_since_switch(root, book, symbol, decision_date),
                    "raw_target_weight": raw_target,
                    "target_weight": target,
                    "held_weight_at_decision": held_weight,
                    "side": side,
                    "delta_weight": delta,
                    "halt_flag": False,
                    "halt_reason": "",
                    "fill_status": "pending" if side != "hold" else "skipped",
                    "freshness_status": freshness,
                    "regime_tag": _regime_tag(
                        symbol, rows_by_symbol[symbol], last_bar or decision_date
                    ),
                    "regime_spec_hash": regime_hash,
                    "guard_applied": book in (S01_BOOK, BOOK_C_BOOK),  # benchmark gets no guard
                    "trigger": trigger,
                    "run_id": run_id,
                },
            )
            written += 1
    run_daily_mtm(root, now)  # D3: every daily run marks the books to market
    _write_anchor(root, decision_date)
    _close_run(root, run_id, now, written, trigger)
    return written


def _halt_action(
    halted_before: bool,
    halted_after: bool,
    dd: float,
    state: Mapping[str, Any],
    trigger_details: list[dict[str, Any]],
) -> str:
    if state.get("stopped"):
        return "stop"  # frozen pre-v1.3 history: the old stop stays on record
    if not halted_before and halted_after:
        return "halt"
    if halted_before and not halted_after:
        return "auto_clear"
    if dd <= HALT_DRAWDOWN:
        # v1.3 (t_2202c100): the -20% line warns in the journal, it does not
        # stop the book (t_554c40f6).
        return "warn"
    if any(e["tripped"] for e in trigger_details) and halted_before:
        return "none"
    return "none"


def _make_closing_run(run_id: str, now: datetime, written: int, trigger: str) -> dict[str, Any]:
    """The run-end row payload."""

    return {
        "event": "run",
        "phase": "end",
        "job": "daily",
        "trigger": trigger,
        "run_id": run_id,
        "recorded_at": _utc_now_text(now),
        "host": host_name(),
        "rows_written": written,
        "ok": True,
        "error_class": None,
    }


def _halt_reason_text(trigger_details: list[dict[str, Any]], closes_ok: bool) -> str:
    if not closes_ok:
        return "insufficient_history"
    return ",".join(str(e["name"]) for e in trigger_details if e["tripped"]) or "halted"


def _finished_decisions(root: Path) -> set[str]:
    """decision_ids that already have a fill OR fill_rejected row (B1 fix).

    A decision's completion is derived from the accumulated events, never
    from the frozen ``fill_status`` column (which v1.1 left at "pending" for
    already-filled rows, producing 4 spurious guard rejections).
    """

    return {
        str(row["decision_id"])
        for row in _load_journal(root)
        if row.get("event") in ("fill", "fill_rejected") and row.get("decision_id")
    }


def _supersede_stale_pendings(
    root: Path,
    now: datetime,
    *,
    trigger: str,
    run_id: str | None,
) -> int:
    """C6 (t_2202c100): close stale pendings when a newer decision exists.

    For each (book, asset): every still-pending decision OLDER than the
    newest decision written for that key is closed with
    ``fill_rejected(reject_reason="superseded")``. Decision rows themselves
    are never rewritten. Without this, a late daily leaves yesterday's
    pending racing today's decision for the same fill bar and the guard
    rejects whichever row happens to sort first — the spec (consultant
    t_69d006ad, reconciliation 残り2) fixes the outcome: the OLD one loses.
    """

    decisions = _decision_rows(root)
    finished = _finished_decisions(root)
    newest: dict[tuple[str, str], tuple[datetime, str]] = {}
    for decision_id, row in decisions.items():
        key = (str(row["book"]), str(row["asset"]))
        stamp = _parse_ts(row["recorded_at"])
        if key not in newest or stamp > newest[key][0]:
            newest[key] = (stamp, decision_id)
    closed = 0
    for decision_id, row in sorted(decisions.items()):
        if decision_id in finished or str(row.get("fill_status")) != "pending":
            continue
        key = (str(row["book"]), str(row["asset"]))
        if newest[key][1] == decision_id:
            continue  # the newest pending per (book, asset) is the one kept
        _append_row(
            root,
            {
                "event": "fill_rejected",
                "book": str(row["book"]),
                "asset": str(row["asset"]),
                "recorded_at": _utc_now_text(now),
                "decision_id": decision_id,
                "reject_reason": "superseded",
                "reject_class": "superseded",
                "rejected_delta_weight": float(row.get("delta_weight", 0.0)),
                "ref_bar_open_ts": None,
                "ref_bar_open": None,
                "decision_recorded_at": row.get("recorded_at"),
                "superseded_by": newest[key][1],
                "backfill": bool(row.get("backfill")),
                "trigger": trigger,
                "run_id": run_id,
            },
        )
        closed += 1
    return closed


def resolve_fills(
    root: Path,
    utc_now: datetime | None = None,
    *,
    run_id: str | None = None,
    trigger: str = "cron",
) -> int:
    """Fill pending decisions from the 1h tape/forming candle (C2/C3/D6).

    v1.2 (B1): a decision is finished when ANY fill/fill_rejected row exists
    for it, so an already-filled decision can never be re-processed into a
    spurious guard rejection. Fill rows carry the §3-2/§3-3 columns
    (decision_recorded_at, fill_bar_close, weight_before/after, turnover,
    guard_applied, equity_at_fill) and rejections carry reject_class,
    rejected_delta_weight and ref_bar_open_ts for the counterfactual P&L.

    v1.3 (C6, t_2202c100): before any fill is attempted, older pendings that
    a newer decision has superseded are closed (see
    ``_supersede_stale_pendings``) so only the newest decision per
    (book, asset) can trade.
    """

    now = utc_now if utc_now is not None else CLOCK(UTC)
    ensure_prereg(root)
    if run_id:
        _open_run(root, run_id, job="fill", trigger=trigger)
    _supersede_stale_pendings(root, now, trigger=trigger, run_id=run_id)
    completed = 0
    decisions = _decision_rows(root)
    finished = _finished_decisions(root)
    for decision_id, row in sorted(decisions.items()):
        if decision_id in finished:
            continue  # B1: already filled or already rejected — never re-process
        if row.get("fill_status") not in ("pending", "skipped"):
            continue
        book = str(row["book"])
        asset = str(row["asset"])
        recorded_at = _parse_ts(row["recorded_at"])
        if asset in BINANCE_TAPE:
            try:
                _append_intraday(root, asset, _fetch_crypto_1h(asset))
            except (OSError, ValueError, KeyError) as exc:
                print(f"1h fetch failed for {asset}: {exc}", file=sys.stderr)
        fill = _first_fill_candidate(root, asset, after=recorded_at)
        if fill is None:
            continue  # stays pending for the next fill run
        fill_open_ts = _parse_ts(fill["open_time"])
        assert fill_open_ts > recorded_at, (
            f"C2 violation: fill bar open {fill['open_time']} predates recorded_at"
        )
        assert now >= fill_open_ts, (
            f"C3 violation: fill written before fill bar opened ({fill['open_time']})"
        )
        side = str(row.get("side"))
        delta_weight = float(row.get("delta_weight", 0.0))
        state = rebuild_state(root, book)
        equity = float(state["equity"])
        held_units = float(state["units"].get(asset, 0.0))
        held_value = held_units * fill["open"]
        held_weight = held_value / equity if equity > 0 else 0.0
        target_weight = held_weight + delta_weight
        delta_notional = abs(delta_weight) * equity
        guard_input = rebuild_guard_input(root, book, asset, fill_open_ts)
        guard_input = GuardInput(
            asset_class=guard_input.asset_class,
            side=side,  # type: ignore[arg-type]
            now=fill_open_ts,
            last_fill_ts=guard_input.last_fill_ts,
            last_fill_side=guard_input.last_fill_side,
            position_opened_ts=guard_input.position_opened_ts,
            fills_today=guard_input.fills_today,
            decision_bar_seconds=86400,
        )
        allowed, reject = check_trade(guard_input)
        halted_after = bool(_halt_active(root, book))
        if halted_after and not allowed:
            reject_class = "halt"
        elif not allowed:
            reject_class = "guard"
        else:
            reject_class = ""
        if not allowed:
            _append_row(
                root,
                {
                    "event": "fill_rejected",
                    "book": book,
                    "asset": asset,
                    "recorded_at": _utc_now_text(now),
                    "decision_id": decision_id,
                    "reject_reason": reject,
                    "reject_class": reject_class,
                    "rejected_delta_weight": delta_weight,
                    "ref_bar_open_ts": fill["open_time"],
                    "ref_bar_open": fill["open"],
                    "decision_recorded_at": row.get("recorded_at"),
                    "guard_inputs": {
                        "last_fill_ts": guard_input.last_fill_ts.isoformat()
                        if guard_input.last_fill_ts
                        else None,
                        "last_fill_side": guard_input.last_fill_side,
                        "position_opened_ts": guard_input.position_opened_ts.isoformat()
                        if guard_input.position_opened_ts
                        else None,
                        "fills_today": guard_input.fills_today,
                    },
                    "backfill": bool(row.get("backfill")),
                    "trigger": trigger,
                    "run_id": run_id,
                },
            )
            continue
        if side == "hold" or delta_notional <= 1e-9:
            _append_row(
                root,
                {
                    "event": "fill",
                    "book": book,
                    "recorded_at": _utc_now_text(now),
                    "decision_id": decision_id,
                    "asset": asset,
                    "side": "hold",
                    "paper_fill_price": fill["open"],
                    "paper_fill_bar_open_ts": fill["open_time"],
                    "fill_bar_close": fill.get("close"),
                    "decision_recorded_at": row.get("recorded_at"),
                    "fill_lag_sec": int((fill_open_ts - recorded_at).total_seconds()),
                    "traded_notional": 0.0,
                    "cost_assumed": 0.0,
                    "cost_assumed_bps": COST_RATE_BPS,
                    "turnover": 0.0,
                    "weight_before": held_weight,
                    "weight_after": held_weight,
                    "equity_at_fill": equity,
                    "units_before": held_units,
                    "units_after": held_units,
                    "cash_after": state["cash"],
                    "guard_applied": book in (S01_BOOK, BOOK_C_BOOK),
                    "backfill": bool(row.get("backfill")),
                    "trigger": trigger,
                    "run_id": run_id,
                },
            )
            completed += 1
            continue
        units_after, cash_after, traded = apply_fill(
            dict(state["units"]),
            float(state["cash"]),
            asset,
            side,
            fill["open"],
            delta_notional,
        )
        # L12 invariant: weight_after - weight_before == the decision's
        # delta_weight. The ACHIEVED weight drifts with the fill price vs the
        # gate price — that drift is the shortfall column's job, not L12's —
        # so the fill row records weight_after as the weight the decision
        # intended, and the market drift stays visible via weight_before.
        weight_after = held_weight + delta_weight
        _append_row(
            root,
            {
                "event": "fill",
                "book": book,
                "recorded_at": _utc_now_text(now),
                "decision_id": decision_id,
                "asset": asset,
                "side": side,
                "paper_fill_price": fill["open"],
                "paper_fill_bar_open_ts": fill["open_time"],
                "fill_bar_close": fill.get("close"),
                "decision_recorded_at": row.get("recorded_at"),
                "fill_lag_sec": int((fill_open_ts - recorded_at).total_seconds()),
                "traded_notional": traded,
                "cost_assumed": traded * COST_RATE,
                "cost_assumed_bps": COST_RATE_BPS,
                "turnover": abs(traded),
                "weight_before": held_weight,
                "weight_after": weight_after,
                "equity_at_fill": equity,
                "target_weight": target_weight,
                "units_before": held_units,
                "units_after": units_after,
                "cash_after": cash_after,
                "guard_applied": book in (S01_BOOK, BOOK_C_BOOK),
                "backfill": bool(row.get("backfill")),
                "trigger": trigger,
                "run_id": run_id,
            },
        )
        completed += 1
    run_daily_mtm(root, now)
    _close_run(root, run_id, now, completed, trigger, job="fill")
    return completed


def _halt_active(root: Path, book: str) -> bool:
    """Whether the book's latest halt_check says it was halted (Q5/Q6)."""

    latest: dict[str, Any] | None = None
    for row in _load_journal(root):
        if row.get("event") == "halt_check" and row.get("book") == book:
            latest = row
    return bool(latest and latest.get("halted_after"))


def mark_up(root: Path, utc_now: datetime | None = None) -> int:
    """After the execution day's daily bar lands: bt fill price + signed
    shortfall + R1 recompute match (strategy books only — D7/D8)."""

    now = utc_now if utc_now is not None else CLOCK(UTC)
    decisions = _decision_rows(root)
    already_marked = {
        str(row.get("decision_id")) for row in _load_journal(root) if row.get("event") == "mark"
    }
    marked = 0
    for decision_id, row in sorted(decisions.items()):
        if decision_id in already_marked:
            continue
        # strategy books only (D7); filled rows are eligible once their
        # execution-day daily bar has landed (mark from the corresponding fill)
        book = str(row["book"])
        if book not in STRATEGY_BOOKS:
            continue
        filled = any(
            entry.get("event") == "fill" and entry.get("decision_id") == decision_id
            for entry in _load_journal(root)
        )
        if not filled:
            continue
        asset = str(row["asset"])
        execution_date = date.fromisoformat(str(row["decision_date"]))
        daily = _daily_map(root, asset)
        bar = daily.get(execution_date)
        if bar is None:
            continue  # the execution day's bar has not landed yet
        frozen_path = inputs_dir(root) / execution_date.isoformat() / f"{book}.json"
        universe = (
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        target_now = _engine_target(
            book, {other: _daily_map(root, other) for other in universe}, execution_date
        )
        match = abs(target_now.get(asset, 0.0) - float(row["target_weight"])) <= 1e-12
        del frozen_path  # kept readable; R1 full sha check runs in paperloop_verify
        # signed shortfall from the corresponding fill row (D8)
        fill_row = next(
            (
                entry
                for entry in _load_journal(root)
                if entry.get("event") == "fill" and entry.get("decision_id") == decision_id
            ),
            None,
        )
        if fill_row is None:
            continue  # nothing was filled: no shortfall to compute
        paper_fill = float(fill_row["paper_fill_price"])
        side = str(fill_row.get("side"))
        sgn = 1.0 if side == "buy" else -1.0
        shortfall_bps = sgn * (paper_fill / bar["open"] - 1.0) * 10_000
        turnover = abs(float(fill_row.get("turnover") or 0.0))
        _append_row(
            root,
            {
                "event": "mark",
                "book": book,
                "recorded_at": _utc_now_text(now),
                "decision_id": decision_id,
                "asset": asset,
                "bt_fill_price": bar["open"],
                "shortfall_bps": shortfall_bps,
                "recompute_target": target_now.get(asset, 0.0),
                "recompute_match": match,
                "cost_assumed_bps": COST_RATE_BPS,
                "turnover": turnover,
                "effective_cost_bps": COST_RATE_BPS + shortfall_bps,
            },
        )
        marked += 1
    return marked


# --- weekly summary ------------------------------------------------------------


def shortfall_bps_mean_upper95(shortfalls: list[float]) -> float | None:
    """One-sided 95% upper bound of the SIGNED shortfall mean (bps).

    The frozen 26w milestone cost gate (t_2202c100): mean + z * sd / sqrt(n)
    with the one-sided normal quantile z = SHORTFALL_UPPER95_Z (1.6449).
    Needs n >= 2; below that the bound is undefined and the answer is None
    ("insufficient"), which the milestone judgment must treat as not-passed.
    """

    n = len(shortfalls)
    if n < 2:
        return None
    mean = sum(shortfalls) / n
    variance = sum((value - mean) ** 2 for value in shortfalls) / (n - 1)
    return mean + SHORTFALL_UPPER95_Z * math.sqrt(variance / n)


def bench_dd_stop_fired(
    book_dd: float,
    bench_dd: float,
    *,
    k: float | None = None,
    bench_dd_min: float = 0.10,
) -> bool:
    """Weekly benchmark-excess-DD stop criterion — HOOK ONLY (t_2202c100).

    Fires when the benchmark is at least ``bench_dd_min`` under water AND the
    book's drawdown is at least ``k`` times deeper. The loop does NOT call
    this while ``BENCH_DD_STOP["k"]`` is undecided (None): the config gate
    stays disabled until the master freezes k. Pure predicate — no clock, no
    files, no journal writes.
    """

    if k is None:
        return False
    return bench_dd <= -bench_dd_min and book_dd <= -k * abs(bench_dd)


# --- edge-lifetime verdict gate (t_e2c98d2b) ----------------------------------
# The monitor's verdict is a recorded FACT (cusum_A.jsonl, hash-chained,
# written weekly). This reader only interprets the latest row; it never
# recomputes the statistic and never appends to the monitor's log.


def cusum_edge_verdict(root: Path) -> dict[str, Any]:
    """Latest registered edge-lifetime verdict for book A, as a gate input.

    ``status`` is exactly one of:

    - ``continue``   every recorded verdict is trustworthy and none alarmed;
    - ``pause``      a recorded alarm is in force (S_w > h*). The alarm is
      LATCHED: the prereg says S is never reset after an alarm, so a later
      ``continue`` row does not reopen the gate — it reports the latest state
      of a monitor whose alarm history still stands. Reopening is a registered
      change (the log is append-only), never an implicit side effect;
    - ``unreadable`` the monitor has run but its log cannot be trusted
      (unparseable/partial row, unknown action, another book, missing h*).
      Fail-closed: an unknown verdict is never read as alive;
    - ``absent``     the monitor has never written a verdict, so there is no
      gate. This is the documented pre-monitor state (the journal predates
      cusum_A.jsonl); the monitor runs inside weekly.sh with ``set -e``, so a
      missing log after its first run is a loud failure, not a silent pass.

    The returned provenance fields (``week``/``S``/``h_star``/``row_hash`` plus
    ``alarm_week``/``alarm_S`` when latched) are carried into the halt_check row
    so a reader can tell *which* verdict held the gate. Missing numbers stay
    ``None``; nothing is filled with 0.
    """

    verdict: dict[str, Any] = {
        "status": "absent",
        "source": CUSUM_LOG_NAME,
        "week": None,
        "weeks_observed": None,
        "S": None,
        "h_star": None,
        "alarm": None,
        "action": None,
        "row_hash": None,
        "alarm_week": None,
        "alarm_S": None,
        "alarm_row_hash": None,
        "why": "monitor_has_not_run",
    }
    path = root / CUSUM_LOG_NAME
    if not path.exists():
        return verdict
    try:
        rows = _rows_from_jsonl(path, strict=True)
    except ChainError:
        verdict.update(status="unreadable", why="log_unreadable")
        return verdict
    if not rows:
        verdict.update(status="unreadable", why="log_empty")
        return verdict

    def trusted(row: Mapping[str, Any]) -> bool:
        constants = row.get("constants")
        h_star = constants.get("h_star") if isinstance(constants, Mapping) else None
        return (
            str(row.get("book_id")) == CUSUM_GATED_BOOK
            and row.get("action") in CUSUM_ACTIONS
            and isinstance(h_star, (int, float))
        )

    latest = rows[-1]
    constants = latest.get("constants")
    verdict.update(
        week=latest.get("week"),
        weeks_observed=latest.get("weeks_observed"),
        S=latest.get("S"),
        h_star=constants.get("h_star") if isinstance(constants, Mapping) else None,
        alarm=latest.get("alarm"),
        action=latest.get("action"),
        row_hash=latest.get("row_hash"),
        why=None,
    )
    if not all(trusted(row) for row in rows):
        verdict.update(status="unreadable", why="malformed_row_in_log")
        return verdict
    alarms = [row for row in rows if row.get("action") == "PAUSE" or row.get("alarm") is True]
    if alarms:
        first = alarms[0]
        verdict.update(
            status="pause",
            alarm_week=first.get("week"),
            alarm_S=first.get("S"),
            alarm_row_hash=first.get("row_hash"),
        )
        return verdict
    verdict["status"] = "continue"
    return verdict


def _cusum_gate_trigger(verdict: Mapping[str, Any]) -> dict[str, Any]:
    """Book-scope HALT trigger dict for a pause / untrusted verdict.

    Shaped like the per-asset triggers from ``_halt_triggers_asset`` so the
    existing halt machinery (halt_check action, ``halt_flag`` + skipped
    HALT-regime decision rows) carries it with no second control path.
    ``margin`` follows the design convention (value - threshold, positive =
    slack), so a latched PAUSE reports the ALARM's S_w - h* (the number that
    crossed the line), not a later, lower S_w.
    """

    if verdict["status"] == "pause":
        value = verdict.get("alarm_S")
        if value is None:
            value = verdict.get("S")
    else:
        value = verdict.get("S")
    threshold = verdict.get("h_star")
    margin: float | None = None
    if isinstance(value, (int, float)) and isinstance(threshold, (int, float)):
        margin = float(value) - float(threshold)
    return {
        "name": "cusum_pause" if verdict["status"] == "pause" else "cusum_verdict_unreadable",
        "value": value,
        "threshold": threshold,
        "margin": margin,
        "tripped": True,
        "why": str(verdict.get("why") or verdict["status"]),
        "source": f"{CUSUM_LOG_NAME}:{verdict.get('row_hash')}",
    }


# --- milestone judgment wiring (t_d33aef07) -----------------------------------
# MILESTONE_RULES was a passive constant: nothing evaluated it, so the design §4
# 4-week judgment lived only in humans reading weekly_summary.jsonl (t_44edd7a0
# gap analysis). The existing weekly path (ops/demo-loop/weekly.sh ->
# loop_cli report -> weekly_summary) now evaluates MILESTONE_RULES[4] once per
# strategy book once its 28-day window is complete. Record-only: the judgment
# adds one block to the summary row — no orders, no journal rows, no state
# transitions, no live promotion. Design §4 is the evaluation standard; it is
# not proof of benefit and not an adoption decision (the human gate stays).

MILESTONE_WEEKS_CONNECTED = (4,)  # 12/26 stay unwired (t_d33aef07 scope)
_KNOWN_JOURNAL_EVENTS = frozenset(
    {"decision", "fill", "fill_rejected", "mark", "mtm", "run", "halt_check"}
)


def _milestone_start_date(rows: list[dict[str, Any]], book: str) -> date | None:
    """Demo start for a book: first non-backfill decision date.

    The journal is the origin of record (S-01 2026-10-10, S-02 2026-10-19 —
    matches prereg and the t_44edd7a0 evidence). Backfill rows are excluded
    (design §4 keeps backfill out of judgment denominators).
    """

    starts = [
        date.fromisoformat(str(row["decision_date"]))
        for row in rows
        if row.get("event") == "decision" and row.get("book") == book and not row.get("backfill")
    ]
    return min(starts) if starts else None


def _milestone_already_judged(root: Path, book: str, weeks: int) -> bool:
    """True once a prior summary row carries the (book, weeks) judgment.

    The deadline can fall between weekly runs; whichever run fires first
    writes the record and every later run dedupes against it — exactly one
    judgment record per (book, weeks), ever.
    """

    for row in _rows_from_jsonl(summary_path(root), strict=True):
        block = row.get("milestone")
        entry = block.get(book) if isinstance(block, Mapping) else None
        if isinstance(entry, Mapping) and entry.get("weeks") == weeks:
            return True
    return False


def _milestone_guard_replay(
    rows: list[dict[str, Any]], book: str, window_end: date
) -> dict[str, Any]:
    """Replay check_trade over accepted fills with journal-only state (D6).

    §4 "guard違反 0": a violation is an ACCEPTED fill that check_trade would
    have rejected (a slipped-through rejection). fill_rejected rows are the
    guard working and are reported as facts, never counted as violations
    (F7: operational counts are no evidence for the guard itself — that proof
    stays with the synthetic-violation UT suite).

    The replay mirrors the operational fill path: now = the fill bar's open
    time, guard state = prior accepted fills of the same book+asset (last
    fill ts/side, position open ts, same-day fill count by recorded_at date
    — the rebuild_guard_input rules).
    """

    state: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}
    for row in rows:
        if (
            row.get("event") == "fill_rejected"
            and row.get("book") == book
            and date.fromisoformat(str(row["recorded_at"])[:10]) < window_end
        ):
            reason = str(row.get("reject_reason"))
            rejections[reason] = rejections.get(reason, 0) + 1
    fills = sorted(
        (row for row in rows if row.get("event") == "fill" and row.get("book") == book),
        key=lambda row: (str(row["recorded_at"]), int(row.get("seq", 0))),
    )
    for row in fills:
        asset = str(row["asset"])
        book_state = state.setdefault(
            asset, {"last_ts": None, "last_side": None, "opened_ts": None, "dates": []}
        )
        recorded = _parse_ts(row["recorded_at"])
        now = _parse_ts(row.get("paper_fill_bar_open_ts") or row["recorded_at"])
        inputs = GuardInput(
            asset_class="crypto" if asset in BINANCE_TAPE else "equity",
            side=str(row.get("side")),  # type: ignore[arg-type]
            now=now,
            last_fill_ts=book_state["last_ts"],
            last_fill_side=book_state["last_side"],
            position_opened_ts=book_state["opened_ts"],
            fills_today=sum(1 for day in book_state["dates"] if day == now.date()),
            decision_bar_seconds=86400,
        )
        allowed, reject = check_trade(inputs)
        if recorded.date() < window_end and not allowed:
            violations.append(
                {
                    "decision_id": row.get("decision_id"),
                    "asset": asset,
                    "recorded_at": row["recorded_at"],
                    "reject_reason": reject,
                }
            )
        book_state["last_ts"] = recorded
        book_state["last_side"] = str(row.get("side"))
        if float(row.get("units_before", 0.0)) == 0.0:
            book_state["opened_ts"] = recorded
        book_state["dates"].append(recorded.date())
    return {"violations": violations, "rejections_by_reason": rejections}


def _milestone_judgment(
    root: Path, rows: list[dict[str, Any]], book: str, weeks: int, week_end: date
) -> dict[str, Any] | None:
    """One design §4 milestone judgment for (book, weeks) — or None.

    Fires on the first weekly run whose week_end reached start + weeks*7
    days, and only if no prior summary row already carries the judgment:
    no early judgment, no duplicate records. Fail-closed on missing facts:
    a criterion without its data is None -> INSUFFICIENT, never back-filled.
    """

    if weeks not in MILESTONE_WEEKS_CONNECTED or _milestone_already_judged(root, book, weeks):
        return None
    start = _milestone_start_date(rows, book)
    if start is None:
        return None
    milestone_date = start + timedelta(days=weeks * 7)
    if week_end < milestone_date:
        return None  # deadline not reached: never judge early
    window_end = milestone_date  # [start, milestone_date): exactly weeks*7 days
    window_days = weeks * 7
    rules = MILESTONE_RULES[weeks]

    # distinct dates, not row counts: a same-day re-run is one ok day.
    days_with_runs: set[str] = set()
    run_ok_dates: set[str] = set()
    days_with_any_run: set[str] = set()
    for row in rows:
        if row.get("event") == "run":
            day = str(row["recorded_at"])[:10]
            if start <= date.fromisoformat(day) < window_end:
                # ANY run row that day (any phase/job — a daily start whose
                # end never arrived is a crashed run, L4) blocks the
                # decision fallback: only days whose loop evidence is a
                # bare non-backfill decision may use it.
                days_with_any_run.add(day)
                if row.get("phase") == "end" and row.get("job") == "daily":
                    days_with_runs.add(day)
                    if row.get("ok"):
                        run_ok_dates.add(day)
    # A day with NO run rows at all: a non-backfill decision row for this
    # book that day is evidence the loop itself ran. The real journal has
    # such days — the run-row writer only came online 2026-10-12 while the
    # loop has been running since day one — so counting them as missing
    # would make the §4 verdict a recording artifact, not an operational
    # fact. A day that HAS any run row is judged on its daily-end ok alone
    # and is never overridden by a decision row: a failed run (ok=false)
    # and a crashed run (start row, no end row) both stay missing. A day
    # with neither record counts as missing too (fail-closed: never
    # back-filled with success).
    decision_days = {
        str(row["recorded_at"])[:10]
        for row in rows
        if row.get("event") == "decision"
        and row.get("book") == book
        and not row.get("backfill")
        and start <= date.fromisoformat(str(row["recorded_at"])[:10]) < window_end
    }
    fallback_dates = sorted(decision_days - days_with_any_run)
    run_ok_dates.update(fallback_dates)
    loop_success_rate = len(run_ok_dates) / window_days

    marks = [
        row
        for row in rows
        if row.get("event") == "mark"
        and row.get("book") == book
        and start <= date.fromisoformat(str(row["recorded_at"])[:10]) < window_end
    ]
    shortfalls = [
        abs(float(row["shortfall_bps"])) for row in marks if row.get("shortfall_bps") is not None
    ]
    observed_cost_bps_mean = sum(shortfalls) / len(shortfalls) if shortfalls else None
    recompute_rate = (
        sum(1 for row in marks if row.get("recompute_match")) / len(marks) if marks else None
    )
    # Zero-order paper (M6): the demo-loop schema has no order-bearing
    # events; any event type outside the frozen set counts here (fail-closed).
    # Scoped to this book and the window so a future schema addition in
    # another book or period cannot flip this judgment; rows without a
    # parseable recorded_at still count (fail-closed).
    unknown_events = 0
    for row in rows:
        if str(row.get("event")) in _KNOWN_JOURNAL_EVENTS:
            continue
        if row.get("book") is not None and row.get("book") != book:
            continue
        try:
            in_window = start <= date.fromisoformat(str(row.get("recorded_at"))[:10]) < window_end
        except (TypeError, ValueError):
            in_window = True
        if in_window:
            unknown_events += 1
    guard = _milestone_guard_replay(rows, book, window_end)

    cost_max = float(rules["observed_cost_bps_max"])
    criteria: dict[str, dict[str, Any]] = {
        "loop_success_rate": {
            "value": loop_success_rate,
            "comparison": ">=",
            "threshold": float(rules["loop_success_rate"]),
            "pass": loop_success_rate >= float(rules["loop_success_rate"]),
        },
        "observed_cost_bps_mean": {
            "value": observed_cost_bps_mean,
            "comparison": "<=",
            "threshold": cost_max,
            "pass": None if observed_cost_bps_mean is None else observed_cost_bps_mean <= cost_max,
        },
        "recompute_match_rate": {
            "value": recompute_rate,
            "comparison": "==",
            "threshold": 1.0,
            "pass": None if recompute_rate is None else recompute_rate == 1.0,
        },
        "orders": {
            "value": unknown_events,
            "comparison": "==",
            "threshold": 0,
            "pass": unknown_events == 0,
        },
        "guard_violations": {
            "value": len(guard["violations"]),
            "comparison": "==",
            "threshold": 0,
            "pass": len(guard["violations"]) == 0,
        },
    }
    failed = sorted(name for name, check in criteria.items() if check["pass"] is False)
    insufficient = sorted(name for name, check in criteria.items() if check["pass"] is None)
    verdict = "FAIL" if failed else ("INSUFFICIENT" if insufficient else "PASS")
    return {
        "weeks": weeks,
        "book": book,
        "start_date": start.isoformat(),
        "window_start": start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_days": window_days,
        "milestone_date": milestone_date.isoformat(),
        "judged_at_week_end": week_end.isoformat(),
        "run_ok_dates": len(run_ok_dates),
        "run_ok_dates_by_decision_fallback": fallback_dates,
        "rules_source": "MILESTONE_RULES[4] + DEMO_LOOP_V1_OPS.md §4 (frozen)",
        "criteria": criteria,
        "verdict": verdict,
        "failed_criteria": failed,
        "insufficient_criteria": insufficient,
        "guard_rejections_by_reason": guard["rejections_by_reason"],
        "promotion": "none",
        "notes": (
            "record-only: no orders, no journal rows, no live promotion. "
            "Design §4 is the evaluation standard, not proof of benefit; "
            "adoption stays a separate human decision. Guard mechanism "
            "evidence remains the synthetic-violation UT suite (F7); the "
            "replay above only checks accepted fills for slipped-through "
            "violations. Loop-run days whose only record is a non-backfill "
            "decision row count via the decision fallback and are listed in "
            "run_ok_dates_by_decision_fallback; days with a failed run, a "
            "crashed run (start row without an end row), or neither record "
            "are never back-filled. orders counts unknown "
            "event types only within this book and the window (fail-closed)."
        ),
    }


def _collect_milestone_judgments(root: Path, week_end: date) -> dict[str, Any]:
    """Milestone blocks for the weekly summary row (empty = judge nothing)."""

    rows = _load_journal(root)
    out: dict[str, Any] = {}
    for book in STRATEGY_BOOKS:
        for weeks in MILESTONE_WEEKS_CONNECTED:
            judgment = _milestone_judgment(root, rows, book, weeks, week_end)
            if judgment is not None:
                out[book] = judgment
    return out


def _max_drawdown_since(marks: Mapping[date, float], base_day: date) -> float | None:
    """Deepest drawdown measured INSIDE the comparison window (None if no mark).

    The running peak starts at the baseline day's equity, not at the book's
    high-water mark since inception. A book that topped out BEFORE the shared
    baseline therefore does not carry that earlier peak's drawdown into the
    window — the benchmark starts at its own baseline value, so both sides must
    be measured from the same footing (review round 3, t_dddae39e).

    The journal's own ``drawdown`` column is untouched: it is still
    ``equity/peak-1`` from inception and still drives the -20% warning.
    """

    window = sorted((day, marks[day]) for day in marks if day >= base_day)
    if not window:
        return None
    peak = window[0][1]
    worst = 0.0
    for _day, equity in window:
        peak = max(peak, equity)
        if peak > 0:
            worst = min(worst, equity / peak - 1.0)
    return worst


def _first_fill_date(root: Path, book: str) -> date | None:
    """Date of a book's first fill (the bar that filled it), None if never.

    The fill row itself carries no date; the bar open timestamp does, and for
    both crypto (18:00Z) and US equity (13:30Z) bars it falls on the session
    date the mtm rows use, so it is directly comparable to ``mtm_date``.
    """

    days: list[date] = []
    for row in _load_journal(root):
        if row.get("event") != "fill" or row.get("book") != book:
            continue
        stamp = row.get("paper_fill_bar_open_ts") or row.get("recorded_at")
        if not stamp:
            continue
        days.append(date.fromisoformat(str(stamp)[:10]))
    return min(days) if days else None


def _mtm_marks(root: Path, book: str) -> dict[date, float]:
    """Last mtm equity per mtm_date for a book (journal facts only)."""

    marks: dict[date, float] = {}
    for row in _load_journal(root):
        if row.get("event") != "mtm" or row.get("book") != book:
            continue
        if row.get("equity") is None or row.get("mtm_date") is None:
            continue
        marks[date.fromisoformat(str(row["mtm_date"]))] = float(row["equity"])
    return marks


def _week_return(
    marks: Mapping[date, float], window_start: date, week_end: date
) -> tuple[float | None, str | None, str | None, str | None]:
    """Return of the 7-day week (``window_start..week_end``), not since inception.

    v1.6 (C-02, docs/PAPERLOOP_MONEY_REVIEW_20261023.md): the weekly row's
    ``week_return`` used to be ``state["equity"] - 1.0`` — the book's CUMULATIVE
    return from the 1.0 inception, while every neighbouring window column
    (decisions/fills-in-window/mtm_days/…) was scoped to the week. The same key
    therefore meant two different things in one row, and the error grew with the
    book's age. A weekly return is chained from the last mark BEFORE the window
    to the last mark inside it, so the move between the previous report's close
    and this one is what gets reported.

    Returns ``(week_return, base_label, as_of, reason)``:
    - ``week_return``: ``end/base - 1``, or None when the week cannot be priced.
    - ``base_label``: ISO date of the base mark, or ``"inception"`` when the book
      has no mark before the window (its equity chain starts at 1.0, so the
      whole history is the week — the only defensible base in that case).
    - ``as_of``: ISO date of the mark the return is measured to.
    - ``reason``: why ``week_return`` is None (missing data is not zero).

    Marks dated after ``week_end`` are ignored: a row generated for week_end must
    never carry a later mark's move (the append-only weekly file cannot take a
    correction back).
    """

    dated = sorted((day, equity) for day, equity in marks.items() if day <= week_end)
    in_window = [(day, equity) for day, equity in dated if day >= window_start]
    if not dated:
        return None, None, None, "no_mark_at_or_before_week_end"
    if not in_window:
        return None, None, None, "no_mark_in_window"
    end_day, end = in_window[-1]
    prior = [(day, equity) for day, equity in dated if day < window_start]
    if prior:
        base_day, base = prior[-1]
        base_label: str | None = base_day.isoformat()
    else:
        base = 1.0
        base_label = "inception"
    if base <= 0:
        return None, base_label, end_day.isoformat(), "base_unusable"
    return end / base - 1.0, base_label, end_day.isoformat(), None


def _comparison_line(
    root: Path, book: str, bench: str, state_by_book: Mapping[str, dict[str, Any]]
) -> dict[str, Any]:
    """One strategy book's comparison line against its own benchmark (§4 gates).

    Both sides are measured from ONE shared baseline date: the earliest mtm
    date that the book and its benchmark both carry on/after the benchmark's
    first fill day. Before that, the two books were in different regimes (one
    still in cash / not started), so subtracting their inception equity would
    credit one side with P&L the other never had the chance to earn.

    Every numeric field is None while the comparison cannot be made, with
    ``comparison_reason`` saying why. Nothing is zero-filled and no gate is
    auto-passed: a benchmark in cash reports None, never "0%".

    Drawdowns are recomputed from the mtm equity marks on/after ``base_day``
    with the running peak started at the baseline value, so a pre-baseline
    high-water mark does not leak a one-sided drawdown into the window.
    """

    book_state = state_by_book.get(book)
    bench_state = state_by_book.get(bench)
    out: dict[str, Any] = {
        "benchmark_book": bench,
        "cum_pnl": None,
        "max_drawdown": None,
        "benchmark_cum_pnl": None,
        "benchmark_max_drawdown": None,
        "vs_benchmark_pp_cum": None,
        "dd_vs_benchmark_ratio": None,
        "dd_ratio_reason": None,
        "comparison_start": None,
        "comparison_reason": None,
    }
    start = _first_fill_date(root, bench)
    if start is None:
        out["comparison_reason"] = "benchmark_has_no_fill"
        return out
    book_marks = _mtm_marks(root, book)
    bench_marks = _mtm_marks(root, bench)
    shared = sorted(day for day in book_marks.keys() & bench_marks.keys() if day >= start)
    if not shared:
        out["comparison_reason"] = "no_shared_mark_since_benchmark_first_fill"
        return out
    base_day = shared[0]
    base_book, base_bench = book_marks[base_day], bench_marks[base_day]
    if base_book <= 0 or base_bench <= 0 or book_state is None or bench_state is None:
        out["comparison_reason"] = "baseline_unusable"
        return out
    book_cum = float(book_state["equity"]) / base_book - 1.0
    bench_cum = float(bench_state["equity"]) / base_bench - 1.0
    book_dd = _max_drawdown_since(book_marks, base_day)
    bench_dd = _max_drawdown_since(bench_marks, base_day)
    out.update(
        {
            "cum_pnl": book_cum,
            "max_drawdown": book_dd,
            "benchmark_cum_pnl": bench_cum,
            "benchmark_max_drawdown": bench_dd,
            "vs_benchmark_pp_cum": (book_cum - bench_cum) * 100.0,
            "comparison_start": base_day.isoformat(),
        }
    )
    out["dd_vs_benchmark_ratio"] = _dd_vs_benchmark_ratio(book_dd, bench_dd)
    if out["dd_vs_benchmark_ratio"] is None:
        if book_dd is None:
            out["dd_ratio_reason"] = "book_drawdown_missing"
        elif bench_dd is None:
            out["dd_ratio_reason"] = "benchmark_drawdown_missing"
        else:
            out["dd_ratio_reason"] = "benchmark_drawdown_not_negative"
    return out


def _dd_vs_benchmark_ratio(book_dd: float | None, bench_dd: float | None) -> float | None:
    """§4 gate ratio: book max DD / benchmark max DD (both negative → >0).

    None when either side has no drawdown yet, or when the benchmark has no
    drawdown to divide by (a ratio against 0 would be a fabricated number).
    """

    if book_dd is None or bench_dd is None or bench_dd >= 0:
        return None
    return book_dd / bench_dd


def weekly_summary(data_dir: Path, week_end: date) -> dict[str, Any]:
    """Weekly summary row (M8/spec §7): per book + milestone judgment.

    v1.2: every book also gets an ``eval`` block (Q1-Q7, design §4) built from
    the derived panel. The journal keeps facts only; the eval numbers live
    here (design §7: never in the journal).
    """

    root = loop_root(data_dir)
    window_start = week_end - timedelta(days=6)
    out: dict[str, Any] = {
        "schema": "demo-loop-summary-v1.6",
        "week_end": week_end.isoformat(),
        "generated_at": _utc_now_text(),
        "books": {},
        "prereg_count": sum(1 for _ in _rows_from_jsonl(prereg_path(root), strict=True)),
        "last_anchor_seq": None,
        "last_anchor_hash": None,
        "anchor_ok": _anchors_ok(root),
        "code_rev": code_rev(),
        "host": host_name(),
    }
    anchors = _rows_from_jsonl(anchors_path(root))
    if anchors:
        out["last_anchor_seq"] = anchors[-1].get("seq")
        out["last_anchor_hash"] = anchors[-1].get("row_hash")
    state_by_book: dict[str, dict[str, Any]] = {}
    for book in BOOKS_ENABLED:
        state = rebuild_state(root, book)
        state_by_book[book] = state
        decisions = [
            row
            for row in _load_journal(root)
            if row.get("event") == "decision"
            and row.get("book") == book
            and window_start <= date.fromisoformat(str(row["decision_date"])) <= week_end
        ]
        judged = [row for row in decisions if not row.get("backfill")]
        fills = [
            row
            for row in _load_journal(root)
            if row.get("event") == "fill" and row.get("book") == book
        ]
        rejects = [
            row
            for row in _load_journal(root)
            if row.get("event") == "fill_rejected" and row.get("book") == book
        ]
        marks = [
            row
            for row in _load_journal(root)
            if row.get("event") == "mark" and row.get("book") == book
        ]
        shortfalls = [float(row["shortfall_bps"]) for row in marks]
        switches = 0  # D11: regime flips RISK_ON <-> RISK_OFF, HALT excluded
        prev_regime: str | None = None
        for row in judged:
            regime = str(row.get("regime_state"))
            if (
                regime in ("RISK_ON", "RISK_OFF")
                and prev_regime is not None
                and prev_regime != regime
            ):
                switches += 1
            if regime in ("RISK_ON", "RISK_OFF"):
                prev_regime = regime
        mtms = [
            row
            for row in _load_journal(root)
            if row.get("event") == "mtm"
            and row.get("book") == book
            and window_start <= date.fromisoformat(str(row["mtm_date"])) <= week_end
        ]
        week_return, week_base, week_as_of, week_reason = _week_return(
            _mtm_marks(root, book), window_start, week_end
        )
        drawdown = state["equity"] / state["peak"] - 1.0 if state["peak"] > 0 else None
        book_out = {
            "decisions": len(judged),
            "switches": switches,
            "fills": len(fills),
            "fills_rejected": len(rejects),
            "backfill_days": len(decisions) - len(judged),
            # v1.6 (C-02): the week's change rate, chained from the last mark
            # before the window. The cumulative figure that this key used to
            # carry (and that older append-only rows still hold) is now
            # ``cum_return`` so neither meaning is lost or renamed silently.
            "week_return": week_return,
            "week_return_base": week_base,
            "week_return_as_of": week_as_of,
            "week_return_reason": week_reason,
            "cum_return": state["equity"] - 1.0,
            "drawdown_now": drawdown,
            "shortfall_bps_mean_signed": (
                sum(shortfalls) / len(shortfalls) if shortfalls else None
            ),
            "shortfall_bps_mean_abs": (
                sum(abs(value) for value in shortfalls) / len(shortfalls) if shortfalls else None
            ),
            "shortfall_bps_mean_upper95": shortfall_bps_mean_upper95(shortfalls),
            "shortfall_n": len(shortfalls),
            "recompute_R1_rate": (
                sum(1 for row in marks if row.get("recompute_match")) / len(marks)
                if marks
                else None
            ),
            "halt_days": sum(1 for row in decisions if row.get("halt_flag")),
            "halted": state.get("halted"),
            "stopped": state.get("stopped"),
            "mtm_days": len(mtms),
            "halt_check_days": sum(
                1
                for row in _load_journal(root)
                if row.get("event") == "halt_check"
                and row.get("book") == book
                and window_start <= date.fromisoformat(str(row.get("check_date"))) <= week_end
            ),
            "runs_completed": sum(
                1
                for row in _load_journal(root)
                if row.get("event") == "run"
                and row.get("phase") == "end"
                and row.get("job") == "daily"
                and row.get("ok")
                and window_start <= date.fromisoformat(str(row.get("recorded_at"))[:10]) <= week_end
            ),
        }
        out["books"][book] = book_out
    # vs benchmark (pp, week + cumulative)
    book_a, book_bench = state_by_book.get(S01_BOOK), state_by_book.get(BENCH_CRYPTO_BOOK)
    if book_a is not None and book_bench is not None:
        out["vs_benchmark_pp_cum"] = (book_a["equity"] - book_bench["equity"]) * 100.0
    # v1.4 (t_dddae39e): every strategy book carries its OWN comparison line so
    # the frozen §4 gates (cum pnl vs benchmark, DD <= benchmark DD x 0.6) are
    # readable from the weekly row instead of being recomputed by hand.
    # Review round 2: the line is measured from a shared baseline date and is
    # None (with a reason) until the benchmark has actually filled — a
    # benchmark still in cash is never reported as a 0% comparison.
    for book, bench in BOOK_BENCHMARK.items():
        book_out = out["books"].get(book)
        if not isinstance(book_out, dict):
            continue
        book_out.update(_comparison_line(root, book, bench, state_by_book))
    # Q1-Q7 eval per book (design §4/§5: from the derived panel, insufficient
    # is a first-class answer with a numeric reason)
    out["eval"] = _eval_blocks(data_dir, book=S01_BOOK, week_end=week_end)
    out["eval_by_book"] = {
        book: _eval_blocks(data_dir, book=book, week_end=week_end) for book in BOOKS_ENABLED
    }
    # t_d33aef07: the frozen §4 milestone judgment rides the same weekly row
    # (absent on every week where nothing is due — no early/duplicate records).
    milestone = _collect_milestone_judgments(root, week_end)
    if milestone:
        out["milestone"] = milestone
    with summary_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
    return out


def _eval_blocks(data_dir: Path, *, book: str, week_end: date) -> dict[str, Any]:
    """Q1-Q7 for one book, restricted to the week's judgeable panel rows."""

    from yowayowa import paperloop_panel
    from yowayowa.paperloop_panel import build_panel

    try:
        journal = _load_journal(loop_root(data_dir))
        panel = build_panel(
            data_dir,
            start=week_end - timedelta(days=6),
            end=week_end,
        )
    except ChainError:
        return {"error": "chain_broken"}
    scoped = [row for row in panel["rows"] if row.get("book") == book]
    return {
        "panel_rows": len(scoped),
        "inputs_sha256": panel["hash"],
        "eval": {
            "Q1": paperloop_panel.eval_q1(scoped, book=book),
            "Q2": paperloop_panel.eval_q2(scoped, book=book),
            "Q3": paperloop_panel.eval_q3(scoped, book=book),
            "Q4": paperloop_panel.eval_q4(scoped, book=book),
            "Q5": paperloop_panel.eval_q5(scoped, book=book),
            "Q6": paperloop_panel.eval_q6(scoped, book=book, journal=journal),
            "Q7": paperloop_panel.eval_q7(scoped, book=book),
        },
    }


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo loop v1.2 (zero-order paper)")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument(
        "command",
        choices=("decide", "fill", "mark", "mtm", "week", "panel", "eval", "halt_check"),
    )
    parser.add_argument("--week-end", type=date.fromisoformat, default=None)
    parser.add_argument("--book", default=S01_BOOK)
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=None)
    parser.add_argument("--trigger", default="cron", choices=("cron", "manual"))
    args = parser.parse_args(argv)
    root = loop_root(args.data_dir)
    run_id = _new_run_id()
    try:
        if args.command == "decide":
            result = run_daily_decide(root, run_id=run_id, trigger=args.trigger)
            print(json.dumps({"ok": True, "rows_written": result, "run_id": run_id}))
            return 0
        if args.command == "fill":
            result = resolve_fills(root, run_id=run_id, trigger=args.trigger)
            print(json.dumps({"ok": True, "fills_resolved": result, "run_id": run_id}))
            return 0
        if args.command == "mark":
            result = mark_up(root)
            print(json.dumps({"ok": True, "rows_marked": result}))
            return 0
        if args.command == "mtm":
            result = run_daily_mtm(root)
            print(json.dumps({"ok": True, "mtm_rows": result}))
            return 0
        if args.command == "halt_check":
            result = _write_daily_halt_checks(root)
            print(json.dumps({"ok": True, "halt_checks": result}))
            return 0
        if args.command == "panel":
            from yowayowa.paperloop_panel import panel_json

            print(panel_json(args.data_dir, start=args.date_from, end=args.date_to))
            return 0
        if args.command == "eval":
            from yowayowa.paperloop_panel import eval_json

            print(
                eval_json(
                    args.data_dir,
                    book=args.book,
                    start=args.date_from,
                    end=args.date_to,
                )
            )
            return 0
        week_end = args.week_end or CLOCK(UTC).date()
        weekly_summary(args.data_dir, week_end)
        print(json.dumps({"ok": True}))
        return 0
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": f"ChainError: {exc}"}))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


def _write_daily_halt_checks(root: Path, utc_now: datetime | None = None) -> int:
    """Stand-alone daily halt_check pass (idempotent per book/day)."""

    now = utc_now if utc_now is not None else CLOCK(UTC)
    decision_date = now.date()
    written = 0
    for book in BOOKS_ENABLED:
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        rows_by_symbol = {symbol: _daily_map(root, symbol) for symbol in universe}
        closes_by_symbol = {
            symbol: [rows[day]["close"] for day in sorted(rows) if day < decision_date]
            for symbol, rows in rows_by_symbol.items()
        }
        is_equity = book in (BOOK_C_BOOK, BENCH_EQUITY_BOOK)
        annualization = (
            CRYPTO_ANNUALIZATION if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_ANNUALIZATION
        )
        state = rebuild_state(root, book)
        trigger_details: list[dict[str, Any]] = []
        for symbol in universe:
            trigger_details.extend(
                _halt_triggers_asset(
                    rows_by_symbol[symbol],
                    closes_by_symbol[symbol],
                    now,
                    annualization,
                    is_equity,
                )
            )
        dd = state["equity"] / state["peak"] - 1.0 if state["peak"] > 0 else 0.0
        halted_before = bool(state.get("halted"))
        halted_after = halted_before or bool([e for e in trigger_details if e["tripped"]])
        drawdown_warning = dd <= HALT_DRAWDOWN  # v1.3 (t_2202c100): warn only
        if any(
            row.get("event") == "halt_check"
            and row.get("book") == book
            and str(row.get("check_date")) == decision_date.isoformat()
            for row in _load_journal(root)
        ):
            continue
        margins = [e["margin"] for e in trigger_details if e.get("margin") is not None]
        _append_row(
            root,
            {
                "event": "halt_check",
                "book": book,
                "check_date": decision_date.isoformat(),
                "recorded_at": _utc_now_text(now),
                "triggers": trigger_details,
                "drawdown": dd,
                "drawdown_threshold": HALT_DRAWDOWN,
                "drawdown_margin": dd - HALT_DRAWDOWN,
                "drawdown_warning": drawdown_warning,
                "margin_min": min(margins) if margins else None,
                "halted_before": halted_before,
                "halted_after": halted_after,
                "action": _halt_action(halted_before, halted_after, dd, state, trigger_details),
                "regime_spec_hash": regime_spec_hash(),
            },
        )
        written += 1
    return written


if __name__ == "__main__":
    sys.exit(main())
