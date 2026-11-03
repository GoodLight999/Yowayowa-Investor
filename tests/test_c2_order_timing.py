"""Tests for C2 weekly order timing alignment with decision session date."""

from __future__ import annotations

import datetime
from datetime import date
from pathlib import Path

import pytest

from scripts.paper_c2 import c2_runner
from scripts.paper_c2.alpaca_paper_client import AlpacaAccountSummary


def test_weekly_cycle_skips_broker_submission_when_prior_to_decision_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that weekly rebalance orders are NOT submitted prior to the decision date.

    When weekly cycle is executed prior to decision session date (e.g. executed on 2026-11-02
    with as_of_date=2026-11-03), broker submission must be skipped (dry_run=True).
    Otherwise, orders become active in the prior session (2026-11-02 regular market)
    and execute 1 session too early, violating the decision schedule.
    """
    test_state_file = tmp_path / "state.json"
    test_orders_file = tmp_path / "orders.jsonl"
    test_ledger_file = tmp_path / "decisions.jsonl"

    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", test_state_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", test_orders_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DEFAULT_LEDGER_PATH", test_ledger_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))

    initial_state = {
        "alpaca_paper_equity": 100000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "current_weights_10": {},
        "current_weights_15": {},
        "initial_paper_equity": 100000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": None,
        "last_mtm_bar_date": None,
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
    }
    c2_runner.save_state(initial_state)

    submitted_orders = []

    class MockAlpacaClient:
        def __init__(self, *args, **kwargs):
            self.base_url = "https://paper-api.alpaca.markets/v2"

        def get_account(self):
            return AlpacaAccountSummary(
                account_id="mock_id",
                masked_id="mock...id",
                status="ACTIVE",
                currency="USD",
                equity=100000.0,
                cash=100000.0,
                buying_power=200000.0,
                raw={},
            )

        def get_positions(self):
            return []

        def get_daily_bars(self, *args, **kwargs):
            return {}

        def submit_paper_order(
            self,
            symbol: str,
            qty: int | float,
            side: str,
            order_type: str = "market",
            time_in_force: str = "day",
            limit_price: float | None = None,
            client_order_id: str | None = None,
        ):
            record = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "limit_price": limit_price,
                "client_order_id": client_order_id,
            }
            submitted_orders.append(record)
            return {"status": "accepted", "id": "mock_order_id", **record}

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", MockAlpacaClient)

    # Simulate execution on 2026-11-02 for as_of_date=2026-11-03
    class MockDatetimePrior(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 2, 10, 16, 0, tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(c2_runner, "datetime", MockDatetimePrior)

    res = c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=False,
        force_rule_only=True,
        ledger_path=test_ledger_file,
    )

    assert res["status"] == "skipped_prior_to_decision_date"
    # When current date (2026-11-02) is before as_of_date (2026-11-03),
    # the entire cycle is skipped early to avoid writing decisions or consuming the schedule.
    assert len(submitted_orders) == 0, (
        f"Expected 0 orders submitted when executing prior to as_of_date, but got {len(submitted_orders)}"
    )
    assert not test_ledger_file.exists() or test_ledger_file.stat().st_size == 0
    saved_state = c2_runner.load_state()
    assert saved_state.get("last_decision_date") is None


def test_weekly_cycle_submits_day_orders_on_decision_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that weekly rebalance orders are submitted with time_in_force='day' when executed on decision date."""
    test_state_file = tmp_path / "state.json"
    test_orders_file = tmp_path / "orders.jsonl"
    test_ledger_file = tmp_path / "decisions.jsonl"

    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", test_state_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", test_orders_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DEFAULT_LEDGER_PATH", test_ledger_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))

    initial_state = {
        "alpaca_paper_equity": 100000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "current_weights_10": {},
        "current_weights_15": {},
        "initial_paper_equity": 100000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": None,
        "last_mtm_bar_date": None,
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
    }
    c2_runner.save_state(initial_state)

    submitted_orders = []

    class MockAlpacaClient:
        def __init__(self, *args, **kwargs):
            self.base_url = "https://paper-api.alpaca.markets/v2"

        def get_account(self):
            return AlpacaAccountSummary(
                account_id="mock_id",
                masked_id="mock...id",
                status="ACTIVE",
                currency="USD",
                equity=100000.0,
                cash=100000.0,
                buying_power=200000.0,
                raw={},
            )

        def get_positions(self):
            return []

        def get_daily_bars(self, *args, **kwargs):
            return {}

        def submit_paper_order(
            self,
            symbol: str,
            qty: int | float,
            side: str,
            order_type: str = "market",
            time_in_force: str = "day",
            limit_price: float | None = None,
            client_order_id: str | None = None,
        ):
            record = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "limit_price": limit_price,
                "client_order_id": client_order_id,
            }
            submitted_orders.append(record)
            return {"status": "accepted", "id": "mock_order_id", **record}

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", MockAlpacaClient)

    # Simulate execution on 2026-11-03 13:00 UTC (pre-market on decision date)
    class MockDatetimeOnDate(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 3, 13, 0, 0, tzinfo=datetime.timezone.utc)

    monkeypatch.setattr(c2_runner, "datetime", MockDatetimeOnDate)

    res = c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=True,
        force_rule_only=True,
        ledger_path=test_ledger_file,
    )

    assert res["status"] == "success"
    assert len(submitted_orders) == 4
    for o in submitted_orders:
        assert o["time_in_force"] == "day", f"Expected day TIF, got {o['time_in_force']}"


