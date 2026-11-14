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

``corporate_action_missing_from_calendar`` / ``corporate_action_ratio_mismatch``
    With ``--corporate-actions <snapshot.json>`` or ``--corporate-actions-live``
    (fetch from Alpaca, needs credentials): a provider split the calendar does
    not confirm, or one whose ratio disagrees with the calendar entry. This is
    the audit criterion "every Alpaca corporate action must exist in the split
    calendar; a missing entry fails".

Smaller unexplained moves (>= ``--watch-threshold``) are reported as
``watch_jump`` findings but do not fail the run: earnings gaps and genuine
crashes live in that band.

Exit code 0 = clean, 1 = at least one failure, 2 = cannot run (missing input).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
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
# Corporate-actions cross-check (docs/US_STOCK_PRICE_ADJUSTMENT_POLICY_20261105.md):
# every split Alpaca knows about must exist in the SEC-corroborated calendar,
# otherwise the engine silently leaves it unadjusted. A ratio disagreement is
# the same class of failure (the engine would skip the correction).
CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
DEFAULT_CA_START = "2019-01-01"
CA_RATIO_TOLERANCE = 0.15
CA_KINDS_FAILING = (
    "corporate_action_missing_from_calendar",
    "corporate_action_ratio_mismatch",
)


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
class CorporateAction:
    """A split event reported by the provider (Alpaca corporate-actions)."""

    symbol: str
    ex_date: str
    ratio: float


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
    notices: list[str] = field(default_factory=list)
    corporate_actions_checked: int = 0

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
        if str(symbol).startswith("_"):
            continue  # snapshot metadata (e.g. "_provenance"), not a symbol
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


def normalize_corporate_actions(payload: Any) -> list[CorporateAction]:
    """Normalize split events to ``CorporateAction`` records.

    Accepts the three shapes this tool encounters:

    * the raw Alpaca response: ``{"corporate_actions": {"forward_splits": [...]}}``
      where each item carries ``symbol``/``ex_date``/``old_rate``/``new_rate``;
    * the recorded per-symbol snapshot: ``{"SYM": [["YYYY-MM-DD", old, new], ...]}``
      (keys starting with ``_`` are metadata and are skipped);
    * a flat list of item dicts with ``symbol``/``ex_date``/``old_rate``/``new_rate``.

    The split ratio follows the calendar convention: ``new_rate / old_rate``
    (a 4-for-1 forward split is 4.0).
    """

    actions: list[CorporateAction] = []

    def _from_item(item: Mapping[str, Any], default_symbol: str | None = None) -> None:
        symbol = str(item.get("symbol") or default_symbol or "").strip().upper()
        ex_date = str(item.get("ex_date") or "")[:10]
        old_rate = item.get("old_rate")
        new_rate = item.get("new_rate")
        if not symbol or not ex_date or old_rate in (None, 0) or new_rate is None:
            return
        actions.append(
            CorporateAction(
                symbol=symbol,
                ex_date=ex_date,
                ratio=float(new_rate) / float(old_rate),
            )
        )

    if isinstance(payload, Mapping) and isinstance(payload.get("corporate_actions"), Mapping):
        for kind in ("forward_splits", "reverse_splits", "stock_splits"):
            for item in payload["corporate_actions"].get(kind) or []:
                if isinstance(item, Mapping):
                    _from_item(item)
    elif isinstance(payload, Mapping):
        for symbol, events in payload.items():
            if str(symbol).startswith("_"):
                continue  # snapshot metadata (e.g. "_provenance")
            if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
                for entry in events:
                    if isinstance(entry, Mapping):
                        _from_item(entry, default_symbol=str(symbol))
                    elif (
                        isinstance(entry, (list, tuple))
                        and len(entry) == 3
                        and entry[1] not in (None, 0)
                    ):
                        actions.append(
                            CorporateAction(
                                symbol=str(symbol).strip().upper(),
                                ex_date=str(entry[0])[:10],
                                ratio=float(entry[2]) / float(entry[1]),
                            )
                        )
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for item in payload:
            if isinstance(item, Mapping):
                _from_item(item)

    deduped: dict[tuple[str, str], CorporateAction] = {}
    for action in actions:
        deduped.setdefault((action.symbol, action.ex_date), action)
    return list(deduped.values())


