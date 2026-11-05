#!/usr/bin/env python3
"""Split-calendar coverage monitor for the persisted US-stock OHLCV store.

Policy: prices are persisted **raw** (``adjustment=raw``) and the backtest
engine is the *only* place where splits are applied, driven by
``data/fundamentals/pbr/split-calendar.json`` (see
``docs/US_STOCK_PRICE_ADJUSTMENT_POLICY_20261105.md``). That makes calendar
completeness a correctness requirement: a split missing from the calendar is
silently left unadjusted and shows up as a fake -50%/-90% return.

This monitor looks for the two ways that policy can break:

``unexplained_jump``
    A session-to-session move at or beyond ``--jump-threshold`` that the
    calendar does not account for. A genuine >=2:1 split always shows up as
    <= -50%, so anything at that magnitude without a calendar entry is either
    an unregistered split or corrupt data.

``calendar_mismatch``
    A calendar entry whose date exists in the store but whose observed ratio
    does *not* corroborate the recorded split ratio. This is what
    double-adjustment looks like: if the stored series were already
    split-adjusted, the observed ratio would be ~1.0 while the calendar
    expects ``1/ratio``. The engine would silently skip the correction, so
    this must fail loudly here instead.

``calendar_symbol_missing``
    A symbol that has split events in the calendar but no rows in the store.

Smaller unexplained moves (>= ``--watch-threshold``) are reported as
``watch_jump`` findings but do not fail the run: earnings gaps and genuine
crashes live in that band.

Exit code 0 = clean, 1 = at least one failure, 2 = cannot run (missing input).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_CALENDAR = DEFAULT_DATA_DIR / "fundamentals" / "pbr" / "split-calendar.json"
DEFAULT_JUMP_THRESHOLD = 0.35
DEFAULT_WATCH_THRESHOLD = 0.15
# The engine corroborates a calendar entry only when both the open and the close
# ratio are within 15% of the expected factor (the inline gate in
# services/backtest.py:656-657 and 682-683). Mirror it exactly: a wider value
# would report engine-skips that do not happen, a tighter one would miss them.
ENGINE_CORROBORATION_TOLERANCE = 0.15


@dataclass
class Session:
    symbol: str
    as_of: str
    open: float
    close: float


@dataclass
class Finding:
    kind: str
    symbol: str
    session: str
    detail: str
    observed: float | None = None
    expected: float | None = None


@dataclass
class SymbolReport:
    symbol: str
    sessions: int = 0
    first: str | None = None
    last: str | None = None
    calendar_events: int = 0
    calendar_events_outside_window: int = 0
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Report:
    data_dir: str
    calendar_path: str
    jump_threshold: float
    watch_threshold: float
    symbols: list[SymbolReport] = field(default_factory=list)
    failures: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.warnings


def load_calendar(path: Path) -> dict[str, dict[str, float]]:
    """Normalize the split calendar to ``{SYMBOL: {YYYY-MM-DD: ratio}}``."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"split calendar is not a JSON object: {path}")
    out: dict[str, dict[str, float]] = {}
    for symbol, events in raw.items():
        normalized: dict[str, float] = {}
        if isinstance(events, Mapping):
            for day, ratio in events.items():
                normalized[str(day)[:10]] = float(ratio)
        elif isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            for item in events:
                if isinstance(item, Mapping) and "effective_date" in item and "ratio" in item:
                    normalized[str(item["effective_date"])[:10]] = float(item["ratio"])
        out[str(symbol).strip().upper()] = normalized
    return out


