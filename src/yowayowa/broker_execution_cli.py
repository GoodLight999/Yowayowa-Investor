"""Broker execution CLI (P2A) — domain-only, direct service calls.

No HTTP, no transport. There is deliberately NO submit command anywhere
in this app: the Rakuten submission connector is a future task.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from pydantic import ValidationError
from rich import print
from rich.table import Table

from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService
from yowayowa.config import Settings

app = typer.Typer(
    help=(
        "Broker execution domain: proposals, interlocks, audit. "
        "Evaluate only — there is no submit command."
    )
)


def _service(audit_dir: str | None) -> BrokerExecutionDomainService:
    settings = Settings()
    return BrokerExecutionDomainService(
        settings=settings,
        audit_dir=audit_dir if audit_dir is not None else settings.broker_execution_audit_dir,
    )


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _find_proposal(service: BrokerExecutionDomainService, client_order_id: str) -> OrderProposal:
    """Rebuild the proposal from the audit trail; error when never proposed."""

    for entry in reversed(service.audit_entries()):
        if entry.client_order_id == client_order_id and entry.kind == "intent":
            payload = entry.payload
            proposal_payload = payload.get("proposal")
            if isinstance(proposal_payload, dict):
                created = payload.get("created_at")
                if isinstance(created, str):
                    proposal_payload = dict(proposal_payload)
                    proposal_payload.setdefault("created_at", created)
                return OrderProposal.model_validate(proposal_payload)
    raise typer.BadParameter(f"unknown client_order_id: {client_order_id}")


@app.command("audit")
def audit(
    limit: int = typer.Option(20, "--limit", min=1, max=1000),
    audit_dir: str | None = typer.Option(None, "--audit-dir", help="Audit directory override"),
    as_json: bool = typer.Option(False, "--json", help="Print the full entries JSON"),
) -> None:
    """Show recent audit entries (oldest first)."""

    service = _service(audit_dir)
    entries = service.audit_entries(limit=limit)
    if as_json:
        print(_dump([entry.model_dump(mode="json") for entry in entries]))
        return
    table = Table("Seq", "Ts", "Kind", "Client order id")
    for entry in entries:
        table.add_row(str(entry.seq), entry.ts.isoformat(), entry.kind, entry.client_order_id)
    print(table)


@app.command("audit-verify")
def audit_verify(
    audit_dir: str | None = typer.Option(None, "--audit-dir", help="Audit directory override"),
) -> None:
    """Verify the hash chain; print OK or the list of problems."""

    service = _service(audit_dir)
    problems = service.verify_audit()
    if problems:
        print("[bold red]AUDIT CHAIN PROBLEMS[/bold red]")
        for problem in problems:
            print(f"- {problem}")
        raise typer.Exit(code=1)
    print("audit chain OK")


@app.command("proposals-create")
def proposals_create(
    client_order_id: str = typer.Option(..., "--client-order-id"),
    symbol: str = typer.Option(..., "--symbol"),
    market: str = typer.Option("jp", "--market", help="e.g. jp or us"),
    side: str = typer.Option(..., "--side", help="buy or sell"),
    quantity: int = typer.Option(..., "--quantity", min=1),
    order_type: str = typer.Option(..., "--order-type", help="market or limit"),
    limit_price: str | None = typer.Option(None, "--limit-price"),
    reference_price: str | None = typer.Option(None, "--reference-price"),
    currency: str = typer.Option("JPY", "--currency", min=3, max=3),
    motivation: str = typer.Option(..., "--motivation", help="Research → proposal traceability"),
    source_research_link: str | None = typer.Option(None, "--source-research-link"),
    audit_dir: str | None = typer.Option(None, "--audit-dir", help="Audit directory override"),
    as_json: bool = typer.Option(False, "--json", help="Print the full proposal JSON"),
) -> None:
    """Create a proposal, append the intent audit entry, print hash + preview."""

    service = _service(audit_dir)
    fields: dict[str, Any] = {
        "client_order_id": client_order_id,
        "symbol": symbol,
        "market": market,
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "limit_price": limit_price,
        "reference_price": reference_price,
        "currency": currency,
        "motivation": motivation,
    }
    if source_research_link is not None:
        fields["source_research_link"] = source_research_link
    try:
        proposal = service.propose(**fields)
    except (ValueError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    preview = service.preview(proposal)
    if as_json:
        print(
            _dump(
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "proposal_hash": proposal.proposal_hash(),
                    "preview": preview.model_dump(mode="json"),
                }
            )
        )
        return
    print(
        f"[bold]{proposal.client_order_id}[/bold] · {proposal.symbol} · "
        f"{proposal.side.value} {proposal.quantity} · {proposal.order_type.value}"
    )
    print(f"hash {proposal.proposal_hash()}")
    notional_text = "—" if preview.estimated_notional is None else str(preview.estimated_notional)
    print(f"notional {notional_text} {preview.currency}")
    for warning in preview.warnings:
        print(f"warning: {warning}")


@app.command("proposals-evaluate")
def proposals_evaluate(
    client_order_id: str = typer.Argument(..., help="Client order id of an existing proposal"),
    armed: bool = typer.Option(
        False,
        "--armed/--no-armed",
        help="Explicit runtime arming switch (fail-closed default: not armed)",
    ),
    audit_dir: str | None = typer.Option(None, "--audit-dir", help="Audit directory override"),
    as_json: bool = typer.Option(False, "--json", help="Print the full decision JSON"),
) -> None:
    """Evaluate interlocks for a proposal. NEVER submits anything."""

    service = _service(audit_dir)
    proposal = _find_proposal(service, client_order_id)
    decision = service.evaluate(proposal, armed=armed)
    if as_json:
        print(
            _dump(
                {
                    "allowed": decision.allowed,
                    "reasons": list(decision.reasons),
                    "proposal_hash": proposal.proposal_hash(),
                }
            )
        )
        return
    state = (
        "[bold green]ALLOWED[/bold green]" if decision.allowed else "[bold red]BLOCKED[/bold red]"
    )
    print(f"{state} · {client_order_id} · hash {proposal.proposal_hash()}")
    for reason in decision.reasons:
        print(f"- {reason}")


__all__ = ["app"]
