"""CLI surface for crypto daily-OHLCV acquisition and display (P4-E).

Two flat top-level commands wired from ``cli_entry`` (matching the
jpx/credit-margin operator workflow):

- ``crypto-fetch``: pull daily OHLCV (default BTC,ETH) from the CoinGecko
  and Binance public APIs and persist to the JSONL store. Source errors are
  isolated per provider and never abort the other source.
- ``crypto-ohlcv``: show persisted rows (per-source separated, no merging).
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.crypto_acquisition import CryptoOhlcvStore, default_store, fetch_crypto_ohlcv
from yowayowa.crypto_models import normalize_crypto_symbol

DEFAULT_SYMBOLS = ("BTC", "ETH")


def _providers() -> dict[str, Any]:
    from yowayowa.providers.binance import BinanceKlinesProvider
    from yowayowa.providers.coingecko import CoinGeckoOhlcProvider

    settings = get_settings()
    return {
        "coingecko": CoinGeckoOhlcProvider(settings),
        "binance": BinanceKlinesProvider(settings),
    }


def crypto_fetch(
    symbols: list[str] = typer.Argument(None),
    days: int = typer.Option(30, min=1, max=365),
) -> None:
    """Fetch daily OHLCV (default BTC,ETH) from both providers and persist to the JSONL store."""

    normalized: list[str] = []
    for raw in symbols or list(DEFAULT_SYMBOLS):
        try:
            normalized.append(normalize_crypto_symbol(raw))
        except Exception as exc:
            raise typer.BadParameter(str(exc)) from exc
    summary = fetch_crypto_ohlcv(_providers(), default_store(), normalized, days=days)
    for symbol, per_provider in summary.items():
        for provider_name, outcome in per_provider.items():
            error = outcome.get("error")
            if error:
                print(f"[yellow]{symbol}/{provider_name}: {error}[/yellow]")
            else:
                print(f"{symbol}/{provider_name}: persisted {outcome['persisted']} rows")
    print(f"Store: {default_store().root}")


def crypto_ohlcv(
    symbol: str = typer.Argument(...),
    provider: str | None = typer.Option(None, help="Filter by provider: coingecko or binance"),
    limit: int = typer.Option(30, min=1, max=250),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show persisted daily OHLCV rows for one symbol (per-source separated)."""

    try:
        normalized = normalize_crypto_symbol(symbol)
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    store: CryptoOhlcvStore = default_store()
    rows = store.read(normalized, provider=provider, limit=limit)
    if not rows:
        print(f"No persisted crypto OHLCV rows for {normalized}")
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
        "Quote vol",
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
            "—" if row.get("quote_volume") is None else f"{row['quote_volume']:.6g}",
            str(row.get("currency")),
        )
    print(table)
