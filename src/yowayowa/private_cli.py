from __future__ import annotations

import json

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Private acquisition toolkit: authenticated personal data sources.")


@app.command("list")
def list_connectors(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/private/connectors").raise_for_status().json()
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


@app.command("register")
def register(
    connector_id: str = typer.Argument(...),
    provider: str = typer.Option(...),
    base_url: str = typer.Option(..., "--base-url", help="Connector origin URL"),
    method: str = typer.Option("private_http"),
    parser: str = typer.Option("json"),
    ttl: int | None = typer.Option(None, min=0, help="freshness ttl seconds"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body: dict[str, object] = {
        "id": connector_id,
        "provider": provider,
        "base_url": base_url,
        "method": method,
        "parser": parser,
    }
    if ttl is not None:
        body["freshness"] = {"ttl_seconds": ttl}
    with _client(api_url, token) as client:
        payload = client.post("/v1/private/connectors", json=body).raise_for_status().json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("fetch")
def fetch(
    connector_id: str,
    resource: str,
    param: list[str] = typer.Option([], "--param", help="k=v query parameter"),
    force_refresh: bool = typer.Option(False, "--force-refresh"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str] = {}
    for raw in param:
        key, separator, value = raw.partition("=")
        if not separator or not key:
            raise typer.BadParameter(f"Invalid --param {raw!r}; expected k=v")
        params[key] = value
    body: dict[str, object] = {
        "resource": resource,
        "params": params or None,
        "force_refresh": force_refresh,
    }
    with _client(base_url, token) as client:
        payload = (
            client.post(f"/v1/private/connectors/{connector_id}/fetch", json=body)
            .raise_for_status()
            .json()
        )
    snapshot = payload.get("snapshot") or {}
    diff = payload.get("diff") or {}
    print(
        f"[bold]{connector_id}/{resource}[/bold] · state {payload['fetch_state']} · "
        f"auth {payload['auth_state']} · "
        f"source {payload.get('source_url') or '—'} · "
        f"retrieved {payload.get('retrieved_at') or '—'}"
    )
    if snapshot:
        print(f"snapshot {snapshot.get('snapshot_id')} · captured {snapshot.get('captured_at')}")
    if diff:
        print(f"changed: {'yes' if diff.get('changed') else 'no'}")
    if payload.get("notes"):
        print(f"notes: {'; '.join(payload['notes'])}")
    if payload.get("payload") is not None:
        print(json.dumps(payload["payload"], ensure_ascii=False, indent=2))


@app.command("auth-check")
def auth_check(
    connector_id: str,
    resource: str | None = typer.Option(None, "--resource"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    body: dict[str, object] = {"resource": resource}
    with _client(base_url, token) as client:
        payload = (
            client.post(f"/v1/private/connectors/{connector_id}/auth-check", json=body)
            .raise_for_status()
            .json()
        )
    print(
        f"state {payload['fetch_state']} · auth {payload['auth_state']} · "
        f"reason {'; '.join(payload.get('notes') or []) or '—'}"
    )


@app.command("snapshots")
def snapshots(
    connector_id: str,
    resource: str = typer.Option(...),
    limit: int = typer.Option(20, min=1, max=1000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/private/connectors/{connector_id}/snapshots",
                params={"resource": resource, "limit": limit},
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
    connector_id: str,
    resource: str = typer.Option(...),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/private/connectors/{connector_id}/diff",
                params={"resource": resource},
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
