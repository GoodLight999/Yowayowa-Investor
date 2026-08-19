from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(no_args_is_help=True, help="Manage saved research presets.")


def _kind(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"chart", "screener", "compare"}:
        raise typer.BadParameter("kind must be chart, screener, or compare")
    return normalized


@app.command("list")
def list_presets(
    kind: str | None = typer.Option(None, help="chart, screener, or compare"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params = {"kind": _kind(kind)} if kind else None
    with _client(base_url, token) as client:
        payload = client.get("/v1/research-presets", params=params).raise_for_status().json()
    table = Table("ID", "Kind", "Name", "Updated")
    for item in payload:
        table.add_row(str(item["id"]), item["kind"], item["name"], item["updated_at"])
    print(table)


@app.command("show")
def show_preset(
    preset_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/research-presets/{preset_id}").raise_for_status().json()
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@app.command("save")
def save_preset(
    name: str,
    kind: str = typer.Option(..., help="chart, screener, or compare"),
    file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    payload = json.loads(file.read_text(encoding="utf-8"))
    request = {"name": name, "kind": _kind(kind), "payload": payload}
    with _client(base_url, token) as client:
        saved = client.post("/v1/research-presets", json=request).raise_for_status().json()
    print(f"Saved preset {saved['id']}: {saved['name']} ({saved['kind']})")


@app.command("update")
def update_preset(
    preset_id: int,
    name: str,
    kind: str = typer.Option(..., help="chart, screener, or compare"),
    file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    payload = json.loads(file.read_text(encoding="utf-8"))
    request = {"name": name, "kind": _kind(kind), "payload": payload}
    with _client(base_url, token) as client:
        saved = (
            client.put(f"/v1/research-presets/{preset_id}", json=request).raise_for_status().json()
        )
    print(f"Updated preset {saved['id']}: {saved['name']} ({saved['kind']})")


@app.command("remove")
def remove_preset(
    preset_id: int,
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        client.delete(f"/v1/research-presets/{preset_id}").raise_for_status()
    print(f"Removed preset {preset_id}")
