"""Daily strategy-signal Telegram delivery tests (t_8e24821d).

All tests are offline: the sender is ALWAYS injected (a scripted fake) and
the real ``hermes`` CLI is never invoked except in
``test_default_sender_runs_hermes_send``, which monkeypatches
``subprocess.run``. State files live in ``tmp_path`` only.

Covered contract:
- one send per (signal date, alert fingerprint); same-day re-run suppressed;
- changed alerts for the same date send again;
- sender failure keeps state unwritten and retries on the next run;
- corrupt state is fail-open (sends anyway);
- messages preserve as-of date and provider/source/retrieved_at provenance;
- the CLI ``signals-send`` command sends only on its explicit path, honors
  the test kill switch, and an empty candidate day never spawns a transport;
- empty-candidate days still record delivery without an alert block.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from yowayowa.cli_entry import app as cli_app
from yowayowa.services.signal_notify import (
    SIGNAL_NOTIFY_VERSION,
    SignalDeliveryNotifier,
    build_alert_fingerprint,
    render_signal_message,
)
from yowayowa.services.strategy_signals import compute_daily_strategy_signals

runner = CliRunner()

UNIVERSE = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM", "XOM", "SPY")
SIGNAL_DATE = date(2025, 3, 3)


@pytest.fixture(autouse=True)
def _forbid_real_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blast-radius guard: any unmocked subprocess spawn fails the test loudly."""

    def _no_subprocess(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "real subprocess.run attempted during signal-notify tests; the sender must be injected"
        )

    monkeypatch.setattr(subprocess, "run", _no_subprocess)


def _history(symbol_index: int = 0, count: int = 62) -> list[dict[str, Any]]:
    start = date(2025, 1, 1)
    return [
        {
            "as_of": start + timedelta(days=index),
            "close": 100 + symbol_index * 10 + index * (symbol_index + 1),
            "provider": "alpaca",
            "source_url": "https://example.invalid/data",
            "license_class": "personal_only",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        }
        for index in range(count)
    ]


def _fake_histories() -> dict[str, list[dict[str, Any]]]:
    histories = {symbol: _history(index) for index, symbol in enumerate(UNIVERSE)}
    histories["MSFT"] = [{**row, "close": 200 - index * 2} for index, row in enumerate(_history(1))]
    return histories


def _signal_result() -> dict[str, Any]:
    return compute_daily_strategy_signals(_fake_histories())


class _FakeStore:
    def __init__(self, histories: dict[str, list[dict[str, Any]]]) -> None:
        self._histories = histories

    def list_symbols(self) -> list[str]:
        return sorted(self._histories)

    def read(self, symbol: str, *, provider: str, limit: int) -> list[dict[str, Any]]:
        return self._histories[symbol]


