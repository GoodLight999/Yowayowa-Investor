from __future__ import annotations

import json

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Broker read-side connectors: balances, positions, orders, executions.")

_Market = typer.Option("jp", "--market", help="jp or us")


@app.command("list")
def list_connectors(
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = client.get("/v1/broker-read/connectors").raise_for_status().json()
    table = Table("Id", "Provider", "Method", "Parser", "Last state", "Last success")
    for runtime in payload["connectors"]:
        definition = runtime["definition"]
        table.add_row(
            definition["id"],
            definition["provider"],
            definition["method"],
            definition["parser"],
            runtime.get("last_fetch_state") or "—",
            runtime.get("last_success_at") or "—",
        )
    print(table)


@app.command("auth-check")
def auth_check(
    connector_id: str = typer.Argument(...),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = (
            client.post(f"/v1/broker-read/connectors/{connector_id}/auth-check")
            .raise_for_status()
            .json()
        )
    print(
        f"state {payload['fetch_state']} · auth {payload['auth_state']} · "
        f"reason {'; '.join(payload.get('notes') or []) or '—'}"
    )


def _print_fetch_summary(connector_id: str, payload: dict[str, object]) -> None:
    detail = payload.get("detail")
    margin_state = detail.get("margin_state") if isinstance(detail, dict) else None
    print(
        f"[bold]{connector_id}/{payload['resource']} ({payload['market']})[/bold] · "
        f"state {payload['fetch_state']} · auth {payload['auth_state']}"
    )
    account = payload.get("account")
    if isinstance(account, dict):
        currency = account.get("currency", "")
        cash = account.get("cash_balance")
        buying = account.get("buying_power")
        cash_text = "—" if cash is None else str(cash)
        buying_text = "—" if buying is None else str(buying)
        print(f"cash {cash_text} {currency} · buying power {buying_text} {currency}")
    positions = payload.get("positions")
    if isinstance(positions, list) and positions:
        print(f"positions: {len(positions)}")
        table = Table("Symbol", "Qty", "Avg cost", "Price", "Value", "P/L", "CCY", "Type")
        for row in positions:
            table.add_row(
                str(row["symbol"]),
                str(row["quantity"]),
                str(row.get("average_cost") or "—"),
                str(row.get("market_price") or "—"),
                str(row.get("market_value") or "—"),
                str(row.get("unrealized_pnl") or "—"),
                row.get("currency", ""),
                row.get("account_type") or "—",
            )
        print(table)
    orders = payload.get("orders")
    if isinstance(orders, list) and orders:
        print(f"orders: {len(orders)}")
        table = Table("Order id", "Symbol", "Side", "Qty", "Filled", "Avg fill", "Status")
        for row in orders:
            table.add_row(
                row.get("broker_order_id") or "(none)",
                str(row["symbol"]),
                str(row["side"]),
                str(row["quantity"]),
                str(row["filled_quantity"]),
                str(row.get("average_fill_price") or "—"),
                str(row["status"]),
            )
        print(table)
    if isinstance(margin_state, dict) and margin_state:
        print(f"margin: {json.dumps(margin_state, ensure_ascii=False, default=str)}")
    notes = payload.get("notes")
    if isinstance(notes, list) and notes:
        note_strings = [str(note) for note in notes]
        print(f"notes: {'; '.join(note_strings)}")


@app.command("fetch")
def fetch(
    connector_id: str = typer.Argument(...),
    resource: str = typer.Argument(...),
    market: str = _Market,
    force_refresh: bool = typer.Option(False, "--force-refresh"),
    as_json: bool = typer.Option(False, "--json", help="print the full outcome JSON"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body: dict[str, object] = {
        "resource": resource,
        "market": market,
        "force_refresh": force_refresh,
    }
    with _client(api_url, token) as client:
        payload = (
            client.post(f"/v1/broker-read/connectors/{connector_id}/fetch", json=body)
            .raise_for_status()
            .json()
        )
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_fetch_summary(connector_id, payload)


@app.command("snapshots")
def snapshots(
    connector_id: str = typer.Argument(...),
    resource: str = typer.Argument(...),
    market: str = _Market,
    limit: int = typer.Option(20, min=1, max=1000),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = (
            client.get(
                f"/v1/broker-read/connectors/{connector_id}/snapshots",
                params={"resource": resource, "market": market, "limit": limit},
            )
            .raise_for_status()
            .json()
        )
    table = Table("Snapshot", "Captured", "State", "Parser")
    for record in payload["snapshots"]:
        table.add_row(
            record["snapshot_id"],
            record["captured_at"],
            record["fetch_state"],
            record["parser_version"],
        )
    print(table)


@app.command("diff")
def diff(
    connector_id: str = typer.Argument(...),
    resource: str = typer.Argument(...),
    market: str = _Market,
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(api_url, token) as client:
        payload = (
            client.get(
                f"/v1/broker-read/connectors/{connector_id}/diff",
                params={"resource": resource, "market": market},
            )
            .raise_for_status()
            .json()
        )
    result = payload.get("diff")
    if result is None:
        print("No diff available (fewer than one snapshot).")
        return
    print(
        f"changed: {'yes' if result['changed'] else 'no'} · "
        f"{len(result['changes'])} field change(s)"
    )
    table = Table("Path", "Previous", "Current")
    for change in result["changes"][:20]:
        table.add_row(change["path"], change.get("previous") or "—", change.get("current") or "—")
    print(table)
