"""CLI surface for weekly credit margin balances (P4-C).

Exposed as two top-level commands from ``cli_entry`` (flat top-level verbs,
matching the operator workflow, same style as the JPX margin CLI):

- ``credit-margin-fetch CODE...``: fetch the Yahoo!ファイナンス (and optionally
  kabutan) weekly credit pages for the codes and persist them (personal mode
  only, 1 URL = 1 request, sequential).
- ``credit-margin CODE``: show the persisted weekly history for one code.
"""

from __future__ import annotations

import httpx
import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.credit_margin_models import normalize_credit_margin_code
from yowayowa.db import get_session
from yowayowa.providers.credit_margin_kabutan import (
    CreditMarginKabutanError,
    enforce_credit_margin_kabutan_policy,
    fetch_credit_margin_kabutan,
)
from yowayowa.providers.credit_margin_yahoo import (
    CreditMarginYahooError,
    enforce_credit_margin_yahoo_policy,
    fetch_credit_margin_yahoo,
)
from yowayowa.services.credit_margin import persist_credit_margin_weekly, read_credit_margin_by_code


def credit_margin_fetch(
    codes: list[str] = typer.Argument(..., help="One or more local codes (e.g. 7203 6758)"),
    source: str = typer.Option(
        "yahoo",
        "--source",
        help="Provider to fetch: 'yahoo', 'kabutan', or 'both'.",
    ),
) -> None:
    """Fetch weekly credit margin pages for the codes and persist them."""

    settings = get_settings()
    enforce_credit_margin_yahoo_policy(mode=settings.mode)
    enforce_credit_margin_kabutan_policy(mode=settings.mode)
    normalized: list[str] = []
    for raw in codes:
        try:
            normalized.append(normalize_credit_margin_code(raw))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    source_key = source.strip().lower()
    if source_key not in {"yahoo", "kabutan", "both"}:
        raise typer.BadParameter("source must be one of: yahoo, kabutan, both")

    # One URL = one request, sequential (rate consideration; no auth/cookies).
    with httpx.Client(
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            )
        },
        timeout=30.0,
    ) as client:
        for code in normalized:
            if source_key in {"yahoo", "both"}:
                try:
                    weeklies = fetch_credit_margin_yahoo(code, client=client)
                except CreditMarginYahooError as exc:
                    raise typer.BadParameter(f"Yahoo credit margin rejected: {exc}") from exc
                counts = persist_credit_margin_weekly(get_session(), weeklies)
                print(f"[yahoo] {code}: {counts}")
            if source_key in {"kabutan", "both"}:
                try:
                    weeklies = fetch_credit_margin_kabutan(code, client=client)
                except CreditMarginKabutanError as exc:
                    raise typer.BadParameter(f"kabutan credit margin rejected: {exc}") from exc
                counts = persist_credit_margin_weekly(get_session(), weeklies)
                print(f"[kabutan] {code}: {counts}")


def credit_margin(
    code: str = typer.Argument(...),
    limit: int = typer.Option(20, min=1, max=250),
) -> None:
    """Show the persisted weekly credit margin history for one code."""

    enforce_credit_margin_yahoo_policy(mode=get_settings().mode)
    try:
        normalized = normalize_credit_margin_code(code)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    session = get_session()
    try:
        series = read_credit_margin_by_code(session, normalized, limit=limit)
    finally:
        session.close()
    if not series.points:
        print(f"No credit margin weeks persisted for {normalized}")
        raise typer.Exit(code=1)
    table = Table("As of", "Code", "Short", "Long", "Short chg", "Long chg", "S/L ratio")
    for point in series.points:
        table.add_row(
            point.as_of_date.isoformat(),
            point.code,
            str(point.short_total),
            str(point.long_total),
            "—" if point.short_change is None else f"{point.short_change:+d}",
            "—" if point.long_change is None else f"{point.long_change:+d}",
            "—" if point.short_long_ratio is None else f"{point.short_long_ratio:.3f}",
        )
    print(table)
    print(
        "Derived fields are computed from persisted weeks only: no previous week "
        "means no change; a zero long balance means no ratio."
    )
