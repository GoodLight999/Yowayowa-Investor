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

Note: equity books (C, EQUITY_EW) remain schema-silent v1 (BOOK_C_ENABLED).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
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
STRATEGY_BOOKS = (S01_BOOK,)
BOOKS_ENABLED = (S01_BOOK, BENCH_CRYPTO_BOOK)
CRYPTO_UNIVERSE = ("BTC", "ETH")
EQUITY_UNIVERSE = (VOLATILITY_TARGET_SYMBOL, "IWM", "JNJ", "PG")
SMA_LOOKBACK = 200
VOL_WINDOW = 21
CRYPTO_ANNUALIZATION = 365
EQUITY_ANNUALIZATION = 252
COST_RATE = 0.0015
BOOK_C_TARGET_VOL = 0.15
HALT_DRAWDOWN = -0.20  # stop line: stopped, not resumable
HALT_FRESHNESS_CRYPTO = timedelta(hours=26)
HALT_FRESHNESS_EQUITY_DAYS = 2
HALT_SIGMA_MOVE = 5.0
FLOOR_SIGMA_DAILY = 0.001  # degenerate-window floor: 0.1%/day (~1.9% ann.)
GUARD_MIN_HOLD_HOURS = 4.0
GUARD_MAX_FILLS_PER_DAY = 2
BINANCE_TAPE = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
SCHEMA = "demo-loop-v1.1"
BOOK_C_ENABLED = False
MILESTONE_RULES = {  # design §4, frozen numeric gates
    4: {"loop_success_rate": 0.95, "observed_cost_bps_max": 30.0},
    12: {"dd_vs_benchmark": 0.6, "cum_pnl_min": -0.10, "switches_multiple": 2.0},
    26: {"dd_vs_benchmark": 0.6, "observed_cost_bps_max": 22.5},
}

# clock injection point (single, for tests) — D5
CLOCK = datetime.now


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
    """The ONLY journal writer: seq numbering + prev-hash chain + fsync."""

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


def _write_anchor(root: Path) -> dict[str, Any] | None:
    """Daily anchor: last seq + hash (chain-cut detection)."""

    rows = _load_journal(root)
    if not rows:
        return None
    last = rows[-1]
    out = {
        "seq": last["seq"],
        "row_hash": last["row_hash"],
        "anchored_at": CLOCK().isoformat(),
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


def _halt_reasons_asset(
    rows: Mapping[date, Mapping[str, float]],
    closes: list[float],
    utc_now: datetime,
    annualization: int,
    is_equity: bool,
) -> list[str]:
    reasons: list[str] = []
    latest = max(rows, default=None)
    if is_equity:
        if latest is None or (utc_now.date() - latest) > timedelta(
            days=HALT_FRESHNESS_EQUITY_DAYS + 1
        ):
            reasons.append("stale_data")
    else:
        # crypto freshness: now - (last bar date + 1 day 00:00 UTC) > 26h (H-a)
        if latest is None:
            reasons.append("stale_data")
        else:
            last_close_utc = datetime.combine(
                latest + timedelta(days=1), datetime.min.time(), tzinfo=UTC
            )
            if utc_now - last_close_utc > HALT_FRESHNESS_CRYPTO:
                reasons.append("stale_data")
    sigma_prior = _sigma21_prior(closes, annualization)
    if sigma_prior is not None and len(closes) >= 2:
        # a degenerate (zero-variance) prior window still guards the gate:
        # use the frozen minimum-daily-sigma floor of 0.1% (≈1.9% annualized)
        daily_sigma = max(sigma_prior / annualization**0.5, FLOOR_SIGMA_DAILY)
        last_return = abs(closes[-1] / closes[-2] - 1.0)
        if last_return > HALT_SIGMA_MOVE * daily_sigma:
            reasons.append("sigma_5_move")
    if closes and not math.isfinite(closes[-1]):
        reasons.append("non_finite")
    return reasons


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
    if dd <= HALT_DRAWDOWN and not state.get("stopped"):
        for symbol in results:
            results[symbol].append("drawdown_stop")
    if state.get("halted"):
        for symbol in results:
            results[symbol].append("halt_carryover")
    return results


# --- prereg (book registry, F6) ------------------------------------------------


def ensure_prereg(root: Path) -> None:
    """Register enabled books on first run (hash-chained prereg ledger)."""

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
                else "Benchmark: BTC/ETH 1/N buy-and-hold"
            ),
            "judgment_rule": "design §4 milestones at 4/12/26 weeks",
            "registered_at": CLOCK().isoformat(),
            "registered_by": "worker-flash:t_d9a25dd2",
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


