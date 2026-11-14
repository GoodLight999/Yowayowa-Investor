"""Direct order-path observation; RED is an in-memory mutation, no live sender."""

import inspect
import json
import os
import socket
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yowayowa.hl_exec.s07_forward import runner

DAY = "2026-01-01"


@pytest.fixture
def context(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network prohibited")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    source = tmp_path / "input"
    source.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(
        runner,
        "verify_authenticity",
        Mock(return_value=SimpleNamespace(exit_code=0, result=SimpleNamespace(verdict="PASS"))),
    )
    monkeypatch.setattr(runner, "_decide", Mock(return_value={"day": DAY, "signal": "LONG"}))
    return out, {"data_dir": source, "account": "synthetic", "offline": True}


def test_no_flag_direct_path_not_called(context, monkeypatch):
    out, auth = context
    calls = []
    original = runner._run_order_path

    def observed(*args, **kwargs):
        calls.append(1)
        if os.environ.get("S07_NEGATIVE_CONTROL") == "1":
            return {"authorized": True}
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_order_path", observed)
    if os.environ.get("S07_NEGATIVE_CONTROL") == "1":
        source = textwrap.dedent(inspect.getsource(runner.run_day))
        old = "if enable_order_path and sender_callable is not None:"
        assert source.count(old) == 1
        namespace = dict(runner.__dict__)
        exec(compile(source.replace(old, "if True:"), "<negative-control-only>", "exec"), namespace)
        monkeypatch.setattr(runner, "run_day", namespace["run_day"])
    runner.run_day(day=DAY, market={}, out_dir=out, authenticity=auth)
    print(f"no_flag direct_path_calls={len(calls)}")
    assert len(calls) == 0, f"calls={len(calls)}"


def test_flag_sender_and_idempotence(context, monkeypatch):
    out, auth = context
    calls = []
    original = runner._run_order_path

    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_order_path", observed)
    sender = Mock()
    kwargs = dict(
        day=DAY,
        market={},
        out_dir=out,
        authenticity=auth,
        enable_order_path=True,
        sender=sender,
        order_status="cancel_confirmed",
    )
    first = runner.run_day(**kwargs)
    first_count = len(calls)
    second = runner.run_day(**kwargs)
    rows = (out / "ledger.jsonl").read_text().splitlines()
    events = [
        json.loads(row) for row in (out / "orders" / f"{DAY}.events.jsonl").read_text().splitlines()
    ]
    completed = sum(event["event"] == "resend_completed" for event in events)
    print(
        f"flag first={first['status']} second={second['status']}"
        f" direct_calls={len(calls)} sender_calls={sender.call_count}"
        f" ledger_rows={len(rows)} completed_events={completed}"
    )
    assert first_count == len(calls) == 1
    assert sender.call_count == completed == 1
    assert len(rows) == 1
    assert second["status"] == "already_recorded"


def test_flag_without_sender_fails_closed(context, monkeypatch):
    out, auth = context
    path = Mock(wraps=runner._run_order_path)
    monkeypatch.setattr(runner, "_run_order_path", path)
    with pytest.raises(NotImplementedError, match="requires an injected test sender"):
        runner.run_day(day=DAY, market={}, out_dir=out, authenticity=auth, enable_order_path=True)
    print(
        f"missing_sender direct_calls={path.call_count}"
        f" ledger_exists={(out / 'ledger.jsonl').exists()}"
    )
    assert path.call_count == 0
    assert not (out / "ledger.jsonl").exists()
