from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Inspect macroeconomic series from public-safe official sources.")


@app.command("bls-catalog")
def bls_catalog(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/macro/bls/catalog").raise_for_status().json()

    table = Table("Series", "Category", "Title", "Unit")
    for item in payload["series"]:
        table.add_row(item["series_id"], item["category"], item["title"], item["unit"])
    print(table)


@app.command("bls")
def bls_series(
    series_id: str,
    start_year: int | None = typer.Option(None, min=1900, max=9999),
    end_year: int | None = typer.Option(None, min=1900, max=9999),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, int] = {}
    if start_year is not None:
        params["start_year"] = start_year
    if end_year is not None:
        params["end_year"] = end_year
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/macro/bls/{series_id}", params=params).raise_for_status().json()

    print(f"[bold]{payload['series_id']} · {payload['title']}[/bold]")
    table = Table("Date", "Period", "Value")
    for item in payload["observations"]:
        table.add_row(
            item.get("date") or str(item["year"]),
            item["period_name"],
            str(item["value"]),
        )
    print(table)


@app.command("bea-catalog")
def bea_catalog(
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get("/v1/macro/bea/nipa/catalog").raise_for_status().json()

    table = Table("Table", "Category", "Title", "Frequency", "Default line")
    for item in payload["tables"]:
        table.add_row(
            item["table_name"],
            item["category"],
            item["title"],
            item["default_frequency"],
            str(item["default_line_number"]),
        )
    print(table)


def _years_param(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return []
    years: list[tuple[str, str]] = []
    for token in value.split(","):
        normalized = token.strip()
        if not normalized.isdigit():
            raise typer.BadParameter("--years must be comma-separated calendar years")
        years.append(("years", normalized))
    return years


@app.command("bea")
def bea_nipa(
    table_name: str,
    frequency: str = typer.Option("Q", help="A, Q, or M"),
    years: str | None = typer.Option(None, help="Comma-separated years, e.g. 2024,2025,2026"),
    line_number: int | None = typer.Option(None, min=1),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("frequency", frequency.upper()),
        *_years_param(years),
    ]
    if line_number is not None:
        params.append(("line_number", line_number))
    with _client(base_url, token) as client:
        payload = (
            client.get(f"/v1/macro/bea/nipa/{table_name}", params=params).raise_for_status().json()
        )

    print(f"[bold]{payload['table_name']} · {payload['frequency']}[/bold]")
    table = Table("Period", "Line", "Description", "Value", "Unit")
    for item in payload["rows"]:
        table.add_row(
            item["time_period"],
            str(item.get("line_number") or "—"),
            item["line_description"],
            "—" if item.get("value") is None else str(item["value"]),
            item.get("unit") or "—",
        )
    print(table)


@app.command("estat-search")
def estat_search(
    query: str,
    lang: str = typer.Option("J", help="J or E"),
    limit: int = typer.Option(50, min=1, max=100),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                "/v1/macro/estat/tables",
                params={"q": query, "lang": lang.upper(), "limit": limit},
            )
            .raise_for_status()
            .json()
        )

    table = Table("Table ID", "Agency", "Statistics", "Title", "Updated")
    for item in payload["tables"]:
        table.add_row(
            item["stats_data_id"],
            item.get("gov_org") or "—",
            item["stat_name"],
            item["title"],
            str(item.get("updated_at") or "—"),
        )
    print(table)
    print(f"Matched {payload['matched_count']} table(s)")


@app.command("estat-meta")
def estat_meta(
    stats_data_id: str,
    lang: str = typer.Option("J", help="J or E"),
    max_items: int = typer.Option(200, min=1, max=5000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/macro/estat/{stats_data_id}/meta",
                params={"lang": lang.upper()},
            )
            .raise_for_status()
            .json()
        )

    table_info = payload.get("table") or {}
    print(f"[bold]{stats_data_id} · {table_info.get('title') or 'e-Stat table'}[/bold]")
    table = Table("Dimension", "Code", "Name", "Level", "Unit")
    remaining = max_items
    for dimension in payload["dimensions"]:
        for item in dimension["items"]:
            if remaining <= 0:
                break
            table.add_row(
                f"{dimension['id']} · {dimension['name']}",
                item["code"],
                item["name"],
                str(item.get("level") or "—"),
                item.get("unit") or "—",
            )
            remaining -= 1
        if remaining <= 0:
            break
    print(table)


@app.command("estat")
def estat_data(
    stats_data_id: str,
    filter: list[str] | None = typer.Option(
        None,
        "--filter",
        "-f",
        help="Repeatable dimension filter, e.g. -f area=00000 -f cat01=0001",
    ),
    lang: str = typer.Option("J", help="J or E"),
    limit: int = typer.Option(5000, min=1, max=10_000),
    start_position: int | None = typer.Option(None, min=1),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("lang", lang.upper()),
        ("limit", limit),
    ]
    for value in filter or []:
        params.append(("filter", value))
    if start_position is not None:
        params.append(("start_position", start_position))
    with _client(base_url, token) as client:
        payload = (
            client.get(f"/v1/macro/estat/{stats_data_id}/data", params=params)
            .raise_for_status()
            .json()
        )

    table_info = payload.get("table") or {}
    print(f"[bold]{stats_data_id} · {table_info.get('title') or 'e-Stat data'}[/bold]")
    table = Table("Dimensions", "Value", "Unit", "Annotation")
    for item in payload["values"]:
        dimensions = " · ".join(f"{key}={value}" for key, value in item["dimensions"].items())
        table.add_row(
            dimensions or "—",
            item["value"],
            item.get("unit") or "—",
            item.get("annotation") or "—",
        )
    print(table)
    print(
        f"Returned {len(payload['values'])} / {payload['total_number']} fact(s)"
        + (f" · next {payload['next_key']}" if payload.get("next_key") else "")
    )
