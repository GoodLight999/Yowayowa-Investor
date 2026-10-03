"""CLI surface for machine screening (P4-D).

Exposed as two top-level commands from ``cli_entry`` (flat top-level verbs,
matching the JPX/credit margin CLI style):

- ``screening-run``: run the pipeline once and persist it, then print the
  candidate count and per-source breakdown (personal mode only).
- ``screening-candidates CODE|ALL``: show persisted candidates, optionally
  filtered to one code.
"""

from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.screening_models import ScreeningSource
from yowayowa.services.screening_pipeline import (
    persist_screening_run,
    read_screening_candidates,
    run_screening_pipeline,
)


def screening_run() -> None:
    """Run the machine screening pipeline once and persist it."""

    settings = get_settings()
    try:
        from yowayowa.providers.yahoo_screener import YahooScreenerProvider

        YahooScreenerProvider(settings)
    except Exception as exc:
        raise typer.BadParameter(
            "Machine screening includes personal-only scraped data and is "
            f"not available in this mode ({exc})"
        ) from exc
    session = get_session()
    try:
        result = run_screening_pipeline(session)
        counts = persist_screening_run(session, result)
    finally:
        session.close()
    print(
        f"[bold]screening run {result.run_date.isoformat()}[/bold]: "
        f"{len(result.candidates)} candidates (inserted={counts['inserted']}, "
        f"updated(replaced)={counts['updated']})"
    )
    table = Table("Source", "Candidates", "Detail")
    for source in ScreeningSource:
        detail = result.per_source_counts.get(source.value, {})
        table.add_row(
            source.value,
            str(detail.get("candidates", 0)),
            ", ".join(f"{key}={value}" for key, value in detail.items() if key != "candidates"),
        )
    print(table)
    for candidate in result.candidates:
        print(f"{candidate.code} [{candidate.signal.value}] {candidate.reason}")


def screening_candidates(
    code: str = typer.Argument(..., help="A 4-digit local code, 5-digit code, or ALL"),
    limit: int = typer.Option(50, min=1, max=250),
) -> None:
    """Show persisted screening candidates (``ALL`` lists every code)."""

    session = get_session()
    try:
        rows = read_screening_candidates(session, limit=limit)
    finally:
        session.close()
    wanted = code.strip().upper()
    if wanted != "ALL":
        rows = [row for row in rows if row["code"] == wanted]
    if not rows:
        print(f"No screening candidates persisted for {code}")
        raise typer.Exit(code=1)
    table = Table("Run", "Code", "Source", "Signal", "Reason")
    for row in rows:
        table.add_row(
            str(row["run_date"]),
            str(row["code"]),
            str(row["source"]),
            str(row["signal"]),
            str(row["reason"]),
        )
    print(table)
