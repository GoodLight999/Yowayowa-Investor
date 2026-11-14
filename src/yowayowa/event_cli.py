from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Manage saved event subscriptions and inbox notifications.")


def _scope(scope: str, scope_id: int | None) -> str:
    normalized = scope.strip().lower()
    if normalized not in {"all", "watchlist", "portfolio"}:
        raise typer.BadParameter("scope must be all, watchlist, or portfolio")
    if normalized == "all" and scope_id is not None:
        raise typer.BadParameter("--scope-id is not valid with --scope all")
    if normalized != "all" and scope_id is None:
        raise typer.BadParameter("--scope-id is required for watchlist or portfolio scope")
    return normalized


@app.command("subscribe")
def subscribe(
    scope: str = typer.Option("all", help="all, watchlist, or portfolio"),
    scope_id: int | None = typer.Option(None, min=1),
    types: str = typer.Option("earnings,dividend"),
    lead_days: int = typer.Option(7, min=0, max=30),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    normalized = _scope(scope, scope_id)
    event_types = list(
        dict.fromkeys(value.strip().lower() for value in types.split(",") if value.strip())
    )
    if not event_types or set(event_types) - {"earnings", "dividend"}:
        raise typer.BadParameter("--types must contain earnings and/or dividend")
    payload: dict[str, object] = {
        "scope": normalized,
        "event_types": event_types,
        "lead_days": lead_days,
    }
    if scope_id is not None:
        payload["scope_id"] = scope_id
    with _client(base_url, token) as client:
        print(client.post("/v1/event-subscriptions", json=payload).raise_for_status().json())


@app.command("list")
def list_subscriptions(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/event-subscriptions").raise_for_status().json()
    table = Table("ID", "Scope", "Types", "Lead days", "Status", "Last checked")
    for item in payload:
        scope = item["scope"]
        if item.get("scope_id") is not None:
            scope = f"{scope}:{item['scope_id']}"
        table.add_row(
            str(item["id"]),
            scope,
            ", ".join(item["event_types"]),
            str(item["lead_days"]),
            "active" if item["enabled"] else "inactive",
            item.get("last_checked_at") or "—",
        )
    print(table)


@app.command("remove")
def remove(
    subscription_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        client.delete(f"/v1/event-subscriptions/{subscription_id}").raise_for_status()


@app.command("evaluate")
def evaluate(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.post("/v1/event-subscriptions/evaluate").raise_for_status().json()
    print(
        f"Checked {len(payload['subscriptions'])} subscriptions · "
        f"{len(payload['inbox'])} unread notifications"
    )
    if payload["unavailable_symbols"]:
        print(f"Unavailable: {', '.join(payload['unavailable_symbols'])}")


@app.command("inbox")
def inbox(
    include_acknowledged: bool = typer.Option(False, "--all"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                "/v1/event-inbox",
                params={"include_acknowledged": str(include_acknowledged).lower()},
            )
            .raise_for_status()
            .json()
        )
    table = Table("ID", "When", "Type", "Symbol", "Event", "Status")
    for item in payload:
        table.add_row(
            str(item["id"]),
            item["starts_at"],
            item["event_type"],
            item["symbol"],
            item["title"],
            "acknowledged" if item.get("acknowledged_at") else "unread",
        )
    print(table)


@app.command("ack")
def acknowledge(
    inbox_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        print(client.post(f"/v1/event-inbox/{inbox_id}/ack").raise_for_status().json())