def read_store(data_dir: Path) -> dict[str, list[Session]]:
    """Read every ``{data_dir}/stock-ohlcv/*/ohlcv.jsonl`` alpaca series, oldest first."""

    root = data_dir / "stock-ohlcv"
    if not root.is_dir():
        raise FileNotFoundError(f"stock OHLCV store not found: {root}")
    out: dict[str, list[Session]] = {}
    for path in sorted(root.glob("*/ohlcv.jsonl")):
        symbol = path.parent.name.upper()
        rows: list[Session] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, Mapping) or entry.get("provider") != "alpaca":
                    continue
                try:
                    rows.append(
                        Session(
                            symbol=symbol,
                            as_of=str(entry["as_of"])[:10],
                            open=float(entry["open"]),
                            close=float(entry["close"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        rows.sort(key=lambda row: row.as_of)
        if rows:
            out[symbol] = rows
    return out


def _ratio(new: float, old: float) -> float | None:
    if not (old > 0 and new > 0):
        return None
    return new / old


def check_symbol(
    symbol: str,
    sessions: Sequence[Session],
    calendar: Mapping[str, float],
    *,
    jump_threshold: float,
    watch_threshold: float,
) -> SymbolReport:
    report = SymbolReport(
        symbol=symbol,
        sessions=len(sessions),
        first=sessions[0].as_of if sessions else None,
        last=sessions[-1].as_of if sessions else None,
        calendar_events=len(calendar),
    )
    seen = {row.as_of for row in sessions}

    for previous, current in pairwise(sessions):
        close_ratio = _ratio(current.close, previous.close)
        if close_ratio is None:
            continue
        entry_ratio = calendar.get(current.as_of)

        if entry_ratio is not None and entry_ratio > 0:
            expected = 1.0 / entry_ratio
            open_ratio = _ratio(current.open, previous.close) or close_ratio
            for label, observed in (("open", open_ratio), ("close", close_ratio)):
                deviation = abs(observed - expected) / expected
                if deviation > ENGINE_CORROBORATION_TOLERANCE:
                    report.findings.append(
                        Finding(
                            kind="calendar_mismatch",
                            symbol=symbol,
                            session=current.as_of,
                            detail=(
                                f"calendar says {entry_ratio:g}:1 (expected {label} factor "
                                f"{expected:.4f}) but observed {observed:.4f} "
                                f"({deviation * 100:.1f}% away); engine would skip this "
                                f"correction - stored series may already be split-adjusted "
                                f"(double adjustment) or the prices are wrong"
                            ),
                            observed=observed,
                            expected=expected,
                        )
                    )
                    break
            continue

        move = close_ratio - 1.0
        if abs(move) >= jump_threshold:
            report.findings.append(
                Finding(
                    kind="unexplained_jump",
                    symbol=symbol,
                    session=current.as_of,
                    detail=(
                        f"close move {move * 100:+.2f}% with no split-calendar entry; "
                        f"a >=2:1 split is <= -50%, so this is an unregistered split "
                        f"or corrupt data"
                    ),
                    observed=close_ratio,
                )
            )
        elif abs(move) >= watch_threshold:
            report.findings.append(
                Finding(
                    kind="watch_jump",
                    symbol=symbol,
                    session=current.as_of,
                    detail=f"close move {move * 100:+.2f}% with no calendar entry (watch only)",
                    observed=close_ratio,
                )
            )

    for day in sorted(calendar):
        if day not in seen and (not sessions or sessions[0].as_of <= day <= sessions[-1].as_of):
            report.findings.append(
                Finding(
                    kind="calendar_corroboration_missing",
                    symbol=symbol,
                    session=day,
                    detail=(
                        "calendar entry falls inside the stored window but the store has no "
                        "session for that date; the correction cannot be corroborated"
                    ),
                )
            )
    report.calendar_events_outside_window = sum(
        1 for day in calendar if not sessions or day < sessions[0].as_of or day > sessions[-1].as_of
    )
    return report


def build_report(
    *,
    data_dir: Path,
    calendar_path: Path,
    jump_threshold: float = DEFAULT_JUMP_THRESHOLD,
    watch_threshold: float = DEFAULT_WATCH_THRESHOLD,
    symbols: Iterable[str] | None = None,
) -> Report:
    calendar = load_calendar(calendar_path)
    store = read_store(data_dir)
    selected = sorted(store) if symbols is None else sorted({s.upper() for s in symbols})

    report = Report(
        data_dir=str(data_dir),
        calendar_path=str(calendar_path),
        jump_threshold=jump_threshold,
        watch_threshold=watch_threshold,
    )
    if not store:
        report.warnings.append(f"no alpaca sessions found under {data_dir / 'stock-ohlcv'}")

    for symbol in selected:
        sessions = store.get(symbol)
        if not sessions:
            report.warnings.append(f"symbol {symbol} has no persisted sessions; skipped")
            continue
        report.symbols.append(
            check_symbol(
                symbol,
                sessions,
                calendar.get(symbol, {}),
                jump_threshold=jump_threshold,
                watch_threshold=watch_threshold,
            )
        )

    for symbol, events in sorted(calendar.items()):
        if events and symbol not in store:
            report.failures.append(
                Finding(
                    kind="calendar_symbol_missing",
                    symbol=symbol,
                    session="",
                    detail=(
                        f"{len(events)} calendar split event(s) but no persisted series for "
                        f"{symbol}; coverage hole"
                    ),
                )
            )

    for entry in report.symbols:
        for finding in entry.findings:
            if finding.kind in {"unexplained_jump", "calendar_mismatch"}:
                report.failures.append(finding)
    return report


def render(report: Report) -> str:
    lines = [
        "split-calendar coverage check",
        f"  store        : {report.data_dir}",
        f"  calendar     : {report.calendar_path}",
        f"  thresholds   : jump>={report.jump_threshold:.0%}, watch>={report.watch_threshold:.0%}",
        "",
    ]
    header = f"{'symbol':7} {'sessions':>8} {'first':10} {'last':10} {'cal_ev':>6} {'findings':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for entry in report.symbols:
        lines.append(
            f"{entry.symbol:7} {entry.sessions:8d} {entry.first or '-':10} {entry.last or '-':10} "
            f"{entry.calendar_events:6d} {len(entry.findings):8d}"
        )
    lines.append("")
    for entry in report.symbols:
        for finding in entry.findings:
            marker = "FAIL" if finding.kind in {"unexplained_jump", "calendar_mismatch"} else "info"
            lines.append(
                f"[{marker}] {finding.kind} {finding.symbol} {finding.session}: {finding.detail}"
            )
    for finding in report.failures:
        if finding.kind == "calendar_symbol_missing":
            lines.append(f"[FAIL] {finding.kind} {finding.symbol}: {finding.detail}")
    for warning in report.warnings:
        lines.append(f"[warn] {warning}")
    lines.append("")
    verdict = "PASS" if report.ok else f"FAIL ({len(report.failures)} finding(s))"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--jump-threshold", type=float, default=DEFAULT_JUMP_THRESHOLD)
    parser.add_argument("--watch-threshold", type=float, default=DEFAULT_WATCH_THRESHOLD)
    parser.add_argument("--symbol", action="append", dest="symbols", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        report = build_report(
            data_dir=args.data_dir,
            calendar_path=args.calendar,
            jump_threshold=args.jump_threshold,
            watch_threshold=args.watch_threshold,
            symbols=args.symbols,
        )
    except (OSError, ValueError) as exc:
        print(f"cannot run: {exc}", file=sys.stderr)
        return 2

    print(render(report))
    if args.json_out is not None:
        payload: dict[str, Any] = {
            "ok": report.ok,
            "data_dir": report.data_dir,
            "calendar_path": report.calendar_path,
            "jump_threshold": report.jump_threshold,
            "watch_threshold": report.watch_threshold,
            "failures": [asdict(item) for item in report.failures],
            "warnings": report.warnings,
            "symbols": [
                {
                    "symbol": entry.symbol,
                    "sessions": entry.sessions,
                    "first": entry.first,
                    "last": entry.last,
                    "calendar_events": entry.calendar_events,
                    "calendar_events_outside_window": entry.calendar_events_outside_window,
                    "findings": [asdict(item) for item in entry.findings],
                }
                for entry in report.symbols
            ],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