def test_prior_execution_skipped_allows_full_execution_on_decision_date_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that an early cycle execution prior to as_of_date skips cleanly without consuming the decision,

    allowing the scheduled run on decision date to execute fully without force=True.
    """
    test_state_file = tmp_path / "state.json"
    test_orders_file = tmp_path / "orders.jsonl"
    test_ledger_file = tmp_path / "decisions.jsonl"

    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", test_state_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", test_orders_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DEFAULT_LEDGER_PATH", test_ledger_file)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))

    initial_state = {
        "alpaca_paper_equity": 100000.0,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": 100000.0,
        "current_equity_15": 100000.0,
        "current_weights_10": {},
        "current_weights_15": {},
        "initial_paper_equity": 100000.0,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": None,
        "last_mtm_bar_date": None,
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": 100000.0,
        "peak_equity_15": 100000.0,
    }
    c2_runner.save_state(initial_state)

    submitted_orders = []

    class MockAlpacaClient:
        def __init__(self, *args, **kwargs):
            self.base_url = "https://paper-api.alpaca.markets/v2"

        def get_account(self):
            return AlpacaAccountSummary(
                account_id="mock_id",
                masked_id="mock...id",
                status="ACTIVE",
                currency="USD",
                equity=100000.0,
                cash=100000.0,
                buying_power=200000.0,
                raw={},
            )

        def get_positions(self):
            return []

        def get_daily_bars(self, *args, **kwargs):
            return {}

        def submit_paper_order(
            self,
            symbol: str,
            qty: int | float,
            side: str,
            order_type: str = "market",
            time_in_force: str = "day",
            limit_price: float | None = None,
            client_order_id: str | None = None,
        ):
            record = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "limit_price": limit_price,
                "client_order_id": client_order_id,
            }
            submitted_orders.append(record)
            return {"status": "accepted", "id": "mock_order_id", **record}

    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", MockAlpacaClient)

    # 1. First execution: Pre-scheduled run on 2026-11-02 (prior to decision session 2026-11-03)
    current_simulated_time = datetime.datetime(2026, 11, 2, 10, 16, 0, tzinfo=datetime.timezone.utc)

    class MockDatetimeDynamic(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return current_simulated_time

    monkeypatch.setattr(c2_runner, "datetime", MockDatetimeDynamic)

    res_prior = c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=False,
        force_rule_only=True,
        ledger_path=test_ledger_file,
    )

    # Verify prior execution skipped cleanly
    assert res_prior["status"] == "skipped_prior_to_decision_date"
    assert len(submitted_orders) == 0
    # Decisions and state must NOT have been updated (decision is not consumed)
    assert not test_ledger_file.exists() or test_ledger_file.stat().st_size == 0
    state_after_prior = c2_runner.load_state()
    assert state_after_prior.get("last_decision_date") is None

    # 2. Second execution: Production run on decision date 2026-11-03 without force!
    current_simulated_time = datetime.datetime(2026, 11, 3, 13, 0, 0, tzinfo=datetime.timezone.utc)

    res_on_date = c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=False,  # <--- CRUCIAL: Must succeed without force=True!
        force_rule_only=True,
        ledger_path=test_ledger_file,
    )

    # Verify that decision date run executes fully and places 4 day orders
    assert res_on_date["status"] == "success"
    assert len(submitted_orders) == 4
    for o in submitted_orders:
        assert o["time_in_force"] == "day"

    state_after_exec = c2_runner.load_state()
    assert state_after_exec.get("last_decision_date") == "2026-11-03"
    assert test_ledger_file.exists()
    assert len(test_ledger_file.read_text().strip().splitlines()) == 2
