from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Research official SEC Form 13F institutional holdings.")


@app.command("manager")
def manager(
    cik: str,
    quarters: int = typer.Option(2, min=1, max=8),
    limit: int = typer.Option(30, min=1, max=200),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/institutional/13f/{cik}",
                params={"quarters": quarters},
            )
            .raise_for_status()
            .json()
        )
    print(f"[bold]{payload['manager_name']}[/bold] · CIK {payload['cik']}")
    if not payload["filings"]:
        print("No 13F filing could be loaded.")
        return
    latest = payload["filings"][0]
    print(
        f"Report date {latest['report_date']} · filed {latest['filing_date']} · "
        f"reported value ${latest['total_value_usd']:,.0f}"
    )
    holdings = Table(
        "Issuer",
        "CUSIP",
        "Value",
        "Weight",
        "Shares / principal",
    )
    for item in latest["holdings"][:limit]:
        holdings.add_row(
            item["issuer"],
            item["cusip"],
            f"${item['value_usd']:,.0f}",
            "—" if item["weight"] is None else f"{item['weight']:.2%}",
            f"{item['shares_or_principal']:,.2f}",
        )
    print(holdings)
    if payload["changes"]:
        changes = Table(
            "Change",
            "Issuer",
            "Shares Δ",
            "% Δ",
            "Current value",
        )
        for item in payload["changes"][:limit]:
            changes.add_row(
                item["status"],
                item["issuer"],
                f"{item['share_change']:,.2f}",
                (
                    "—"
                    if item["share_change_fraction"] is None
                    else f"{item['share_change_fraction']:.2%}"
                ),
                f"${item['current_value_usd']:,.0f}",
            )
        print(changes)
