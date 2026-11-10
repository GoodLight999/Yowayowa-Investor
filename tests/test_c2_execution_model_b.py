"""Tests for the C2 paper execution model B.

Covers the two changes from the 2026-11-06 verdict
(docs/C2_LANE15_EVIDENCE_VERDICT_20261106.md §5.4):

1. Limit-order references are re-based on the live quote at submission time
   instead of the previous session close (11-02: SPY opened +49.4bps against a
   +50bps cap, IWM did not fill at the open at all).
2. At 10:00 ET an unfilled C2 day limit is canceled per-order and replaced with
   a market order, and the transition is recorded in ``orders.jsonl`` with
   ``reference_price_source="market_fallback"``.

Plus the safety invariant: ``cancel_all_orders`` is reachable only from the
kill-switch path. No network is touched: every broker call is a fake.
"""

from __future__ import annotations

import ast
import datetime
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.paper_c2 import c2_runner
from scripts.paper_c2.alpaca_paper_client import AlpacaAccountSummary, AlpacaPaperClient
from tests.c2_synthetic_history import detached_copy, synthetic_base_history

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION = date(2026, 11, 3)
SESSION_STR = SESSION.isoformat()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _state() -> dict:
    return {
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


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every C2 runner path at tmp_path (never data/paper_c2)."""
    monkeypatch.setattr(c2_runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(c2_runner, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(c2_runner, "ORDERS_FILE", tmp_path / "orders.jsonl")
    monkeypatch.setattr(c2_runner, "MTM_FILE", tmp_path / "mtm_history.jsonl")
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_JSON", tmp_path / "attribution_summary.json")
    monkeypatch.setattr(c2_runner, "ATTRIBUTION_MD", tmp_path / "attribution_summary.md")
    monkeypatch.setattr(c2_runner, "PHASE_START_DATE", date(2026, 11, 1))
    monkeypatch.setattr(
        c2_runner, "load_local_history", lambda: detached_copy(synthetic_base_history())
    )
    c2_runner.save_state(_state())


def _fixed_now(monkeypatch: pytest.MonkeyPatch, when_utc: datetime.datetime) -> None:
    class _MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when_utc

    monkeypatch.setattr(c2_runner, "datetime", _MockDatetime)


def _read_orders(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


class _WeeklyBroker:
    """Fake paper broker for the weekly cycle."""

    def __init__(
        self,
        *,
        trades: dict[str, float] | None = None,
        quotes: dict[str, float] | None = None,
        trade_error: str | None = None,
        quote_error: str | None = None,
    ) -> None:
        self.base_url = "https://paper-api.alpaca.markets/v2"
        self.trades = trades or {}
        self.quotes = quotes or {}
        self.trade_error = trade_error
        self.quote_error = quote_error
        self.submitted: list[dict] = []

    def get_account(self) -> AlpacaAccountSummary:
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

    def get_positions(self) -> list[dict]:
        return []

    def get_daily_bars(self, *args, **kwargs) -> dict:
        return {}

    def get_latest_trades(self, symbols, feed="iex") -> dict:
        if self.trade_error:
            raise RuntimeError(self.trade_error)
        return {
            s: {"price": self.trades[s], "timestamp": "2026-11-03T13:00:00Z"}
            for s in symbols
            if s in self.trades
        }

    def get_latest_quotes(self, symbols, feed="iex") -> dict:
        if self.quote_error:
            raise RuntimeError(self.quote_error)
        return {
            s: {"price": self.quotes[s], "timestamp": "2026-11-03T13:00:00Z"}
            for s in symbols
            if s in self.quotes
        }

    def submit_paper_order(
        self,
        symbol: str,
        qty,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price=None,
        client_order_id=None,
    ) -> dict:
        row = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "limit_price": limit_price,
            "client_order_id": client_order_id,
        }
        self.submitted.append(row)
        return {"status": "accepted", "id": f"oid-{symbol}", **row}

    def cancel_all_orders(self, *, reason: str):  # pragma: no cover - must never run here
        raise AssertionError("weekly/fallback paths must not cancel all orders")


def _run_weekly(monkeypatch, broker, tmp_path, *, when_utc=None, dry_run=False):
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)
    _fixed_now(
        monkeypatch,
        when_utc or datetime.datetime(2026, 11, 3, 13, 0, tzinfo=datetime.UTC),
    )
    return c2_runner.run_weekly_cycle(
        as_of_date=SESSION,
        dry_run=dry_run,
        force=True,
        force_rule_only=True,
        ledger_path=tmp_path / "decisions.jsonl",
    )


def _stale_closes() -> dict[str, float]:
    hist = synthetic_base_history()
    return {s: bars[-1]["c"] for s, bars in hist.items()}


# --------------------------------------------------------------------------- #
# 1. limit reference is re-based on the live quote
# --------------------------------------------------------------------------- #


def test_weekly_limit_uses_live_quote_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live print replaces the stale close as the limit reference.

    Prices are +80bps above the previous close: under the old (stale close)
    model the +50bps limit would have been below the market at the open, which
    is exactly the 11-02 IWM failure.
    """
    _isolate(tmp_path, monkeypatch)
    closes = _stale_closes()
    live = {sym: round(close * 1.008, 2) for sym, close in closes.items()}
    broker = _WeeklyBroker(trades=live)

    res = _run_weekly(monkeypatch, broker, tmp_path)

    assert res["status"] == "success"
    assert len(broker.submitted) == 4
    rows = _read_orders(tmp_path / "orders.jsonl")
    assert len(rows) == 4
    for row in rows:
        sym = row["symbol"]
        assert row["reference_price_source"] == "live_quote"
        assert row["reference_price"] == pytest.approx(live[sym])
        assert row["reference_price_as_of_utc"] == "2026-11-03T13:00:00Z"
        assert row["stale_close_price"] == pytest.approx(closes[sym])
        # limit stays ref * (1 + 50bps); the +50bps cap is unchanged, the base moved
        assert row["limit_price"] == round(live[sym] * (1.0 + 50.0 / 10000.0), 2)
        headroom_bps = (row["limit_price"] / live[sym] - 1.0) * 10000.0
        assert 49.5 <= headroom_bps <= 50.5, f"{sym} headroom {headroom_bps:.2f}bps"
        # counterfactual: the stale-close limit would have been below the live price
        stale_limit = round(closes[sym] * (1.0 + 50.0 / 10000.0), 2)
        assert stale_limit < live[sym], "fixture must reproduce the 11-02 stale-reference trap"


def test_weekly_reference_uses_quote_mid_when_no_trade_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    closes = _stale_closes()
    quotes = {sym: round(close * 1.003, 2) for sym, close in closes.items()}
    broker = _WeeklyBroker(trades={}, quotes=quotes)

    res = _run_weekly(monkeypatch, broker, tmp_path)

    assert res["status"] == "success"
    for row in _read_orders(tmp_path / "orders.jsonl"):
        assert row["reference_price_source"] == "live_quote"
        assert row["reference_detail"] == "live_quote_mid"
        assert row["reference_price"] == pytest.approx(quotes[row["symbol"]])


def test_weekly_reference_falls_back_to_stale_close_on_data_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    closes = _stale_closes()
    broker = _WeeklyBroker(trade_error="data api 503", quote_error="data api 503")

    res = _run_weekly(monkeypatch, broker, tmp_path)

    assert res["status"] == "success"
    assert len(broker.submitted) == 4
    for row in _read_orders(tmp_path / "orders.jsonl"):
        sym = row["symbol"]
        assert row["reference_price_source"] == "stale_close"
        assert row["reference_price"] == pytest.approx(closes[sym])
        assert row["limit_price"] == round(closes[sym] * 1.005, 2)
        assert row["reference_detail"] == "data api 503"


def test_weekly_reference_ignores_absurd_print(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A print >20% away from the close is bad data: keep the close and say so."""
    _isolate(tmp_path, monkeypatch)
    closes = _stale_closes()
    broker = _WeeklyBroker(trades={sym: round(close * 2.0, 2) for sym, close in closes.items()})

    res = _run_weekly(monkeypatch, broker, tmp_path)

    assert res["status"] == "success"
    for row in _read_orders(tmp_path / "orders.jsonl"):
        assert row["reference_price_source"] == "stale_close"
        assert row["reference_detail"].startswith("live_trade_deviation_")
        assert row["reference_price"] == pytest.approx(closes[row["symbol"]])


# --------------------------------------------------------------------------- #
# 2. 10:00 ET market fallback
# --------------------------------------------------------------------------- #


class _SessionBroker:
    """Fake broker holding one open limit order per symbol for the session."""

    def __init__(self, *, cancel_works: bool = True) -> None:
        self.cancel_works = cancel_works
        self.calls: list[tuple] = []
        self.orders: dict[str, dict] = {}
        for i, sym in enumerate(c2_runner.C2_UNIVERSE):
            oid = f"oid-{sym}"
            self.orders[oid] = {
                "id": oid,
                "client_order_id": f"c2_{sym}_buy_{SESSION_STR}",
                "symbol": sym,
                "side": "buy",
                "qty": str(10 + i),
                "filled_qty": "0",
                "status": "new",
                "type": "limit",
                "limit_price": "1.00",
                "time_in_force": "day",
            }

    def get_orders(self, status: str = "open") -> list[dict]:
        self.calls.append(("get_orders", status))
        return [
            dict(o) for o in self.orders.values() if o["status"] in c2_runner.OPEN_ORDER_STATUSES
        ]

    def cancel_order(self, order_id: str) -> dict:
        self.calls.append(("cancel_order", order_id))
        if self.cancel_works:
            self.orders[order_id]["status"] = "canceled"
        return {}

    def submit_paper_order(
        self,
        symbol: str,
        qty,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price=None,
        client_order_id=None,
    ) -> dict:
        self.calls.append(("submit", symbol, order_type, time_in_force))
        row = {
            "id": f"fb-{symbol}",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "filled_qty": str(qty),
            "status": "filled",
            "type": order_type,
            "filled_avg_price": "100.00",
            "time_in_force": time_in_force,
        }
        self.orders[row["id"]] = row
        return dict(row)

    def get_latest_trades(self, symbols, feed="iex") -> dict:
        return {s: {"price": 100.0, "timestamp": "2026-11-03T15:05:00Z"} for s in symbols}

    def cancel_all_orders(self, *, reason: str):  # pragma: no cover - must never run here
        raise AssertionError("market fallback must cancel one order at a time")


def _run_fallback(monkeypatch, broker, *, when_utc=None, dry_run=False):
    monkeypatch.setattr(c2_runner, "AlpacaPaperClient", lambda *a, **k: broker)
    _fixed_now(
        monkeypatch,
        when_utc or datetime.datetime(2026, 11, 3, 15, 5, tzinfo=datetime.UTC),
    )
    return c2_runner.run_market_fallback(as_of_date=SESSION, dry_run=dry_run)


def test_market_fallback_replaces_unfilled_then_fills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unfilled limit -> per-order cancel -> market order -> filled, all recorded."""
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()

    res = _run_fallback(monkeypatch, broker)

    # 15:05 UTC == 10:05 EST (EST, not EDT: 10:00 ET is 15:00 UTC in November)
    assert res["status"] == "success"
    assert res["replaced_count"] == 4
    assert res["skipped"] == []

    rows = _read_orders(tmp_path / "orders.jsonl")
    assert len(rows) == 4
    for row in rows:
        sym = row["symbol"]
        assert row["order_type"] == "market"
        assert row["time_in_force"] == "day"  # GTC is forbidden
        assert row["reference_price_source"] == "market_fallback"
        assert row["limit_price"] is None
        assert row["replaces_order_id"] == f"oid-{sym}"
        assert row["replaces_client_order_id"] == f"c2_{sym}_buy_{SESSION_STR}"
        assert row["client_order_id"] == f"c2_{sym}_buy_{SESSION_STR}_fb"
        assert row["alpaca_response"]["status"] == "filled"

    # transition: every original is canceled, every replacement is filled
    for sym in c2_runner.C2_UNIVERSE:
        assert broker.orders[f"oid-{sym}"]["status"] == "canceled"
        assert broker.orders[f"fb-{sym}"]["status"] == "filled"
        assert broker.orders[f"fb-{sym}"]["filled_qty"] == broker.orders[f"fb-{sym}"]["qty"]

    # cancel must precede the replacement submission, per symbol
    for sym in c2_runner.C2_UNIVERSE:
        idx_cancel = broker.calls.index(("cancel_order", f"oid-{sym}"))
        idx_submit = broker.calls.index(("submit", sym, "market", "day"))
        assert idx_cancel < idx_submit

    state = c2_runner.load_state()
    assert state["last_market_fallback_date"] == SESSION_STR


def test_market_fallback_dry_run_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()

    res = _run_fallback(monkeypatch, broker, dry_run=True)

    assert res["status"] == "dry_run"
    assert res["replaced_count"] == 4
    assert not any(c[0] == "cancel_order" for c in broker.calls)
    assert not any(c[0] == "submit" for c in broker.calls)
    assert _read_orders(tmp_path / "orders.jsonl") == []
    assert c2_runner.load_state().get("last_market_fallback_date") is None


def test_market_fallback_skips_before_1000_et(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """09:30 ET (14:30 UTC) is still in the opening auction window: do nothing."""
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()

    res = _run_fallback(
        monkeypatch, broker, when_utc=datetime.datetime(2026, 11, 3, 14, 30, tzinfo=datetime.UTC)
    )

    assert res["status"] == "skipped_before_fallback_cutoff"
    assert res["cutoff_et"] == "10:00"
    assert broker.calls == []
    assert _read_orders(tmp_path / "orders.jsonl") == []


def test_market_fallback_skips_after_market_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()

    res = _run_fallback(
        monkeypatch, broker, when_utc=datetime.datetime(2026, 11, 3, 22, 0, tzinfo=datetime.UTC)
    )

    assert res["status"] == "skipped_after_market_close"
    assert broker.calls == []


def test_market_fallback_skips_when_kill_switch_triggered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    state = c2_runner.load_state()
    state["kill_switch_triggered"] = True
    state["kill_switch_event"] = {"trigger_type": "MAXDD_LIMIT_KILL"}
    c2_runner.save_state(state)
    broker = _SessionBroker()

    res = _run_fallback(monkeypatch, broker)

    assert res["status"] == "halted_by_kill_switch"
    assert broker.calls == []


def test_market_fallback_no_open_orders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()
    for o in broker.orders.values():
        o["status"] = "filled"

    res = _run_fallback(monkeypatch, broker)

    assert res["status"] == "no_open_orders"
    assert _read_orders(tmp_path / "orders.jsonl") == []


def test_market_fallback_refuses_duplicate_when_cancel_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel that did not take effect must not leave a second live order."""
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker(cancel_works=False)

    res = _run_fallback(monkeypatch, broker)

    assert res["status"] == "nothing_replaced"
    assert not any(c[0] == "submit" for c in broker.calls)
    assert {s["reason"] for s in res["skipped"]} == {"cancel_not_confirmed"}
    assert _read_orders(tmp_path / "orders.jsonl") == []


def test_market_fallback_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    broker = _SessionBroker()

    first = _run_fallback(monkeypatch, broker)
    assert first["status"] == "success"
    submitted_after_first = sum(1 for c in broker.calls if c[0] == "submit")

    second = _run_fallback(monkeypatch, broker)
    assert second["status"] == "already_executed"
    assert sum(1 for c in broker.calls if c[0] == "submit") == submitted_after_first

    # Even with the state marker gone, the ledger row for the session blocks a
    # second replacement for the same symbol.
    state = c2_runner.load_state()
    state.pop("last_market_fallback_date", None)
    c2_runner.save_state(state)
    for sym in c2_runner.C2_UNIVERSE:  # simulate the old limit somehow still open
        broker.orders[f"oid-{sym}"]["status"] = "new"

    third = _run_fallback(monkeypatch, broker)
    assert third["status"] == "nothing_replaced"
    assert {s["reason"] for s in third["skipped"]} == {"already_replaced_this_session"}
    assert len(_read_orders(tmp_path / "orders.jsonl")) == 4


# --------------------------------------------------------------------------- #
# 3. cancel_all_orders is kill-switch only
# --------------------------------------------------------------------------- #


def _cancel_all_call_sites() -> list[tuple[str, str | None, ast.Call]]:
    """Every ``*.cancel_all_orders(...)`` call in non-test code."""
    found: list[tuple[str, str | None, ast.Call]] = []
    for base in (REPO_ROOT / "scripts", REPO_ROOT / "src"):
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    child._parent = parent  # type: ignore[attr-defined]
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "cancel_all_orders":
                    enclosing = None
                    cur = getattr(node, "_parent", None)
                    while cur is not None:
                        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            enclosing = cur.name
                            break
                        cur = getattr(cur, "_parent", None)
                    found.append((str(path.relative_to(REPO_ROOT)), enclosing, node))
    return found


def test_cancel_all_orders_has_exactly_one_call_site_and_it_is_the_kill_switch() -> None:
    sites = _cancel_all_call_sites()
    assert len(sites) == 1, [(path, fn, call.lineno) for path, fn, call in sites]
    path, enclosing, call = sites[0]
    assert path == "scripts/paper_c2/c2_runner.py"
    assert enclosing == "run_daily_cycle"

    # it must carry the kill-switch reason token ...
    reasons = [kw.value for kw in call.keywords if kw.arg == "reason"]
    assert len(reasons) == 1
    assert isinstance(reasons[0], ast.Name)
    assert reasons[0].id == "KILL_SWITCH_CANCEL_REASON"

    # ... and sit inside the `if kill_eval.triggered:` branch
    guards: list[str] = []
    cur = getattr(call, "_parent", None)
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if isinstance(cur, ast.If):
            guards.append(ast.unparse(cur.test))
        cur = getattr(cur, "_parent", None)
    assert any("kill_eval.triggered" in g for g in guards), guards


def test_cancel_all_orders_rejects_non_kill_switch_reason() -> None:
    client = AlpacaPaperClient(
        key_id="test-key",
        secret_key="test-secret",
        base_url="https://paper-api.alpaca.markets/v2",
    )
    called: list[str] = []
    client._request = lambda url, method="GET", data=None, timeout=30.0: (  # type: ignore[method-assign]
        called.append(method) or []
    )

    with pytest.raises(PermissionError):
        client.cancel_all_orders(reason="weekly_rebalance")
    with pytest.raises(PermissionError):
        client.cancel_all_orders(reason="")
    assert called == []

    client.cancel_all_orders(reason="kill_switch")
    assert called == ["DELETE"]


def test_cancel_order_is_scoped_to_one_id() -> None:
    client = AlpacaPaperClient(
        key_id="test-key",
        secret_key="test-secret",
        base_url="https://paper-api.alpaca.markets/v2",
    )
    urls: list[str] = []
    client._request = lambda url, method="GET", data=None, timeout=30.0: (  # type: ignore[method-assign]
        urls.append(f"{method} {url}") or {}
    )

    client.cancel_order("order-123")
    assert urls == ["DELETE https://paper-api.alpaca.markets/v2/orders/order-123"]
