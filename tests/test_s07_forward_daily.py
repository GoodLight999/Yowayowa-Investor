"""S-07 forward tests: no network, no keys and isolated output."""

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from yowayowa.hl_exec.s07_forward.runner import run_day
from yowayowa.hl_exec.s07_offline.manifest import response_sha256

REAL = Path(__file__).parent / "fixtures/s07_authenticity/real"
DAY = "2026-11-08"


def market():
    end = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)
    start = end - dt.timedelta(days=60)
    rows = []
    for i in range(60):
        t = int((start + dt.timedelta(days=i)).timestamp() * 1000)
        rows.append({"t": t, "T": t + 86400000 - 1, "o": "100", "h": "102", "l": "98", "c": "100"})
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


def x1():
    info = REAL / "info"
    return {
        "data_dir": REAL / "data",
        "account": "0xF6cB1079F73cA0163E2EfF24eFbB651f99E2Dd82",
        "offline": True,
        "fills_json": info / "userFills.json",
        "funding_json": info / "userFunding.json",
        "state_json": info / "clearinghouseState.json",
        "orders_json": info / "openOrders.json",
    }


def test_one_day_idempotent_and_no_order_path(tmp_path):
    with patch("yowayowa.hl_exec.s07_forward.runner.order_path") as order:
        first = run_day(day=DAY, market=market(), out_dir=tmp_path, authenticity=x1())
        second = run_day(day=DAY, market=market(), out_dir=tmp_path, authenticity=x1())
        assert first["status"] == "appended"
        assert second["status"] == "already_recorded"
        order.assert_not_called()
    records = (tmp_path / "ledger.jsonl").read_text().splitlines()
    assert len(records) == 1
    assert json.loads(records[0])["day"] == DAY
    assert (tmp_path / "reports" / (DAY + ".json")).is_file()


def test_realistic_hourly_funding_millisecond_offsets(tmp_path):
    data = market()
    for row in data["funding"]:
        row["time"] += 64
    result = run_day(day=DAY, market=data, out_dir=tmp_path, authenticity=x1())
    assert result["status"] == "appended"


def test_missing_report_recovered_without_second_ledger_row(tmp_path):
    first = run_day(day=DAY, market=market(), out_dir=tmp_path, authenticity=x1())
    report = tmp_path / "reports" / (DAY + ".json")
    report.unlink()
    second = run_day(day=DAY, market=market(), out_dir=tmp_path, authenticity=x1())
    assert second["status"] == "already_recorded"
    assert json.loads(report.read_text()) == first["decision"]
    assert len((tmp_path / "ledger.jsonl").read_text().splitlines()) == 1


def test_verification_failure_is_fail_closed(tmp_path):
    bad = x1()
    bad["fills_json"] = REAL / "missing.json"
    with pytest.raises(ValueError, match="authenticity prerequisite errored"):
        run_day(day=DAY, market=market(), out_dir=tmp_path, authenticity=bad)
    assert not (tmp_path / "ledger.jsonl").exists()
