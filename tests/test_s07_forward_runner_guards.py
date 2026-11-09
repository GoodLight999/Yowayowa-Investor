"""Isolation guards for the S-07 replacement runner (synthetic, no network)."""

import datetime as dt
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from yowayowa.hl_exec.s07_forward import runner
from yowayowa.hl_exec.s07_forward.order_gate import OrderGateError
from yowayowa.hl_exec.s07_offline.manifest import response_sha256

DAY = "2026-01-01"
E2E_DAY = "2026-11-08"
REAL = Path(__file__).parent / "fixtures/s07_authenticity/real"


def _market():
    """60 日分の合成バーと 24 時間分の funding (実 X1 fixture と組み合わせる)。"""
    end = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)
    start = end - dt.timedelta(days=60)
    rows = []
    for i in range(60):
        stamp = int((start + dt.timedelta(days=i)).timestamp() * 1000)
        rows.append(
            {"t": stamp, "T": stamp + 86_400_000 - 1, "o": "100", "h": "102", "l": "98", "c": "100"}
        )
    return {
        "network": "testnet",
        "coin": "BTC",
        "interval": "1d",
        "source_url": "https://api.hyperliquid-testnet.xyz/info",
        "retrieved_at": end.isoformat(),
        "as_of": end.isoformat(),
        "data_kind": "synthetic",
        "response": rows,
        "response_sha256": response_sha256(rows),
        "funding": [
            {"time": int((end - dt.timedelta(hours=24 - i)).timestamp() * 1000), "rate": 0.0}
            for i in range(24)
        ],
    }


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access prohibited")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def env(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    out = tmp_path / "output"
    out.mkdir()
    check = Mock(return_value=SimpleNamespace(exit_code=0, result=SimpleNamespace(verdict="PASS")))
    monkeypatch.setattr(runner, "verify_authenticity", check)
    monkeypatch.setattr(runner, "_decide", Mock(return_value={"day": DAY, "signal": "LONG"}))
    return out, {"data_dir": source, "account": "synthetic", "offline": True}


def test_sender_is_ignored_without_explicit_flag(env):
    out, auth = env
    sender = Mock()
    result = runner.run_day(day=DAY, market={}, out_dir=out, authenticity=auth, sender=sender)
    sender.assert_not_called()
    assert result["status"] == "appended"
    assert "order" not in result


def test_non_test_double_sender_is_refused_before_any_write(env):
    out, auth = env
    with pytest.raises(OrderGateError):
        runner.run_day(
            day=DAY,
            market={},
            out_dir=out,
            authenticity=auth,
            enable_order_path=True,
            sender=object(),
        )
    assert not (out / "ledger.jsonl").exists()


def test_non_offline_authenticity_is_refused(env):
    out, auth = env
    with pytest.raises(ValueError, match="offline"):
        runner.run_day(day=DAY, market={}, out_dir=out, authenticity=dict(auth, offline=False))
    assert not (out / "ledger.jsonl").exists()


def test_missing_order_status_is_blocked_and_recorded(env):
    out, auth = env
    sender = Mock()
    result = runner.run_day(
        day=DAY,
        market={},
        out_dir=out,
        authenticity=auth,
        enable_order_path=True,
        sender=sender,
    )
    sender.assert_not_called()
    assert result["status"] == "order_blocked"
    assert result["order"]["reason"] == "order_status_missing"
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1
    events = (out / "orders" / f"{DAY}.events.jsonl").read_text().splitlines()
    assert json.loads(events[0])["event"] == "resend_blocked"


def test_confirmed_cancel_is_recorded_as_synthetic_and_sent_once(env):
    out, auth = env
    sender = Mock()
    for _ in range(2):
        runner.run_day(
            day=DAY,
            market={},
            out_dir=out,
            authenticity=auth,
            enable_order_path=True,
            sender=sender,
            order_status="cancel_confirmed",
        )
    sender.assert_called_once()
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1
    payload = json.loads((out / "orders" / f"{DAY}.submitted").read_text())
    assert payload["evidence"]["verified"] is False
    assert payload["evidence"]["kind"] == "synthetic_test_only"


