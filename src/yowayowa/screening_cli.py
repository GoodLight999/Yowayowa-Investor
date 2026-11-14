"""CLI surface for machine screening (P4-D).

Exposed as two top-level commands from ``cli_entry`` (flat top-level verbs,
matching the JPX/credit margin CLI style):

- ``screening-run``: run the pipeline once and persist it, then print the
  candidate count and per-source breakdown (personal mode only).
- ``screening-candidates CODE|ALL``: show persisted candidates, optionally
  filtered to one code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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

_UNAVAILABLE = "unavailable"


def _provenance_date(provenance: Any, key: str) -> str:
    """Render one provenance date field for display, or ``unavailable``.

    Date contract for the candidates table (roadmap P4: expose stale and
    incomplete coverage explicitly):

    - ``Run date`` is when the pipeline ran; it says nothing about how fresh
      the underlying data is.
    - ``As of`` is the data's own as-of timestamp (``provenance.as_of``).
    - ``Retrieved`` is when the source was fetched (``provenance.retrieved_at``).

    Only the candidate's own persisted provenance is shown. A missing, legacy
    or unparseable value degrades to ``unavailable`` — it is never backfilled
    from the run date, the current time or any other field. The persisted ISO
    string is echoed verbatim after validation, so an offset (or the absence
    of one) is preserved exactly as captured; naive values are never silently
    relabelled as UTC.
    """

    if not isinstance(provenance, dict):
        return _UNAVAILABLE
    raw = provenance.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return _UNAVAILABLE
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        return _UNAVAILABLE
    return raw


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

    wanted = code.strip().upper()
    session = get_session()
    try:
        # The code filter belongs in the query, not in a slice of the first
        # ``limit`` rows: filtering after the limit reported a persisted
        # candidate as absent whenever it fell outside the first page
        # (workflow dead-end audit 2026-10-23, doc P5_DEADEND_AUDIT_20261023).
        rows = read_screening_candidates(
            session,
            code=None if wanted == "ALL" else wanted,
            limit=limit,
        )
    finally:
        session.close()
    if not rows:
        print(f"No screening candidates persisted for {code}")
        raise typer.Exit(code=1)
    table = Table("Run date", "Code", "Source", "Signal", "Reason", "As of", "Retrieved")
    for row in rows:
        provenance = row.get("provenance")
        table.add_row(
            str(row["run_date"]),
            str(row["code"]),
            str(row["source"]),
            str(row["signal"]),
            str(row["reason"]),
            _provenance_date(provenance, "as_of"),
            _provenance_date(provenance, "retrieved_at"),
        )
    print(table)
