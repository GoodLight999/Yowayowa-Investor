"""CLI surface for LLM research brief / ask (P5-A).

Exposed as three top-level commands from ``cli_entry`` (flat top-level verbs,
matching the screening CLI style):

- ``research-brief``: generate the morning brief via the configured AI
  provider and persist it (idempotent per run date). ``--send`` additionally
  delivers it through ``hermes send`` (Telegram home channel) using an
  injectable sender (shell is never used).
- ``research-ask QUESTION``: deterministic cross-evidence lookup over
  screening candidates / EDINET daily / macro observations, then one strict
  agent round; prints answer, citations and tool trace.
- ``research-brief-latest``: print the latest persisted brief.

Provider overrides (``--provider/--model/--api-key/--base-url``) are
per-request BYOK options: the key comes from the environment
(``YOWAYOWA_BRIEF_API_KEY``) or the option, is passed to the agent request,
and is never echoed or persisted.
"""

from __future__ import annotations

import typer
from rich import print
from rich.table import Table

from yowayowa.config import get_settings
from yowayowa.db import get_session
from yowayowa.research_models import AIProviderConfig
from yowayowa.services.research_ask import research_ask as _research_ask_service
from yowayowa.services.research_brief import (
    MorningBriefService,
    persist_research_brief,
    read_latest_research_brief,
)


def _provider_from_options(
    provider: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> AIProviderConfig | None:
    """Per-request provider override; None falls back to server settings."""

    if not (provider and model and api_key):
        if any([provider, model, api_key]):
            raise typer.BadParameter("--provider, --model and --api-key must be supplied together")
        return None
    return AIProviderConfig(
        provider="openai_compatible",
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def research_brief(
    run_date: str = typer.Option(
        "", formats=["%Y-%m-%d"], help="Run date YYYY-MM-DD (default: today)"
    ),
    send: bool = typer.Option(False, "--send", help="Deliver via hermes send (Telegram)"),
    provider: str = typer.Option("", help="openai_compatible provider id override"),
    model: str = typer.Option("", help="Model name override"),
    api_key: str = typer.Option(
        "",
        envvar="YOWAYOWA_BRIEF_API_KEY",
        help="API key override (env var recommended; never echoed)",
    ),
    base_url: str = typer.Option("", help="OpenAI-compatible base URL override"),
    no_persist: bool = typer.Option(False, help="Skip persisting the generated brief"),
) -> None:
    """Generate the morning research brief (and optionally send it)."""

    from datetime import date as date_type

    resolved_run_date = None
    if run_date.strip():
        try:
            resolved_run_date = date_type.fromisoformat(run_date.strip())
        except ValueError as exc:
            raise typer.BadParameter("--run-date must be YYYY-MM-DD") from exc
    provider_config = _provider_from_options(
        provider.strip() or None,
        model.strip() or None,
        api_key.strip() or None,
        base_url.strip() or None,
    )
    settings = get_settings()
    session = get_session()
    try:
        service = MorningBriefService(settings, session, provider_config=provider_config)
        brief = service.compose_brief(run_date=resolved_run_date)
        persisted: dict[str, int] | None = None
        if not no_persist:
            persisted = persist_research_brief(session, brief)
    finally:
        session.close()
    print(
        f"[bold]research brief {brief.run_date.isoformat()}[/bold] "
        f"(provider={brief.provider}/{brief.model}, "
        f"citations={len(brief.citations)}, "
        f"missing_inputs={len(brief.coverage.get('missing_inputs', []))})"
    )
    if persisted is not None:
        print(f"persisted: {persisted}")
    print(brief.answer)
    if send:
        service.send_brief(brief)
        print("[bold]sent via hermes send (telegram)[/bold]")


def research_ask(
    question: str = typer.Argument(..., help="Research question (Japanese or English)"),
    provider: str = typer.Option("", help="openai_compatible provider id override"),
    model: str = typer.Option("", help="Model name override"),
    api_key: str = typer.Option(
        "",
        envvar="YOWAYOWA_BRIEF_API_KEY",
        help="API key override (env var recommended; never echoed)",
    ),
    base_url: str = typer.Option("", help="OpenAI-compatible base URL override"),
) -> None:
    """Answer a question from local evidence, then one agent round."""

    provider_config = _provider_from_options(
        provider.strip() or None,
        model.strip() or None,
        api_key.strip() or None,
        base_url.strip() or None,
    )
    settings = get_settings()
    session = get_session()
    try:
        response = _research_ask_service(
            question,
            session,
            settings,
            provider_config=provider_config,
        )
    finally:
        session.close()
    print(f"[bold]Q:[/bold] {response.question}")
    print(f"provider={response.provider}/{response.model}")
    print(response.answer)
    table = Table("Kind", "Code/Series", "Provider", "As of")
    for citation in response.citations:
        table.add_row(
            citation.kind,
            citation.code_or_series or "—",
            citation.provider,
            citation.as_of or "—",
        )
    print(table)
    print(f"tool_trace: {len(response.tool_trace)} lookups")
    for trace in response.tool_trace:
        matched = trace.get("matched", "n/a")
        print(f"  - {trace.get('tool')}: {trace.get('arguments')} -> matched={matched}")
    missing = response.coverage.get("missing_inputs") or []
    if missing:
        print(f"未取得: {', '.join(missing)}")


def latest_research_brief() -> None:
    """Show the latest persisted research brief."""

    session = get_session()
    try:
        brief = read_latest_research_brief(session)
    finally:
        session.close()
    if brief is None:
        print("No persisted research brief")
        raise typer.Exit(code=1)
    print(
        f"[bold]research brief {brief.run_date.isoformat()}[/bold] "
        f"(provider={brief.provider}/{brief.model}, generated_at={brief.generated_at.isoformat()})"
    )
    print(brief.answer)
