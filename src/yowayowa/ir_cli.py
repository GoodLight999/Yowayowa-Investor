from __future__ import annotations

import json

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Company IR monitoring: discovery, acquisition, KPI diff, timeline.")


@app.command("sources")
def list_sources(
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = client.get("/v1/ir/sources").raise_for_status().json()
    table = Table("Source", "Symbol", "Provider", "License", "Listing URL")
    for source in payload["sources"]:
        table.add_row(
            source["source_id"],
            source["symbol"],
            source["provider"],
            source.get("license_class") or "—",
            source["listing_url"],
        )
    print(table)


@app.command("add-source")
def add_source(
    source_id: str = typer.Argument(...),
    symbol: str = typer.Option(..., "--symbol", help="instrument symbol, e.g. 9843.T"),
    provider: str = typer.Option(..., "--provider", help="source provider name"),
    listing_url: str = typer.Option(..., "--listing-url", help="IR listing page URL"),
    license_class: str = typer.Option("official_public", "--license-class"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body = {
        "source_id": source_id,
        "symbol": symbol,
        "provider": provider,
        "listing_url": listing_url,
        "license_class": license_class,
    }
    with _client(api_url, token) as client:
        payload = client.post("/v1/ir/sources", json=body).raise_for_status().json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("monitor")
def monitor(
    source_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json", help="print the full outcome JSON"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = client.post(f"/v1/ir/sources/{source_id}/monitor").raise_for_status().json()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"[bold]{payload['source_id']} ({payload['symbol']})[/bold] · "
        f"state {payload['fetch_state']} · new {payload['new_count']} · "
        f"revised {payload['revised_count']} · "
        f"unchanged {payload['unchanged_count']} · seen {payload.get('seen_count', 0)} · "
        f"timeline +{payload['timeline_entries']}"
    )
    table = Table("Status", "Format", "Doc", "KPIs", "Diff")
    for document in payload["documents"][:40]:
        kpis = ", ".join(f"{item['kpi']}={item['value']}" for item in document.get("kpis", [])[:4])
        diffs = ", ".join(
            f"{item['kpi']} {item['change']}" for item in document.get("kpi_diff", [])[:4]
        )
        table.add_row(
            document["status"],
            document.get("format") or "—",
            document["label"][:44] or document["url"].rsplit("/", 1)[-1][:44],
            kpis or "—",
            diffs or "—",
        )
    print(table)
    if payload.get("notes"):
        print(f"notes: {'; '.join(payload['notes'])}")


@app.command("timeline")
def timeline(
    symbol: str = typer.Argument(...),
    kind: str | None = typer.Option(None, "--kind"),
    limit: int = typer.Option(50, min=1, max=1000),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int] = {"limit": limit}
    if kind:
        params["kind"] = kind
    with _client(api_url, token) as client:
        payload = (
            client.get(f"/v1/ir/instruments/{symbol}/timeline", params=params)
            .raise_for_status()
            .json()
        )
    table = Table("Recorded", "Kind", "Provider", "License", "Source", "KPIs")
    for entry in payload["entries"]:
        provenance = entry.get("provenance", {})
        kpis = ", ".join(
            f"{item['kpi']}={item['value']}"
            for item in entry.get("payload", {}).get("kpis", [])[:4]
        )
        table.add_row(
            entry.get("recorded_at", "—"),
            entry.get("kind", "—"),
            provenance.get("provider", "—"),
            provenance.get("license_class", "—"),
            provenance.get("source_url", "—"),
            kpis or "—",
        )
    print(table)


@app.command("kpi-history")
def kpi_history(
    url: str = typer.Argument(...),
    kpi: str | None = typer.Option(None, "--kpi"),
    limit: int = typer.Option(10, min=1, max=200),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int] = {"url": url, "limit": limit}
    if kpi:
        params["kpi"] = kpi
    with _client(api_url, token) as client:
        payload = (
            client.get("/v1/ir/documents/kpi-history", params=params).raise_for_status().json()
        )
    print(f"[bold]{payload['url']}[/bold]")
    table = Table("Recorded", "KPI", "Value", "Raw", "Unit")
    for entry in payload["entries"]:
        for item in entry.get("kpis", []):
            table.add_row(
                entry.get("recorded_at", "—"),
                item.get("kpi", "—"),
                str(item.get("value", "—")),
                str(item.get("raw_value", "—")),
                str(item.get("unit") or "—"),
            )
    print(table)
