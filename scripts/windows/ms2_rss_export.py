"""MS2 RSS -> JSONL batch exporter for the operator Windows Node.

Runs on the Windows machine where MARKET SPEED II (`MARKET SPEED II`) is
installed together with Microsoft Excel and the Rakuten Securities RSS add-in.
The exporter reads live quote cells through the RssMarket_* plugin functions
(ODBC-free, pure COM via pywin32) and appends one JSON line per instrument to
``F:\\yowayowa\\ms2\\ms2_quotes_YYYYMMDD.jsonl``.

It must NEVER be pointed at the VPS: the only network hop for the data is the
manual (or Task-Scheduler-scheduled) `scp` copy of the produced JSONL file —
see docs/MS2_RSS.md on the repo for the full operator procedure.

Usage (from a native Windows Python 3.11+ shell with the Rakuten RSS add-in):

    python ms2_rss_export.py --symbols 7203 6758 --out-dir F:\\yowayowa\\ms2

Missing four-price fields are exported as ``null`` — the exporter never
zero-fills a gap because a zero bid / volume would corrupt price arithmetic
downstream.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from datetime import UTC
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import win32com.client  # type: ignore[import-not-found]
except ImportError:  # allow --check / --dry-run on non-Windows dev shells
    win32com = None

JST = ZoneInfo("Asia/Tokyo")

# Default column layout of the MARKET SPEED II RSS quote template sheet
# (row 1 = header captions, column A = instrument code, B..G = four-price set).
QUOTES_HEADER = ("bid", "ask", "high", "low", "last", "volume")


def fetch_symbol_quotes(
    symbol: str,
    sheet: Any | None = None,
    *,
    base_row: int = 2,
    base_column: int = 1,
) -> dict[str, Any]:
    """Read one instrument block off an MS2 RSS template worksheet.

    Layout (start-of-block at ``base_row``/``base_column``, 1-indexed):

        <symbol cell>  | 現在値 | 前日比 | 高値 | 安値 | 売気配数量 |
        <symbol cell2> | 売買約定値合計(出来高) | 買気配 | ...

    The two RssMarket row-header captions below are matched with
    ``startswith`` because MS2 appends symbol-specific tail text to them.
    """
    if sheet is None:
        return _fetch_via_plugin_formula(symbol)

    def _cell(row: int, col: int) -> Any:
        return sheet.Cells(base_row + row - 1, base_column + col - 1).Value

    caption = _cell(2, 1)  # e.g. "7203 日産自動車(株) 現在値"
    if not _matches_symbol_cell(caption, symbol):
        raise ValueError(
            f"MS2 sheet cell at {(base_row, base_column)} is not for {symbol}: {caption!r}"
        )

    row2_captions = [
        str(_cell(3, 1) or ""),
        str(_cell(4, 1) or ""),
        str(_cell(5, 1) or ""),
        str(_cell(6, 1) or ""),
        str(_cell(7, 1) or ""),
        str(_cell(8, 1) or ""),
    ]
    # Zeros never appear as sentinels: an MS2 empty cell becomes None.
    raw_values = [_cell(r, 2) for r in range(3, 9)]
    by_caption = dict(zip(row2_captions, raw_values, strict=False))

    quotes: dict[str, Any] = {}
    quotes["bid"] = _num(by_caption.get(_K_BID))
    quotes["ask"] = _num(by_caption.get(_K_ASK))
    quotes["high"] = _num(_cell(4, 2))
    quotes["low"] = _num(_cell(5, 2))
    quotes["last"] = _num(by_caption.get(_K_LAST))
    quotes["volume"] = _num(by_caption.get(_K_VOLUME))
    return {"symbol": symbol, "quotes": quotes, "row_start": base_row}


_K_LAST = "現在値"
_K_ASK = "買気配"
_K_BID = "売気配"
_K_VOLUME = "出来高"


def _matches_symbol_cell(caption: Any, symbol: str) -> bool:
    return bool(caption) and str(caption).startswith(str(symbol))


def _num(value: Any) -> float | None:
    """MS2/Excel numbers arrive as float or 2-tuple (link-formula) or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) or None
    if isinstance(value, tuple):
        return _num(value[0])
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text) or None
    except ValueError:
        return None


def _fetch_via_plugin_formula(symbol: str) -> dict[str, Any]:
    """Fallback path: read the RssMarket_* functions via COM automation.

    MARKET SPEED II registers a COM server that exposes the RSS functions to
    any OLE-automation host (not just Excel). When a worksheet object is not
    passed in, we drive the MS2 plugin through the same RssMarket_* functions
    the Excel add-in uses. Requires MARKET SPEED II to be logged in and
    running on the same Windows machine.
    """
    if win32com is None:
        raise RuntimeError(
            "pywin32 is required on Windows to drive the MARKET SPEED II RSS "
            "COM automation server; run: pip install pywin32"
        )
    ms2 = win32com.client.Dispatch("Rakuten.RSS")  # MS2 RSS ProgID
    fnames = {
        "bid": _K_BID,
        "ask": _K_ASK,
        "high": "高値",
        "low": "安値",
        "last": _K_LAST,
        "volume": _K_VOLUME,
    }
    quotes: dict[str, Any] = {}
    for field, caption in fnames.items():
        quotes[field] = _num(ms2.RssMarket(str(symbol), caption))
    return {"symbol": symbol, "quotes": quotes}


def build_record(
    symbol: str,
    name: str,
    quotes: dict[str, Any],
    *,
    as_of: _dt.datetime,
    retrieved_at: _dt.datetime,
) -> dict[str, Any]:
    """One JSON-serializable line, exactly the agreed transport schema."""

    return {
        "symbol": symbol,
        "name": name,
        "quotes": {field: quotes.get(field) for field in QUOTES_HEADER},
        "as_of": _iso_jst(as_of),
        "retrieved_at": retrieved_at.astimezone(UTC).isoformat(),
    }


def _iso_jst(ts: _dt.datetime) -> str:
    return ts.astimezone(JST).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated instrument codes, e.g. --symbols 7203 6758",
    )
    parser.add_argument(
        "--out-dir",
        default=r"F:\\yowayowa\\ms2",
        help="SCP staging directory (default F:\\yowayowa\\ms2)",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        metavar="SYMBOL=NAME",
        help="Optional display names, e.g. --name 7203=Toyota",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print JSONL, write nothing")
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.replace(",", " ").split() if s.strip()]
    if not symbols:
        parser.error("--symbols contained no usable codes")
    names = {}
    for entry in args.name:
        key, _, value = entry.partition("=")
        names[key.strip()] = value.strip() or key.strip()

    now_jst = _dt.datetime.now(JST)
    now_utc = _dt.datetime.now(UTC)
    records = [
        build_record(
            symbol,
            names.get(symbol, symbol),
            _fetch_via_plugin_formula(symbol) if not args.dry_run else {"quotes": {}},
            as_of=now_jst,
            retrieved_at=now_utc,
        )
        for symbol in symbols
    ]
    lines = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)

    if args.dry_run:
        print(lines)
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"ms2_quotes_{now_jst:%Y%m%d}.jsonl"
    with out_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(lines + "\n")
    print(f"appended {len(records)} record(s) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
