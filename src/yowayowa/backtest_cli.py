from __future__ import annotations

from datetime import date
from typing import Any, Literal, cast

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(no_args_is_help=True, help="Run historical portfolio backtests.")


def _run_request(
    strategy: str,
    start: date,
    end: date,
    base_url: str,
    token: str | None,
    commission_bps: float,
    slippage_bps: float,
    provider: str,
) -> dict[str, Any]:
    payload = {
        "strategy_id": strategy,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
        "provider": provider,
    }
    with _client(base_url, token) as client:
        response = client.post("/v1/backtest/run", json=payload)
        if response.status_code >= 400:
            raise typer.BadParameter(f"HTTP {response.status_code}: {response.text[:300]}")
        return cast(dict[str, Any], response.json())


@app.command("run")
def run(
    strategy: str = typer.Option(..., "--strategy"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    commission_bps: float = typer.Option(10.0, min=0, max=1000),
    slippage_bps: float = typer.Option(5.0, min=0, max=1000),
    provider: Literal["alpaca"] = typer.Option("alpaca", hidden=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    """Run a named strategy with persisted OHLCV through the API."""
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter("Dates must use YYYY-MM-DD format") from exc
    if start_date >= end_date:
        raise typer.BadParameter("--start must be before --end")
    payload = _run_request(
        strategy, start_date, end_date, base_url, token, commission_bps, slippage_bps, provider
    )
    print(f"[bold]{payload['strategy']['name']}[/bold] — {payload['status']}")
    table = Table("Metric", "Value", "Samples", "Status")
    for name, metric in payload["metrics"].items():
        if isinstance(metric, dict) and "status" in metric:
            value = "—" if metric["value"] is None else f"{metric['value']:.6g}"
            table.add_row(name, value, str(metric["sample_count"]), metric["status"])
    print(table)
    print(f"Trades: {len(payload['trades'])}; equity observations: {len(payload['equity_curve'])}")
    if payload.get("oos_metrics"):
        print(
            f"OOS start: {payload.get('oos_start')}; "
            f"purged sessions: {payload.get('purged_sessions')}"
        )
        for name, metric in payload["oos_metrics"].items():
            if isinstance(metric, dict) and metric.get("status") == "available":
                print(f"OOS {name}: {metric['value']:.6g}")
    if payload.get("in_sample_metrics"):
        print("In-sample metrics are available under in_sample_metrics in the API response.")
    for warning in payload.get("warnings", []):
        print(f"[yellow]Warning: {warning}[/yellow]")
