#!/usr/bin/env python3
"""Build a point-in-time snapshot for the Japan value/quality research mode.

Data sources (all public, no paid subscription):
  * Tokyo Stock Exchange listed-issue list (JPX, official public) -- universe,
    market segment (Prime/Standard) and 33-sector classification.
  * Yahoo Finance via ``yfinance`` (personal-use licence class) -- daily closes
    (split-adjusted, price only) and annual financial statements.

The snapshot is written under ``data/jp-value-quality/`` (git-ignored, large):

  universe.json     -- filtered universe + provenance + criteria
  prices.jsonl      -- one line per symbol: daily bars
  fundamentals.jsonl-- one line per symbol: annual statements (FY end labelled)
  benchmark.jsonl   -- TOPIX-proxy ETF and Nikkei 225 closes
  manifest.json     -- coverage / counts / provenance roll-up

The script is resumable: symbols already present in the jsonl files are skipped,
so a throttled or interrupted crawl can be restarted without refetching.

Point-in-time discipline is *not* applied here: statements carry their fiscal
year end only. The consumer derives "disclosed at" as FY-end + a documented
lag (see ``services/jp_value_quality.DISCLOSURE_LAG_DAYS``).

Usage:
    python scripts/research/fetch_jp_value_quality_snapshot.py --universe topix500
    python scripts/research/fetch_jp_value_quality_snapshot.py --universe all
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

JPX_INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
JPX_LISTED_PAGE_LICENSE = "official_public"
YAHOO_LICENSE = "personal_only"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

PRIME_STANDARD_DOMESTIC = ("プライム（内国株式）", "スタンダード（内国株式）")
FINANCIAL_SECTORS = (
    "銀行業",
    "証券、商品先物取引業",
    "保険業",
    "その他金融業",
)
TOPIX_LARGE_TIERS = ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400")

BENCHMARKS = {
    "1306.T": "TOPIX-linked ETF (NEXT FUNDS TOPIX), price-only proxy for TOPIX",
    "^N225": "Nikkei 225 price index",
}

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _log(message: str) -> None:
    print(f"[{datetime.now(UTC):%H:%M:%S}] {message}", flush=True)


def _http_get(url: str, *, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"([A-Z]+)", cell_ref)
    assert letters is not None
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _read_xlsx_rows(path: Path) -> list[list[object]]:
    """Minimal .xlsx reader (shared strings + first worksheet), dependency-free."""

    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{_XLSX_NS}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{_XLSX_NS}t")))
        sheet_name = next(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        sheet = ElementTree.fromstring(archive.read(sheet_name))
    rows: list[list[object]] = []
    for row in sheet.iter(f"{_XLSX_NS}row"):
        values: dict[int, object] = {}
        for cell in row.findall(f"{_XLSX_NS}c"):
            ref = cell.get("r") or ""
            kind = cell.get("t")
            value_node = cell.find(f"{_XLSX_NS}v")
            if kind == "inlineStr":
                text = "".join(
                    node.text or "" for node in cell.iter(f"{_XLSX_NS}t")
                )
                values[_column_index(ref)] = text
                continue
            if value_node is None or value_node.text is None:
                continue
            raw = value_node.text
            if kind == "s":
                values[_column_index(ref)] = shared[int(raw)]
            elif kind == "str":
                values[_column_index(ref)] = raw
            else:
                try:
                    number = float(raw)
                except ValueError:
                    values[_column_index(ref)] = raw
                else:
                    values[_column_index(ref)] = int(number) if number.is_integer() else number
        if values:
            width = max(values) + 1
            rows.append([values.get(index) for index in range(width)])
    return rows


def fetch_universe(out_dir: Path) -> dict[str, object]:
    """Download the JPX listed-issue list and build the filtered universe."""

    page = _http_get(JPX_INDEX_URL).decode("utf-8", "replace")
    match = re.search(r'href="([^"]*data_j\.xlsx)"', page)
    if match is None:
        raise RuntimeError("JPX listed-issue workbook link not found on the index page")
    link = match.group(1)
    workbook_url = link if link.startswith("http") else f"https://www.jpx.co.jp{link}"
    retrieved_at = datetime.now(UTC).isoformat()
    workbook_path = out_dir / "jpx_listed_issues.xlsx"
    workbook_path.write_bytes(_http_get(workbook_url, timeout=120))
    _log(f"downloaded {workbook_url} ({workbook_path.stat().st_size} bytes)")

    rows = _read_xlsx_rows(workbook_path)
    header = [str(cell) if cell is not None else "" for cell in rows[0]]
    index = {name: position for position, name in enumerate(header)}
    required = ("日付", "コード", "銘柄名", "市場・商品区分", "33業種区分", "規模区分")
    missing = [name for name in required if name not in index]
    if missing:
        raise RuntimeError(f"unexpected JPX workbook header, missing columns: {missing}")

    seen: set[str] = set()
    all_symbols: list[dict[str, object]] = []
    for row in rows[1:]:
        def cell(name: str) -> object:
            position = index[name]
            return row[position] if position < len(row) else None

        market = str(cell("市場・商品区分") or "")
        if market not in PRIME_STANDARD_DOMESTIC:
            continue
        sector = str(cell("33業種区分") or "")
        if sector in FINANCIAL_SECTORS or sector in {"-", ""}:
            continue
        code = str(cell("コード") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        size = str(cell("規模区分") or "")
        all_symbols.append(
            {
                "code": code,
                "symbol": f"{code}.T",
                "name": str(cell("銘柄名") or ""),
                "market": market,
                "sector33": sector,
                "topix_size": size,
                "list_date": str(cell("日付") or ""),
            }
        )

    topix500 = [item for item in all_symbols if item["topix_size"] in TOPIX_LARGE_TIERS]
    payload = {
        "generated_at": retrieved_at,
        "source": {
            "label": "JPX 東証上場銘柄一覧",
            "url": workbook_url,
            "page_url": JPX_INDEX_URL,
            "license_class": JPX_LISTED_PAGE_LICENSE,
            "retrieved_at": retrieved_at,
        },
        "criteria": {
            "market": list(PRIME_STANDARD_DOMESTIC),
            "excluded_sectors33": list(FINANCIAL_SECTORS),
            "tiers": {
                "all_non_financial": "全銘柄（プライム・スタンダード、金融4業種除く）",
                "topix500": "上記のうち TOPIX規模区分 Core30/Large70/Mid400",
            },
            "duplicate_codes": "removed (first occurrence kept)",
        },
        "counts": {"all_non_financial": len(all_symbols), "topix500": len(topix500)},
        "symbols": all_symbols,
    }
    (out_dir / "universe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _log(
        f"universe: all_non_financial={len(all_symbols)} topix500={len(topix500)} "
        f"-> {out_dir / 'universe.json'}"
    )
    return payload


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _existing_symbols(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(str(json.loads(line)["symbol"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pick(frame, names: tuple[str, ...], column) -> float | None:
    if frame is None or column not in frame.columns:
        return None
    for name in names:
        if name in frame.index:
            value = _finite(frame.loc[name, column])
            if value is not None:
                return value
    return None


def fetch_prices(symbols: list[dict[str, object]], out_dir: Path, *, pace: float) -> None:
    import yfinance as yf

    path = out_dir / "prices.jsonl"
    done = _existing_symbols(path)
    pending = [item for item in symbols if item["symbol"] not in done]
    _log(f"prices: {len(done)} cached, {len(pending)} to fetch")
    failures = 0
    for position, item in enumerate(pending, start=1):
        symbol = str(item["symbol"])
        retrieved_at = datetime.now(UTC).isoformat()
        try:
            frame = yf.Ticker(symbol).history(period="10y", interval="1d", auto_adjust=False)
        except Exception as exc:  # noqa: BLE001 - journal the failure, keep crawling
            failures += 1
            _append_jsonl(
                path,
                {
                    "symbol": symbol,
                    "provider": "yahoo/yfinance",
                    "license_class": YAHOO_LICENSE,
                    "source_url": f"https://finance.yahoo.com/quote/{symbol}/history",
                    "retrieved_at": retrieved_at,
                    "error": f"{type(exc).__name__}: {exc}",
                    "bars": [],
                },
            )
            _log(f"prices {position}/{len(pending)} {symbol} ERROR {exc}")
            time.sleep(max(pace, 1.0))
            continue
        bars: list[list[object]] = []
        if not frame.empty:
            for index, row in frame.iterrows():
                close = _finite(row.get("Close"))
                if close is None or close <= 0:
                    continue
                opened = _finite(row.get("Open"))
                day = index.date().isoformat() if hasattr(index, "date") else str(index)[:10]
                bars.append([day, close if opened is None else opened, close])
        _append_jsonl(
            path,
            {
                "symbol": symbol,
                "provider": "yahoo/yfinance",
                "license_class": YAHOO_LICENSE,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/history",
                "retrieved_at": retrieved_at,
                "currency": "JPY",
                "bars": bars,
            },
        )
        if position % 25 == 0 or not bars:
            _log(f"prices {position}/{len(pending)} {symbol} bars={len(bars)} failures={failures}")
        time.sleep(pace)
    _log(f"prices done: failures={failures}")


def fetch_fundamentals(
    symbols: list[dict[str, object]], out_dir: Path, *, pace: float
) -> None:
    import yfinance as yf

    path = out_dir / "fundamentals.jsonl"
    done = _existing_symbols(path)
    pending = [item for item in symbols if item["symbol"] not in done]
    _log(f"fundamentals: {len(done)} cached, {len(pending)} to fetch")
    for position, item in enumerate(pending, start=1):
        symbol = str(item["symbol"])
        retrieved_at = datetime.now(UTC).isoformat()
        ticker = yf.Ticker(symbol)
        income = balance = None
        error = None
        try:
            income = ticker.financials
            balance = ticker.balance_sheet
        except Exception as exc:  # noqa: BLE001 - journal and continue
            error = f"{type(exc).__name__}: {exc}"
        periods: dict[str, dict[str, object]] = {}
        columns = []
        for frame in (income, balance):
            if frame is not None and not frame.empty:
                columns.extend(list(frame.columns))
        for column in sorted(set(columns)):
            fy_end = (
                column.date().isoformat() if hasattr(column, "date") else str(column)[:10]
            )
            record = {
                "fy_end": fy_end,
                "revenue": _pick(income, ("Total Revenue", "Operating Revenue"), column),
                "operating_income": _pick(income, ("Operating Income",), column),
                "net_income": _pick(
                    income,
                    ("Net Income Common Stockholders", "Net Income", "Net Income Including Noncontrolling Interests"),
                    column,
                ),
                "interest_expense": _pick(
                    income,
                    ("Interest Expense", "Interest Expense Non Operating", "Interest Expense For Capitalized Interest"),
                    column,
                ),
                "eps_diluted": _pick(income, ("Diluted EPS", "Basic EPS"), column),
                "equity": _pick(
                    balance,
                    ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
                    column,
                ),
                "assets": _pick(balance, ("Total Assets",), column),
                "current_assets": _pick(balance, ("Current Assets",), column),
                "current_liabilities": _pick(balance, ("Current Liabilities",), column),
                "liabilities": _pick(
                    balance,
                    ("Total Liabilities Net Minority Interest", "Total Liabilities"),
                    column,
                ),
                "shares_outstanding": _pick(
                    balance, ("Ordinary Shares Number", "Share Issued"), column
                ),
                "currency": "JPY",
            }
            periods[fy_end] = record
        _append_jsonl(
            path,
            {
                "symbol": symbol,
                "provider": "yahoo/yfinance",
                "license_class": YAHOO_LICENSE,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/analysis",
                "retrieved_at": retrieved_at,
                "error": error,
                "annual": [periods[key] for key in sorted(periods)],
            },
        )
        if position % 25 == 0:
            _log(f"fundamentals {position}/{len(pending)} {symbol} periods={len(periods)}")
        time.sleep(pace)


def fetch_benchmarks(out_dir: Path) -> None:
    import yfinance as yf

    path = out_dir / "benchmark.jsonl"
    done = _existing_symbols(path)
    for symbol, note in BENCHMARKS.items():
        if symbol in done:
            continue
        retrieved_at = datetime.now(UTC).isoformat()
        frame = yf.Ticker(symbol).history(period="10y", interval="1d", auto_adjust=False)
        bars = []
        for index, row in frame.iterrows():
            close = _finite(row.get("Close"))
            if close is None or close <= 0:
                continue
            day = index.date().isoformat() if hasattr(index, "date") else str(index)[:10]
            bars.append([day, close])
        _append_jsonl(
            path,
            {
                "symbol": symbol,
                "note": note,
                "provider": "yahoo/yfinance",
                "license_class": YAHOO_LICENSE,
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/history",
                "retrieved_at": retrieved_at,
                "bars": bars,
            },
        )
        _log(f"benchmark {symbol} bars={len(bars)}")
        time.sleep(1.0)


def write_manifest(out_dir: Path) -> dict[str, object]:
    universe = json.loads((out_dir / "universe.json").read_text(encoding="utf-8"))

    def summarise(path: Path) -> dict[str, object]:
        total = errors = with_bars = 0
        first: str | None = None
        last: str | None = None
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    total += 1
                    record = json.loads(line)
                    if record.get("error"):
                        errors += 1
                    bars = record.get("bars") or []
                    if bars:
                        with_bars += 1
                        first = min(first, bars[0][0]) if first else bars[0][0]
                        last = max(last, bars[-1][0]) if last else bars[-1][0]
        return {
            "records": total,
            "with_data": with_bars,
            "errors": errors,
            "first_bar": first,
            "last_bar": last,
        }

    def summarise_fundamentals(path: Path) -> dict[str, object]:
        total = errors = 0
        periods = 0
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    total += 1
                    record = json.loads(line)
                    if record.get("error"):
                        errors += 1
                    periods += len(record.get("annual") or [])
        return {"records": total, "errors": errors, "annual_periods": periods}

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "universe": universe["counts"],
        "prices": summarise(out_dir / "prices.jsonl"),
        "fundamentals": summarise_fundamentals(out_dir / "fundamentals.jsonl"),
        "benchmark": summarise(out_dir / "benchmark.jsonl"),
        "provenance": {
            "jpx": universe["source"],
            "yahoo": {
                "label": "Yahoo Finance via yfinance (daily closes, annual statements)",
                "license_class": YAHOO_LICENSE,
                "note": (
                    "Personal-use classification: derived research artefacts are not "
                    "redistributed by this repository."
                ),
            },
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _log(f"manifest -> {out_dir / 'manifest.json'}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", choices=("all", "topix500"), default="topix500")
    parser.add_argument("--out", default=str(REPO / "data" / "jp-value-quality"))
    parser.add_argument("--phases", default="universe,prices,fundamentals,benchmark")
    parser.add_argument("--pace", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    phases = {item.strip() for item in args.phases.split(",") if item.strip()}

    universe_path = out_dir / "universe.json"
    if "universe" in phases or not universe_path.exists():
        payload = fetch_universe(out_dir)
    else:
        payload = json.loads(universe_path.read_text(encoding="utf-8"))

    symbols = list(payload["symbols"])  # type: ignore[arg-type]
    if args.universe == "topix500":
        symbols = [item for item in symbols if item["topix_size"] in TOPIX_LARGE_TIERS]
    symbols = sorted(symbols, key=lambda item: str(item["code"]))
    if args.limit:
        symbols = symbols[: args.limit]
    _log(f"target symbols: {len(symbols)} (universe={args.universe})")

    if "prices" in phases:
        fetch_prices(symbols, out_dir, pace=args.pace)
    if "fundamentals" in phases:
        fetch_fundamentals(symbols, out_dir, pace=args.pace)
    if "benchmark" in phases:
        fetch_benchmarks(out_dir)
    write_manifest(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
