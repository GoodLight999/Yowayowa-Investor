from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Inspect the official U.S. Treasury par yield curve.")


@app.command("curve")
def curve(
    year: int | None = typer.Option(None, min=1990, max=2100),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params = {} if year is None else {"year": year}
    with _client(base_url, token) as client:
        payload = client.get("/v1/rates/treasury/curve", params=params).raise_for_status().json()

    latest = payload["latest"]
    print(f"[bold]U.S. Treasury par yield curve[/bold] · {latest['date']}")
    table = Table("Maturity", "Yield")
    for point in latest["points"]:
        table.add_row(point["maturity"], f"{point['yield_percent']:.3f}%")
    print(table)
    print(
        f"10Y-2Y: {latest['spread_10y_2y'] if latest['spread_10y_2y'] is not None else '—'} pp · "
        f"10Y-3M: {latest['spread_10y_3m'] if latest['spread_10y_3m'] is not None else '—'} pp"
    )
