from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(no_args_is_help=True, help="Manage saved and built-in research presets.")


def _kind(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"chart", "screener", "compare"}:
        raise typer.BadParameter("kind must be chart, screener, or compare")
    return normalized


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


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


@app.command("builtins")
def list_builtins(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/strategy-presets").raise_for_status().json()
    table = Table("ID", "Name", "Default region", "Unofficial")
    for item in payload:
        table.add_row(
            item["id"],
            item["name_en"],
            item["default_region"].upper(),
            "yes" if item.get("unofficial") else "no",
        )
    print(table)


@app.command("run-builtin")
def run_builtin(
    strategy_id: str,
    region: str | None = typer.Option(None, help="Yahoo screener region such as jp or us"),
    size: int = typer.Option(25, min=1, max=50),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        strategy = client.get(f"/v1/strategy-presets/{strategy_id}").raise_for_status().json()
        resolved_region = (region or strategy["default_region"]).strip().lower()
        discovery = dict(strategy["discovery"])
        discovery["filters"] = [
            {"field": "region", "operator": "is-in", "value": [resolved_region]},
            *discovery.get("filters", []),
        ]
        discovery["size"] = size
        discovery["offset"] = 0
        screen = client.post("/v1/discover/screen", json=discovery).raise_for_status().json()

        candidates = []
        for row in screen.get("quotes", []):
            symbol = str(row.get("symbol") or "").strip()
            market_cap = _first(row, "marketCap", "intradaymarketcap")
            if not symbol or not isinstance(market_cap, (int, float)) or market_cap <= 0:
                continue
            pe_ratio = _first(row, "trailingPE", "peratio.lasttwelvemonths")
            candidates.append(
                {
                    "symbol": symbol,
                    "market_cap": float(market_cap),
                    "pe_ratio": float(pe_ratio) if isinstance(pe_ratio, (int, float)) else None,
                }
            )
        if not candidates:
            print("No candidates with market-cap data were returned.")
            raise typer.Exit(code=0)
        evaluation = (
            client.post(
                f"/v1/strategy-presets/{strategy_id}/evaluate",
                json={"candidates": candidates},
            )
            .raise_for_status()
            .json()
        )

    table = Table(
        "Symbol",
        "Market cap",
        "P/E",
        "Net cash ratio",
        "Cash-neutral P/E",
        "Revenue YoY",
        "FCF",
        "Basis",
    )
    for item in evaluation["evaluations"]:
        ratio = item.get("net_cash_ratio")
        ratio_text = "—" if ratio is None else f"{ratio:.2f}x"
        if ratio is not None and item.get("net_cash_ratio_is_lower_bound"):
            ratio_text = f">={ratio:.2f}x"
        cnpe = item.get("cash_neutral_pe")
        cnpe_text = "—" if cnpe is None else f"{cnpe:.2f}x"
        if cnpe is not None and item.get("cash_neutral_pe_is_upper_bound"):
            cnpe_text = f"<={cnpe:.2f}x"
        growth = item.get("revenue_growth_yoy")
        fcf = item.get("free_cash_flow")
        table.add_row(
            item["symbol"],
            f"{item['market_cap']:.6g}",
            "—" if item.get("pe_ratio") is None else f"{item['pe_ratio']:.2f}x",
            ratio_text,
            cnpe_text,
            "—" if growth is None else f"{growth:+.1%}",
            "—" if fcf is None else f"{fcf:.6g}",
            "floor" if item.get("net_cash_ratio_is_lower_bound") else "formula",
        )
    print(table)
    if evaluation.get("errors"):
        print(f"Unavailable: {', '.join(evaluation['errors'])}")
