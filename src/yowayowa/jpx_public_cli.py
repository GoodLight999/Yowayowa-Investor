"""CLI for free JPX daily margin publications and deterministic signals."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer
from pydantic import BaseModel
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.jpx_models import normalize_jpx_code
from yowayowa.jpx_public_models import JpxMarginSignalKind
from yowayowa.providers.jpx_public_margin import (
    JpxPublicMarginParseError,
    enforce_jpx_public_margin_policy,
)
from yowayowa.services.jpx_margin_signals import scan_jpx_margin_signals
from yowayowa.services.jpx_public_margin import (
    JpxPublicKind,
    ingest_jpx_public_artifact,
    read_jpx_margin_details,
    read_jpx_margin_flow,
    read_jpx_margin_watch,
    read_jpx_premium,
    sync_jpx_public_margin,
)

_KINDS = frozenset({"balance", "watch", "premium", "flow"})
_SIGNALS = frozenset(
    {
        "crowded-long",
        "crowded-short",
        "long-unwind",
        "short-cover",
        "borrow-stress",
        "flow-buy",
        "flow-sell",
        "buy-flow-divergence",
        "sell-flow-divergence",
        "squeeze-watch",
        "watch-flags",
    }
)


def _require_personal_source() -> None:
    enforce_jpx_public_margin_policy(mode=get_settings().mode)


def _kind(value: str) -> JpxPublicKind:
    normalized = value.strip().lower()
    if normalized not in _KINDS:
        raise typer.BadParameter(
            "kind must be one of balance, watch, premium, flow"
        )
    return cast(JpxPublicKind, normalized)


def _code(value: str) -> str:
    try:
        return normalize_jpx_code(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _echo_models(rows: Sequence[BaseModel]) -> None:
    payload = [row.model_dump(mode="json") for row in rows]
    typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def jpx_public_ingest(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    kind: str = typer.Option(..., "--kind"),
    source_url: str = typer.Option(..., "--source-url"),
) -> None:
    """Ingest one downloaded official JPX PDF/XLSX into the local database."""

    _require_personal_source()
    session = get_session()
    try:
        result = ingest_jpx_public_artifact(
            session,
            file.read_bytes(),
            kind=_kind(kind),
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
        )
    except JpxPublicMarginParseError as exc:
        raise typer.BadParameter(f"JPX public artifact rejected: {exc}") from exc
    finally:
        session.close()
    print(
        f"Ingested {result.kind}: {result.row_count} rows · "
        f"as-of {', '.join(day.isoformat() for day in result.as_of_dates)}"
    )


def jpx_public_sync(
    kinds: str = typer.Option(
        "balance,watch,premium,flow",
        help="Comma-separated: balance,watch,premium,flow",
    ),
) -> None:
    """Fetch and ingest the latest free official JPX publications."""

    _require_personal_source()
    selected = [_kind(value) for value in kinds.split(",") if value.strip()]
    if not selected:
        raise typer.BadParameter("kinds must not be empty")
    settings = get_settings()
    session = get_session()
    try:
        results = sync_jpx_public_margin(
            session,
            kinds=selected,
            cache_dir=Path(settings.jpx_public_raw_cache_dir),
            timeout_seconds=settings.request_timeout_seconds,
        )
    finally:
        session.close()
    table = Table("Kind", "Rows", "As-of", "Raw cache")
    for result in results:
        table.add_row(
            result.kind,
            str(result.row_count),
            ", ".join(day.isoformat() for day in result.as_of_dates),
            result.cached_path or "—",
        )
    print(table)


def jpx_margin_scan(
    signal: str = typer.Argument(...),
    limit: int = typer.Option(50, min=1, max=250),
    long_ratio_threshold: float = typer.Option(10.0, min=0),
    short_ratio_threshold: float = typer.Option(2.0, min=0),
    unwind_threshold: float = typer.Option(0.10, min=0, max=1),
    flow_buy_threshold: float = typer.Option(40.0, min=0, max=100),
    flow_sell_threshold: float = typer.Option(20.0, min=0, max=100),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run a transparent source-backed JPX margin screen."""

    _require_personal_source()
    normalized = signal.strip().lower()
    if normalized not in _SIGNALS:
        raise typer.BadParameter(
            "unknown signal; use crowded-long, crowded-short, long-unwind, "
            "short-cover, borrow-stress, flow-buy, flow-sell, "
            "buy-flow-divergence, sell-flow-divergence, squeeze-watch, or watch-flags"
        )
    session = get_session()
    try:
        rows = scan_jpx_margin_signals(
            session,
            cast(JpxMarginSignalKind, normalized),
            limit=limit,
            long_ratio_threshold=long_ratio_threshold,
            short_ratio_threshold=short_ratio_threshold,
            unwind_threshold=unwind_threshold,
            flow_buy_threshold=flow_buy_threshold,
            flow_sell_threshold=flow_sell_threshold,
        )
    finally:
        session.close()
    if json_output:
        _echo_models(rows)
        return
    table = Table("As-of", "Code", "Company", "Reason", "Metrics", "Available")
    for row in rows:
        table.add_row(
            row.as_of_date.isoformat(),
            row.code,
            row.company_name or "—",
            row.reason,
            json.dumps(row.metrics, ensure_ascii=False, separators=(",", ":")),
            row.available_at.isoformat(),
        )
    print(table)


def jpx_margin_detail(
    code: str = typer.Argument(...),
    limit: int = typer.Option(30, min=1, max=250),
) -> None:
    _require_personal_source()
    session = get_session()
    try:
        rows = read_jpx_margin_details(session, _code(code), limit=limit)
    finally:
        session.close()
    _echo_models(rows)


def jpx_margin_flow(
    code: str = typer.Argument(...),
    limit: int = typer.Option(30, min=1, max=250),
) -> None:
    _require_personal_source()
    session = get_session()
    try:
        rows = read_jpx_margin_flow(session, _code(code), limit=limit)
    finally:
        session.close()
    _echo_models(rows)


def jpx_margin_watch(
    code: str = typer.Argument(...),
    limit: int = typer.Option(30, min=1, max=250),
) -> None:
    _require_personal_source()
    session = get_session()
    try:
        rows = read_jpx_margin_watch(session, _code(code), limit=limit)
    finally:
        session.close()
    _echo_models(rows)


def jpx_premium(
    source_code: str = typer.Argument(...),
    limit: int = typer.Option(30, min=1, max=250),
) -> None:
    _require_personal_source()
    normalized = source_code.strip().upper()
    if not re.fullmatch(r"[0-9]{3}[0-9A-Z]", normalized):
        raise typer.BadParameter("source_code must be a 4-character JPX code")
    session = get_session()
    try:
        rows = read_jpx_premium(session, normalized, limit=limit)
    finally:
        session.close()
    _echo_models(rows)


__all__ = [
    "jpx_margin_detail",
    "jpx_margin_flow",
    "jpx_margin_scan",
    "jpx_margin_watch",
    "jpx_premium",
    "jpx_public_ingest",
    "jpx_public_sync",
]
