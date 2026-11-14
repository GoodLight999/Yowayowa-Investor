"""Regression for one UTC day expressed in multiple ISO representations."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yowayowa.hl_exec.s07_forward import runner


@pytest.mark.parametrize(
    "first,second",
    [
        ("2026-11-08", "20261108"),
        ("20261108", "2026-11-08"),
    ],
)
def test_equivalent_days_share_ledger_and_sender(tmp_path, monkeypatch, first, second):
    source = tmp_path / "synthetic_input"
    source.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(
        runner,
        "verify_authenticity",
        Mock(return_value=SimpleNamespace(exit_code=0, result=SimpleNamespace(verdict="PASS"))),
    )
    monkeypatch.setattr(runner, "_decide", lambda day, market: {"day": day, "signal": "LONG"})
    sender = Mock()
    args = {
        "market": {},
        "out_dir": out,
        "authenticity": {"data_dir": source, "account": "synthetic", "offline": True},
        "enable_order_path": True,
        "sender": sender,
        "order_status": "cancel_confirmed",
    }
    result1 = runner.run_day(day=first, **args)
    result2 = runner.run_day(day=second, **args)
    rows = [json.loads(line) for line in (out / "ledger.jsonl").read_text().splitlines()]
    assert result1["status"] == "appended"
    assert result2["status"] == "already_recorded"
    assert [row["day"] for row in rows] == ["2026-11-08"]
    assert sender.call_count == 1
    assert len(list((out / "orders").glob("*.submitted"))) == 1
