"""CLI surface for daily strategy-signal Telegram delivery (t_8e24821d).

``signals-send`` is the ONE explicit daily entry point that evaluates the
persisted-store strategy signals and delivers the digest through
``hermes send --to telegram``. It is a deliberate side-effecting command:

- The read-only surfaces (``yowayowa signals``, ``GET /v1/screening/strategy``)
  never send; no GET or evaluation path reaches this module.
- Deduplication: one message per (signal date, alert fingerprint); re-running
  the command the same day is a no-op unless the candidates genuinely changed.
- Send failures leave the delivery state unwritten, so the next run retries.
- ``YOWAYOWA_SIGNAL_NOTIFY_DISABLED=1`` disables the transport entirely
  (test/CI kill switch and operator maintenance switch).
- The sender is injectable for tests; the real ``hermes`` CLI is never
  invoked from the test suite.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import typer
from rich import print

from yowayowa.config import get_settings
from yowayowa.services.alerts import (
    evaluate_strategy_signal_alerts,
    load_persisted_signal_histories,
)
from yowayowa.services.signal_notify import SignalDeliveryNotifier, _default_sender
from yowayowa.services.strategy_signals import compute_daily_strategy_signals
from yowayowa.stock_acquisition import default_store


def signals_send(
    state_path: str = typer.Option(
        "",
        help="Delivery state JSON path (default: settings.strategy_signal_notify_state_path)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the payload as JSON"),
) -> None:
    """Evaluate daily strategy signals and send the Telegram digest.

    Explicit daily entry point for the operator scheduler; deduplicates per
    trading date + alert fingerprint. Read-only surfaces never send.
    """

    histories = load_persisted_signal_histories(default_store())
    result = compute_daily_strategy_signals(histories)
    alerts = evaluate_strategy_signal_alerts(histories)
    if json_output:
        print(json.dumps({**result, "alerts": alerts}, ensure_ascii=False, default=str))

    as_of = result.get("as_of")
    resolved_state_path = state_path.strip() or get_settings().strategy_signal_notify_state_path
    if os.getenv("YOWAYOWA_SIGNAL_NOTIFY_DISABLED", "").strip() == "1":
        print(
            f"delivery disabled (YOWAYOWA_SIGNAL_NOTIFY_DISABLED=1): "
            f"evaluated {as_of} with {len(alerts)} alert candidate(s); nothing sent"
        )
        return

    notifier = SignalDeliveryNotifier(resolved_state_path, sender=_default_sender)
    sent = notifier.notify(
        result,
        alerts=alerts,
        evaluated_at=datetime.now(UTC),
    )
    if sent:
        print(f"sent strategy signal digest for {as_of} via hermes send (telegram)")
    else:
        print(
            f"no send for {as_of}: already delivered (same date + alerts) "
            "or send failed (state unwritten; next run retries)"
        )
    if not alerts:
        print("no negative-return alert candidates today")


def build_delivery_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Expose the delivery state for diagnostics (no secrets inside)."""

    return {
        "version": state.get("version"),
        "delivered": state.get("delivered", {}),
    }


__all__ = ["signals_send"]
