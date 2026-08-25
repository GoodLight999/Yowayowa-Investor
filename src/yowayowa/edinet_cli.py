from __future__ import annotations

from datetime import datetime, timedelta

import typer
from rich import print
from rich.table import Table

from yowayowa.cli import _client

app = typer.Typer(help="Research official Japanese EDINET filings and normalized XBRL facts.")


@app.command("documents")
def documents(
    filing_date: datetime = typer.Argument(..., formats=["%Y-%m-%d"]),
    security_code: str | None = typer.Option(None, "--security-code", "-s"),
    csv_only: bool = typer.Option(False, "--csv-only"),
    limit: int = typer.Option(100, min=1, max=1000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int | bool] = {
        "filing_date": filing_date.date().isoformat(),
        "csv_only": csv_only,
        "limit": limit,
    }
    if security_code:
        params["security_code"] = security_code
    with _client(base_url, token) as client:
        payload = (
            client.get("/v1/filings/edinet/documents", params=params).raise_for_status().json()
        )

    table = Table("Doc ID", "Security", "Filer", "Type", "Period", "Submitted", "CSV")
    for item in payload["documents"]:
        period = "—"
        if item.get("period_start") or item.get("period_end"):
            period = f"{item.get('period_start') or '—'} → {item.get('period_end') or '—'}"
        table.add_row(
            item["doc_id"],
            item.get("security_code") or "—",
            item["filer_name"],
            item.get("doc_type_code") or "—",
            period,
            item.get("submitted_at") or "—",
            "yes" if item.get("csv_available") else "no",
        )
    print(table)
    print(
        f"Matched {payload['matched_count']} / {payload['total_count']} filings · "
        f"Source: {payload['provenance']['source']}"
    )


@app.command("index-sync")
def index_sync(
    start_date: datetime = typer.Argument(..., formats=["%Y-%m-%d"]),
    end_date: datetime = typer.Argument(..., formats=["%Y-%m-%d"]),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    start = start_date.date()
    end = end_date.date()
    if end < start:
        raise typer.BadParameter("end date must be on or after start date")

    total_days = 0
    synced_days = 0
    upserted = 0
    failures: list[dict[str, str]] = []
    with _client(base_url, token) as client:
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=30), end)
            payload = (
                client.post(
                    "/v1/filings/edinet/index/sync",
                    params={
                        "start_date": cursor.isoformat(),
                        "end_date": chunk_end.isoformat(),
                    },
                )
                .raise_for_status()
                .json()
            )
            total_days += payload["days_requested"]
            synced_days += payload["days_synced"]
            upserted += payload["documents_upserted"]
            failures.extend(payload["failures"])
            cursor = chunk_end + timedelta(days=1)

    print(
        f"EDINET index: {synced_days}/{total_days} days synchronized · "
        f"{upserted} filing rows upserted"
    )
    if failures:
        table = Table("Date", "Error")
        for failure in failures:
            table.add_row(failure["filing_date"], failure["error"])
        print(table)
        raise typer.Exit(code=1)


@app.command("history")
def history(
    security_code: str = typer.Argument(...),
    start_date: datetime = typer.Option(..., "--start", formats=["%Y-%m-%d"]),
    end_date: datetime = typer.Option(..., "--end", formats=["%Y-%m-%d"]),
    csv_only: bool = typer.Option(False, "--csv-only"),
    limit: int = typer.Option(100, min=1, max=1000),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int | bool] = {
        "security_code": security_code,
        "start_date": start_date.date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "csv_only": csv_only,
        "limit": limit,
    }
    with _client(base_url, token) as client:
        payload = (
            client.get("/v1/filings/edinet/index/history", params=params).raise_for_status().json()
        )

    table = Table("Doc ID", "Filer", "Type", "Period", "Submitted", "CSV")
    for item in payload["documents"]:
        period = "—"
        if item.get("period_start") or item.get("period_end"):
            period = f"{item.get('period_start') or '—'} → {item.get('period_end') or '—'}"
        table.add_row(
            item["doc_id"],
            item["filer_name"],
            item.get("doc_type_code") or "—",
            period,
            item.get("submitted_at") or "—",
            "yes" if item.get("csv_available") else "no",
        )
    print(table)
    coverage = "complete" if payload["coverage_complete"] else "PARTIAL"
    print(
        f"Matched {payload['matched_count']} filings · index coverage {coverage}: "
        f"{payload['indexed_days']}/{payload['expected_days']} requested days · "
        f"index {payload.get('index_start') or '—'} → {payload.get('index_end') or '—'}"
    )


@app.command("financials")
def show_financials(
    doc_id: str,
    all_contexts: bool = typer.Option(False, "--all-contexts"),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    with _client(base_url, token) as client:
        payload = client.get(f"/v1/filings/edinet/{doc_id}/financials").raise_for_status().json()

    title = payload.get("company_name") or payload["doc_id"]
    print(
        f"[bold]{title}[/bold] · {payload.get('security_code') or '—'} · "
        f"{payload.get('period_end') or '—'} · {payload.get('accounting_standard') or '—'}"
    )
    table = Table("Metric", "Value", "Unit", "Scope", "Element")
    for key, observations in payload["metrics"].items():
        selected = observations if all_contexts else observations[:1]
        for observation in selected:
            scope = " · ".join(
                part
                for part in (
                    observation.get("relative_year"),
                    observation.get("consolidation"),
                    observation.get("period_type"),
                    observation.get("context_id"),
                )
                if part
            )
            table.add_row(
                key,
                observation["value"],
                observation.get("unit") or "—",
                scope or "—",
                observation["element_id"],
            )
    print(table)
    if payload["unavailable_metrics"]:
        print(f"Unavailable canonical metrics: {', '.join(payload['unavailable_metrics'])}")
    if payload["parse_warnings"]:
        print("Parse notes: " + " · ".join(payload["parse_warnings"]))


@app.command("facts")
def facts(
    doc_id: str,
    query: str | None = typer.Option(None, "--query", "-q"),
    limit: int = typer.Option(100, min=1, max=500),
    base_url: str = typer.Option("http://127.0.0.1:8000"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
) -> None:
    params: dict[str, str | int] = {"limit": limit}
    if query:
        params["q"] = query
    with _client(base_url, token) as client:
        payload = (
            client.get(
                f"/v1/filings/edinet/{doc_id}/facts",
                params=params,
            )
            .raise_for_status()
            .json()
        )

    table = Table("Element", "Label", "Context", "Unit", "Value")
    for item in payload["facts"]:
        table.add_row(
            item["element_id"],
            item["label"],
            item["context_id"],
            item.get("unit") or "—",
            item["value"][:120],
        )
    print(table)
    print(f"Matched {payload['matched_count']} / {payload['total_count']} facts")
