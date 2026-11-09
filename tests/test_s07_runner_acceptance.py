"""Synthetic acceptance tests; no network, credentials or real sender."""
# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
import ast
import datetime as dt
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access prohibited in acceptance tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def setup_runner(tmp_path, monkeypatch):
    from yowayowa.hl_exec.s07_forward import runner

    source = tmp_path / "input"
    source.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    auth = {"data_dir": source, "account": "synthetic", "offline": True}
    check = Mock(return_value=SimpleNamespace(exit_code=0, result=SimpleNamespace(verdict="PASS")))
    decide = Mock(return_value={"day": "2026-01-01", "signal": "LONG"})
    monkeypatch.setattr(runner, "verify_authenticity", check)
    monkeypatch.setattr(runner, "_decide", decide)
    return runner, out, auth, check, decide


def test_default_dry_run_never_calls_order_path(setup_runner, monkeypatch):
    runner, out, auth, check, decide = setup_runner
    order = Mock(side_effect=AssertionError("order path must not be entered"))
    monkeypatch.setattr(runner, "order_path", order)
    runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth)
    check.assert_called_once()
    decide.assert_called_once()
    order.assert_not_called()
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1


def test_x1_failure_stops_before_decision_ledger_and_order(setup_runner, monkeypatch):
    runner, out, auth, check, decide = setup_runner
    check.return_value = SimpleNamespace(exit_code=3, result=SimpleNamespace(verdict="FAIL"))
    order = Mock()
    monkeypatch.setattr(runner, "order_path", order)
    with pytest.raises(ValueError, match="authenticity"):
        runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth)
    decide.assert_not_called()
    order.assert_not_called()
    assert not (out / "ledger.jsonl").exists()


def test_same_day_is_idempotent(setup_runner, monkeypatch):
    runner, out, auth, check, decide = setup_runner
    order = Mock()
    monkeypatch.setattr(runner, "order_path", order)
    for _ in range(2):
        runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth)
    rows = (out / "ledger.jsonl").read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["day"] == "2026-01-01"
    decide.assert_called_once()
    order.assert_not_called()


def test_unknown_order_is_not_resent(setup_runner):
    runner, out, auth, _, _ = setup_runner
    sender = Mock()
    runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth,
                   enable_order_path=True, sender=sender, order_status="unknown")
    sender.assert_not_called()


def test_confirmed_cancel_allows_only_mock_resend(setup_runner):
    runner, out, auth, _, _ = setup_runner
    sender = Mock()
    runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth,
                   enable_order_path=True, sender=sender, order_status="cancel_confirmed")
    sender.assert_called_once()


def test_cli_x1_failure_returns_nonzero(setup_runner, tmp_path, monkeypatch):
    runner, out, auth, check, decide = setup_runner
    check.return_value = SimpleNamespace(exit_code=3, result=SimpleNamespace(verdict="FAIL"))
    order = Mock()
    monkeypatch.setattr(runner, "order_path", order)
    market = tmp_path / "market.json"
    market.write_text("{}")
    args = ["--day", "2026-01-01", "--market-json", str(market), "--out", str(out),
            "--data-dir", str(auth["data_dir"]), "--account", "synthetic"]
    for name in ("fills", "funding", "state", "orders"):
        args += [f"--{name}-json", str(tmp_path / (name + ".json"))]
    assert runner.main(args) != 0
    decide.assert_not_called()
    order.assert_not_called()
    assert not (out / "ledger.jsonl").exists()