class _FakeSender:
    """Scriptable sender: records messages, optionally raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        if self.error is not None:
            raise self.error
        self.messages.append(message)


def _notifier(tmp_path: Path, sender: _FakeSender) -> SignalDeliveryNotifier:
    return SignalDeliveryNotifier(str(tmp_path / "signals-notify-state.json"), sender=sender)


def test_fingerprint_changes_when_alerts_change() -> None:
    alerts = [
        {
            "type": "mean_reversion_candidate",
            "symbol": "MSFT",
            "return_20d": -0.1,
            "as_of": SIGNAL_DATE,
        },
    ]
    changed = [dict(alerts[0], return_20d=-0.2)]
    assert build_alert_fingerprint(alerts) != build_alert_fingerprint(changed)
    assert build_alert_fingerprint(alerts) == build_alert_fingerprint(list(alerts))
    assert build_alert_fingerprint([]) != build_alert_fingerprint(alerts)


def test_same_day_rerun_is_deduplicated(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)
    result = _signal_result()

    assert notifier.notify(result, evaluated_at=datetime(2026, 10, 1, tzinfo=UTC)) is True
    assert notifier.notify(result, evaluated_at=datetime(2026, 10, 1, 1, tzinfo=UTC)) is False
    assert len(sender.messages) == 1

    state = json.loads((tmp_path / "signals-notify-state.json").read_text(encoding="utf-8"))
    assert state["version"] == SIGNAL_NOTIFY_VERSION
    entry = state["delivered"]["2025-03-03"]
    assert entry["alerts"] == len(result["alerts"])
    assert entry["fingerprint"] == build_alert_fingerprint(result["alerts"])


def test_changed_alerts_same_date_send_again(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)
    result = _signal_result()
    assert notifier.notify(result, evaluated_at=datetime(2026, 10, 1, tzinfo=UTC)) is True

    changed = [dict(result["alerts"][0], return_20d=result["alerts"][0]["return_20d"] - 0.05)]
    assert notifier.notify(result, alerts=changed) is True
    assert len(sender.messages) == 2


def test_new_trading_date_sends_again(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)
    result = _signal_result()
    assert notifier.notify(result, evaluated_at=datetime(2026, 10, 1, tzinfo=UTC)) is True

    # Same alerts attributed to the NEXT trading date must send (per-date dedupe).
    next_day = [dict(alert, as_of=date(2025, 3, 4)) for alert in result["alerts"]]
    assert notifier.notify(result, alerts=next_day) is True
    assert len(sender.messages) == 2


def test_sender_failure_keeps_state_unwritten_and_retries(tmp_path: Path) -> None:
    failing = _FakeSender(error=RuntimeError("hermes send failed"))
    state_path = tmp_path / "nested" / "signals-notify-state.json"
    notifier = SignalDeliveryNotifier(str(state_path), sender=failing)
    result = _signal_result()

    assert notifier.notify(result, evaluated_at=datetime(2026, 10, 1, tzinfo=UTC)) is False
    assert not state_path.exists()

    # A later run with a working sender must retry the same date.
    ok = _FakeSender()
    retry = SignalDeliveryNotifier(str(state_path), sender=ok)
    assert retry.notify(result, evaluated_at=datetime(2026, 10, 1, 2, tzinfo=UTC)) is True
    assert len(ok.messages) == 1


def test_corrupt_state_fails_open_and_sends(tmp_path: Path) -> None:
    state_path = tmp_path / "signals-notify-state.json"
    state_path.write_text("{not json at all", encoding="utf-8")
    sender = _FakeSender()
    notifier = SignalDeliveryNotifier(str(state_path), sender=sender)
    result = _signal_result()

    assert notifier.notify(result) is True
    assert len(sender.messages) == 1


def test_message_preserves_as_of_and_provenance(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)
    result = _signal_result()

    assert notifier.notify(result) is True
    message = sender.messages[0]
    assert str(SIGNAL_DATE) in message  # as-of trading date
    assert "alpaca" in message  # provider provenance
    assert "example.invalid" in message  # source host provenance
    assert "2026-10-01T00:00:00+00:00" in message  # retrieved_at provenance
    assert "MSFT" in message  # worst 20-day return symbol is present
    assert "売買推奨ではない" in message  # explicit non-recommendation marker
    assert len(message) < 4096  # Telegram hard cap
    assert render_signal_message(result) == message


def test_empty_candidate_day_records_delivery_without_alerts(tmp_path: Path) -> None:
    sender = _FakeSender()
    notifier = _notifier(tmp_path, sender)
    result = _signal_result()
    result_no_alerts = {**result, "alerts": []}

    assert notifier.notify(result_no_alerts) is True
    state = json.loads((tmp_path / "signals-notify-state.json").read_text(encoding="utf-8"))
    assert state["delivered"]["2025-03-03"]["alerts"] == 0
    # And a later day where candidates appear sends again.
    assert notifier.notify(result) is True
    assert len(sender.messages) == 2


def test_default_sender_runs_hermes_send(monkeypatch: pytest.MonkeyPatch) -> None:
    from yowayowa.services import signal_notify as signal_notify_module

    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs

        class _Result:
            returncode = 0

        return _Result()

    # Rebind the blast-radius guard for this one test: the module-level
    # subprocess reference is what _default_sender actually calls.
    monkeypatch.setattr(signal_notify_module.subprocess, "run", _fake_run)
    signal_notify_module._default_sender("テストシグナル本文")
    assert captured["argv"] == [
        "hermes",
        "send",
        "--to",
        "telegram",
        "テストシグナル本文",
    ]
    # shell=True must never be used (arg quoting safety).
    assert captured["kwargs"].get("shell") in (None, False)
    assert captured["kwargs"]["check"] is True


def test_cli_signals_send_delivers_once_with_injected_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yowayowa import signal_cli

    monkeypatch.setattr(signal_cli, "default_store", lambda: _FakeStore(_fake_histories()))
    sent_messages: list[str] = []
    monkeypatch.setattr(
        signal_cli, "_default_sender", lambda message: sent_messages.append(message)
    )
    state_path = tmp_path / "cli-state.json"

    first = runner.invoke(cli_app, ["signals-send", "--state-path", str(state_path)])
    assert first.exit_code == 0, first.output
    assert "sent strategy signal digest for 2025-03-03" in first.output

    second = runner.invoke(cli_app, ["signals-send", "--state-path", str(state_path)])
    assert second.exit_code == 0, second.output
    assert "no send for 2025-03-03" in second.output
    assert len(sent_messages) == 1
    assert "MSFT" in sent_messages[0]


def test_cli_signals_send_kill_switch_blocks_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yowayowa import signal_cli

    monkeypatch.setattr(signal_cli, "default_store", lambda: _FakeStore(_fake_histories()))
    monkeypatch.setenv("YOWAYOWA_SIGNAL_NOTIFY_DISABLED", "1")
    state_path = tmp_path / "cli-state.json"

    result = runner.invoke(cli_app, ["signals-send", "--state-path", str(state_path)])
    assert result.exit_code == 0, result.output
    assert "delivery disabled" in result.output
    assert not state_path.exists()


def test_cli_signals_send_with_empty_store_never_sends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yowayowa import signal_cli

    monkeypatch.setattr(signal_cli, "default_store", lambda: _FakeStore({}))
    result = runner.invoke(cli_app, ["signals-send", "--state-path", str(tmp_path / "state.json")])
    assert result.exit_code == 0, result.output
    assert "no send" in result.output
    assert "no negative-return alert candidates today" in result.output


def test_read_only_surfaces_have_no_transport_path() -> None:
    """GET /v1/screening/strategy and `yowayowa signals` can never transmit.

    Structural guard: the signal computation and API route modules import no
    send path at all, so a GET cannot gain a side effect by drift.
    """

    import inspect

    import yowayowa.api.backtest_routes as backtest_routes_module
    import yowayowa.services.alerts as alerts_module
    import yowayowa.services.strategy_signals as strategy_signals_module

    for module in (strategy_signals_module, backtest_routes_module):
        source = inspect.getsource(module)
        assert "subprocess" not in source
        assert "hermes" not in source
        assert "signal_notify" not in source
    # The alerts service exposes the pure evaluator; its send path lives in a
    # separate module reachable only from the CLI entry point.
    assert "signal_notify" not in inspect.getsource(alerts_module)
