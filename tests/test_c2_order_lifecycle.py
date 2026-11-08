"""Tests for the C2 paper order-lifecycle follow-up (refresh / stale / retry).

Measured defect being fixed (2026-11-09): the 2026-11-03 lane15 orders were
submitted 2026-11-02T10:16Z, canceled in bulk at 2026-11-02T11:02:08Z, and
``orders.jsonl`` kept the submit-time ``pending_new`` snapshot forever -- so four
dead orders looked live while ``equity_15`` stayed at the cash baseline.
"""

from __future__ import annotations

import datetime
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.paper_c2 import c2_order_lifecycle as ol
from scripts.paper_c2 import c2_runner
from scripts.paper_c2.alpaca_paper_client import AlpacaAccountSummary

SYMBOLS = ("SPY", "IWM", "JNJ", "PG")
PRICES = {"SPY": 750.0, "IWM": 280.0, "JNJ": 270.0, "PG": 160.0}
WEIGHTS_25 = {"SPY": 0.25, "IWM": 0.25, "JNJ": 0.25, "PG": 0.25, "USD": 0.0}
EQUITY = 100_000.0


# --------------------------------------------------------------------------- #
# fake broker
# --------------------------------------------------------------------------- #
class FakeBroker:
    """In-memory Alpaca paper broker with the subset the lifecycle touches."""

    def __init__(
        self,
        *,
        session_date: date,
        orders: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | None = None,
        equity: float = EQUITY,
        expires_at_for_session: date | None = None,
    ) -> None:
        self.base_url = "https://paper-api.alpaca.markets/v2"
        self.session_date = session_date
        self.orders: list[dict[str, Any]] = list(orders or [])
        self.positions: list[dict[str, Any]] = list(positions or [])
        self.equity = equity
        self.expires_override = expires_at_for_session
        self.submitted: list[dict[str, Any]] = []
        self.canceled: list[str] = []
        self.get_orders_calls = 0
        self.get_order_calls = 0

    # -- reads ------------------------------------------------------------- #
    def get_account(self) -> AlpacaAccountSummary:
        return AlpacaAccountSummary(
            account_id="fake_acc",
            masked_id="fake...acc",
            status="ACTIVE",
            currency="USD",
            equity=self.equity,
            cash=self.equity,
            buying_power=self.equity * 4,
            raw={},
        )

    def get_positions(self) -> list[dict[str, Any]]:
        return list(self.positions)

    def get_orders(self, status: str = "open", **kwargs: Any) -> list[dict[str, Any]]:
        self.get_orders_calls += 1
        return [dict(o) for o in self.orders]

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Read-back used to confirm a cancel reached a terminal state."""
        self.get_order_calls += 1
        for order in self.orders:
            if order["id"] == str(order_id):
                return dict(order)
        return {"http_code": 404, "message": "order not found"}

    def get_clock(self) -> dict[str, Any]:
        return {
            "is_open": False,
            "next_open": f"{self.session_date.isoformat()}T09:30:00-05:00",
            "next_close": f"{self.session_date.isoformat()}T16:00:00-05:00",
        }

    def get_daily_bars(self, *args: Any, **kwargs: Any) -> dict[str, list[Any]]:
        return {}

    # -- writes ------------------------------------------------------------ #
    def submit_paper_order(
        self,
        symbol: str,
        qty: int | float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        expiry_day = self.expires_override or self.session_date
        record = {
            "id": f"fake-{len(self.orders) + len(self.submitted) + 1}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "filled_qty": "0",
            "order_type": order_type,
            "type": order_type,
            "time_in_force": time_in_force,
            "limit_price": str(limit_price),
            "status": "accepted",
            "submitted_at": "2026-11-09T23:00:00Z",
            "created_at": "2026-11-09T23:00:00Z",
            "updated_at": "2026-11-09T23:00:00Z",
            "expires_at": f"{expiry_day.isoformat()}T21:00:00Z",
            "expired_at": None,
            "canceled_at": None,
            "failed_at": None,
            "reject_reason": None,
            "extended_hours": False,
        }
        self.orders.append(record)
        self.submitted.append(record)
        return dict(record)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        for order in self.orders:
            if order["id"] == str(order_id):
                order["status"] = "canceled"
                order["canceled_at"] = "2026-11-09T23:05:00Z"
        return {"id": order_id, "status": "canceled"}

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        return [self.cancel_order(o["id"]) for o in self.orders if o["status"] in ol.OPEN_STATUSES]

    def close_all_positions(self, cancel_orders: bool = True) -> list[dict[str, Any]]:
        self.positions = []
        return []


def _order(
    client_order_id: str,
    *,
    symbol: str = "SPY",
    side: str = "buy",
    status: str = "canceled",
    qty: str = "33",
    expires_at: str | None = "2026-11-02T21:00:00Z",
    updated_at: str = "2026-11-02T11:02:08Z",
    filled_qty: str = "0",
    canceled_at: str | None = "2026-11-02T11:02:08.913876Z",
) -> dict[str, Any]:
    return {
        "id": f"id-{client_order_id}",
        "client_order_id": client_order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "filled_qty": filled_qty,
        "status": status,
        "time_in_force": "day",
        "limit_price": "759.5",
        "submitted_at": "2026-11-02T10:16:05Z",
        "created_at": "2026-11-02T10:16:05Z",
        "updated_at": updated_at,
        "expires_at": expires_at,
        "expired_at": None,
        "canceled_at": canceled_at,
        "failed_at": None,
        "reject_reason": None,
        "extended_hours": False,
    }


def _state(**overrides: Any) -> dict[str, Any]:
    state = {
        "alpaca_paper_equity": EQUITY,
        "completed_trading_days": [],
        "consecutive_api_failures": 0,
        "current_equity_10": EQUITY,
        "current_equity_15": EQUITY,
        "current_weights_10": {},
        "current_weights_15": dict(WEIGHTS_25),
        "initial_paper_equity": EQUITY,
        "kill_switch_event": None,
        "kill_switch_triggered": False,
        "last_decision_date": "2026-11-03",
        "last_mtm_bar_date": None,
        "last_mtm_date": None,
        "missed_rebalance_days": 0,
        "missed_trading_days": 0,
        "peak_equity_10": EQUITY,
        "peak_equity_15": EQUITY,
    }
    state.update(overrides)
    return state


NOW = datetime.datetime(2026, 11, 9, 23, 0, tzinfo=datetime.UTC)  # 18:00 ET, after the close
SESSION = date(2026, 11, 10)


def _run(
    broker: FakeBroker, tmp_path: Path, state: dict[str, Any] | None = None, **kwargs: Any
) -> dict[str, Any]:
    kwargs.setdefault("cancel_poll_interval_s", 0.0)
    return ol.run_order_lifecycle(
        broker,
        state=state if state is not None else _state(),
        as_of_date=date(2026, 11, 9),
        prices=PRICES,
        universe=SYMBOLS,
        now=kwargs.pop("now", NOW),
        ledger_path=tmp_path / "order_lifecycle.jsonl",
        record_submit=kwargs.pop("record_submit", None),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1. refresh
# --------------------------------------------------------------------------- #
def test_refresh_records_true_broker_status_not_the_submit_snapshot(tmp_path: Path) -> None:
    broker = FakeBroker(
        session_date=SESSION,
        orders=[_order(f"c2_{s}_buy_2026-11-03", symbol=s, status="canceled") for s in SYMBOLS],
        positions=[],
    )
    ledger = tmp_path / "order_lifecycle.jsonl"

    res = _run(broker, tmp_path, state=_state(current_weights_15={"USD": 1.0}))

    assert res["status"] == "ok"
    assert len(res["refreshed"]) == 4
    assert {r["status"] for r in res["refreshed"]} == {"canceled"}
    assert all(r["canceled_at"] == "2026-11-02T11:02:08.913876Z" for r in res["refreshed"])
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["refreshed"][0]["status"] == "canceled"


def test_refresh_never_rewrites_the_order_ledger(tmp_path: Path) -> None:
    orders_file = tmp_path / "orders.jsonl"
    original = json.dumps({"client_order_id": "c2_SPY_buy_2026-11-03", "status": "pending_new"})
    orders_file.write_text(original + "\n", encoding="utf-8")
    before = orders_file.read_bytes()

    broker = FakeBroker(session_date=SESSION, orders=[_order("c2_SPY_buy_2026-11-03")])
    _run(broker, tmp_path, state=_state(current_weights_15={"USD": 1.0}))

    assert orders_file.read_bytes() == before


# --------------------------------------------------------------------------- #
# 2. retry + idempotency
# --------------------------------------------------------------------------- #
def test_canceled_book_is_resent_once_and_a_second_run_sends_nothing(tmp_path: Path) -> None:
    broker = FakeBroker(
        session_date=SESSION,
        orders=[_order(f"c2_{s}_buy_2026-11-03", symbol=s) for s in SYMBOLS],
        positions=[],
    )
    recorded: list[dict[str, Any]] = []

    first = _run(broker, tmp_path, record_submit=recorded.append)
    assert first["status"] == "ok"
    assert len(first["submitted"]) == 4
    assert len(recorded) == 4
    assert {s["client_order_id"] for s in first["submitted"]} == {
        f"c2_{sym}_buy_2026-11-10_a1" for sym in SYMBOLS
    }
    assert all(s["window_ok"] for s in first["submitted"])
    assert all(s["session_date"] == "2026-11-10" for s in first["submitted"])

    # Same day, identical state, second run: the broker already holds the four
    # day orders queued for the 2026-11-10 open -> nothing may be re-sent.
    second = _run(broker, tmp_path, record_submit=recorded.append)
    assert second["status"] == "ok"
    assert second["submitted"] == []
    assert len(recorded) == 4, "duplicate submission on the second run"
    assert {s["reason"] for s in second["skipped"]} == {"live_order"}
    assert len(broker.submitted) == 4


def test_filled_book_submits_nothing(tmp_path: Path) -> None:
    positions = []
    for sym in SYMBOLS:
        target = int(EQUITY * 0.25 / PRICES[sym])
        positions.append({"symbol": sym, "qty": str(target)})
    broker = FakeBroker(
        session_date=SESSION,
        orders=[
            _order(f"c2_{s}_buy_2026-11-03", symbol=s, status="filled", filled_qty="33")
            for s in SYMBOLS
        ],
        positions=positions,
    )

    res = _run(broker, tmp_path)
    assert res["submitted"] == []
    assert broker.submitted == []


def test_retry_is_capped_per_session(tmp_path: Path) -> None:
    prior = [
        _order(
            "c2_SPY_buy_2026-11-10_a1",
            symbol="SPY",
            status="canceled",
            expires_at="2026-11-10T21:00:00Z",
        ),
        _order(
            "c2_SPY_buy_2026-11-10_a2",
            symbol="SPY",
            status="canceled",
            expires_at="2026-11-10T21:00:00Z",
        ),
    ]
    broker = FakeBroker(session_date=SESSION, orders=prior)

    res = _run(broker, tmp_path, max_attempts=2)
    spy = [s for s in res["skipped"] if s.get("symbol") == "SPY"]
    assert spy and spy[0]["reason"] == "retry_cap_reached"
    assert "c2_SPY_buy_2026-11-10_a3" not in {o["client_order_id"] for o in broker.submitted}


# --------------------------------------------------------------------------- #
# 3. stale open orders
# --------------------------------------------------------------------------- #
def test_stale_pending_new_is_canceled_then_resent(tmp_path: Path) -> None:
    stale = _order(
        "c2_SPY_buy_2026-11-03",
        symbol="SPY",
        status="pending_new",
        expires_at="2026-11-02T21:00:00Z",
    )
    broker = FakeBroker(session_date=SESSION, orders=[stale])

    res = _run(broker, tmp_path)
    assert [c["id"] for c in res["stale_canceled"]] == ["id-c2_SPY_buy_2026-11-03"]
    assert broker.canceled == ["id-c2_SPY_buy_2026-11-03"]
    spy = [s for s in res["submitted"] if s["symbol"] == "SPY"]
    assert len(spy) == 1 and spy[0]["client_order_id"] == "c2_SPY_buy_2026-11-10_a1"


def test_pending_new_wedged_for_more_than_a_day_is_canceled(tmp_path: Path) -> None:
    wedged = _order(
        "c2_SPY_buy_2026-11-03",
        status="pending_new",
        expires_at=None,
        updated_at="2026-11-07T00:00:00Z",
    )
    broker = FakeBroker(session_date=SESSION, orders=[wedged])

    res = _run(broker, tmp_path)
    assert len(res["stale_canceled"]) == 1
    assert "pending_new" in res["stale_canceled"][0]["reason"]


def test_fresh_open_order_is_left_alone(tmp_path: Path) -> None:
    live = _order(
        "c2_SPY_buy_2026-11-10_a1",
        status="new",
        expires_at="2026-11-10T21:00:00Z",
        updated_at="2026-11-09T22:00:00Z",
    )
    broker = FakeBroker(session_date=SESSION, orders=[live])

    res = _run(broker, tmp_path)
    assert res["stale_canceled"] == []
    assert broker.canceled == []
    assert [s["symbol"] for s in res["submitted"]] == ["IWM", "JNJ", "PG"]


# --------------------------------------------------------------------------- #
# 4. validity-window assertion (the 2026-11-02 defect)
# --------------------------------------------------------------------------- #
def test_window_mismatch_helper_matches_the_2026_11_02_defect() -> None:
    order = {"expires_at": "2026-11-02T21:00:00Z"}
    mismatch, reason = ol.window_mismatch(order, date(2026, 11, 3))
    assert mismatch is True
    assert "2026-11-02" in reason

    ok, _ = ol.window_mismatch({"expires_at": "2026-11-10T21:00:00Z"}, date(2026, 11, 10))
    assert ok is False

    unverified, note = ol.window_mismatch({}, date(2026, 11, 10))
    assert unverified is False and note == "expires_at_missing"


def test_order_whose_window_misses_the_session_is_canceled_not_left_live(tmp_path: Path) -> None:
    broker = FakeBroker(
        session_date=SESSION,
        positions=[],
        expires_at_for_session=date(2026, 11, 9),  # wrong: dies the day before
    )

    res = _run(broker, tmp_path)
    assert res["submitted"], "expected the submission attempt to be recorded"
    assert all(s["window_ok"] is False for s in res["submitted"])
    # every mislabelled order is canceled at the broker instead of being left live
    assert len(broker.canceled) == 4
    assert {o["status"] for o in broker.orders} == {"canceled"}
    assert any("session_window_mismatch" in e for e in res["errors"])


# --------------------------------------------------------------------------- #
# 5. halt / failure handling
# --------------------------------------------------------------------------- #
def test_kill_switch_refreshes_but_never_resends(tmp_path: Path) -> None:
    broker = FakeBroker(
        session_date=SESSION,
        orders=[_order(f"c2_{s}_buy_2026-11-03", symbol=s) for s in SYMBOLS],
    )
    res = _run(broker, tmp_path, state=_state(kill_switch_triggered=True))

    assert res["status"] == "skipped_halted"
    assert len(res["refreshed"]) == 4
    assert broker.submitted == []


def test_order_retry_pause_flag_blocks_resend(tmp_path: Path) -> None:
    broker = FakeBroker(session_date=SESSION)
    res = _run(broker, tmp_path, state=_state(order_retry_paused=True))
    assert res["status"] == "skipped_halted"
    assert broker.submitted == []


def test_non_positive_equity_never_triggers_a_liquidation(tmp_path: Path) -> None:
    broker = FakeBroker(
        session_date=SESSION,
        positions=[{"symbol": s, "qty": "10"} for s in SYMBOLS],
        equity=0.0,
    )
    res = _run(broker, tmp_path)
    assert res["status"] == "error"
    assert broker.submitted == []
    assert any("non_positive_equity" in e for e in res["errors"])


def test_orders_read_failure_fails_closed(tmp_path: Path) -> None:
    class BrokenBroker(FakeBroker):
        def get_orders(self, status: str = "open", **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("401 unauthorized")

    broker = BrokenBroker(session_date=SESSION)
    res = _run(broker, tmp_path)
    assert res["status"] == "error"
    assert broker.submitted == []
    assert any("orders_read_failed" in e for e in res["errors"])


# --------------------------------------------------------------------------- #
# 6. session resolution
# --------------------------------------------------------------------------- #
def test_session_resolution_uses_the_broker_clock() -> None:
    broker = FakeBroker(session_date=SESSION)
    window = ol.resolve_session(broker, NOW)
    assert window.session_date == SESSION
    assert window.source == "clock"


def test_session_resolution_falls_back_after_the_et_close() -> None:
    class NoClock:
        pass

    # Friday 2026-11-06 18:00 ET -> next session is Monday 2026-11-09
    window = ol.resolve_session(
        NoClock(), datetime.datetime(2026, 11, 6, 23, 0, tzinfo=datetime.UTC)
    )
    assert window.session_date == date(2026, 11, 9)
    assert window.source == "local"

    pre_open = ol.resolve_session(
        NoClock(), datetime.datetime(2026, 11, 9, 13, 0, tzinfo=datetime.UTC)
    )
    assert pre_open.session_date == date(2026, 11, 9)


# --------------------------------------------------------------------------- #
# 7. end-to-end through the daily batch (2 runs -> 0 duplicate orders)
# --------------------------------------------------------------------------- #
def _synthetic_history(end_date: date, n_bars: int = 300) -> dict[str, list[dict[str, Any]]]:
    sessions: list[date] = []
    cursor = end_date
    while len(sessions) < n_bars:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    sessions.reverse()

    starts = {"SPY": 750.0, "IWM": 280.0, "JNJ": 270.0, "PG": 160.0}
    history: dict[str, list[dict[str, Any]]] = {}
    for sym in SYMBOLS:
        bars = []
        for i, session in enumerate(sessions):
            close = round(starts[sym] + 0.1 * i, 2)
            prev = round(starts[sym] + 0.1 * (i - 1), 2) if i else close
            bars.append(
                {
                    "t": f"{session.isoformat()}T04:00:00Z",
                    "o": prev,
                    "h": round(max(prev, close) * 1.002, 2),
                    "l": round(min(prev, close) * 0.998, 2),
                    "c": close,
                    "v": 1_000_000.0,
                }
            )
        history[sym] = bars
    return history


def test_weekly_replaces_orders_already_queued_for_the_same_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daily retry queued for the decision session must be replaced, not stacked.

    Timeline this guards: the 09:00 JST daily run can queue the *previous* target
    book for Monday's session 13 hours before the 22:00 JST weekly decision. If the
    weekly then submits its own copy, both fill and the exposure doubles.
    """
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DATA_DIR", tmp_path)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", tmp_path / "orders.jsonl")
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.DEFAULT_LEDGER_PATH", tmp_path / "decisions.jsonl"
    )
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))
    monkeypatch.setattr("scripts.paper_c2.c2_features._load_latest_dgs1", lambda target_date: 0.04)
    history = _synthetic_history(date(2026, 11, 2))
    monkeypatch.setattr(
        c2_runner, "load_local_history", lambda: {k: list(v) for k, v in history.items()}
    )

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 3, 13, 0, tzinfo=datetime.UTC)

    monkeypatch.setattr(c2_runner, "datetime", FrozenDatetime)
    c2_runner.save_state(
        _state(current_weights_10={}, current_weights_15={}, last_decision_date=None)
    )

    prequeued = _order(
        "c2_SPY_buy_2026-11-03_a1",
        symbol="SPY",
        status="accepted",
        expires_at="2026-11-03T21:00:00Z",
        updated_at="2026-11-03T00:05:00Z",
    )
    broker = FakeBroker(session_date=date(2026, 11, 3), orders=[prequeued])
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)

    res = c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=True,
        force_rule_only=True,
        ledger_path=tmp_path / "decisions.jsonl",
    )

    assert res["status"] == "success"
    assert len(res["orders_placed"]) == 4
    assert broker.canceled == ["id-c2_SPY_buy_2026-11-03_a1"], "superseded order must be canceled"
    assert res["orders_placed"][0]["replaced_client_order_ids"] == ["c2_SPY_buy_2026-11-03_a1"]
    assert {o["client_order_id"] for o in res["orders_placed"]} == {
        f"c2_{sym}_buy_2026-11-03" for sym in SYMBOLS
    }


