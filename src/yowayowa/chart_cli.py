from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Compose price, fundamental and FRED series on a shared timeline.")

_SOURCE_HELP = "source must be id=price:SYMBOL, id=fundamental:SYMBOL:METRIC, or id=fred:SERIES"
_FORMULA_HELP = (
    "formula must be id=ratio:left:right, id=spread:left:right, or id=corr:left:right:window"
)


def _source(raw: str) -> dict[str, object]:
    if "=" not in raw:
        raise typer.BadParameter(_SOURCE_HELP)
    series_id, expression = raw.split("=", 1)
    parts = expression.split(":")
    kind = parts[0].lower()
    if kind == "price" and len(parts) == 2:
        return {"id": series_id, "source": "price", "symbol": parts[1], "period": "5y"}
    if kind == "fundamental" and len(parts) == 3:
        return {
            "id": series_id,
            "source": "fundamental",
            "symbol": parts[1],
            "metric": parts[2],
        }
    if kind == "fred" and len(parts) == 2:
        return {"id": series_id, "source": "fred", "series_id": parts[1]}
    raise typer.BadParameter(f"invalid --source; {_SOURCE_HELP}")


def _formula(raw: str) -> dict[str, object]:
    if "=" not in raw:
        raise typer.BadParameter(_FORMULA_HELP)
    series_id, expression = raw.split("=", 1)
    parts = expression.split(":")
    kind = parts[0].lower()
    if kind in {"ratio", "spread"} and len(parts) == 3:
        return {"id": series_id, "kind": kind, "left": parts[1], "right": parts[2]}
    if kind in {"corr", "correlation"} and len(parts) in {3, 4}:
        window = int(parts[3]) if len(parts) == 4 else 60
        return {
            "id": series_id,
            "kind": "rolling_correlation",
            "left": parts[1],
            "right": parts[2],
            "window": window,
        }
    raise typer.BadParameter(f"invalid --formula; {_FORMULA_HELP}")


@app.command("compose")
def compose(
    source: list[str] = typer.Option(
        ...,
        "--source",
        "-s",
        help="Repeatable source specification.",
    ),
    formula: list[str] | None = typer.Option(
        None,
        "--formula",
        "-f",
        help="Repeatable derived-series formula.",
    ),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    payload = {
        "sources": [_source(item) for item in source],
        "transforms": [_formula(item) for item in (formula or [])],
    }
    with _client(base_url, token) as client:
        result = client.post("/v1/charts/compose", json=payload).raise_for_status().json()

    table = Table("ID", "Kind", "Label", "Unit", "Points", "Latest")
    for series in result["series"]:
        points = series["points"]
        latest = "—" if not points else f"{points[-1]['date']} · {points[-1]['value']:.6g}"
        table.add_row(
            series["id"],
            series.get("source") or series.get("transform") or "—",
            series["label"],
            series.get("unit") or "—",
            str(len(points)),
            latest,
        )
    print(table)
    if result["errors"]:
        errors = Table("Unavailable", "Reason")
        for series_id, reason in result["errors"].items():
            errors.add_row(series_id, reason)
        print(errors)