def test_runner_has_no_direct_signer_or_broker_import(setup_runner):
    runner, _, _, _, _ = setup_runner
    tree = ast.parse(Path(runner.__file__).read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not any(part in name.lower() for name in names for part in
                   ("eth_account", "signing", "private_key", "hyperliquid.exchange"))


def test_explicit_flag_without_sender_fails_closed(setup_runner):
    runner, out, auth, _, _ = setup_runner
    with pytest.raises((ValueError, NotImplementedError)):
        runner.run_day(day="2026-01-01", market={}, out_dir=out, authenticity=auth,
                       enable_order_path=True)
    assert not (out / "ledger.jsonl").exists()


# --- day キー正規化（X3 ブロッカー F-1） -----------------------------------
# 同じ UTC 日の別表記が別キーになると、台帳が2行・送信が2回・O_EXCL が
# すり抜ける。ここでは正準形 YYYY-MM-DD からのキー生成を実測で固定する。

DAY_ISO = "2026-11-08"
DAY_BASIC = "20261108"


@pytest.fixture
def day_env(tmp_path, monkeypatch):
    """日キー検証用の隔離環境（X1 は PASS 固定・sender は Mock）。"""
    from yowayowa.hl_exec.s07_forward import runner

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(
        runner,
        "verify_authenticity",
        Mock(return_value=SimpleNamespace(exit_code=0, result=SimpleNamespace(verdict="PASS"))),
    )
    monkeypatch.setattr(runner, "_decide", lambda day, market: {"day": day, "signal": "LONG"})
    auth = {"data_dir": source, "account": "synthetic", "offline": True}

    def new_out():
        out = tmp_path / f"out{len(list(tmp_path.glob('out*')))}"
        out.mkdir()
        return out

    return runner, auth, new_out


def test_iso_then_basic_notation_keeps_one_ledger_row_and_one_send(day_env):
    """YYYY-MM-DD → 1行/1送信、続く YYYYMMDD は already_recorded で送信しない。"""
    runner, auth, new_out = day_env
    out = new_out()
    sender = Mock()
    kwargs = {
        "market": {},
        "out_dir": out,
        "authenticity": auth,
        "enable_order_path": True,
        "sender": sender,
        "order_status": "cancel_confirmed",
    }
    first = runner.run_day(day=DAY_ISO, **kwargs)
    second = runner.run_day(day=DAY_BASIC, **kwargs)
    third = runner.run_day(day=dt.date(2026, 11, 8), **kwargs)
    rows = [json.loads(line) for line in (out / "ledger.jsonl").read_text().splitlines()]
    reports = sorted(path.name for path in (out / "reports").glob("*.json"))
    markers = sorted(path.name for path in (out / "orders").glob("*.submitted"))
    print("ledger days:", [row["day"] for row in rows])
    print("reports:", reports)
    print("submission markers:", markers)
    print("sender calls:", sender.call_count)
    assert first["status"] == "appended"
    assert second["status"] == "already_recorded"
    assert third["status"] == "already_recorded"
    assert [row["day"] for row in rows] == [DAY_ISO]
    assert reports == ["2026-11-08.json"]
    assert markers == ["2026-11-08.submitted"]
    assert sender.call_count == 1


@pytest.mark.parametrize(
    "value",
    ["08/11/2026", "2026-W45-7", "2026-1-1", "2026/11/08", "", None, 20261108,
     dt.datetime(2026, 11, 8, 23, 30)],
)
def test_unsupported_day_notation_stops_fail_closed(day_env, value):
    """列挙した表記以外は例外で停止し、台帳も送信も出力先も増えない。"""
    runner, auth, new_out = day_env
    out = new_out()
    sender = Mock()
    with pytest.raises(ValueError, match="unsupported day representation"):
        runner.run_day(
            day=value, market={}, out_dir=out, authenticity=auth,
            enable_order_path=True, sender=sender, order_status="cancel_confirmed",
        )
    print("rejected day representation:", repr(value))
    assert not (out / "ledger.jsonl").exists()
    assert not (out / "reports").exists()
    assert not (out / "orders").exists()
    assert not (out / ".s07-forward-isolated").exists()
    assert sender.call_count == 0


def test_oc_excl_marker_name_is_identical_for_both_notations(day_env, monkeypatch):
    """O_EXCL のロック名を実測表示し、両表記で同一名になることを示す。"""
    from yowayowa.hl_exec.s07_forward import order_gate

    runner, auth, new_out = day_env
    claimed: list[str] = []
    real_claim = order_gate.claim_submission

    def spy(directory, day, payload):
        path = real_claim(directory, day, payload)
        claimed.append(path.name)
        return path

    monkeypatch.setattr(order_gate, "claim_submission", spy)

    for notation in (DAY_ISO, DAY_BASIC):
        out = new_out()
        sender = Mock()
        runner.run_day(
            day=notation, market={}, out_dir=out, authenticity=auth,
            enable_order_path=True, sender=sender, order_status="cancel_confirmed",
        )
        assert sender.call_count == 1
        assert sorted(path.name for path in (out / "orders").glob("*.submitted")) == [
            "2026-11-08.submitted"
        ]
    print("O_EXCL lock names (YYYY-MM-DD, YYYYMMDD):", claimed)

    # 同一出力先では、正準名のマーカーが既にあるため再送は O_EXCL で拒否される。
    out = new_out()
    sender = Mock()
    runner.run_day(
        day=DAY_ISO, market={}, out_dir=out, authenticity=auth,
        enable_order_path=True, sender=sender, order_status="cancel_confirmed",
    )
    with pytest.raises(order_gate.OrderGateError):
        order_gate.claim_submission(out / "orders", DAY_ISO, {"event": "probe"})
    assert claimed == ["2026-11-08.submitted", "2026-11-08.submitted", "2026-11-08.submitted"]


def test_noncanonical_legacy_ledger_row_stops_before_duplicate_append(day_env):
    """旧実装が残した非正準 day の台帳は照合せず停止する（再利用率の fail-closed）。"""
    runner, auth, new_out = day_env
    out = new_out()
    (out / runner.MARKER_NAME).write_text("")
    legacy = {"day": DAY_BASIC, "signal": "LONG"}
    (out / "ledger.jsonl").write_text(json.dumps(legacy) + "\n")
    sender = Mock()
    with pytest.raises(ValueError, match="non-canonical day in ledger"):
        runner.run_day(day=DAY_ISO, market={}, out_dir=out, authenticity=auth)
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1
    assert sender.call_count == 0


def test_noncanonical_legacy_submission_marker_stops_order_path(day_env):
    """旧実装が残した非正準名の送信マーカーがあれば発注経路へ入らない。"""
    runner, auth, new_out = day_env
    out = new_out()
    (out / runner.MARKER_NAME).write_text("")
    orders = out / "orders"
    orders.mkdir()
    (orders / f"{DAY_BASIC}.submitted").write_text("{}\n")
    sender = Mock()
    with pytest.raises(ValueError, match="non-canonical day in order artifact"):
        runner.run_day(
            day=DAY_ISO, market={}, out_dir=out, authenticity=auth,
            enable_order_path=True, sender=sender, order_status="cancel_confirmed",
        )
    assert sender.call_count == 0
    assert not (out / "ledger.jsonl").exists()


