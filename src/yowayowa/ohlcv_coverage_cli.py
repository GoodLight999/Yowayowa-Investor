"""CLI entrypoint for read-only stock and crypto OHLCV coverage audits."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer
from rich import print
from rich.table import Table

from yowayowa.ohlcv_coverage import audit_ohlcv


def _date_option(raw: str | None, *, default: date, option: str) -> date:
    if raw is None:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"{option} must use YYYY-MM-DD") from exc


def ohlcv_audit(
    store: str = typer.Option("all", help="all, stock, or crypto"),
    start: str | None = typer.Option(None, help="Inclusive start date (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, help="Inclusive end date (YYYY-MM-DD)"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Report persisted OHLCV coverage, duplicate bars, and missing sessions."""

    if store not in {"all", "stock", "crypto"}:
        raise typer.BadParameter("store must be all, stock, or crypto")
    roots = {
        "stock": Path("./data/stock-ohlcv"),
        "crypto": Path("./data/crypto-ohlcv"),
    }
    latest_complete_utc_day = datetime.now(UTC).date() - timedelta(days=1)
    selected = roots if store == "all" else {store: roots[store]}
    expected = {
        "stock": [
            (symbol, "alpaca", "USD")
            for symbol in (
                "AAPL",
                "MSFT",
                "NVDA",
                "GOOGL",
                "AMZN",
                "META",
                "TSLA",
                "JNJ",
                "JPM",
                "PG",
                "XOM",
                "SPY",
                "QQQ",
                "IWM",
            )
        ],
        "crypto": [(symbol, "binance", "USDT") for symbol in ("BTC", "ETH")],
    }
    reports = {
        name: audit_ohlcv(
            root,
            requested_start=_date_option(
                start,
                default=date(2023, 1, 1) if name == "crypto" else date(2020, 1, 1),
                option="--start",
            ),
            requested_end=_date_option(
                end,
                default=(
                    latest_complete_utc_day
                    if name == "crypto"
                    else latest_complete_utc_day - timedelta(days=1)
                ),
                option="--end",
            ),
            expected_series=expected[name],
        )
        for name, root in selected.items()
    }
    if as_json:
        typer.echo(json.dumps(reports, ensure_ascii=False, indent=2))
        return
    for name, report in reports.items():
        print(f"[bold]{name}[/bold]: {report['file_count']} files, {report['bytes']} bytes")
        table = Table(
            "Symbol",
            "Provider",
            "CCY",
            "Start",
            "End",
            "Bars",
            "Missing",
            "Non-session",
            "Dup",
            "Bad",
        )
        for row in report["series"]:
            table.add_row(
                row["symbol"],
                row["provider"],
                row["currency"],
                row["coverage_start"],
                row["coverage_end"],
                str(row["bars_in_requested_window"]),
                str(row["missing_expected_dates"]),
                ",".join(row["non_session_dates"]) or "0",
                str(row["duplicate_dates"]),
                str(row["invalid_rows"]),
            )
        print(table)
        for note in report["calendar_notes"]:
            print(f"Note: {note}")