def _freeze_inputs(
    root: Path,
    book: str,
    decision_date: date,
    closes_by_symbol: Mapping[str, list[float]],
    last_bar_ts_by_symbol: Mapping[str, date],
) -> str:
    """Write frozen decision inputs (per book/date) and return input_sha256."""

    payload = {
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
    path = inputs_dir(root) / decision_date.isoformat() / f"{book}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return _sha256(path.read_text(encoding="utf-8"))


def run_daily_mtm(root: Path, utc_now: datetime | None = None) -> int:
    """Write one mtm row per enabled book (D3): equity chain + stop line."""

    now = utc_now if utc_now is not None else CLOCK(UTC)
    written = 0
    for book in BOOKS_ENABLED:
        state = rebuild_state(root, book)
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        prices = {}
        for symbol in universe:
            daily = _daily_map(root, symbol)
            latest = max(daily, default=None)
            if latest is None:
                continue  # no price yet for this asset: state is carried as-is
            prices[symbol] = daily[latest]["close"]
        units_value = sum(units * prices.get(asset, 0.0) for asset, units in state["units"].items())
        equity = state["cash"] + units_value
        peak = max(state["peak"], equity)
        drawdown = equity / peak - 1.0 if peak > 0 else 0.0
        stopped = bool(state.get("stopped")) or drawdown <= HALT_DRAWDOWN
        _append_row(
            root,
            {
                "event": "mtm",
                "book": book,
                "mtm_date": now.date().isoformat(),
                "recorded_at": now.isoformat(),
                "prices": {asset: prices.get(asset) for asset in universe},
                "units": dict(state["units"]),
                "cash": state["cash"],
                "equity": equity,
                "peak_equity": peak,
                "drawdown": drawdown,
                "halted": bool(state.get("halted")),
                "stopped": stopped,
            },
        )
        written += 1
    return written


def run_daily_decide(root: Path, utc_now: datetime | None = None) -> int:
    """Daily decide phase (D4 idempotent, D5 internal clock, D6 guard at fill,
    D7 benchmark buys 1/N once, D9 frozen inputs, first-hit HALT) + the D3
    daily mtm row per book."""

    now = utc_now if utc_now is not None else CLOCK(UTC)
    ensure_prereg(root)
    decision_date = now.date()
    written = 0
    for book in BOOKS_ENABLED:
        check_prereg(root, book)
        universe = list(
            CRYPTO_UNIVERSE if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_UNIVERSE
        )
        rows_by_symbol = {symbol: _daily_map(root, symbol) for symbol in universe}
        closes_by_symbol: dict[str, list[float]] = {
            symbol: [rows[day]["close"] for day in sorted(rows) if day < decision_date]
            for symbol, rows in rows_by_symbol.items()
        }
        halts = evaluate_halt(
            book,
            rows_by_symbol,
            closes_by_symbol,
            now,
            CRYPTO_ANNUALIZATION if book in (S01_BOOK, BENCH_CRYPTO_BOOK) else EQUITY_ANNUALIZATION,
            rebuild_state(root, book),
        )
        state = rebuild_state(root, book)
        equity = float(state["equity"])
        for symbol in universe:
            decision_id = f"{book}:{symbol}:{decision_date.isoformat()}"
            if decision_id in _decision_seq(root):
                continue  # D4: idempotent — a decision is written at most once
            closes = closes_by_symbol[symbol]
            halt_reasons = halts[symbol]
            halted = bool(halt_reasons)
            annualization = (
                CRYPTO_ANNUALIZATION
                if book in (S01_BOOK, BENCH_CRYPTO_BOOK)
                else EQUITY_ANNUALIZATION
            )
            closes_ok = len(closes) >= SMA_LOOKBACK + 1
            sma = _sma200(closes)
            sigma = _sigma21(closes, annualization)
            backfill = decision_date < now.date()  # C4: auto (never an argument)
            if not closes_ok or halted:
                # decision row + regime + skip (no pending fill, D10 chain kept)
                _append_row(
                    root,
                    {
                        "event": "decision",
                        "book": book,
                        "recorded_at": now.isoformat(),
                        "decision_id": decision_id,
                        "decision_date": decision_date.isoformat(),
                        "asset": symbol,
                        "backfill": backfill or decision_date < now.date(),
                        "inputs_last_bar_ts": max(rows_by_symbol[symbol]).isoformat()
                        if rows_by_symbol[symbol]
                        else None,  # D9: the newest as_of actually READ
                        "close": closes[-1] if closes else None,
                        "sma200": sma,
                        "sigma21": sigma,
                        "regime_state": "HALT",
                        "prev_regime_state": None,
                        "switch_flag": False,
                        "switch_reason": "",
                        "target_weight": 0.0,
                        "held_weight_at_decision": 0.0,
                        "side": "hold",
                        "delta_weight": 0.0,
                        "halt_flag": True,
                        "halt_reason": ",".join(halt_reasons) or "insufficient_history",
                        "fill_status": "skipped",
                    },
                )
                written += 1
                continue
            if book == S01_BOOK:
                target_map = _engine_target(
                    book, {other: rows_by_symbol[other] for other in universe}, decision_date
                )
            elif book == BENCH_CRYPTO_BOOK:
                # D7: buy 1/N on the first decision; afterwards hold only.
                prior_fills = [
                    row
                    for row in _load_journal(root)
                    if row.get("event") == "fill" and row.get("book") == book
                ]
                target_map = _benchmark_weights(book) if not prior_fills else {}
            else:
                target_map = _benchmark_weights(book)  # pragma: no cover
            target = float(target_map.get(symbol, 0.0))
            held_units = float(state["units"].get(symbol, 0.0))
            gate_price = closes[-1]
            held_weight = (held_units * gate_price / equity) if equity > 0 else 0.0
            delta = target - held_weight
            if book == BENCH_CRYPTO_BOOK and any(
                row.get("event") == "fill" and row.get("book") == book
                for row in _load_journal(root)
            ):
                delta = 0.0  # D7: the benchmark never trades again after buy-in
            side = "hold" if abs(delta) < 1e-6 else ("buy" if delta > 0 else "sell")
            prev_regime = None
            prev_decision = [
                row
                for row in _load_journal(root)
                if row.get("event") == "decision"
                and row.get("asset") == symbol
                and row.get("book") == book
            ]
            if prev_decision:
                prev_regime = prev_decision[-1].get("regime_state")
            if sma is None or gate_price > sma:
                regime = "HALT" if sma is None else "RISK_ON"  # fail closed on SMA
            else:
                regime = "RISK_OFF"
            switch = prev_regime is not None and prev_regime != regime and regime != "HALT"
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
                    "recorded_at": now.isoformat(),
                    "decision_id": decision_id,
                    "decision_date": decision_date.isoformat(),
                    "asset": symbol,
                    "backfill": backfill,
                    "inputs_last_bar_ts": max(rows_by_symbol[symbol]).isoformat(),
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
                    "target_weight": target,
                    "held_weight_at_decision": held_weight,
                    "side": side,
                    "delta_weight": delta,
                    "halt_flag": False,
                    "halt_reason": "",
                    "fill_status": "pending" if side != "hold" else "skipped",
                },
            )
            written += 1
    run_daily_mtm(root, now)  # D3: every daily run marks the books to market
    _write_anchor(root)
    return written