def resolve_alpaca_credentials() -> tuple[str | None, str | None]:
    """Find Alpaca credentials the way scripts/paper_c2/alpaca_paper_client.py does."""

    key_id = os.environ.get("YOWAYOWA_ALPACA_KEY_ID") or os.environ.get("ALPACA_KEY_ID")
    secret_key = os.environ.get("YOWAYOWA_ALPACA_SECRET_KEY") or os.environ.get(
        "ALPACA_SECRET_KEY"
    )
    if key_id and secret_key:
        return key_id.strip(), secret_key.strip()
    candidates = [
        (REPO_ROOT / ".env", "YOWAYOWA_ALPACA_KEY_ID", "YOWAYOWA_ALPACA_SECRET_KEY"),
        (Path("/root/.secrets/alpaca_paper.env"), "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"),
        (Path("/root/.hermes/.env"), "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"),
    ]
    for path, key_name, secret_name in candidates:
        if not path.is_file():
            continue
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
        found_key = values.get(key_name)
        found_secret = values.get(secret_name)
        if found_key and found_secret:
            return found_key, found_secret
    return None, None


def fetch_corporate_actions(
    symbols: Iterable[str],
    *,
    start: str,
    end: str,
    key_id: str,
    secret_key: str,
    timeout: float = 30.0,
) -> list[CorporateAction]:
    """Fetch forward/reverse splits from Alpaca (one request per symbol)."""

    actions: list[CorporateAction] = []
    for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
        token: str | None = None
        while True:
            query = urllib.parse.urlencode(
                {
                    "symbols": symbol,
                    "types": "forward_split,reverse_split",
                    "start": start,
                    "end": end,
                    "limit": "1000",
                    **({"page_token": token} if token else {}),
                }
            )
            request = urllib.request.Request(
                f"{CORPORATE_ACTIONS_URL}?{query}",
                headers={
                    "APCA-API-KEY-ID": key_id,
                    "APCA-API-SECRET-KEY": secret_key,
                    "accept": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode())
            raw_actions = body.get("corporate_actions") or {}
            if isinstance(raw_actions, Mapping):
                for kind in ("forward_splits", "reverse_splits", "stock_splits"):
                    for item in raw_actions.get(kind) or []:
                        if isinstance(item, Mapping):
                            item = dict(item)
                            item.setdefault("symbol", symbol)
                            actions.extend(normalize_corporate_actions([item]))
            token = body.get("next_page_token")
            if not token:
                break
    deduped: dict[tuple[str, str], CorporateAction] = {}
    for action in actions:
        deduped.setdefault((action.symbol, action.ex_date), action)
    return list(deduped.values())


def _normalize_calendar_view(
    calendar: Mapping[str, Mapping[str, float]]
    | Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, float]]:
    """Accept both the load_calendar output and the raw list-of-events shape."""

    out: dict[str, dict[str, float]] = {}
    for symbol, events in calendar.items():
        if isinstance(events, Mapping):
            out[str(symbol).strip().upper()] = {str(day)[:10]: float(ratio) for day, ratio in events.items()}
        elif isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            normalized: dict[str, float] = {}
            for item in events:
                if isinstance(item, Mapping) and "effective_date" in item and "ratio" in item:
                    normalized[str(item["effective_date"])[:10]] = float(item["ratio"])
            out[str(symbol).strip().upper()] = normalized
    return out


def compare_corporate_actions(
    actions: Sequence[CorporateAction],
    calendar: Mapping[str, Mapping[str, float]]
    | Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tolerance: float = CA_RATIO_TOLERANCE,
) -> tuple[list[Finding], list[str]]:
    """Cross-check provider splits against the calendar.

    Returns ``(failures, notices)``. A provider split missing from the calendar
    - or present with a disagreeing ratio - is a failure: the engine would leave
    it unadjusted or skip the correction. Calendar events no provider event
    corroborates are notices only (the SEC calendar may legitimately be a
    superset of Alpaca's corporate actions).
    """

    failures: list[Finding] = []
    notices: list[str] = []
    cal_view = _normalize_calendar_view(calendar)
    seen: set[tuple[str, str]] = set()
    for action in actions:
        seen.add((action.symbol, action.ex_date))
        cal_events = cal_view.get(action.symbol) or {}
        cal_ratio = cal_events.get(action.ex_date)
        if cal_ratio is None:
            failures.append(
                Finding(
                    kind="corporate_action_missing_from_calendar",
                    symbol=action.symbol,
                    session=action.ex_date,
                    detail=(
                        f"provider reports a split ratio {action.ratio:g}:1 on "
                        f"{action.ex_date} but the split calendar has no entry for it; "
                        f"the engine would leave it unadjusted (fake -"
                        f"{(1 - 1 / action.ratio) * 100:.0f}% return) - regenerate the "
                        f"calendar via scripts/fetch_sec_pbr.py"
                    ),
                    observed=action.ratio,
                    expected=None,
                )
            )
            continue
        if cal_ratio > 0:
            deviation = abs(action.ratio - cal_ratio) / cal_ratio
            if deviation > tolerance:
                failures.append(
                    Finding(
                        kind="corporate_action_ratio_mismatch",
                        symbol=action.symbol,
                        session=action.ex_date,
                        detail=(
                            f"provider ratio {action.ratio:g}:1 disagrees with calendar "
                            f"ratio {cal_ratio:g}:1 ({deviation * 100:.1f}% away, tolerance "
                            f"{tolerance:.0%}); the engine would skip the correction"
                        ),
                        observed=action.ratio,
                        expected=cal_ratio,
                    )
                )

    for symbol, events in sorted(cal_view.items()):
        for day in sorted(events):
            if events[day] > 0 and (symbol, day) not in seen:
                notices.append(
                    f"calendar event {symbol} {day} {events[day]:g}:1 has no matching "
                    f"provider corporate action (info only; SEC may be a superset)"
                )
    return failures, notices


