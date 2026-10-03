"""Broker execution CLI (P2A/P2B) — direct service/transport calls.

P2A commands are domain-only (proposals, interlocks, audit). The P2B
``submit`` command is the only submission surface, and it fails closed:
the COO freeze gate (``--submissions-enabled``, default False) is
checked before anything else, so the default invocation can never reach
the proposal lookup, the browser session, the DOM, or a stage=submit
audit entry. No HTTP API route exists for submission by design (CTO
decision: keep the HTTP exposure unchanged; API-first is satisfied by
CLI and domain sharing the same service).

The P2C2 ``orders`` / ``order-status`` commands are read-only and call
the API (the P1B web session lives behind it), mirroring the
broker-read CLI pattern.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import typer
from pydantic import ValidationError
from rich import print
from rich.table import Table

from yowayowa.broker.execution.models import OrderProposal
from yowayowa.broker.execution.service import BrokerExecutionDomainService, DuplicateProposalError
from yowayowa.config import Settings

if TYPE_CHECKING:
    from yowayowa.broker.execution.transport import RakutenWebSubmissionTransport

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


def _submission_transport(
    audit_dir: str | None, submissions_enabled: bool
) -> tuple[BrokerExecutionDomainService, RakutenWebSubmissionTransport, Any]:
    """Build the domain service and the Rakuten web submission transport.

    Importing the real web session (and therefore playwright) is
    deferred to here so the module — and every existing command — keeps
    working on machines without the operator-browser extra. When the COO
    freeze gate is shut, no session object is created at all: the
    transport is built with a null session it can never reach (the gate
    fires before any session access inside submit_order).
    """

    settings = Settings()
    service = BrokerExecutionDomainService(
        settings=settings,
        audit_dir=audit_dir if audit_dir is not None else settings.broker_execution_audit_dir,
    )
    if not submissions_enabled:
        from yowayowa.broker.execution.transport import (
            RakutenWebSubmissionTransport as _Transport,
        )

        return (
            service,
            _Transport(
                session=_FrozenNullWebSession(),
                service=service,
                settings=settings,
                submissions_enabled=False,
            ),
            _FrozenNullWebSession(),
        )
    try:
        from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession
    except ImportError as exc:
        raise typer.BadParameter(
            "the Rakuten web session requires the operator-browser extra: "
            "uv sync --extra operator-browser && uv run playwright install chromium"
        ) from exc
    from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_BASE_URL

    session = PersistentBrokerWebSession(
        base_url=RAKUTEN_WEB_BASE_URL,
        profile_dir=settings.broker_rakuten_web_profile_dir,
        headless=False,
        user_agent=settings.broker_rakuten_web_user_agent,
    )
    from yowayowa.broker.execution.transport import RakutenWebSubmissionTransport
    from yowayowa.broker.session_notify import SessionExpiryNotifier

    transport = RakutenWebSubmissionTransport(
        session=session,
        service=service,
        settings=settings,
        submissions_enabled=submissions_enabled,
        expiry_notifier=SessionExpiryNotifier(settings.broker_session_notify_state_path),
    )
    return service, transport, session


def _dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _find_proposal(service: BrokerExecutionDomainService, client_order_id: str) -> OrderProposal:
    """Rebuild the proposal from the audit trail; error when never proposed."""

    proposal = service.find_audited_proposal(client_order_id)
    if proposal is None:
        raise typer.BadParameter(f"unknown client_order_id: {client_order_id}")
    return proposal


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
    except DuplicateProposalError as exc:
        print(f"[bold red]error: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc
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


@app.command("submit")
def submit(
    client_order_id: str = typer.Argument(..., help="Client order id of an existing proposal"),
    armed: bool = typer.Option(
        False,
        "--armed/--no-armed",
        help="Explicit runtime arming switch (fail-closed default: not armed)",
    ),
    submissions_enabled: bool = typer.Option(
        False,
        "--submissions-enabled/--no-submissions-enabled",
        help=(
            "COO freeze master gate for real submission. Default False: the "
            "command audits submit-frozen and never touches the session, the "
            "DOM, or stage=submit audit"
        ),
    ),
    audit_dir: str | None = typer.Option(None, "--audit-dir", help="Audit directory override"),
    as_json: bool = typer.Option(False, "--json", help="Print the full receipt JSON"),
) -> None:
    """Submit one audited proposal through the Rakuten web session.

    Fail-closed by construction: the intent is rebuilt from the audit
    trail (never re-entered), and while --submissions-enabled is False
    (the default) the COO freeze gate rejects the submission before any
    browser session is opened.
    """

    service, transport, session = _submission_transport(audit_dir, submissions_enabled)
    proposal = _find_proposal(service, client_order_id)
    from yowayowa.broker_models import BrokerOrderIntent

    intent = BrokerOrderIntent(
        client_order_id=proposal.client_order_id,
        symbol=proposal.symbol,
        side=proposal.side,
        quantity=proposal.quantity,
        order_type=proposal.order_type,
        limit_price=proposal.limit_price,
        reference_price=proposal.reference_price,
        currency=proposal.currency,
    )
    try:
        if submissions_enabled:
            session.start()
        receipt = transport.submit_order(intent, armed=armed)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    if as_json:
        typer.echo(_dump(receipt.model_dump(mode="json")))
        return
    state = (
        "[bold green]ACCEPTED[/bold green]"
        if receipt.accepted
        else "[bold red]NOT ACCEPTED[/bold red]"
    )
    print(f"{state} · {receipt.status.value} · {receipt.client_order_id}")
    if receipt.broker_order_id is not None:
        print(f"broker order id {receipt.broker_order_id}")
    if receipt.message:
        print(receipt.message)


class _FrozenNullWebSession:
    """Placeholder session while the COO freeze gate is shut.

    Every method raises, so an accidental gate regression cannot silently
    touch a browser: fail-closed by construction.
    """

    def open(self, path: str = "") -> Any:
        raise RuntimeError("submissions_enabled is False; no browser session is available")

    def request(self, method: str, path: str) -> Any:
        raise RuntimeError("submissions_enabled is False; no browser session is available")

    def page(self) -> Any:
        raise RuntimeError("submissions_enabled is False; no browser session is available")

    def start(self) -> None:
        raise RuntimeError("submissions_enabled is False; no browser session is available")

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# P2C2: read-only order inquiry (API-backed, same _client pattern as peers)
# ---------------------------------------------------------------------------


def _orders_report(
    api_url: str,
    token: str | None,
    path: str,
    params: dict[str, str],
) -> dict[str, Any]:
    from yowayowa.cli import _client

    with _client(api_url, token) as client:
        payload: dict[str, Any] = client.get(path, params=params).raise_for_status().json()
    return payload


def _orders_table(payload: dict[str, Any]) -> Table:
    table = Table(
        "Broker order id",
        "Symbol",
        "Side",
        "Qty",
        "Filled",
        "Status",
        "Match",
        "Client order id",
    )
    for item in payload.get("items", []):
        order = item.get("order", {})
        table.add_row(
            str(order.get("broker_order_id") or "—"),
            str(order.get("symbol") or "—"),
            str(order.get("side") or "—"),
            str(order.get("quantity") if order.get("quantity") is not None else "—"),
            str(order.get("filled_quantity") if order.get("filled_quantity") is not None else "—"),
            str(order.get("status") or "—"),
            str(item.get("match") or "—"),
            str(item.get("client_order_id") or "—"),
        )
    return table


def _orders_print_notes(payload: dict[str, Any]) -> None:
    if not payload.get("intact_audit", True):
        print("[bold red]audit chain problems reported (intact_audit=false)[/bold red]")
    for note in payload.get("notes", []):
        print(f"note: {note}")


@app.command("orders")
def orders(
    market: str = typer.Option("jp", "--market", help="jp or us"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh/--no-force-refresh", help="Bypass the read cache"
    ),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
    as_json: bool = typer.Option(False, "--json", help="Print the full report JSON"),
) -> None:
    """List web-queried orders matched against the execution audit trail."""

    market = _validate_market(market)
    payload = _orders_report(
        api_url,
        token,
        "/v1/broker-execution/orders",
        {"market": market, "force_refresh": "true" if force_refresh else "false"},
    )
    if as_json:
        typer.echo(_dump(payload))
        return
    print(
        f"{payload.get('broker')} · {payload.get('market')} · "
        f"{len(payload.get('items', []))} orders"
    )
    _orders_print_notes(payload)
    print(_orders_table(payload))


@app.command("order-status")
def order_status(
    client_order_id: str = typer.Argument(..., help="Client order id of an audited proposal"),
    market: str = typer.Option("jp", "--market", help="jp or us"),
    api_url: str = typer.Option("http://127.0.0.1:8000", "--api-url"),
    token: str | None = typer.Option(None, envvar="YOWAYOWA_API_TOKEN"),
    as_json: bool = typer.Option(False, "--json", help="Print the full report JSON"),
) -> None:
    """Show one audited order matched against the web inquiry (404 if unknown)."""

    market = _validate_market(market)
    try:
        payload = _orders_report(
            api_url,
            token,
            f"/v1/broker-execution/orders/{client_order_id}",
            {"market": market},
        )
    except Exception as exc:
        message = str(exc)
        if "404" in message:
            print(f"[bold red]unknown client_order_id: {client_order_id}[/bold red]")
            raise typer.Exit(code=1) from exc
        raise
    if as_json:
        typer.echo(_dump(payload))
        return
    print(
        f"{payload.get('broker')} · {payload.get('market')} · "
        f"{len(payload.get('items', []))} item(s)"
    )
    _orders_print_notes(payload)
    print(_orders_table(payload))


def _validate_market(market: str) -> str:
    normalized = market.strip().lower()
    if normalized not in {"jp", "us"}:
        raise typer.BadParameter("market must be jp or us")
    return normalized


__all__ = ["_FrozenNullWebSession", "app"]
