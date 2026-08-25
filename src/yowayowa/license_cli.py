from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Inspect source licensing and public redistribution policy.")


@app.command("list")
def list_source_licenses(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/licensing/sources").raise_for_status().json()

    table = Table("Source", "Access", "Display", "API", "Derived", "Reviewed")
    for item in payload["sources"]:
        table.add_row(
            item["source"],
            item["access"],
            "yes" if item["public_display"] else "no",
            "yes" if item["public_api"] else "no",
            "yes" if item["derived_analysis_public"] else "no",
            item["reviewed_on"],
        )
    print(table)
    print(f"Mode: {payload['mode']}")