def resolve_fills(root: Path, utc_now: datetime | None = None) -> int:
    """Fill pending decisions from the 1h tape/forming candle (C2/C3/D6)."""

    now = utc_now if utc_now is not None else CLOCK(UTC)
    ensure_prereg(root)
    completed = 0
    decisions = _decision_rows(root)
    for decision_id, row in sorted(decisions.items()):
        if row.get("fill_status") != "pending":
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
        if not allowed:
            _append_row(
                root,
                {
                    "event": "fill_rejected",
                    "book": book,
                    "recorded_at": now.isoformat(),
                    "decision_id": decision_id,
                    "reject_reason": reject,
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
                },
            )
            continue
        if side == "hold" or delta_notional <= 1e-9:
            _append_row(
                root,
                {
                    "event": "fill",
                    "book": book,
                    "recorded_at": now.isoformat(),
                    "decision_id": decision_id,
                    "asset": asset,
                    "side": "hold",
                    "paper_fill_price": fill["open"],
                    "paper_fill_bar_open_ts": fill["open_time"],
                    "fill_lag_sec": int((fill_open_ts - recorded_at).total_seconds()),
                    "traded_notional": 0.0,
                    "cost_assumed": 0.0,
                    "units_before": held_units,
                    "units_after": held_units,
                    "cash_after": state["cash"],
                    "backfill": bool(row.get("backfill")),
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
        _append_row(
            root,
            {
                "event": "fill",
                "book": book,
                "recorded_at": now.isoformat(),
                "decision_id": decision_id,
                "asset": asset,
                "side": side,
                "paper_fill_price": fill["open"],
                "paper_fill_bar_open_ts": fill["open_time"],
                "fill_lag_sec": int((fill_open_ts - recorded_at).total_seconds()),
                "traded_notional": traded,
                "cost_assumed": traded * COST_RATE,
                "target_weight": target_weight,
                "units_before": held_units,
                "units_after": units_after,
                "cash_after": cash_after,
                "backfill": bool(row.get("backfill")),
            },
        )
        completed += 1
    return completed


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
        target_now = _engine_target(
            book, {other: _daily_map(root, other) for other in CRYPTO_UNIVERSE}, execution_date
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
        _append_row(
            root,
            {
                "event": "mark",
                "book": book,
                "recorded_at": now.isoformat(),
                "decision_id": decision_id,
                "asset": asset,
                "bt_fill_price": bar["open"],
                "shortfall_bps": shortfall_bps,
                "recompute_target": target_now.get(asset, 0.0),
                "recompute_match": match,
            },
        )
        marked += 1
    return marked


# --- weekly summary ------------------------------------------------------------


def weekly_summary(data_dir: Path, week_end: date) -> dict[str, Any]:
    """Weekly summary row (M8/spec §7): per book + milestone judgment."""

    root = loop_root(data_dir)
    window_start = week_end - timedelta(days=6)
    out: dict[str, Any] = {
        "week_end": week_end.isoformat(),
        "generated_at": CLOCK().isoformat(),
        "books": {},
        "prereg_count": sum(1 for _ in _rows_from_jsonl(prereg_path(root), strict=True)),
        "last_anchor_seq": None,
        "last_anchor_hash": None,
        "anchor_ok": _anchors_ok(root),
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
        drawdown = state["equity"] / state["peak"] - 1.0 if state["peak"] > 0 else None
        book_out = {
            "decisions": len(judged),
            "switches": switches,
            "fills": len(fills),
            "fills_rejected": len(rejects),
            "backfill_days": len(decisions) - len(judged),
            "week_return": state["equity"] - 1.0,
            "drawdown_now": drawdown,
            "shortfall_bps_mean_signed": (
                sum(shortfalls) / len(shortfalls) if shortfalls else None
            ),
            "shortfall_bps_mean_abs": (
                sum(abs(value) for value in shortfalls) / len(shortfalls) if shortfalls else None
            ),
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
        }
        out["books"][book] = book_out
    # vs benchmark (pp, week + cumulative)
    book_a, book_bench = state_by_book.get(S01_BOOK), state_by_book.get(BENCH_CRYPTO_BOOK)
    if book_a is not None and book_bench is not None:
        out["vs_benchmark_pp_cum"] = (book_a["equity"] - book_bench["equity"]) * 100.0
    with summary_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")
    return out


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo loop v1.1 (zero-order paper)")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("command", choices=("decide", "fill", "mark", "mtm", "week", "loop_run"))
    parser.add_argument("--week-end", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    root = loop_root(args.data_dir)
    try:
        if args.command == "loop_run":
            print(json.dumps({"ok": True}))
            return 0
        if args.command == "decide":
            result = run_daily_decide(root)
            result += run_daily_mtm(root)
            print(json.dumps({"ok": True, "rows_written": result}))
            return 0
        if args.command == "fill":
            result = resolve_fills(root)
            result += run_daily_mtm(root)
            print(json.dumps({"ok": True, "fills_resolved": result}))
            return 0
        if args.command == "mark":
            result = mark_up(root)
            print(json.dumps({"ok": True, "rows_marked": result}))
            return 0
        if args.command == "mtm":
            result = run_daily_mtm(root)
            print(json.dumps({"ok": True, "mtm_rows": result}))
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


if __name__ == "__main__":
    sys.exit(main())
