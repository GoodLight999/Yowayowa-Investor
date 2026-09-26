"""CLI surface for US stock daily-OHLCV acquisition and display (P4-F).

Two flat top-level commands wired from ``cli_entry`` (matching the crypto
operator workflow):

- ``stock-fetch``: pull daily OHLCV (default AAPL,MSFT,NVDA) from the Alpaca
  Market Data API and persist to the JSONL store. Errors are isolated per
  symbol and never abort the remaining symbols.
- ``stock-ohlcv``: show persisted rows (per-source separated, no merging).
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.stock_acquisition import StockOhlcvStore, default_store, fetch_stock_ohlcv
from yowayowa.stock_models import normalize_stock_symbol

DEFAULT_SYMBOLS = ("AAPL", "MSFT", "NVDA")


def _providers() -> dict[str, Any]:
    from yowayowa.providers.alpaca import AlpacaMarketDataProvider

    settings = get_settings()
    return {
        "alpaca": AlpacaMarketDataProvider(settings),
    }


def stock_fetch(
    symbols: list[str] = typer.Argument(None),
    days: int = typer.Option(30, min=5, max=3650),
) -> None:
    """Fetch daily OHLCV (default AAPL,MSFT,NVDA) from Alpaca and persist to the JSONL store."""

    normalized: list[str] = []
    for raw in symbols or list(DEFAULT_SYMBOLS):
        try:
            normalized.append(normalize_stock_symbol(raw))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    summary = fetch_stock_ohlcv(_providers(), default_store(), normalized, days=days)
    total = 0
    for symbol, per_provider in summary.items():
        for provider_name, outcome in per_provider.items():
            error = outcome.get("error")
            if error:
                print(f"[yellow]{symbol}/{provider_name}: {error}[/yellow]")
            else:
                total += int(outcome.get("persisted", 0))
                print(f"{symbol}/{provider_name}: persisted {outcome['persisted']} rows")
    print(f"Store: {default_store().root}")
    print(f"+{total} rows")


def stock_ohlcv(
    symbol: str = typer.Argument(...),
    provider: str | None = typer.Option(None, help="Filter by provider: alpaca"),
    limit: int = typer.Option(30, min=1, max=250),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show persisted daily OHLCV rows for one symbol (per-source separated)."""

    try:
        normalized = normalize_stock_symbol(symbol)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    store: StockOhlcvStore = default_store()
    rows = store.read(normalized, provider=provider, limit=limit)
    if not rows:
        print(f"No persisted stock OHLCV rows for {normalized}")
        raise typer.Exit(code=1)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    table = Table(
        "Date",
        "Provider",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "VWAP",
        "Trades",
        "CCY",
    )
    for row in rows:
        table.add_row(
            str(row.get("as_of"))[:10],
            str(row.get("provider")),
            f"{row['open']:.6g}",
            f"{row['high']:.6g}",
            f"{row['low']:.6g}",
            f"{row['close']:.6g}",
            "—" if row.get("volume") is None else f"{row['volume']:.6g}",
            "—" if row.get("vwap") is None else f"{row['vwap']:.6g}",
            "—" if row.get("trade_count") is None else str(row["trade_count"]),
            str(row.get("currency")),
        )
    print(table)
