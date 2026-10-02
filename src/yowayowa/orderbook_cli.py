from __future__ import annotations

from typing import Annotated, Literal

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from yowayowa.orderbook_models import OrderbookSnapshot
from yowayowa.services.orderbook_service import OrderbookService

app = typer.Typer(
    help="Level-2 Orderbook inspection, microstructure metrics, and execution impact analysis."
)
_service = OrderbookService()


@app.command("show")
def show_orderbook(
    symbol: Annotated[str, typer.Argument(help="Instrument code or symbol (e.g. 7203, BTC/USD)")],
    depth: Annotated[
        int, typer.Option("--depth", "-d", help="Number of price levels to display")
    ] = 10,
) -> None:
    """Display visual market depth ladder (Level-2 Orderbook) with bids and asks."""
    snapshot: OrderbookSnapshot = _service.get_snapshot(symbol)
    m = snapshot.metrics

    ofi_str = f"{m.order_flow_imbalance:+.2%}" if m.order_flow_imbalance is not None else "—"
    spread_str = f"{m.spread:,.2f}" if m.spread is not None else "—"
    spread_bps_str = f"{m.spread_bps:.1f} bps" if m.spread_bps is not None else "—"
    mid_str = f"{m.mid_price:,.2f}" if m.mid_price is not None else "—"
    hft_str = m.hft_activity_indicator

    summary_text = (
        f"[bold cyan]{snapshot.symbol}[/bold cyan] | "
        f"Mid: [bold]{mid_str}[/bold] | "
        f"Spread: [yellow]{spread_str}[/yellow] ({spread_bps_str}) | "
        f"Imbalance (OFI): [magenta]{ofi_str}[/magenta] | "
        f"HFT Indicator: [bold red]{hft_str}[/bold red]\n"
        f"[dim]As of: {snapshot.as_of.isoformat()} ({snapshot.provenance.source})[/dim]"
    )
    rprint(Panel(summary_text, title="Orderbook Market Depth", border_style="blue"))

    title = f"Depth of Market (Top {depth} Levels)"
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Bid Count", justify="right", style="dim", width=10)
    table.add_column("Bid Size", justify="right", style="green", width=14)
    table.add_column("Bid Price", justify="right", style="bold green", width=12)
    table.add_column("Ask Price", justify="left", style="bold red", width=12)
    table.add_column("Ask Size", justify="left", style="red", width=14)
    table.add_column("Ask Count", justify="left", style="dim", width=10)

    max_rows = min(depth, max(len(snapshot.bids), len(snapshot.asks)))
    for i in range(max_rows):
        b = snapshot.bids[i] if i < len(snapshot.bids) else None
        a = snapshot.asks[i] if i < len(snapshot.asks) else None

        b_cnt = str(b.order_count) if (b and b.order_count is not None) else ""
        b_sz = f"{b.size:,.2f}" if b else ""
        b_px = f"{b.price:,.2f}" if b else ""

        a_px = f"{a.price:,.2f}" if a else ""
        a_sz = f"{a.size:,.2f}" if a else ""
        a_cnt = str(a.order_count) if (a and a.order_count is not None) else ""

        table.add_row(b_cnt, b_sz, b_px, a_px, a_sz, a_cnt)

    rprint(table)


@app.command("metrics")
def orderbook_metrics(
    symbol: Annotated[str, typer.Argument(help="Instrument code or symbol (e.g. 7203, BTC/USD)")],
) -> None:
    """Display microstructure metrics: spread, order flow imbalance, and micro-price."""
    snapshot = _service.get_snapshot(symbol)
    m = snapshot.metrics

    table = Table(title=f"Microstructure Metrics — {snapshot.symbol}", show_header=False)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold")

    micro_str = f"{m.micro_price:,.2f}" if m.micro_price is not None else "—"
    spread_bps_str = f"{m.spread_bps:.2f} bps" if m.spread_bps is not None else "—"

    table.add_row("Best Bid", f"{m.best_bid:,.2f}" if m.best_bid is not None else "—")
    table.add_row("Best Ask", f"{m.best_ask:,.2f}" if m.best_ask is not None else "—")
    table.add_row("Mid Price", f"{m.mid_price:,.2f}" if m.mid_price is not None else "—")
    table.add_row("Micro Price (Volume-Weighted)", micro_str)
    table.add_row("Spread", f"{m.spread:,.2f}" if m.spread is not None else "—")
    table.add_row("Spread (bps)", spread_bps_str)
    table.add_row("Bid Depth Total", f"{m.bid_depth_total:,.2f}")
    table.add_row("Ask Depth Total", f"{m.ask_depth_total:,.2f}")
    table.add_row("Total Depth", f"{m.total_depth:,.2f}")
    table.add_row(
        "Order Flow Imbalance (OFI)",
        f"{m.order_flow_imbalance:+.2%}" if m.order_flow_imbalance is not None else "—",
    )
    table.add_row("HFT Activity Indicator", f"[bold red]{m.hft_activity_indicator}[/bold red]")

    rprint(table)


@app.command("impact")
def orderbook_impact(
    symbol: Annotated[str, typer.Argument(help="Instrument code or symbol (e.g. 7203, BTC/USD)")],
    side: Annotated[str, typer.Option("--side", "-s", help="Order side: buy or sell")] = "buy",
    quantity: Annotated[float, typer.Option("--qty", "-q", help="Order size to simulate")] = 100.0,
) -> None:
    """Simulate market-order execution impact and slippage across the orderbook."""
    clean_side = side.strip().lower()
    if clean_side not in {"buy", "sell"}:
        rprint("[bold red]Error: side must be 'buy' or 'sell'[/bold red]")
        raise typer.Exit(code=1)

    side_literal: Literal["buy", "sell"] = "buy" if clean_side == "buy" else "sell"
    result = _service.estimate_impact(symbol, side=side_literal, quantity=quantity)

    table_title = f"Execution Impact Estimation — {result.symbol} ({result.side.upper()})"
    table = Table(title=table_title, show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value", style="bold")

    best_quote_str = f"{result.best_quote_price:,.2f}" if result.best_quote_price else "—"
    avg_price_str = f"{result.average_price:,.2f}" if result.average_price else "—"
    slippage_str = f"{result.slippage:,.2f}" if result.slippage else "—"
    slippage_bps_str = f"{result.slippage_bps:.1f} bps" if result.slippage_bps else "—"
    cost_str = f"{result.total_cost:,.2f}" if result.total_cost else "—"

    table.add_row("Requested Quantity", f"{result.requested_quantity:,.2f}")
    table.add_row("Fillable Quantity", f"{result.fillable_quantity:,.2f}")
    table.add_row(
        "Fully Filled",
        "[green]YES[/green]" if result.fully_filled else "[red]NO[/red]",
    )
    table.add_row("Best Quote Price", best_quote_str)
    table.add_row("Estimated Avg Fill Price", avg_price_str)
    table.add_row("Estimated Slippage", slippage_str)
    table.add_row("Estimated Slippage (bps)", slippage_bps_str)
    table.add_row("Estimated Total Cost", cost_str)
    table.add_row("Book Levels Swept", str(result.levels_swept))

    if result.warning:
        table.add_row("Warning", f"[bold red]{result.warning}[/bold red]")

    rprint(table)