def test_daily_cycle_resent_book_is_idempotent_across_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = date(2026, 11, 10)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("scripts.paper_c2.c2_runner.MTM_FILE", tmp_path / "mtm_history.jsonl")
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DATA_DIR", tmp_path)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", tmp_path / "orders.jsonl")
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.ATTRIBUTION_JSON", tmp_path / "attribution_summary.json"
    )
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.ATTRIBUTION_MD", tmp_path / "attribution_summary.md"
    )
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))
    monkeypatch.setattr("scripts.paper_c2.c2_features._load_latest_dgs1", lambda target_date: 0.04)

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 9, 23, 0, tzinfo=datetime.UTC)

    monkeypatch.setattr(c2_runner, "datetime", FrozenDatetime)

    c2_runner.save_state(
        _state(last_mtm_date=None, last_mtm_bar_date=None, completed_trading_days=[])
    )

    broker = FakeBroker(
        session_date=session,
        orders=[_order(f"c2_{s}_buy_2026-11-03", symbol=s) for s in SYMBOLS],
        positions=[],
    )
    history = _synthetic_history(date(2026, 11, 9))

    first = c2_runner.run_daily_cycle(
        as_of_date=date(2026, 11, 9),
        override_history=history,
        client=broker,
        ledger_path=tmp_path / "decisions.jsonl",
    )
    assert first["status"] == "success"
    life1 = first["order_lifecycle"]
    assert life1 is not None and len(life1["submitted"]) == 4
    assert len(broker.submitted) == 4

    second = c2_runner.run_daily_cycle(
        as_of_date=date(2026, 11, 9),
        override_history=history,
        client=broker,
        ledger_path=tmp_path / "decisions.jsonl",
    )
    assert second["status"] == "already_executed"
    life2 = second["order_lifecycle"]
    assert life2 is not None
    assert life2["submitted"] == [], "second run on the same session must not duplicate orders"
    assert len(broker.submitted) == 4

    # the lifecycle snapshot is append-only and the order ledger picked up the resends
    lifecycle_lines = (
        (tmp_path / "order_lifecycle.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    assert len(lifecycle_lines) == 2
    orders_lines = (tmp_path / "orders.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(orders_lines) == 4
    assert {json.loads(line)["origin"] for line in orders_lines} == {"order_lifecycle_retry"}
    assert c2_runner.load_state()["last_order_lifecycle"]["session_date"] == "2026-11-10"


# --------------------------------------------------------------------------- #
# 8. cancel confirmation: never re-send next to an unconfirmed old order
#    (review finding 2026-11-09: DELETE 422 / connection error / pending_cancel)
# --------------------------------------------------------------------------- #
class CancelRefusedBroker(FakeBroker):
    """DELETE is refused (422) and the order stays live."""

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        return {"http_code": 422, "message": "order cannot be canceled"}


class CancelAcceptedButStillOpenBroker(FakeBroker):
    """DELETE returns 204 (an accepted *request*) but the order is still live."""

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        return {}


class CancelConnectionErrorBroker(FakeBroker):
    """DELETE raises (connection reset) and the order stays live."""

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        raise ConnectionError("connection reset by peer")


class PendingCancelBroker(FakeBroker):
    """DELETE moves the order to pending_cancel, where it can still fill."""

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        for order in self.orders:
            if order["id"] == str(order_id):
                order["status"] = "pending_cancel"
        return {}


class PartialFillOnCancelBroker(FakeBroker):
    """10 SPY shares fill before the cancel lands; the broker then reports canceled."""

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.canceled.append(str(order_id))
        for order in self.orders:
            if order["id"] == str(order_id):
                order["status"] = "canceled"
                order["filled_qty"] = "10"
                order["canceled_at"] = "2026-11-09T23:05:00Z"
        self.positions = [{"symbol": "SPY", "qty": "10"}]
        return {}


def test_cancel_request_refused_blocks_the_resend(tmp_path: Path) -> None:
    stale = _order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")
    broker = CancelRefusedBroker(session_date=SESSION, orders=[stale])

    res = _run(broker, tmp_path)

    assert res["stale_canceled"] == []
    assert len(res["cancel_unconfirmed"]) == 1
    assert res["cancel_unconfirmed"][0]["broker_status"] == "pending_new"
    assert [s for s in res["submitted"] if s["symbol"] == "SPY"] == []
    assert [o["symbol"] for o in broker.submitted] == ["IWM", "JNJ", "PG"], (
        "SPY must not be re-sent next to its still-live order"
    )
    assert any("cancel_unconfirmed" in e for e in res["errors"])


def test_accepted_cancel_that_has_not_settled_blocks_the_resend(tmp_path: Path) -> None:
    """204 only means the request was accepted; pending_new is not terminal."""
    stale = _order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")
    broker = CancelAcceptedButStillOpenBroker(session_date=SESSION, orders=[stale])

    res = _run(broker, tmp_path)

    assert res["stale_canceled"] == []
    assert res["cancel_unconfirmed"][0]["cancel_detail"]
    assert [o["symbol"] for o in broker.submitted] == ["IWM", "JNJ", "PG"]


def test_pending_cancel_blocks_the_resend(tmp_path: Path) -> None:
    stale = _order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")
    broker = PendingCancelBroker(session_date=SESSION, orders=[stale])

    res = _run(broker, tmp_path)

    assert res["cancel_unconfirmed"][0]["broker_status"] == "pending_cancel"
    assert [o["symbol"] for o in broker.submitted] == ["IWM", "JNJ", "PG"]


def test_cancel_connection_failure_blocks_every_resend(tmp_path: Path) -> None:
    """A raised cancel error is a failure to confirm, not a licence to re-send."""
    stale_book = [
        _order(f"c2_{sym}_buy_2026-11-03", symbol=sym, status="pending_new") for sym in SYMBOLS
    ]
    broker = CancelConnectionErrorBroker(session_date=SESSION, orders=stale_book)

    res = _run(broker, tmp_path)

    assert len(res["cancel_unconfirmed"]) == 4
    assert res["submitted"] == []
    assert broker.submitted == [], "connection failure must not lead to a re-send"


def test_confirmed_cancel_allows_one_resend_then_the_book_is_quiet(tmp_path: Path) -> None:
    """Once the broker reports a terminal state the next run may re-send, once."""
    stale = _order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")
    broker = FakeBroker(session_date=SESSION, orders=[stale])

    first = _run(broker, tmp_path)
    assert len(first["stale_canceled"]) == 1
    assert len(first["submitted"]) == 4

    second = _run(broker, tmp_path)
    assert second["submitted"] == []
    assert len(broker.submitted) == 4


def test_confirmed_cancel_after_a_partial_fill_resends_only_the_residual(tmp_path: Path) -> None:
    stale = _order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")
    broker = PartialFillOnCancelBroker(session_date=SESSION, orders=[stale])

    res = _run(broker, tmp_path)

    assert len(res["stale_canceled"]) == 1
    assert res["stale_canceled"][0]["filled_qty"] == 10.0
    spy = [s for s in res["submitted"] if s["symbol"] == "SPY"]
    assert len(spy) == 1
    assert spy[0]["qty"] == 23, "must send target 33 minus the 10 already filled"
    assert spy[0]["client_order_id"] == "c2_SPY_buy_2026-11-10_a1"


def test_cancel_refused_but_order_already_filled_counts_as_confirmed() -> None:
    """A 422 can mean 'already filled'; the read-back decides, not the status code."""

    class FilledBeforeCancelBroker(FakeBroker):
        def cancel_order(self, order_id: str) -> dict[str, Any]:
            for order in self.orders:
                if order["id"] == str(order_id):
                    order["status"] = "filled"
                    order["filled_qty"] = "33"
            return {"http_code": 422, "message": "order already filled"}

    broker = FilledBeforeCancelBroker(
        session_date=SESSION,
        orders=[_order("c2_SPY_buy_2026-11-03", symbol="SPY", status="pending_new")],
    )
    confirmation = ol.cancel_and_confirm(broker, "id-c2_SPY_buy_2026-11-03", polls=2, interval_s=0)

    assert confirmation.confirmed is True
    assert confirmation.status == "filled"
    assert confirmation.filled_qty == 33.0


# --------------------------------------------------------------------------- #
# 9. weekly replacement must also confirm the cancel (same review finding)
# --------------------------------------------------------------------------- #
def _prepare_weekly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("scripts.paper_c2.c2_runner.DATA_DIR", tmp_path)
    monkeypatch.setattr("scripts.paper_c2.c2_runner.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("scripts.paper_c2.c2_runner.ORDERS_FILE", tmp_path / "orders.jsonl")
    monkeypatch.setattr(
        "scripts.paper_c2.c2_runner.DEFAULT_LEDGER_PATH", tmp_path / "decisions.jsonl"
    )
    monkeypatch.setattr("scripts.paper_c2.c2_runner.PHASE_START_DATE", date(2026, 11, 1))
    monkeypatch.setattr("scripts.paper_c2.c2_features._load_latest_dgs1", lambda target_date: 0.04)
    history = _synthetic_history(date(2026, 11, 2))
    monkeypatch.setattr(
        c2_runner, "load_local_history", lambda: {k: list(v) for k, v in history.items()}
    )

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 11, 3, 13, 0, tzinfo=datetime.UTC)

    monkeypatch.setattr(c2_runner, "datetime", FrozenDatetime)
    c2_runner.save_state(
        _state(current_weights_10={}, current_weights_15={}, last_decision_date=None)
    )


def _weekly(_tmp_path: Path) -> dict[str, Any]:
    return c2_runner.run_weekly_cycle(
        as_of_date=date(2026, 11, 3),
        dry_run=False,
        force=True,
        force_rule_only=True,
        ledger_path=_tmp_path / "decisions.jsonl",
    )


def test_weekly_blocks_a_symbol_whose_old_order_cancel_is_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_weekly(monkeypatch, tmp_path)
    prequeued = _order(
        "c2_SPY_buy_2026-11-03_a1",
        symbol="SPY",
        status="accepted",
        expires_at="2026-11-03T21:00:00Z",
        updated_at="2026-11-03T00:05:00Z",
    )
    broker = CancelRefusedBroker(session_date=date(2026, 11, 3), orders=[prequeued])
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)

    res = _weekly(tmp_path)

    assert res["status"] == "success"
    assert [e["symbol"] for e in res["orders_placed"]] == ["IWM", "JNJ", "PG"]
    blocked = [b for b in res["orders_blocked"] if b["symbol"] == "SPY"]
    assert len(blocked) == 1 and blocked[0]["reason"] == "cancel_unconfirmed"
    assert [o["symbol"] for o in broker.submitted] == ["IWM", "JNJ", "PG"], (
        "SPY must not be re-sent while its old order is still live"
    )


def test_weekly_resends_only_the_residual_after_a_fill_raced_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_weekly(monkeypatch, tmp_path)
    prequeued = _order(
        "c2_SPY_buy_2026-11-03_a1",
        symbol="SPY",
        status="accepted",
        expires_at="2026-11-03T21:00:00Z",
        updated_at="2026-11-03T00:05:00Z",
    )
    broker = PartialFillOnCancelBroker(session_date=date(2026, 11, 3), orders=[prequeued])
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)

    res = _weekly(tmp_path)

    entry = next(e for e in res["orders_placed"] if e["symbol"] == "SPY")
    weight = float(res["record_15"]["final_weights"].get("SPY", 0.0))
    target_shares = int(EQUITY * weight / float(entry["reference_price"]))
    assert int(entry["qty"]) == target_shares - 10
    assert entry["client_order_id"] == "c2_SPY_buy_2026-11-03"


def test_weekly_rerun_same_day_leaves_one_live_order_per_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_weekly(monkeypatch, tmp_path)
    broker = FakeBroker(session_date=date(2026, 11, 3))
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)

    first = _weekly(tmp_path)
    assert len(first["orders_placed"]) == 4
    assert first["orders_blocked"] == []

    second = _weekly(tmp_path)
    assert second["orders_blocked"] == []
    assert len(second["orders_placed"]) == 4
    # the bare client_order_ids were already used, so the replacements are versioned
    assert {e["client_order_id"] for e in second["orders_placed"]} == {
        f"c2_{sym}_buy_2026-11-03_a1" for sym in SYMBOLS
    }
    live = [o for o in broker.orders if o["status"] in ol.OPEN_STATUSES]
    assert len(live) == 4, "re-running the weekly must not stack a second book"