def build_report(
    *,
    data_dir: Path,
    calendar_path: Path,
    jump_threshold: float = DEFAULT_JUMP_THRESHOLD,
    watch_threshold: float = DEFAULT_WATCH_THRESHOLD,
    symbols: Iterable[str] | None = None,
    corporate_actions: Sequence[CorporateAction] | None = None,
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

    if corporate_actions is not None:
        ca_failures, ca_notices = compare_corporate_actions(corporate_actions, calendar)
        report.failures.extend(ca_failures)
        report.notices.extend(ca_notices)
        report.corporate_actions_checked = len(corporate_actions)
    return report


def render(report: Report) -> str:
    lines = [
        "split-calendar coverage check",
        f"  store        : {report.data_dir}",
        f"  calendar     : {report.calendar_path}",
        f"  thresholds   : jump>={report.jump_threshold:.0%}, watch>={report.watch_threshold:.0%}",
    ]
    if report.corporate_actions_checked:
        lines.append(
            f"  corp actions : {report.corporate_actions_checked} provider event(s) cross-checked"
        )
    lines.append("")
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
        if finding.kind == "calendar_symbol_missing" or finding.kind in CA_KINDS_FAILING:
            lines.append(
                f"[FAIL] {finding.kind} {finding.symbol} {finding.session}: {finding.detail}"
            )
    for notice in report.notices:
        lines.append(f"[info] {notice}")
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
    parser.add_argument(
        "--corporate-actions",
        type=Path,
        default=None,
        dest="corporate_actions_file",
        help=(
            "cross-check a recorded corporate-actions snapshot (JSON, per-symbol "
            "shape or raw Alpaca response) against the calendar; missing events fail"
        ),
    )
    parser.add_argument(
        "--corporate-actions-live",
        action="store_true",
        help=(
            "fetch splits from Alpaca corporate-actions for every store/calendar "
            "symbol and cross-check them against the calendar (needs credentials)"
        ),
    )
    parser.add_argument("--ca-start", default=DEFAULT_CA_START)
    parser.add_argument("--ca-end", default=date.today().isoformat())
    args = parser.parse_args(argv)

    corporate_actions: list[CorporateAction] | None = None
    if args.corporate_actions_file is not None and args.corporate_actions_live:
        print(
            "cannot run: --corporate-actions and --corporate-actions-live are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if args.corporate_actions_file is not None:
        try:
            payload = json.loads(args.corporate_actions_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"cannot run: cannot read corporate actions snapshot "
                f"{args.corporate_actions_file}: {exc}",
                file=sys.stderr,
            )
            return 2
        corporate_actions = normalize_corporate_actions(payload)
    elif args.corporate_actions_live:
        key_id, secret_key = resolve_alpaca_credentials()
        if not key_id or not secret_key:
            print(
                "cannot run: --corporate-actions-live needs Alpaca credentials "
                "(env YOWAYOWA_ALPACA_KEY_ID/YOWAYOWA_ALPACA_SECRET_KEY, repo .env, "
                "/root/.secrets/alpaca_paper.env or /root/.hermes/.env)",
                file=sys.stderr,
            )
            return 2
        try:
            calendar_keys = set(load_calendar(args.calendar))
        except (OSError, ValueError) as exc:
            print(f"cannot run: {exc}", file=sys.stderr)
            return 2
        store_root = args.data_dir / "stock-ohlcv"
        store_keys = (
            {path.parent.name.upper() for path in store_root.glob("*/ohlcv.jsonl")}
            if store_root.is_dir()
            else set()
        )
        try:
            corporate_actions = fetch_corporate_actions(
                calendar_keys | store_keys,
                start=args.ca_start,
                end=args.ca_end,
                key_id=key_id,
                secret_key=secret_key,
            )
        except (OSError, ValueError) as exc:
            print(f"cannot run: corporate-actions fetch failed: {exc}", file=sys.stderr)
            return 2

    try:
        report = build_report(
            data_dir=args.data_dir,
            calendar_path=args.calendar,
            jump_threshold=args.jump_threshold,
            watch_threshold=args.watch_threshold,
            symbols=args.symbols,
            corporate_actions=corporate_actions,
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
            "notices": report.notices,
            "corporate_actions_checked": report.corporate_actions_checked,
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
