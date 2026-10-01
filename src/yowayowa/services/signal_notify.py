"""Daily strategy-signal Telegram delivery with per-trading-date deduplication (t_8e24821d).

The screener surfaces (``GET /v1/screening/strategy``, ``yowayowa signals`` and
``evaluate_strategy_signal_alerts``) are strictly read-only and never transmit.
This module is the ONLY send path, reached exclusively through the explicit
daily entry point (``yowayowa signals-send`` / the ``yowayowa-signals-notify``
systemd oneshot). A GET can never send, and tests always inject a sender.

Delivery contract (mirrors ``broker/session_notify.py``):

- Default transport is a subprocess list-argv ``hermes send --to telegram``
  (no shell, no quoting pitfalls).
- One message per trading date: the JSON state file records the signal
  date + alert fingerprint that was last delivered; a re-run for the same
  date with unchanged alerts is suppressed. A DIFFERENT fingerprint for the
  same date (rankings genuinely changed intraday) sends again and overwrites
  the record.
- Fail-open for state: a missing, unreadable, or corrupt state file is an
  empty state, so the notification is guaranteed to be attempted rather than
  silently swallowed.
- Fail-closed for sending: a sender failure leaves the state unwritten, so
  the next run retries. State-write failures after a successful send never
  mask the send (worst case: one duplicate notification, accepted as in
  session_notify).
- Every message carries the source-backed as-of trading date and the
  provenance (provider / source / license / retrieved_at) of the underlying
  OHLCV rows, so the operator can judge the data before acting.

This state file is NOT the broker-execution single-writer audit directory and
has no ordering guarantees, exactly like ``session_notify``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SIGNAL_NOTIFY_VERSION = 1
DEFAULT_NOTIFY_TARGET = "telegram"  # hermes send's home channel

_HERMES_SEND_TIMEOUT_SECONDS = 60


def _default_sender(message: str) -> None:
    """Send via the hermes CLI (no shell, no quoting pitfalls)."""

    subprocess.run(
        ["hermes", "send", "--to", DEFAULT_NOTIFY_TARGET, message],
        check=True,
        capture_output=True,
        timeout=_HERMES_SEND_TIMEOUT_SECONDS,
    )


def build_alert_fingerprint(alerts: list[dict[str, Any]]) -> str:
    """Content fingerprint of the alert list (type/symbol/as_of/value)."""

    parts = [
        "|".join(
            (
                str(alert.get("type")),
                str(alert.get("symbol")),
                str(alert.get("as_of")),
                f"{float(alert.get('return_20d', 0.0)):.10g}",
            )
        )
        for alert in alerts
    ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def render_signal_message(result: dict[str, Any]) -> str:
    """Render the operator-facing Telegram message with provenance preserved.

    ``result`` is the ``compute_daily_strategy_signals`` payload. The message
    is research candidates only — never buy recommendations — and keeps the
    source/as-of provenance attached to every rank it lists.
    """

    as_of = result.get("as_of")
    header = f"戦略シグナル {as_of}" if as_of is not None else "戦略シグナル (基準日なし)"
    lines = [header]
    for label, key, field in (
        ("低ボラ", "low_volatility", "daily_volatility"),
        ("20日下落", "mean_reversion", "return_20d"),
    ):
        rows = result.get(key) or []
        if not rows:
            continue
        rendered = ", ".join(f"{row['symbol']}({float(row[field]):.4g})" for row in rows)
        lines.append(f"{label}: {rendered}")
    alerts = result.get("alerts") or []
    lines.append(f"下落候補アラート: {len(alerts)}件")
    provenance = result.get("provenance") or {}
    providers = sorted({str(row.get("provider")) for row in provenance.values() if row})
    sources = sorted({str(row.get("source_url")) for row in provenance.values() if row})
    retrieved = [str(row.get("retrieved_at")) for row in provenance.values() if row]
    lines.append(f"provider: {', '.join(providers) or 'unknown'}")
    # Compact provenance: distinct source hosts + the retrieved_at window,
    # never a per-symbol URL flood (Telegram-bound message).
    hosts = sorted({url.split("/")[2] for url in sources if "://" in url})
    if not hosts:
        hosts = ["unknown-source"]
    lines.append(f"source: {', '.join(hosts)} ({len(sources)} distinct URL(s))")
    if retrieved:
        lines.append(f"retrieved_at: {min(retrieved)} .. {max(retrieved)}")
    else:
        lines.append("retrieved_at: unknown")
    lines.append("備考: 候補提示のみ・売買推奨ではない")
    return "\n".join(lines)


class SignalDeliveryNotifier:
    """Deduplicated, retryable sender of daily strategy-signal notifications."""

    def __init__(
        self,
        state_path: str,
        *,
        sender: Callable[[str], None] | None = None,
    ) -> None:
        self._state_path = state_path
        self._sender: Callable[[str], None] = sender if sender is not None else _default_sender

    # --------------------------------------------------------------- state io

    def _load_state(self) -> dict[str, Any]:
        """Fail-open read: missing/unreadable/corrupt state is an empty state."""

        try:
            with open(self._state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            return {}
        if not isinstance(state, dict) or not isinstance(state.get("delivered"), dict):
            return {}
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        target = Path(self._state_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----------------------------------------------------------------- notify

    def notify(
        self,
        result: dict[str, Any],
        *,
        alerts: list[dict[str, Any]] | None = None,
        evaluated_at: datetime | None = None,
    ) -> bool:
        """Deliver today's signal message once per (signal date, fingerprint).

        Returns True when a notification was actually sent (and recorded),
        False when the same date+alerts were already delivered or when the
        sender failed (state stays unwritten so the next run retries).
        """

        resolved_alerts = result["alerts"] if alerts is None else alerts
        as_of = result.get("as_of")
        date_key = as_of.isoformat() if as_of is not None else "unknown"
        fingerprint = build_alert_fingerprint(list(resolved_alerts))

        state = self._load_state()
        delivered = state.setdefault("delivered", {})
        previous = delivered.get(date_key)
        if (
            isinstance(previous, dict)
            and previous.get("fingerprint") == fingerprint
            and previous.get("sent_at")
        ):
            return False

        message = render_signal_message(result)
        try:
            self._sender(message)
        except Exception:
            # Sender failure: keep state unwritten so the next run retries.
            return False

        delivered[date_key] = {
            "fingerprint": fingerprint,
            "alerts": len(resolved_alerts),
            "sent_at": (evaluated_at or datetime.now(UTC)).isoformat(),
        }
        state["version"] = SIGNAL_NOTIFY_VERSION
        try:
            self._save_state(state)
        except Exception:
            # The message was sent; a state-write failure must not mask it.
            return True
        return True


__all__ = [
    "DEFAULT_NOTIFY_TARGET",
    "SIGNAL_NOTIFY_VERSION",
    "SignalDeliveryNotifier",
    "build_alert_fingerprint",
    "render_signal_message",
]
