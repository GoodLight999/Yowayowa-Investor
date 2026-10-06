"""CLI for authorized private mailbox sources (P1D).

Mirrors ``ir_cli``: every command is a thin API client so the CLI, REST API,
and browser UI cannot drift apart. Source registration and fetch are the only
mutating operations, and they mutate local operator configuration only — the
mailbox itself is never written to (the reader always runs ``--readonly``).
"""

from __future__ import annotations

import json

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(
    help="Authorized private sources: read-only mailbox alert ingestion (personal mode only)."
)


@app.command("sources")
def list_sources(
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = client.get("/v1/private-sources/sources").raise_for_status().json()
    table = Table("Source", "Provider", "Account", "Kind", "License", "Query")
    for source in payload["sources"]:
        table.add_row(
            source["source_id"],
            source["provider"],
            source["account"],
            source["kind"],
            source.get("license_class") or "—",
            source["query"],
        )
    print(table)


@app.command("add-source")
def add_source(
    source_id: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider", help="source provider name"),
    account: str = typer.Option(..., "--account", help="authorized mail account"),
    query: str = typer.Option(..., "--query", help="Gmail search query for the alert mail"),
    kind: str = typer.Option("earnings_calendar", "--kind"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body = {
        "source_id": source_id,
        "provider": provider,
        "account": account,
        "kind": kind,
        "query": query,
    }
    with _client(api_url, token) as client:
        payload = client.post("/v1/private-sources/sources", json=body).raise_for_status().json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("fetch")
def fetch(
    source_id: str = typer.Argument(...),
    force_refresh: bool = typer.Option(False, "--force-refresh"),
    as_json: bool = typer.Option(False, "--json", help="print the full outcome JSON"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body = {"force_refresh": force_refresh}
    with _client(api_url, token) as client:
        payload = (
            client.post(f"/v1/private-sources/sources/{source_id}/fetch", json=body)
            .raise_for_status()
            .json()
        )
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"[bold]{payload['source_id']}[/bold] ({payload['provider']}) · "
        f"state {payload['fetch_state']} · auth {payload['auth_state']} · "
        f"scanned {payload['messages_scanned']} · new messages {payload['messages_new']} · "
        f"events {len(payload['events'])} · new events {payload['new_events']} · "
        f"timeline +{payload['timeline_entries']}"
    )
    table = Table("Symbol", "Market", "Announcement", "Name", "Message")
    for event in payload["events"][:40]:
        table.add_row(
            event["symbol"],
            event["market"],
            str(event["announcement_date"]),
            event.get("name") or "—",
            event["source_message_id"],
        )
    print(table)
    if payload.get("notes"):
        print(f"notes: {'; '.join(payload['notes'])}")


@app.command("events")
def events(
    source_id: str | None = typer.Option(None, "--source-id"),
    limit: int = typer.Option(200, min=1, max=1000),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int] = {"limit": limit}
    if source_id:
        params["source_id"] = source_id
    with _client(api_url, token) as client:
        payload = client.get("/v1/private-sources/events", params=params).raise_for_status().json()
    table = Table("Announcement", "Symbol", "Market", "Name", "Provider", "License")
    for event in payload["events"]:
        table.add_row(
            str(event.get("announcement_date") or "—"),
            str(event.get("symbol") or "—"),
            str(event.get("market") or "—"),
            str(event.get("name") or "—"),
            str(event.get("market_provider") or "—"),
            str(event.get("license_class") or "—"),
        )
    print(table)
    print(f"{payload['count']} event(s)")
