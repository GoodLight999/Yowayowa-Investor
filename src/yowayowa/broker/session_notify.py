"""Session-expiry notification for the Rakuten broker web session (P2C).

When a broker read or submission path detects ``AUTH_EXPIRED`` / a failed
authentication probe, the operator is told once — through the ``hermes send``
CLI, targeting its Telegram home channel — that the session has expired and a
re-login is required. Repeated detections of the same connector's expiry are
suppressed for a configurable window so a burst of reads cannot flood the
channel; a successful ``notify_authenticated`` clears the suppression so the
NEXT expiry after a re-login is notified again.

M4 note: this JSON state file is NOT the broker-execution single-writer
audit directory and has no ordering guarantees. The worst case under a
concurrent race is at most one duplicate Telegram notification, which is
accepted by design; the audit trail itself remains the single-writer M4
surface and is untouched here.

Fail-open for state: a missing, unreadable, or corrupt state file is treated
as empty so the notification is guaranteed to be sent rather than silently
swallowed. Fail-closed for sending: a sender failure never records state, so
the next detection retries the notification.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yowayowa.operator_bridge.rakuten_web import RAKUTEN_WEB_CONNECTOR_ID

SESSION_NOTIFY_VERSION = 1
DEFAULT_SUPPRESS_SECONDS = 3600  # 1 hour
DEFAULT_NOTIFY_TARGET = "telegram"  # hermes send's home channel
RELOGIN_REQUIRED_MESSAGE = "楽天証券のセッションが失効しています。再ログインが必要です。"

_HERMES_SEND_TIMEOUT_SECONDS = 60


def _default_sender(message: str) -> None:
    """Send via the hermes CLI (no shell, no quoting pitfalls)."""

    subprocess.run(
        ["hermes", "send", "--to", DEFAULT_NOTIFY_TARGET, message],
        check=True,
        capture_output=True,
        timeout=_HERMES_SEND_TIMEOUT_SECONDS,
    )


class SessionExpiryNotifier:
    """Deduplicated, suppressible sender of session-expiry notifications."""

    def __init__(
        self,
        state_path: str | Path,
        *,
        sender: Callable[[str], None] | None = None,
        suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._state_path = Path(state_path)
        self._sender: Callable[[str], None] = sender if sender is not None else _default_sender
        self._suppress_seconds = suppress_seconds
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    # --------------------------------------------------------------- state io

    def _load_state(self) -> dict[str, Any]:
        """Fail-open read: missing/unreadable/corrupt state is an empty state."""

        try:
            raw = self._state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(state, dict):
            return {}
        suppressed = state.get("suppressed")
        if not isinstance(suppressed, dict):
            return {}
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ----------------------------------------------------------------- notify

    def notify_session_expired(
        self,
        *,
        source: str,
        connector_id: str = RAKUTEN_WEB_CONNECTOR_ID,
        detail: str = "",
    ) -> bool:
        """Notify once per connector within the suppression window.

        Returns True when a notification was actually sent (and recorded),
        False when suppressed or when the sender failed (state stays
        unwritten so the next detection retries).
        """

        state = self._load_state()
        suppressed = state.setdefault("suppressed", {})
        entry = suppressed.get(connector_id)
        now = self._clock()
        if isinstance(entry, dict):
            sent_at = entry.get("sent_at")
            if isinstance(sent_at, str):
                try:
                    previous = datetime.fromisoformat(sent_at)
                except ValueError:
                    previous = None
                if previous is not None:
                    if previous.tzinfo is None:
                        previous = previous.replace(tzinfo=UTC)
                    if (now - previous).total_seconds() < self._suppress_seconds:
                        return False

        lines = [RELOGIN_REQUIRED_MESSAGE, f"source: {source}"]
        if detail:
            lines.append(f"detail: {detail}")
        lines.append(f"time: {now.isoformat()}")
        message = "\n".join(lines)

        try:
            self._sender(message)
        except Exception:
            # Sender failure: keep state unwritten so the next detection
            # retries the notification.
            return False

        suppressed[connector_id] = {
            "source": source,
            "sent_at": now.isoformat(),
        }
        state["version"] = SESSION_NOTIFY_VERSION
        try:
            self._save_state(state)
        except Exception:
            # The message was sent; a state-write failure must not mask it.
            return True
        return True

    def notify_authenticated(self, connector_id: str = RAKUTEN_WEB_CONNECTOR_ID) -> None:
        """Clear the suppression entry so the NEXT expiry notifies again."""

        state = self._load_state()
        suppressed = state.get("suppressed")
        if not isinstance(suppressed, dict):
            return
        if connector_id not in suppressed:
            return
        del suppressed[connector_id]
        try:
            self._save_state(state)
        except Exception:
            # Best-effort clearing: a failure here only risks an extra
            # suppressed notification later, never a wrong notification.
            return


__all__ = [
    "DEFAULT_NOTIFY_TARGET",
    "DEFAULT_SUPPRESS_SECONDS",
    "RELOGIN_REQUIRED_MESSAGE",
    "SESSION_NOTIFY_VERSION",
    "SessionExpiryNotifier",
]
