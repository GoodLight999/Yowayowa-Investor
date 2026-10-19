"""Unit tests for the demo paper-loop v1.1 (event-journal edition).

Covers: G1-G9 synthetic guard violations, D1 5-sigma prior-window firing,
D2 delta-notional fills, D3 daily mtm, D4 decide idempotency, D5 recorded_at
internal clock, D7 benchmark buy-once, D8 signed shortfall, D10 ChainError,
C2 commit-before-fill assert, plus the zero-order-path grep guard (M6).

v1.3 (t_2202c100): the -20% DD line is a warning (not a stop), C6 superseded
pendings, R2/R3 split in the verifier, and the disabled bench-DD hook.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from yowayowa import paperloop as pl
from yowayowa import paperloop_verify as pv
from yowayowa.backtest_models import BacktestStrategyDefinition
from yowayowa.paperloop_guard import GuardInput, check_trade
from yowayowa.services.backtest import _target_weights


def _write_crypto_days(data_dir: Path, symbol: str, days: list[tuple[str, float, float]]) -> None:
    path = data_dir / "crypto-ohlcv" / symbol / "ohlcv.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for as_of, opened, close in days:
            handle.write(
                json.dumps(
                    {
                        "symbol": symbol,
                        "interval": "1d",
                        "currency": "USDT",
                        "provider": "binance",
                        "source_url": "https://example/klines",
                        "license_class": "personal_only",
                        "retrieved_at": "2026-01-01T00:00:00+00:00",
                        "as_of": as_of + "T00:00:00Z",
                        "open": opened,
                        "high": max(opened, close) * 1.001,
                        "low": min(opened, close) * 0.999,
                        "close": close,
                        "volume": 1000.0,
                    }
                )
                + "\n"
            )


def _write_stock_days(data_dir: Path, symbol: str, days: list[tuple[str, float, float]]) -> None:
    path = data_dir / "stock-ohlcv" / symbol / "ohlcv.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for as_of, opened, close in days:
            handle.write(
                json.dumps(
                    {
                        "symbol": symbol,
                        "interval": "1d",
                        "currency": "USD",
                        "provider": "alpaca",
                        "source_url": "https://example/stocks",
                        "license_class": "personal_only",
                        "retrieved_at": "2026-01-01T00:00:00+00:00",
                        "as_of": as_of + "T04:00:00Z",
                        "open": opened,
                        "high": max(opened, close) * 1.001,
                        "low": min(opened, close) * 0.999,
                        "close": close,
                        "volume": 1000.0,
                    }
                )
                + "\n"
            )


def _series(
    start: float, n: int, *, drift: float = 0.0001, rising_after: int | None = None
) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    value = start
    for i in range(n):
        day_drift = drift * 2 if rising_after is not None and i >= rising_after else drift
        opened = value * (1 + day_drift / 2)
        close = value * (1 + day_drift)
        out.append(((date(2026, 1, 1) + timedelta(days=i)).isoformat(), opened, close))
        value = close
    return out


def _s01_strategy() -> BacktestStrategyDefinition:
    return BacktestStrategyDefinition(
        id="s01-a",
        name="crypto_trend_dual_ma",
        description="paper book A",
        signal="crypto_trend_dual_ma",
        universe=list(pl.CRYPTO_UNIVERSE),
        weighting="equal",
    )


def _now_of(day: date, minute: int = 20) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(minutes=minute)


def _stub_networks(monkeypatch) -> None:
    monkeypatch.setattr(pl, "_forming_candle_open", lambda symbol: None)
    monkeypatch.setattr(pl, "_fetch_crypto_1h", lambda symbol, limit=48: [])


def _prepare(tmp_path: Path, monkeypatch, *, days: int = 260):
    _stub_networks(monkeypatch)
    data_dir = tmp_path / "data"
    btc = _series(100.0, days, drift=0.0001, rising_after=100)
    eth = _series(50.0, days, drift=0.0001, rising_after=100)
    _write_crypto_days(data_dir, "BTC", btc)
    _write_crypto_days(data_dir, "ETH", eth)
    if pl.BOOK_C_ENABLED:
        for sym in pl.EQUITY_UNIVERSE:
            stock_series = _series(100.0, days, drift=0.0001, rising_after=100)
            _write_stock_days(data_dir, sym, stock_series)
    root = pl.loop_root(data_dir)
    return data_dir, root, _now_of(date.fromisoformat(btc[-1][0]) + timedelta(days=1))


def _inject_bar(root: Path, symbol: str, open_ts: datetime, open_price: float) -> None:
    path = pl.tape_dir(root) / f"{symbol}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "open_time": open_ts.isoformat(),
                    "open": open_price,
                    "close": open_price * 1.005,
                }
            )
            + "\n"
        )


# --- G1-G9 synthetic guard violations -----------------------------------------


def _g(**overrides) -> GuardInput:
    base: dict[str, object] = dict(
        asset_class="crypto",
        side="buy",
        now=datetime(2026, 10, 10, 16, 0, tzinfo=UTC),
        last_fill_ts=None,
        last_fill_side=None,
        position_opened_ts=None,
        fills_today=0,
        decision_bar_seconds=86400,
    )
    base.update(overrides)
    return GuardInput(**base)  # type: ignore[arg-type]


def test_G1_G9_guard() -> None:
    # G1: 30-min holding then sell -> min_hold
    allowed, reason = check_trade(
        _g(side="sell", position_opened_ts=_g().now - timedelta(minutes=30))
    )
    assert not allowed and reason == "min_hold"
    # G2: 3rd fill today -> max_fills_per_day
    allowed, reason = check_trade(_g(fills_today=2))
    assert not allowed and reason == "max_fills_per_day"
    # G3: 59-min interval rejects; exactly 60 min passes (boundary)
    allowed, reason = check_trade(
        _g(last_fill_ts=_g().now - timedelta(minutes=59), last_fill_side="buy")
    )
    assert not allowed and reason == "min_fill_interval"
    allowed, _ = check_trade(
        _g(last_fill_ts=_g().now - timedelta(minutes=60), last_fill_side="buy")
    )
    assert allowed
    # G4: buy then sell on the same UTC day -> same_day_reversal
    opened = _g().now - timedelta(hours=5)
    allowed, reason = check_trade(
        _g(side="sell", position_opened_ts=opened, last_fill_ts=opened, last_fill_side="buy")
    )
    assert not allowed and reason == "same_day_reversal"
    # G5: 15-min decision bar -> bar_too_fine
    allowed, reason = check_trade(_g(decision_bar_seconds=900))
    assert not allowed and reason == "bar_too_fine"
    # G6: FX holding 23h -> min_hold (24h floor)
    allowed, reason = check_trade(
        _g(asset_class="fx", side="sell", position_opened_ts=_g().now - timedelta(hours=23))
    )
    assert not allowed and reason == "min_hold"
    allowed, _ = check_trade(
        _g(asset_class="fx", side="sell", position_opened_ts=_g().now - timedelta(hours=25))
    )
    assert allowed
    # G7: daily normal case -> allowed
    allowed, reason = check_trade(
        _g(last_fill_ts=_g().now - timedelta(days=1), last_fill_side="buy")
    )
    assert allowed and reason == ""
    # G9: purity — repeated calls give identical results (no clock/file reads)
    g = _g()
    first = check_trade(g)
    for _ in range(1000):
        assert check_trade(g) == first


# --- v1.1 behavior tests -------------------------------------------------------


def test_decide_fill_idempotency_and_mtm(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    expected_decisions = 8 if pl.BOOK_C_ENABLED else 4
    assert pl.run_daily_decide(root, now) == expected_decisions
    decisions = [row for row in pl._load_journal(root) if row.get("event") == "decision"]
    assert len(decisions) == expected_decisions
    # D4 idempotent: a second identical decide rewrites no decisions (returns
    # 0) but still writes the D3 daily mtm rows (2 per run)
    assert pl.run_daily_decide(root, now) == 0
    # D5: recorded_at equals the injected wall clock, taken inside decide
    assert all(row["recorded_at"] == now.isoformat() for row in decisions)
    # mtm rows exist for both books (D3) — B2: one pair only; the second run's
    # duplicate mtm for the same date is refused, not re-written
    mtms = [row for row in pl._load_journal(root) if row.get("event") == "mtm"]
    assert len(mtms) == len(pl.BOOKS_ENABLED)
    # fill with no tape bars yet -> 0 resolved
    assert pl.resolve_fills(root, now) == 0
    # inject 1h bars AFTER recorded_at; both books fill 2 assets
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    resolved = pl.resolve_fills(root, now + timedelta(hours=2))
    assert resolved == 4
    fills = [row for row in pl._load_journal(root) if row.get("event") == "fill"]
    assert all(row["side"] == "buy" for row in fills)
    # D2: traded notional = |target-held| * equity = 0.5 * 1.0
    assert all(abs(row["traded_notional"] - 0.5) < 1e-9 for row in fills)
    # D4: filled rows are never re-filled
    assert pl.resolve_fills(root, now + timedelta(hours=3)) == 0
    # D7: the benchmark's day-2 rows are hold-only
    later = now + timedelta(days=1)
    pl.run_daily_decide(root, later)
    day2 = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row["book"] == pl.BENCH_CRYPTO_BOOK
        and row["decision_date"] == later.date().isoformat()
    ]
    assert len(day2) == 2 and all(row["side"] == "hold" for row in day2)
    # book A's day-2 rows rebalance the (BTC-driven) drift: BTC up, ETH down
    day2_a = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row["book"] == pl.S01_BOOK
        and row["decision_date"] == later.date().isoformat()
    ]
    assert len(day2_a) == 2 and all(row["side"] != "hold" for row in day2_a)
    # they fill as sell (overweight BTC) and buy (underweight ETH) with the
    # DELTA notional (D2) — resolve_fills completes the pending rows
    _inject_bar(root, "BTC", later + timedelta(hours=1), 102.0)
    _inject_bar(root, "ETH", later + timedelta(hours=1), 61.0)
    resolved2 = pl.resolve_fills(root, later + timedelta(hours=2))
    assert resolved2 == 4  # book A rebalance 2 + benchmark hold-only fills 2
    rebalance_fills = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "fill"
        and str(row.get("decision_id", "")).endswith(f":{later.date().isoformat()}")
        and row.get("book") == pl.S01_BOOK
    ]
    assert {row["side"] for row in rebalance_fills} == {"buy", "sell"}
    # the traded notional is the DELTA (far less than the full position sale)
    assert all(0 < row["traded_notional"] < 0.5 for row in rebalance_fills)


def test_commit_before_fill_discards_early_bars(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    # a bar opening BEFORE recorded_at must never serve as a fill (C2)
    _inject_bar(root, "BTC", now - timedelta(hours=1), 99.0)
    _inject_bar(root, "ETH", now - timedelta(hours=1), 49.0)
    assert pl.resolve_fills(root, now + timedelta(hours=1)) == 0
    # after a strictly-later bar lands, the fill uses it
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    assert pl.resolve_fills(root, now + timedelta(hours=2)) == 4


def test_chain_error_on_corrupt_journal(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    path = pl.journal_path(root)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    lines.insert(0, "{corrupt json")  # forge a broken first line
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        pl._load_journal(root)
    except pl.ChainError:
        return
    raise AssertionError("ChainError expected on a corrupt journal line")


def test_mark_signed_shortfall(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    pl.resolve_fills(root, now + timedelta(hours=2))
    # the execution day's daily bar (open 100.5) lands
    for symbol, open_price in (("BTC", 100.5), ("ETH", 59.5)):
        path = data_dir / "crypto-ohlcv" / symbol / "ohlcv.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "symbol": symbol,
                        "interval": "1d",
                        "currency": "USDT",
                        "provider": "binance",
                        "source_url": "u",
                        "license_class": "personal_only",
                        "retrieved_at": "2026-01-01T00:00:00+00:00",
                        "as_of": now.date().isoformat() + "T00:00:00Z",
                        "open": open_price,
                        "close": open_price * 1.01,
                    }
                )
                + "\n"
            )
    marked = pl.mark_up(root, now + timedelta(hours=25))
    assert marked == 2  # strategy book only (benchmark marks are excluded, D7)
    marks = [row for row in pl._load_journal(root) if row.get("event") == "mark"]
    assert all(row["shortfall_bps"] > 0 for row in marks)  # paper > bt open, buy
    btc_mark = next(row for row in marks if row["asset"] == "BTC")
    assert abs(btc_mark["shortfall_bps"] - (101.0 / 100.5 - 1.0) * 10_000) < 1e-9
    assert all(row["recompute_match"] for row in marks)


def test_sigma_prior_5sigma_halts(tmp_path: Path, monkeypatch) -> None:
    """D1/H3: the prior-window sigma fires on a surprise day (bugged w. does
    not). The jump is injected into the day BEFORE the decision."""

    _stub_networks(monkeypatch)
    data_dir = tmp_path / "data"
    days = _series(100.0, 260, drift=0.0001, rising_after=100)
    # rewrite the last bar's close as a +6% jump
    as_of, opened, _close = days[-1]
    days[-1] = (as_of, opened, opened * 1.06)
    _write_crypto_days(data_dir, "BTC", days)
    _write_crypto_days(data_dir, "ETH", _series(50.0, 260, drift=0.0001, rising_after=100))
    root = pl.loop_root(data_dir)
    now = _now_of(date.fromisoformat(days[-1][0]) + timedelta(days=1))
    pl.run_daily_decide(root, now)
    btc_rows = [row for row in pl._load_journal(root) if row.get("asset") == "BTC"]
    assert any(row.get("halt_flag") or row.get("sigma_5_move") for row in btc_rows) or any(
        "sigma_5_move" in str(row.get("halt_reason", "")) for row in btc_rows
    ), "the +6% surprise day must fire sigma_5_move (D1 fixed)"


def test_zero_order_path_is_free() -> None:
    source = Path(pl.__file__).read_text(encoding="utf-8")
    forbidden = (
        "broker_execution",
        "operator_bridge",
        "mt5",
        "rpyc",
        "/api/v3/order",
        "order_inquiry",
        "newOrder",
        "placeOrder",
    )
    for token in forbidden:
        assert token not in source, f"paperloop must not reference {token}"


def test_engine_targets_reconcile(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    engine_rows = {symbol: pl._daily_map(root, symbol) for symbol in pl.CRYPTO_UNIVERSE}
    target = _target_weights(_s01_strategy(), engine_rows, now.date())
    assert target == {"BTC": 0.5, "ETH": 0.5}


# --- v1.3 (t_2202c100): -20% DD is a warning, not a stop -----------------------


def _noisy_then_slide_series(
    n_rise: int = 260, n_slide: int = 10, start: float = 100.0
) -> list[tuple[str, float, float]]:
    """Rising alternating-noise phase, then a controlled ~-2.5%/day slide.

    The alternating spread keeps sigma21 near 1% so no single slide day
    (max -3.2%) trips the 5-sigma trigger — only the DD line is exercised.
    """

    out: list[tuple[str, float, float]] = []
    value = start
    for i in range(n_rise + n_slide):
        if i < n_rise:
            drift = 0.008 if i % 2 == 0 else -0.006
        else:
            drift = -0.018 if i % 2 == 0 else -0.032
        value *= 1 + drift
        day = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        out.append((day, value / (1 + drift / 2), value))
    return out


def test_dd20_is_warning_not_stop(tmp_path: Path, monkeypatch) -> None:
    _stub_networks(monkeypatch)
    data_dir = tmp_path / "data"
    days = _noisy_then_slide_series()
    rising = days[:260]
    _write_crypto_days(data_dir, "BTC", rising)
    _write_crypto_days(data_dir, "ETH", [(d, o, c / 2) for d, o, c in rising])
    root = pl.loop_root(data_dir)
    buy_day = _now_of(date.fromisoformat(rising[-1][0]) + timedelta(days=1))
    # buy at the top of the rising phase
    pl.run_daily_decide(root, buy_day)
    _inject_bar(root, "BTC", buy_day + timedelta(hours=1), rising[-1][2] * 1.001)
    _inject_bar(root, "ETH", buy_day + timedelta(hours=1), rising[-1][2] / 2 * 1.001)
    assert pl.resolve_fills(root, buy_day + timedelta(hours=2)) == 4
    # append the slide bars, then mark to market: equity falls through -20%
    _write_crypto_days(data_dir, "BTC", days[260:])
    _write_crypto_days(data_dir, "ETH", [(d, o, c / 2) for d, o, c in days[260:]])
    slide_day = _now_of(date.fromisoformat(days[-1][0]) + timedelta(days=1))
    pl.run_daily_mtm(root, slide_day)
    mtms = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm" and row["mtm_date"] == slide_day.date().isoformat()
    ]
    crypto_mtms = [
        row for row in mtms if row.get("book") in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
    ]
    assert crypto_mtms and all(row["drawdown"] <= pl.HALT_DRAWDOWN for row in crypto_mtms)
    assert all(row["drawdown_warning"] is True for row in crypto_mtms)
    assert all(row["stopped"] is False for row in mtms)  # v1.3: no stop
    assert pl.rebuild_state(root, pl.S01_BOOK)["stopped"] is False
    # the next decide is NOT halted by the drawdown: halt_check warns instead
    pl.run_daily_decide(root, slide_day)
    halt_checks = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("check_date") == slide_day.date().isoformat()
        and row.get("book") in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
    ]
    assert len(halt_checks) == 2
    for row in halt_checks:
        assert row["drawdown_warning"] is True
        assert row["halted_after"] is False
        assert row["action"] == "warn"
    day_decisions = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row.get("decision_date") == slide_day.date().isoformat()
        and row.get("book") in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
    ]
    assert len(day_decisions) == 4
    assert all(row["halt_flag"] is False for row in day_decisions)
    # the action mapping itself: a frozen stopped state stays "stop" on the
    # record, but a live v1.3 -20% DD is "warn"
    assert pl._halt_action(False, False, -0.25, {"stopped": True}, []) == "stop"
    assert pl._halt_action(False, False, -0.25, {"stopped": False}, []) == "warn"


# --- v1.3 (t_2202c100): C6 superseded pendings ---------------------------------


def test_C6_superseded_pending(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    # day 1: decisions are written; with no fillable bar they stay pending
    pl.run_daily_decide(root, now)
    assert pl.resolve_fills(root, now) == 0
    day1_ids = {
        f"{book}:{asset}:{now.date().isoformat()}"
        for book in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
        for asset in pl.CRYPTO_UNIVERSE
    }
    if pl.BOOK_C_ENABLED:
        day1_ids |= {
            f"{pl.BOOK_C_BOOK}:{asset}:{now.date().isoformat()}"
            for asset in pl.EQUITY_UNIVERSE
        }
    journal = pl._load_journal(root)
    assert all(
        row["fill_status"] == "pending"
        for row in journal
        if row.get("event") == "decision" and row["decision_id"] in day1_ids
    )
    # day 2: NEW decisions for the same (book, asset) supersede the old ones
    later = now + timedelta(days=1)
    pl.run_daily_decide(root, later)
    _inject_bar(root, "BTC", later + timedelta(hours=1), 102.0)
    _inject_bar(root, "ETH", later + timedelta(hours=1), 61.0)
    resolved = pl.resolve_fills(root, later + timedelta(hours=2))
    assert resolved == 4  # only the NEW decisions fill
    journal = pl._load_journal(root)
    superseded = [row for row in journal if row.get("reject_reason") == "superseded"]
    assert {row["decision_id"] for row in superseded} == day1_ids
    assert all(row["reject_class"] == "superseded" for row in superseded)
    assert all(
        str(row.get("decision_id", "")).endswith(later.date().isoformat())
        for row in journal
        if row.get("event") == "fill"
    )
    # the decision rows themselves are never rewritten (D4): still "pending"
    assert all(
        row["fill_status"] == "pending"
        for row in journal
        if row.get("event") == "decision" and row["decision_id"] in day1_ids
    )
    # B1/L5: exactly one terminal event per decision_id across the journal
    terminal: dict[str, int] = {}
    for row in journal:
        if row.get("event") in ("fill", "fill_rejected") and row.get("decision_id"):
            terminal[str(row["decision_id"])] = terminal.get(str(row["decision_id"]), 0) + 1
    assert set(terminal.values()) == {1}
    # a second pass resolves nothing new
    assert pl.resolve_fills(root, later + timedelta(hours=3)) == 0


# --- v1.3 (t_2202c100): R3 counts revisions, R2 excludes them ------------------


def test_R3_revision_excluded_from_R2(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    pl.resolve_fills(root, now + timedelta(hours=2))
    start = now.date() - timedelta(days=1)
    end = now.date()
    clean = pv.verify(data_dir, start, end)
    assert clean["exit_code"] == 0, json.dumps(clean.get("failed_checks"))
    assert clean["R1_match"] == clean["R1_total"] > 0
    assert clean["R3_revised"] == 0 and clean["R2_excluded_revised"] == 0
    # simulate a store revision: halve one close inside the frozen 201-day
    # window (rows[10] would be sliced away and change nothing)
    path = data_dir / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    rows[100]["close"] = rows[100]["close"] * 0.5
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    revised = pv.verify(data_dir, start, end)
    # R1 (frozen FILE hash) is immune to store revisions
    assert revised["R1_match"] == revised["R1_total"]
    # R3 counts the revised decision_ids; R2 excludes exactly those
    assert revised["R3_revised"] >= 1
    assert revised["R3_revised"] == len(revised["R3_revised_ids"])
    assert revised["R2_excluded_revised"] == revised["R3_revised"]
    assert revised["R2_total"] + revised["R2_excluded_revised"] == clean["R2_total"]
    assert revised["R2_mismatches"] == []
    # a revision is counted, not failed: the verifier still exits 0
    assert revised["exit_code"] == 0, json.dumps(revised.get("failed_checks"))


# --- v1.3 (t_2202c100): bench-DD hook disabled; 26w cost gate replaced ---------


def test_bench_dd_stop_hook_and_milestone26() -> None:
    # the hook exists but is disabled until the master freezes k
    assert pl.BENCH_DD_STOP["enabled"] is False
    assert pl.BENCH_DD_STOP["k"] is None
    assert pl.bench_dd_stop_fired(-0.30, -0.15) is False  # disabled: k undecided
    assert pl.bench_dd_stop_fired(-0.30, -0.15, k=2.0) is True
    assert pl.bench_dd_stop_fired(-0.25, -0.15, k=2.0) is False  # not k-times deeper
    assert pl.bench_dd_stop_fired(-0.40, -0.05, k=2.0) is False  # bench DD < 10%
    assert pl.bench_dd_stop_fired(0.0, -0.20, k=1.0) is False  # book not under water
    # the 26w milestone cost gate is the frozen shortfall criterion
    assert "observed_cost_bps_max" not in pl.MILESTONE_RULES[26]
    assert pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"] == 15.0
    # zero-variance shortfalls: the bound equals the mean
    bound = pl.shortfall_bps_mean_upper95([10.0] * 100)
    assert bound is not None and abs(bound - 10.0) < 1e-9
    # spread pushes the bound above the mean (n=2: sd/√n = sd/√2)
    b2 = pl.shortfall_bps_mean_upper95([0.0, 20.0])
    assert b2 is not None and b2 > 10.0
    # n < 2 is "insufficient", not a pass
    assert pl.shortfall_bps_mean_upper95([]) is None
    assert pl.shortfall_bps_mean_upper95([5.0]) is None