def test_existing_submission_marker_blocks_resend(env):
    out, auth = env
    (out / ".s07-forward-isolated").write_text("")
    journal = out / "orders"
    journal.mkdir()
    (journal / f"{DAY}.submitted").write_text("{}\n")
    sender = Mock()
    result = runner.run_day(
        day=DAY,
        market={},
        out_dir=out,
        authenticity=auth,
        enable_order_path=True,
        sender=sender,
        order_status={
            "status": "cancel_confirmed",
            "order_id": "oid-1",
            "cancel_confirmed_at": "2026-01-01T00:00:00+00:00",
            "remaining_qty": 0,
            "source": "reconciliation",
        },
    )
    sender.assert_not_called()
    assert result["status"] == "order_blocked"
    assert result["order"]["reason"] == "submission_already_claimed"


def test_structured_evidence_is_verified_but_incomplete_is_blocked(env):
    out, auth = env
    sender = Mock()
    runner.run_day(
        day=DAY,
        market={},
        out_dir=out,
        authenticity=auth,
        enable_order_path=True,
        sender=sender,
        order_status={
            "status": "cancel_confirmed",
            "order_id": "oid-1",
            "cancel_confirmed_at": "2026-01-01T00:00:00+00:00",
            "remaining_qty": 0,
            "source": "reconciliation",
        },
    )
    sender.assert_called_once()
    assert (
        json.loads((out / "orders" / f"{DAY}.submitted").read_text())["evidence"]["verified"]
        is True
    )

    other = out.parent / "output2"
    other.mkdir()
    blocked_sender = Mock()
    result = runner.run_day(
        day=DAY,
        market={},
        out_dir=other,
        authenticity=auth,
        enable_order_path=True,
        sender=blocked_sender,
        order_status={
            "status": "cancel_confirmed",
            "order_id": "oid-1",
            "source": "reconciliation",
        },
    )
    blocked_sender.assert_not_called()
    assert result["order"]["reason"] == "cancel_evidence_incomplete"


def test_cli_enable_order_path_fails_closed(env, tmp_path):
    out, auth = env
    market = tmp_path / "market.json"
    market.write_text("{}")
    args = [
        "--day",
        DAY,
        "--market-json",
        str(market),
        "--out",
        str(out),
        "--data-dir",
        str(auth["data_dir"]),
        "--account",
        "synthetic",
        "--enable-order-path",
    ]
    for name in ("fills", "funding", "state", "orders"):
        args += [f"--{name}-json", str(tmp_path / (name + ".json"))]
    assert runner.main(args) == 2
    assert not (out / "ledger.jsonl").exists()


def test_run_never_constructs_exchange_client(tmp_path, monkeypatch):
    """X1 オフライン経路が送信系クライアントを生成しないことを実測で固定する。"""
    from yowayowa.hl_exec import client as hl_client

    def boom(*args, **kwargs):
        raise AssertionError("exchange client must never be constructed")

    monkeypatch.setattr(hl_client.HyperliquidExchangeClient, "__init__", boom)
    info = REAL / "info"
    out = tmp_path / "out"
    out.mkdir()
    result = runner.run_day(
        day=E2E_DAY,
        market=_market(),
        out_dir=out,
        authenticity={
            "data_dir": REAL / "data",
            "account": "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82",
            "offline": True,
            "fills_json": info / "userFills.json",
            "funding_json": info / "userFunding.json",
            "state_json": info / "clearinghouseState.json",
            "orders_json": info / "openOrders.json",
        },
    )
    assert result["status"] == "appended"


def test_cli_dry_run_end_to_end_uses_real_decision(tmp_path):
    """CLI → 実 X1 (オフライン fixture) → 決定 → 台帳 → レポートを実際に通す。"""
    info = REAL / "info"
    market = tmp_path / "market.json"
    market.write_text(json.dumps(_market()), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    args = [
        "--day",
        E2E_DAY,
        "--market-json",
        str(market),
        "--out",
        str(out),
        "--data-dir",
        str(REAL / "data"),
        "--account",
        "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82",
        "--fills-json",
        str(info / "userFills.json"),
        "--funding-json",
        str(info / "userFunding.json"),
        "--state-json",
        str(info / "clearinghouseState.json"),
        "--orders-json",
        str(info / "openOrders.json"),
    ]
    assert runner.main(args) == 0
    rows = (out / "ledger.jsonl").read_text().splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["day"] == E2E_DAY
    assert record["signal"] in ("LONG", "SHORT", "HOLD")
    assert record["mode"] == "dry_run" and record["official_result"] is False
    report = out / "reports" / f"{E2E_DAY}.json"
    assert json.loads(report.read_text(encoding="utf-8")) == record
    assert (out / "authenticity").is_dir()

    # 同日再実行: 追記なし・二重発注なし。
    assert runner.main(args) == 0
    assert len((out / "ledger.jsonl").read_text().splitlines()) == 1
