"""P2C SessionExpiryNotifier tests (contract: >= 6 required cases).

All tests inject a fake sender; the real ``hermes`` CLI is never invoked
except in ``test_default_sender_runs_hermes_send``, which monkeypatches
``subprocess.run``. State files live in ``tmp_path`` only.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yowayowa.broker.session_notify import (
    DEFAULT_NOTIFY_TARGET,
    RELOGIN_REQUIRED_MESSAGE,
    SESSION_NOTIFY_VERSION,
    SessionExpiryNotifier,
)

_FIXED_NOW = datetime(2026, 9, 23, 2, 0, tzinfo=UTC)


class _FakeSender:
    """Scriptable sender: records messages, optionally raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(message)


def _notifier(
    tmp_path: Path,
    sender: _FakeSender,
    *,
    clock: Callable[[], datetime] | None = None,
    suppress_seconds: int = 3600,
) -> SessionExpiryNotifier:
    return SessionExpiryNotifier(
        tmp_path / "notify-state.json",
        sender=sender,
        suppress_seconds=suppress_seconds,
        clock=clock or (lambda: _FIXED_NOW),
    )


def test_expiry_notifies_once_within_suppression_window(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)

    first = notifier.notify_session_expired(source="broker-read")
    second = notifier.notify_session_expired(source="broker-read")

    assert first is True
    assert second is False
    assert len(sender.messages) == 1
    assert sender.messages[0].splitlines()[0] == RELOGIN_REQUIRED_MESSAGE
    # The state file records exactly one suppression entry.
    state = json.loads((tmp_path / "notify-state.json").read_text(encoding="utf-8"))
    assert state["version"] == SESSION_NOTIFY_VERSION
    assert set(state["suppressed"]) == {"rakuten-web"}


def test_distinct_sources_dedupe_to_single_notification(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)

    first = notifier.notify_session_expired(source="broker-read")
    second = notifier.notify_session_expired(source="broker-exec")

    assert first is True
    assert second is False
    assert len(sender.messages) == 1  # fingerprint key is connector_id, not source


def test_reauth_clears_suppression(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)

    assert notifier.notify_session_expired(source="broker-read") is True
    notifier.notify_authenticated("rakuten-web")

    # After re-authentication the NEXT expiry must notify again.
    assert notifier.notify_session_expired(source="broker-read") is True
    assert len(sender.messages) == 2


def test_sender_failure_does_not_record_state_and_retries(tmp_path: Path) -> None:
    failing = _FakeSender(error=RuntimeError("hermes send failed"))
    notifier = _notifier(tmp_path, failing)

    assert notifier.notify_session_expired(source="broker-read") is False
    # No state was written: a retry with a working sender must notify.
    sender_ok = _FakeSender()
    clock = {"now": _FIXED_NOW}
    notifier2 = SessionExpiryNotifier(
        tmp_path / "notify-state.json",
        sender=sender_ok,
        suppress_seconds=3600,
        clock=lambda: clock["now"],
    )
    clock["now"] = _FIXED_NOW + timedelta(seconds=10)
    assert notifier2.notify_session_expired(source="broker-read") is True
    assert len(sender_ok.messages) == 1


def test_default_sender_runs_hermes_send(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("yowayowa.broker.session_notify.subprocess.run", _fake_run)
    notifier = SessionExpiryNotifier(
        tmp_path / "notify-state.json",
        suppress_seconds=3600,
        clock=lambda: _FIXED_NOW,
    )
    assert notifier.notify_session_expired(source="broker-read", detail="rakuten-web") is True
    assert captured["argv"] == [
        "hermes",
        "send",
        "--to",
        DEFAULT_NOTIFY_TARGET,
        captured["argv"][4],  # message is the single last positional argument
    ]
    message = captured["argv"][4]
    assert message.splitlines()[0] == RELOGIN_REQUIRED_MESSAGE
    assert "source: broker-read" in message
    assert "detail: rakuten-web" in message
    assert "time: " in message
    # shell=True must never be used (arg quoting safety).
    assert captured["kwargs"].get("shell") in (None, False)
    assert captured["kwargs"]["check"] is True


def test_state_file_roundtrip_and_corrupt_state_fails_open(tmp_path: Path) -> None:
    sender = _FakeSender()
    state_path = tmp_path / "nested" / "notify-state.json"
    notifier = SessionExpiryNotifier(
        state_path,
        sender=sender,
        suppress_seconds=60,
        clock=lambda: _FIXED_NOW,
    )
    assert notifier.notify_session_expired(source="broker-read") is True

    # Roundtrip: a second notifier instance reads the persisted suppression.
    notifier2 = SessionExpiryNotifier(
        state_path,
        sender=sender,
        suppress_seconds=60,
        clock=lambda: _FIXED_NOW + timedelta(seconds=30),
    )
    assert notifier2.notify_session_expired(source="broker-read") is False
    assert len(sender.messages) == 1

    # Corrupt JSON is treated as an empty state (fail-open: notify again).
    state_path.write_text("{not json at all", encoding="utf-8")
    assert notifier2.notify_session_expired(source="broker-read") is True
    assert len(sender.messages) == 2
