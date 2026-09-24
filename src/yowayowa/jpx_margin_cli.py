"""CLI surface for JPX daily issue-level margin balances (P4-A).

Exposed as two top-level commands from ``cli_entry`` (flat top-level verbs,
matching the operator workflow rather than a nested sub-app):

- ``jpx-margin-ingest FILE``: parse a downloaded JPX CSV and persist it
  (local DB, same services as the API). Ingest is offline: the file is
  fetched by the operator (or a later scheduled task); this command never
  performs network access.
- ``jpx-margin CODE``: show the persisted balance history for one code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.db import JpxMarginBalanceRecord, get_session
from yowayowa.jpx_models import normalize_jpx_code
from yowayowa.providers.jpx_margin import JpxMarginCsvError, enforce_jpx_margin_policy
from yowayowa.services.jpx_margin import ingest_jpx_margin_csv, read_jpx_margin_by_code


def jpx_margin_ingest(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    source_url: str = typer.Option(
        ...,
        "--source-url",
        help="Where these bytes came from (download URL / FTP note); stored in provenance.",
    ),
) -> None:
    """Parse a JPX margin CSV (EN or JP) and persist it, replacing that application date."""

    enforce_jpx_margin_policy(mode=get_settings().mode)
    try:
        balances = ingest_jpx_margin_csv(
            get_session(),
            file.read_bytes(),
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
        )
    except JpxMarginCsvError as exc:
        raise typer.BadParameter(f"JPX margin CSV rejected: {exc}") from exc
    dates = sorted({balance.application_date for balance in balances})
    print(
        f"Ingested {len(balances)} rows for {len(dates)} application date(s): "
        f"{', '.join(day.isoformat() for day in dates)}"
    )


def jpx_margin(
    code: str = typer.Argument(...),
    limit: int = typer.Option(30, min=1, max=250),
) -> None:
    """Show the persisted balance history for one code (local DB)."""

    enforce_jpx_margin_policy(mode=get_settings().mode)
    try:
        normalized = normalize_jpx_code(code)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    session = get_session()
    series = read_jpx_margin_by_code(session, normalized, limit=limit)
    if not series.points:
        print(f"No JPX margin balances persisted for {normalized}")
        raise typer.Exit(code=1)
    companies = {
        row.code: (row.company_name or "—")
        for row in session.query(JpxMarginBalanceRecord)
        .filter(JpxMarginBalanceRecord.code == normalized)
        .all()
    }
    table = Table(
        "Application date",
        "Code",
        "Company",
        "Short total",
        "Long total",
        "Short chg",
        "Long chg",
        "S/L ratio",
        "Short value (JPY)",
        "Long value (JPY)",
    )
    for point in series.points:
        table.add_row(
            point.application_date.isoformat(),
            point.code,
            companies.get(point.code, "—"),
            str(point.short_total),
            str(point.long_total),
            "—" if point.short_change is None else f"{point.short_change:+d}",
            "—" if point.long_change is None else f"{point.long_change:+d}",
            "—" if point.short_long_ratio is None else f"{point.short_long_ratio:.3f}",
            "—" if point.short_total_value is None else f"{point.short_total_value:,}",
            "—" if point.long_total_value is None else f"{point.long_total_value:,}",
        )
    print(table)
    print(
        "Derived fields are computed from persisted balances only: no previous "
        "application date means no change; a zero long balance means no ratio."
    )
