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

import pytest

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


def _prepare(tmp_path: Path, monkeypatch, *, days: int = 263):
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
    # v1.4 (t_dddae39e): count from the enabled book set, so a book that is
    # enabled later cannot silently drift this expectation again.
    expected_decisions = sum(
        len(pl.CRYPTO_UNIVERSE) if book in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK) else len(pl.EQUITY_UNIVERSE)
        for book in pl.BOOKS_ENABLED
    )
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
    day1_crypto_ids = {
        f"{book}:{asset}:{now.date().isoformat()}"
        for book in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
        for asset in pl.CRYPTO_UNIVERSE
    }
    day1_ids = set(day1_crypto_ids)
    if pl.BOOK_C_ENABLED:
        # v1.4 (t_dddae39e): the equity EW benchmark rides the same Monday
        # cadence, so its day-1 decisions also stay pending on day 2.
        day1_ids |= {
            f"{book}:{asset}:{now.date().isoformat()}"
            for book in (pl.BOOK_C_BOOK, pl.BENCH_EQUITY_BOOK)
            for asset in pl.EQUITY_UNIVERSE
        }
    journal = pl._load_journal(root)
    assert all(
        row["fill_status"] == "pending"
        for row in journal
        if row.get("event") == "decision" and row["decision_id"] in day1_ids
    )
    # day 2: NEW decisions for the same (book, asset) supersede the old ones.
    # Crypto decides daily, so Day 1 crypto decisions are superseded on Day 2.
    # Book C is weekly (Mondays), so Day 1 Book C decisions stay active.
    later = now + timedelta(days=1)
    pl.run_daily_decide(root, later)
    _inject_bar(root, "BTC", later + timedelta(hours=1), 102.0)
    _inject_bar(root, "ETH", later + timedelta(hours=1), 61.0)
    resolved = pl.resolve_fills(root, later + timedelta(hours=2))
    assert resolved == 4  # only the NEW decisions fill
    journal = pl._load_journal(root)
    superseded = [row for row in journal if row.get("reject_reason") == "superseded"]
    assert {row["decision_id"] for row in superseded} == day1_crypto_ids
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


# --- t_feb3aa55 Round 2 regression tests (D1 & D2) ---------------------------


def test_stock_fetch_pipeline_advances_bars(tmp_path: Path, monkeypatch) -> None:
    """Regression test for D1 (t_feb3aa55): daily pipeline fetches equity data
    and stock store bars advance, preventing stale_hours > 72 HALT on Day 2."""
    from yowayowa.config import get_settings
    from yowayowa.providers.alpaca import AlpacaMarketDataProvider
    from yowayowa.stock_acquisition import StockOhlcvStore, fetch_stock_ohlcv
    import httpx

    # Verify daily.sh contains stock-fetch for EQUITY_UNIVERSE
    daily_sh_path = Path(__file__).resolve().parent.parent / "ops" / "demo-loop" / "daily.sh"
    daily_sh_content = daily_sh_path.read_text(encoding="utf-8")
    assert "stock-fetch" in daily_sh_content
    for sym in pl.EQUITY_UNIVERSE:
        assert sym in daily_sh_content

    # Verify stock-fetch persists new bars and store advances
    store = StockOhlcvStore(tmp_path / "stock-ohlcv")
    symbols = list(pl.EQUITY_UNIVERSE)
    canned_bars = {
        sym: [
            {"t": "2026-10-16T04:00:00Z", "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0, "v": 1000, "n": 10, "vw": 100.5},
            {"t": "2026-10-19T04:00:00Z", "o": 101.0, "h": 103.0, "l": 100.0, "c": 102.0, "v": 1000, "n": 10, "vw": 101.5},
        ]
        for sym in symbols
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": canned_bars})

    provider = AlpacaMarketDataProvider(get_settings())
    provider.client._transport = httpx.MockTransport(handler)
    summary = fetch_stock_ohlcv({"alpaca": provider}, store, symbols, days=5)
    for sym in symbols:
        persisted = summary[sym]["alpaca"]["persisted"]
        assert persisted == 2
        rows = store.read(sym, provider="alpaca")
        assert len(rows) == 2
        assert rows[0]["as_of"] == "2026-10-19T04:00:00Z"


def test_book_c_weekly_cadence_gate(tmp_path: Path, monkeypatch) -> None:
    """Regression test for D2 (t_feb3aa55): Book C decide is gated to Monday.
    On non-rebalance days (Tue-Sun), Book C does not generate trade decisions
    and only MTM runs daily, preventing 15bps daily drift turnover."""
    _data_dir, root, mon = _prepare(tmp_path, monkeypatch, days=263)
    assert mon.weekday() == 0  # Monday
    # Day 1 (Monday): Book C executes weekly decide
    # v1.4 (t_dddae39e): the equity EW benchmark book decides on the same
    # Monday cadence, so the expected count comes from the enabled book set.
    expected = sum(
        len(pl.CRYPTO_UNIVERSE)
        if book in (pl.S01_BOOK, pl.BENCH_CRYPTO_BOOK)
        else len(pl.EQUITY_UNIVERSE)
        for book in pl.BOOKS_ENABLED
    )
    assert pl.run_daily_decide(root, mon) == expected  # 4 crypto + 4 stock + 4 EW
    mon_decisions = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision" and row.get("book") == pl.BOOK_C_BOOK
    ]
    assert len(mon_decisions) == 4
    assert all(row["side"] == "buy" for row in mon_decisions)
    assert all(row["fill_status"] == "pending" for row in mon_decisions)
    # the benchmark book buys the same 1/N target on the same day
    mon_bench = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision" and row.get("book") == pl.BENCH_EQUITY_BOOK
    ]
    assert len(mon_bench) == 4
    assert all(abs(float(row["target_weight"]) - 0.25) < 1e-9 for row in mon_bench)

    # Day 2 (Tuesday): Non-rebalance day
    tue = mon + timedelta(days=1)
    assert tue.weekday() == 1  # Tuesday
    pl.run_daily_decide(root, tue)
    # Crypto may decide, but Book C MUST NOT generate decisions on Tuesday
    tue_c_decisions = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row.get("book") == pl.BOOK_C_BOOK
        and row.get("decision_date") == tue.date().isoformat()
    ]
    assert len(tue_c_decisions) == 0, "Book C must not generate decisions on non-rebalance days"
    # v1.4: the equity benchmark book is gated identically (same cadence).
    tue_bench = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row.get("book") == pl.BENCH_EQUITY_BOOK
        and row.get("decision_date") == tue.date().isoformat()
    ]
    assert len(tue_bench) == 0, "EQUITY_EW must ride the same Monday cadence"

    # MTM is maintained daily for Book C
    tue_c_mtm = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm"
        and row.get("book") == pl.BOOK_C_BOOK
        and row.get("mtm_date") == tue.date().isoformat()
    ]
    assert len(tue_c_mtm) == 1, "Book C must have daily MTM row on Tuesday"


# --- per-book comparison line against the book's own benchmark (t_dddae39e) ---


def _append_row(root: Path, **row: object) -> None:
    """Append one hand-built journal row (chain/seq handled by _append_row)."""

    payload: dict[str, object] = {
        "recorded_at": datetime(2026, 10, 27, 0, 20, tzinfo=UTC).isoformat()
    }
    payload.update(row)
    pl._append_row(root, payload)  # type: ignore[arg-type]


def _mtm(root: Path, book: str, day: str, equity: float, drawdown: float) -> None:
    _append_row(
        root,
        event="mtm",
        book=book,
        mtm_date=day,
        equity=equity,
        cash=0.0,
        drawdown=drawdown,
        peak_equity=1.0,
    )


def _fill(root: Path, book: str, day: str) -> None:
    _append_row(
        root,
        event="fill",
        book=book,
        asset="SPY",
        side="buy",
        paper_fill_price=100.0,
        paper_fill_bar_open_ts=f"{day}T13:30:00+00:00",
        units_after=0.25,
        cash_after=0.0,
        traded_notional=0.25,
    )


def _comparison_books(data_dir: Path) -> tuple[dict, dict]:
    """weekly_summary on a hand-built journal, without the Q1-Q7 panel."""

    out = pl.weekly_summary(data_dir, date(2026, 10, 27))
    return out["books"][pl.S01_BOOK], out["books"][pl.BOOK_C_BOOK]


def test_comparison_line_is_null_while_benchmark_has_no_fill(tmp_path: Path, monkeypatch) -> None:
    """A benchmark sitting in cash must never be reported as a 0% comparison.

    Review round 2 (t_dddae39e) item 1: before EQUITY_EW fills, Book C's
    vs_benchmark_pp_cum / benchmark_cum_pnl must be None (the weekly_summary
    row is append-only, so a wrong 0 would be永久に残る), with a reason field.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    for day in ("2026-10-21", "2026-10-22", "2026-10-23"):
        _mtm(root, pl.BOOK_C_BOOK, day, 1.01, 0.0)
        _mtm(root, pl.BENCH_EQUITY_BOOK, day, 1.0, 0.0)  # cash, never filled
    book_a, book_c = _comparison_books(data_dir)
    assert book_c["fills"] == 0
    assert book_c["benchmark_book"] == pl.BENCH_EQUITY_BOOK
    for key in (
        "cum_pnl",
        "max_drawdown",
        "benchmark_cum_pnl",
        "benchmark_max_drawdown",
        "vs_benchmark_pp_cum",
        "dd_vs_benchmark_ratio",
    ):
        assert book_c[key] is None, f"{key} must be None while the benchmark is in cash"
    assert book_c["comparison_start"] is None
    assert book_c["comparison_reason"] == "benchmark_has_no_fill"
    # the crypto book has no fills either -> same honest None
    assert book_a["benchmark_book"] == pl.BENCH_CRYPTO_BOOK
    assert book_a["vs_benchmark_pp_cum"] is None
    assert book_a["comparison_reason"] == "benchmark_has_no_fill"


def test_comparison_line_starts_at_the_shared_baseline_after_benchmark_fill(
    tmp_path: Path, monkeypatch
) -> None:
    """Once the benchmark fills, both books are measured from one baseline day.

    Review round 2 (t_dddae39e) item 2: Book C gained 10% BEFORE EQUITY_EW was
    invested. Subtracting inception equity would book that gain as a
    comparison win it never traded for, so the line must start at the first
    mtm date both books carry on/after the benchmark's first fill.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    # pre-fill: C is up 10%, the benchmark is still 100% cash (no fill yet)
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-23", 1.10, 0.0)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-23", 1.0, 0.0)
    # 2026-10-26: the benchmark buys 1/N; both books carry a mark that day
    _fill(root, pl.BENCH_EQUITY_BOOK, "2026-10-26")
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-26", 1.10, 0.0)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-26", 1.0, 0.0)
    # 2026-10-27: C -5% from its 10/26 mark, benchmark -1%
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-27", 1.045, -0.05)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-27", 0.99, -0.01)

    _book_a, book_c = _comparison_books(data_dir)
    assert book_c["comparison_start"] == "2026-10-26"
    assert book_c["comparison_reason"] is None
    # measured from the shared baseline, not from inception (that would be +4.5%)
    assert abs(book_c["cum_pnl"] - (-0.05)) < 1e-9
    assert abs(book_c["benchmark_cum_pnl"] - (-0.01)) < 1e-9
    assert abs(book_c["vs_benchmark_pp_cum"] - (-4.0)) < 1e-9
    assert abs(book_c["max_drawdown"] - (-0.05)) < 1e-9
    assert abs(book_c["benchmark_max_drawdown"] - (-0.01)) < 1e-9
    assert abs(book_c["dd_vs_benchmark_ratio"] - 5.0) < 1e-9
    assert book_c["dd_ratio_reason"] is None
    # the pre-fill mark (10/23) is outside the comparison window
    window_dd = pl._max_drawdown_since(pl._mtm_marks(root, pl.BOOK_C_BOOK), date(2026, 10, 26))
    assert window_dd is not None and abs(window_dd - (-0.05)) < 1e-12


def test_comparison_max_drawdown_starts_its_peak_at_the_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    """A pre-baseline high must not leak a one-sided drawdown into the window.

    Review round 3 (t_dddae39e): the journal's ``drawdown`` column is measured
    from the peak since inception, so filtering those rows by ``mtm_date``
    still carried the pre-baseline high into the comparison window, while the
    benchmark started from its own baseline value (real data: Book C peaked at
    1.00986 on 10/22 and was -0.348% on 10/23). Here C tops at 1.10 BEFORE the
    shared baseline of 1.05, so the window drawdown must be 1.00/1.05-1, not
    the journal's 1.00/1.10-1.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    # pre-baseline: C at 1.10 (its high-water mark), benchmark still in cash
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-23", 1.10, 0.0)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-23", 1.0, 0.0)
    # baseline day: the benchmark fills, both books carry a mark; C has given
    # back 5% from its pre-baseline high (journal drawdown = -0.04545...)
    _fill(root, pl.BENCH_EQUITY_BOOK, "2026-10-26")
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-26", 1.05, 1.05 / 1.10 - 1.0)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-26", 1.0, 0.0)
    # 10/27: C falls to 1.00 (journal drawdown = 1.00/1.10-1 = -9.09%),
    # the benchmark stays flat at its baseline
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-27", 1.00, 1.00 / 1.10 - 1.0)
    _mtm(root, pl.BENCH_EQUITY_BOOK, "2026-10-27", 1.0, 0.0)

    book_a, book_c = _comparison_books(data_dir)
    assert book_c["comparison_start"] == "2026-10-26"
    # measured from the baseline (1.05), not from the 1.10 high-water mark
    assert abs(book_c["cum_pnl"] - (1.00 / 1.05 - 1.0)) < 1e-12
    assert abs(book_c["max_drawdown"] - (1.00 / 1.05 - 1.0)) < 1e-12
    # the journal's inception-based drawdown is NOT what the comparison uses
    assert abs(book_c["max_drawdown"] - (1.00 / 1.10 - 1.0)) > 1e-3
    assert abs(book_c["benchmark_cum_pnl"]) < 1e-12
    assert abs(book_c["benchmark_max_drawdown"]) < 1e-12
    assert abs(book_c["vs_benchmark_pp_cum"] - (1.00 / 1.05 - 1.0) * 100.0) < 1e-9
    # a flat benchmark gives nothing to divide by -> no fabricated ratio
    assert book_c["dd_vs_benchmark_ratio"] is None
    assert book_c["dd_ratio_reason"] == "benchmark_drawdown_not_negative"
    # the journal's own drawdown column keeps its inception peak (unchanged)
    last = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm" and row.get("book") == pl.BOOK_C_BOOK
    ][-1]
    assert abs(float(last["drawdown"]) - (1.00 / 1.10 - 1.0)) < 1e-12
    assert book_a["benchmark_book"] == pl.BENCH_CRYPTO_BOOK


# --- milestone judgment wiring (t_d33aef07) -----------------------------------


def _milestone_last_close(data_dir: Path, symbol: str) -> float:
    """Last close of the seeded daily series (to continue its drift family)."""

    path = data_dir / "crypto-ohlcv" / symbol / "ohlcv.jsonl"
    last = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    return float(last["close"])


def _milestone_drive_days(root: Path, data_dir: Path, first_day: date, days: int) -> None:
    """Drive the real decide->fill->mark path once a day via the CLI-equivalent
    entry points (run_id passed so the daily/fill run rows are written).

    The appended daily bars continue the seeded series' drift family (a flat
    continuation degenerates the 21-day sigma window and fires the 5-sigma
    HALT, which would suppress decisions). Crypto tape bars are injected at
    exactly the daily bar open, so every Book A fill marks with zero shortfall
    and a matching recompute. Book C gets Monday decisions but no equity tape:
    its fills never resolve, so its judgment comes out INSUFFICIENT — the
    missing-data path, on facts.
    """

    values = {symbol: _milestone_last_close(data_dir, symbol) for symbol in ("BTC", "ETH")}
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        opens: dict[str, float] = {}
        for symbol, value in values.items():
            opened = value * 1.0001  # _series tail drift: 0.0002/day
            values[symbol] = value * 1.0002
            _write_crypto_days(data_dir, symbol, [(day.isoformat(), opened, values[symbol])])
            opens[symbol] = opened
        now = _now_of(day, minute=20)
        run_id = f"test-daily-{day.isoformat()}"
        pl.run_daily_decide(root, now, run_id=run_id)
        _inject_bar(root, "BTC", now + timedelta(hours=1), opens["BTC"])
        _inject_bar(root, "ETH", now + timedelta(hours=1), opens["ETH"])
        pl.resolve_fills(root, now + timedelta(hours=2), run_id=f"test-fill-{day.isoformat()}")
        pl.mark_up(root, now + timedelta(hours=2))


def _milestone_start(root: Path, book: str) -> date:
    start = pl._milestone_start_date(pl._load_journal(root), book)
    assert start is not None
    return start


def test_milestone_judgment_fires_once_at_deadline(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()  # 2026-09-21, a Monday: Book C decides on day one
    _milestone_drive_days(root, _data_dir, first_day, 28)

    start = _milestone_start(root, pl.S01_BOOK)
    deadline = start + timedelta(days=28)
    assert deadline.isoformat() == (first_day + timedelta(days=28)).isoformat()

    journal_rows = len(pl._load_journal(root))
    summary_rows = len(pl._rows_from_jsonl(pl.summary_path(root)))

    # deadline - 1 day: no early judgment, ever
    out_early = pl.weekly_summary(_data_dir, deadline - timedelta(days=1))
    assert "milestone" not in out_early
    assert len(pl._rows_from_jsonl(pl.summary_path(root))) == summary_rows + 1

    # at the deadline: one judgment per strategy book, on the frozen gates
    out = pl.weekly_summary(_data_dir, deadline)
    assert len(pl._load_journal(root)) == journal_rows  # record-only: journal untouched
    assert set(out["milestone"]) == {pl.S01_BOOK, pl.BOOK_C_BOOK}
    a = out["milestone"][pl.S01_BOOK]
    assert a["weeks"] == 4
    assert a["start_date"] == first_day.isoformat()
    assert a["milestone_date"] == deadline.isoformat()
    assert a["judged_at_week_end"] == deadline.isoformat()
    assert a["verdict"] == "PASS"
    assert a["criteria"]["loop_success_rate"]["value"] == 1.0
    assert (
        a["criteria"]["loop_success_rate"]["threshold"]
        == pl.MILESTONE_RULES[4]["loop_success_rate"]
    )
    assert a["criteria"]["observed_cost_bps_mean"]["value"] == 0.0
    assert (
        a["criteria"]["observed_cost_bps_mean"]["threshold"]
        == pl.MILESTONE_RULES[4]["observed_cost_bps_max"]
    )
    assert a["criteria"]["recompute_match_rate"]["value"] == 1.0
    assert a["criteria"]["orders"]["value"] == 0
    # every day has run rows here: the fallback list is empty and present
    assert a["run_ok_dates_by_decision_fallback"] == []
    assert a["criteria"]["guard_violations"]["value"] == 0
    assert a["promotion"] == "none"

    # Book C never filled (no equity tape): missing facts -> INSUFFICIENT,
    # never back-filled; the global daily runs still pass its loop criterion.
    c = out["milestone"][pl.BOOK_C_BOOK]
    assert c["verdict"] == "INSUFFICIENT"
    assert set(c["insufficient_criteria"]) == {"observed_cost_bps_mean", "recompute_match_rate"}
    assert c["criteria"]["loop_success_rate"]["pass"] is True

    assert len(pl._rows_from_jsonl(pl.summary_path(root))) == summary_rows + 2

    # dedupe: the next weekly run writes no second judgment record
    out_again = pl.weekly_summary(_data_dir, deadline + timedelta(days=7))
    assert "milestone" not in out_again
    all_rows = pl._rows_from_jsonl(pl.summary_path(root))
    assert len(all_rows) == summary_rows + 3
    judged = [
        row["milestone"][pl.S01_BOOK]
        for row in all_rows
        if isinstance(row.get("milestone"), dict) and pl.S01_BOOK in row["milestone"]
    ]
    assert len(judged) == 1, "exactly one 4-week judgment record per book, ever"


def test_milestone_judgment_boundary_27_of_28_days(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()
    _milestone_drive_days(root, _data_dir, first_day, 27)  # design §4: 1 missing day allowed
    deadline = _milestone_start(root, pl.S01_BOOK) + timedelta(days=28)
    out = pl.weekly_summary(_data_dir, deadline)
    a = out["milestone"][pl.S01_BOOK]
    assert a["run_ok_dates"] == 27
    assert abs(a["criteria"]["loop_success_rate"]["value"] - 27 / 28) < 1e-12
    assert a["criteria"]["loop_success_rate"]["pass"] is True
    assert a["verdict"] == "PASS"


def test_milestone_judgment_fails_on_missing_run_days(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()
    _milestone_drive_days(root, _data_dir, first_day, 26)  # 2 missing days: below 95%
    deadline = _milestone_start(root, pl.S01_BOOK) + timedelta(days=28)
    out = pl.weekly_summary(_data_dir, deadline)
    a = out["milestone"][pl.S01_BOOK]
    assert a["verdict"] == "FAIL"
    assert a["failed_criteria"] == ["loop_success_rate"]
    assert abs(a["criteria"]["loop_success_rate"]["value"] - 26 / 28) < 1e-12
    # neither run rows nor decision rows on the missing days: fail-closed,
    # never back-filled with the fallback
    assert a["run_ok_dates_by_decision_fallback"] == []


def test_milestone_judgment_counts_decision_only_days_as_loop_run(
    tmp_path: Path, monkeypatch
) -> None:
    """Real-journal shape (S-01 10-10/10-11): the first two days have
    non-backfill decisions and fills but NO run rows — the run-row writer
    only came online later, while the loop itself ran from day one. Those
    days count via the decision fallback (26 ok-run days + 2 = 28/28 PASS),
    and the fallback dates are recorded explicitly in the output."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()
    values = {s: _milestone_last_close(_data_dir, s) for s in ("BTC", "ETH")}
    for offset in range(28):
        day = first_day + timedelta(days=offset)
        opens: dict[str, float] = {}
        for symbol, value in values.items():
            opened = value * 1.0001
            values[symbol] = value * 1.0002
            _write_crypto_days(_data_dir, symbol, [(day.isoformat(), opened, values[symbol])])
            opens[symbol] = opened
        stamp = _now_of(day, minute=20)
        # first two days: the loop runs end-to-end but writes no run rows
        # (run_id omitted) — exactly the real 10-10/10-11 recording gap
        run_id = None if offset < 2 else f"test-daily-{day.isoformat()}"
        pl.run_daily_decide(root, stamp, run_id=run_id)
        _inject_bar(root, "BTC", stamp + timedelta(hours=1), opens["BTC"])
        _inject_bar(root, "ETH", stamp + timedelta(hours=1), opens["ETH"])
        pl.resolve_fills(
            root,
            stamp + timedelta(hours=2),
            run_id=None if offset < 2 else f"test-fill-{day.isoformat()}",
        )
        pl.mark_up(root, stamp + timedelta(hours=2))
    deadline = _milestone_start(root, pl.S01_BOOK) + timedelta(days=28)
    out = pl.weekly_summary(_data_dir, deadline)
    a = out["milestone"][pl.S01_BOOK]
    assert a["verdict"] == "PASS"
    assert a["criteria"]["loop_success_rate"]["value"] == 1.0
    assert a["run_ok_dates"] == 28
    assert a["run_ok_dates_by_decision_fallback"] == [
        first_day.isoformat(),
        (first_day + timedelta(days=1)).isoformat(),
    ]


def test_milestone_judgment_never_falls_back_on_failed_run_days(
    tmp_path: Path, monkeypatch
) -> None:
    """A day that HAS run rows is judged on its daily-end ok alone: a failed
    run is never overridden by the decision fallback, even when the same day
    also carries decision evidence. That day stays missing (26 ok runs + 1
    fallback day = 27/28, within the §4 one-missing-day allowance)."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()
    values = {s: _milestone_last_close(_data_dir, s) for s in ("BTC", "ETH")}
    for offset in range(28):
        day = first_day + timedelta(days=offset)
        opens: dict[str, float] = {}
        for symbol, value in values.items():
            opened = value * 1.0001
            values[symbol] = value * 1.0002
            _write_crypto_days(_data_dir, symbol, [(day.isoformat(), opened, values[symbol])])
            opens[symbol] = opened
        stamp = _now_of(day, minute=20)
        if offset == 0:
            # day0: loop ran but no run rows written -> decision-fallback day
            run_id: str | None = None
        elif offset == 1:
            # day1: a FAILED daily run end row, plus decision evidence on the
            # same day (run_id omitted) — the fallback must not count this day
            pl._append_row(
                root,
                {
                    "event": "run",
                    "phase": "end",
                    "job": "daily",
                    "ok": False,
                    "error_class": "ValueError",
                    "run_id": "failed-daily",
                    "recorded_at": stamp.isoformat(),
                    "host": "test",
                },
            )
            run_id = None
        else:
            run_id = f"test-daily-{day.isoformat()}"
        pl.run_daily_decide(root, stamp, run_id=run_id)
        _inject_bar(root, "BTC", stamp + timedelta(hours=1), opens["BTC"])
        _inject_bar(root, "ETH", stamp + timedelta(hours=1), opens["ETH"])
        pl.resolve_fills(
            root,
            stamp + timedelta(hours=2),
            run_id=None if offset < 2 else f"test-fill-{day.isoformat()}",
        )
        pl.mark_up(root, stamp + timedelta(hours=2))
    deadline = _milestone_start(root, pl.S01_BOOK) + timedelta(days=28)
    out = pl.weekly_summary(_data_dir, deadline)
    a = out["milestone"][pl.S01_BOOK]
    assert a["run_ok_dates"] == 27
    assert abs(a["criteria"]["loop_success_rate"]["value"] - 27 / 28) < 1e-12
    assert a["run_ok_dates_by_decision_fallback"] == [first_day.isoformat()]
    assert a["verdict"] == "PASS"


def test_milestone_judgment_never_falls_back_on_crashed_run_days(
    tmp_path: Path, monkeypatch
) -> None:
    """A daily run that crashed after writing its start row (start row only,
    no end row) HAS a run row that day: the decision fallback must not count
    it, even when the same day also carries decision evidence — a start
    without an end is a crashed run (L4), never a success. Both crashed days
    stay missing (26 ok runs = 26/28, below 95%: FAIL with fallback empty)."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    first_day = now.date()
    values = {s: _milestone_last_close(_data_dir, s) for s in ("BTC", "ETH")}
    for offset in range(28):
        day = first_day + timedelta(days=offset)
        opens: dict[str, float] = {}
        for symbol, value in values.items():
            opened = value * 1.0001
            values[symbol] = value * 1.0002
            _write_crypto_days(_data_dir, symbol, [(day.isoformat(), opened, values[symbol])])
            opens[symbol] = opened
        stamp = _now_of(day, minute=20)
        if offset < 2:
            # days 0-1: crashed daily run — the start row is written, the
            # end row never arrives; decisions are still recorded that day
            # (run_id omitted), but the fallback must not count the day
            pl._append_row(
                root,
                {
                    "event": "run",
                    "phase": "start",
                    "job": "daily",
                    "run_id": f"crashed-daily-{day.isoformat()}",
                    "recorded_at": stamp.isoformat(),
                    "host": "test",
                },
            )
            run_id: str | None = None
        else:
            run_id = f"test-daily-{day.isoformat()}"
        pl.run_daily_decide(root, stamp, run_id=run_id)
        _inject_bar(root, "BTC", stamp + timedelta(hours=1), opens["BTC"])
        _inject_bar(root, "ETH", stamp + timedelta(hours=1), opens["ETH"])
        pl.resolve_fills(
            root,
            stamp + timedelta(hours=2),
            run_id=None if offset < 2 else f"test-fill-{day.isoformat()}",
        )
        pl.mark_up(root, stamp + timedelta(hours=2))
    deadline = _milestone_start(root, pl.S01_BOOK) + timedelta(days=28)
    out = pl.weekly_summary(_data_dir, deadline)
    a = out["milestone"][pl.S01_BOOK]
    assert a["run_ok_dates"] == 26
    assert abs(a["criteria"]["loop_success_rate"]["value"] - 26 / 28) < 1e-12
    # the crashed days carry decisions, yet are never back-filled
    assert a["run_ok_dates_by_decision_fallback"] == []
    assert a["verdict"] == "FAIL"
    assert a["failed_criteria"] == ["loop_success_rate"]


def test_milestone_guard_replay_counts_slipped_through_violations(
    tmp_path: Path, monkeypatch
) -> None:
    _data_dir, root, _now = _prepare(tmp_path, monkeypatch)
    day0 = _now.date()
    # Book A start + a clean 28-day run/decision/mark panel (zero shortfall)
    opens = {s: _milestone_last_close(_data_dir, s) * 1.0001 for s in ("BTC", "ETH")}
    for symbol, opened in opens.items():
        _write_crypto_days(_data_dir, symbol, [(day0.isoformat(), opened, opened * 1.0001)])
    first_now = _now_of(day0, minute=20)
    pl.run_daily_decide(root, first_now, run_id="seed-daily")
    _inject_bar(root, "BTC", first_now + timedelta(hours=1), opens["BTC"])
    _inject_bar(root, "ETH", first_now + timedelta(hours=1), opens["ETH"])
    pl.resolve_fills(root, first_now + timedelta(hours=2), run_id="seed-fill")
    start = _milestone_start(root, pl.S01_BOOK)
    assert start == day0
    for offset in range(28):
        stamp = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(
            days=offset, minutes=30
        )
        pl._append_row(
            root,
            {
                "event": "run",
                "phase": "end",
                "job": "daily",
                "ok": True,
                "run_id": f"mk-{offset}",
                "recorded_at": stamp.isoformat(),
                "host": "test",
            },
        )
        for asset, shortfall in (("BTC", 5.0), ("ETH", -5.0)):
            pl._append_row(
                root,
                {
                    "event": "mark",
                    "book": pl.S01_BOOK,
                    "asset": asset,
                    "decision_id": f"{pl.S01_BOOK}:{asset}:{start.isoformat()}",
                    "shortfall_bps": shortfall,
                    "recompute_match": True,
                    "recorded_at": stamp.isoformat(),
                },
            )
    # a slipped-through violation: a second BTC fill 30 minutes after the
    # first accepted one — check_trade would have rejected (min_fill_interval)
    fills = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "fill"
        and row.get("book") == pl.S01_BOOK
        and row.get("asset") == "BTC"
    ]
    assert fills, "seeded journal must contain a BTC fill"
    first_fill = fills[0]
    violation_fill = dict(first_fill)
    violation_fill["decision_id"] = f"{pl.S01_BOOK}:BTC:viol"
    violation_fill["recorded_at"] = (
        pl._parse_ts(first_fill["recorded_at"]) + timedelta(minutes=30)
    ).isoformat()
    stripped = {
        k: v
        for k, v in violation_fill.items()
        if k not in ("seq", "prev_hash", "row_hash", "schema", "code_rev")
    }
    pl._append_row(root, stripped)

    rows = pl._load_journal(root)
    replay = pl._milestone_guard_replay(rows, pl.S01_BOOK, start + timedelta(days=28))
    assert len(replay["violations"]) == 1
    assert replay["violations"][0]["reject_reason"] == "min_fill_interval"

    judgment = pl._milestone_judgment(root, rows, pl.S01_BOOK, 4, start + timedelta(days=28))
    assert judgment is not None
    assert judgment["verdict"] == "FAIL"
    assert judgment["failed_criteria"] == ["guard_violations"]
    # the other criteria still evaluate clean on this journal
    assert judgment["criteria"]["guard_violations"]["value"] == 1
    assert judgment["criteria"]["loop_success_rate"]["pass"] is True


def test_milestone_no_judgment_without_decisions(tmp_path: Path, monkeypatch) -> None:
    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    root.mkdir(parents=True, exist_ok=True)  # no pipeline driven: bare data dir
    # no start date -> nothing to judge, no crash
    out = pl.weekly_summary(_data_dir, now.date() + timedelta(days=28))
    assert "milestone" not in out


# --- v1.5 (t_e2c98d2b): the recorded edge-lifetime verdict gates the next -----
# mono-verify: cusum_watch writes cusum_A.jsonl with action PAUSE/continue and,
# until this card, nothing read it. These tests pin the consumption side only;
# the monitor's own arithmetic stays covered by tests/test_cusum_watch.py.


def _append_cusum_verdict(
    root: Path,
    *,
    action: object = "PAUSE",
    S: float | None = 5.13,
    h_star: object = 4.8,
    book_id: str = "A",
) -> dict:
    """Append one hash-chained monitor row, shaped like cusum_watch.run_weekly."""

    path = root / pl.CUSUM_LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = pl._rows_from_jsonl(path, strict=True) if path.exists() else []
    row: dict = {
        "schema": "cusum-watch-v1",
        "book_id": book_id,
        "computed_at": "2026-10-19T00:50:53.558848+00:00",
        "prereg_row_hash": "0" * 64,
        "baseline_ref_sha256": "1" * 64,
        "baseline_sha_match": True,
        "constants": {
            "kappa0": 0.5,
            "k": 0.5,
            "mean": 0.004933,
            "sigma_hat": 0.048186,
            "h_star": h_star,
        },
        "week": "2026-W42",
        "weeks_observed": 2,
        "z": -0.63,
        "S": S,
        "alarm": action == "PAUSE",
        "action": action,
        "note": None,
    }
    row["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    row["row_hash"] = pl._sha256(json.dumps(row, sort_keys=True, default=str))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def _decision_rows(root: Path, book: str, day: date) -> list[dict]:
    return [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row.get("book") == book
        and row.get("decision_date") == day.isoformat()
    ]


def test_cusum_pause_gates_the_next_decision(tmp_path: Path, monkeypatch) -> None:
    """A recorded PAUSE must reach the next decision — not just the log."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    logged = _append_cusum_verdict(root, action="PAUSE", S=5.13)
    assert pl.run_daily_decide(root, now) > 0

    gated = _decision_rows(root, pl.S01_BOOK, now.date())
    assert len(gated) == len(pl.CRYPTO_UNIVERSE)
    for row in gated:
        assert row["regime_state"] == "HALT"
        assert row["halt_flag"] is True
        assert row["fill_status"] == "skipped"
        assert "cusum_pause" in row["halt_reason"]
        assert row["target_weight"] == 0.0

    # the verdict read is recorded with its provenance on the halt_check row
    check = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("book") == pl.S01_BOOK
        and row.get("check_date") == now.date().isoformat()
    ]
    assert len(check) == 1
    assert check[0]["action"] == "halt"
    assert check[0]["halted_after"] is True
    assert check[0]["edge_lifetime"]["status"] == "pause"
    assert check[0]["edge_lifetime"]["S"] == 5.13
    assert check[0]["edge_lifetime"]["h_star"] == 4.8
    assert check[0]["edge_lifetime"]["row_hash"] == logged["row_hash"]
    tripped = [e for e in check[0]["triggers"] if e["tripped"]]
    assert [e["name"] for e in tripped] == ["cusum_pause"]
    assert tripped[0]["margin"] == pytest.approx(5.13 - 4.8)

    # the gate is scoped to the monitored book: the crypto benchmark trades on
    assert all(
        row["halt_flag"] is False for row in _decision_rows(root, pl.BENCH_CRYPTO_BOOK, now.date())
    )
    # a re-decision is still idempotent while paused
    assert pl.run_daily_decide(root, now) == 0


def test_cusum_continue_verdict_leaves_decisions_unchanged(tmp_path: Path, monkeypatch) -> None:
    """The live 2026-W42 verdict is `continue`: the gate stays open."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    _append_cusum_verdict(root, action="continue", S=0.1307875935134487)
    pl.run_daily_decide(root, now)

    rows = _decision_rows(root, pl.S01_BOOK, now.date())
    assert rows and all(row["halt_flag"] is False for row in rows)
    assert all(row["regime_state"] in ("RISK_ON", "RISK_OFF") for row in rows)
    check = next(
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("book") == pl.S01_BOOK
        and row.get("check_date") == now.date().isoformat()
    )
    assert check["action"] == "none"
    assert check["edge_lifetime"]["status"] == "continue"
    assert check["edge_lifetime"]["S"] == 0.1307875935134487
    assert all(e["name"] != "cusum_pause" for e in check["triggers"])


def test_cusum_alarm_latches_after_a_later_continue_row(tmp_path: Path, monkeypatch) -> None:
    """'S is never reset after an alarm' — a later lower S does not reopen the gate."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    _append_cusum_verdict(root, action="PAUSE", S=5.13)
    _append_cusum_verdict(root, action="continue", S=2.0)  # later week below h*
    verdict = pl.cusum_edge_verdict(root)
    assert verdict["status"] == "pause"
    assert verdict["alarm_S"] == 5.13 and verdict["S"] == 2.0

    pl.run_daily_decide(root, now)
    rows = _decision_rows(root, pl.S01_BOOK, now.date())
    assert rows and all(row["halt_flag"] is True for row in rows)
    check = next(
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("book") == pl.S01_BOOK
        and row.get("check_date") == now.date().isoformat()
    )
    trigger = next(e for e in check["triggers"] if e["name"] == "cusum_pause")
    # the margin reports the ALARM's S_w - h*, not the later, lower S_w
    assert trigger["value"] == pytest.approx(5.13)
    assert trigger["margin"] == pytest.approx(5.13 - 4.8)
    assert check["edge_lifetime"]["alarm_week"] == "2026-W42"


def test_cusum_untrusted_verdict_halts_fail_closed(tmp_path: Path, monkeypatch) -> None:
    """An untrustworthy verdict is never read as 'the edge is alive'."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    _append_cusum_verdict(root, action="banana")
    assert pl.cusum_edge_verdict(root)["status"] == "unreadable"
    pl.run_daily_decide(root, now)
    rows = _decision_rows(root, pl.S01_BOOK, now.date())
    assert rows and all(row["halt_flag"] is True for row in rows)
    assert all("cusum_verdict_unreadable" in row["halt_reason"] for row in rows)

    # a partially written / corrupt log line is unreadable too
    (root / pl.CUSUM_LOG_NAME).write_text("{not json\n", encoding="utf-8")
    verdict = pl.cusum_edge_verdict(root)
    assert verdict["status"] == "unreadable"
    assert verdict["why"] == "log_unreadable"


def test_cusum_absent_log_does_not_gate(tmp_path: Path, monkeypatch) -> None:
    """No monitor log yet (pre-monitor journals): no gate, no fabricated pass."""

    _data_dir, root, now = _prepare(tmp_path, monkeypatch)
    assert not (root / pl.CUSUM_LOG_NAME).exists()
    assert pl.cusum_edge_verdict(root)["status"] == "absent"
    pl.run_daily_decide(root, now)
    rows = _decision_rows(root, pl.S01_BOOK, now.date())
    assert rows and all(row["halt_flag"] is False for row in rows)
    check = next(
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("book") == pl.S01_BOOK
        and row.get("check_date") == now.date().isoformat()
    )
    # the gate is recorded as "absent" (a fact), never as a silent pass
    assert check["edge_lifetime"]["status"] == "absent"
    assert check["edge_lifetime"]["why"] == "monitor_has_not_run"
    assert all(e["name"] != "cusum_pause" for e in check["triggers"])
    # books without a registered monitor carry no verdict at all
    other = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check" and row.get("book") == pl.BENCH_CRYPTO_BOOK
    ]
    assert other and all(row["edge_lifetime"] is None for row in other)


# --- equity_gross: same market returns, costs never charged (t_a2d67004) -----


def _costed_fill(root: Path, book: str, day: str, cost: float, notional: float = 0.25) -> None:
    """Append a hand-built fill row that carries an assumed cost."""

    _append_row(
        root,
        event="fill",
        book=book,
        asset="SPY",
        side="buy",
        paper_fill_price=100.0,
        paper_fill_bar_open_ts=f"{day}T13:30:00+00:00",
        units_after=0.25,
        cash_after=0.0,
        traded_notional=notional,
        cost_assumed=cost,
    )


def test_equity_gross_tracks_market_returns(tmp_path: Path) -> None:
    """Regression (t_a2d67004): gross must include the market returns.

    The pre-fix `_cost_accumulators` compounded only the cost rates from 1.0
    (market returns ignored), so a book that fell 1% gross-of-costs still
    reported equity_gross > 1.0. With no fills at all, gross == equity exactly.
    """

    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.0, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.10, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-23", 0.99, 0.0)
    cum_cost, cum_turnover, gross = pl._cost_accumulators(root, pl.S01_BOOK, 0.99)
    assert cum_cost == 0.0
    assert cum_turnover == 0.0
    assert gross == pytest.approx(0.99, abs=1e-12)


def test_equity_gross_adds_back_only_costs_on_a_flat_market(tmp_path: Path) -> None:
    """Flat market: gross stays at inception 1.0 and the drag equals the cost.

    Chain identity on a flat market: the add-back exactly restores the cost,
    so cost_drag_pp == the actually incurred cost share (2% here), not the
    pre-fix compounding of cost rates against a stale base.
    """

    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.0, 0.0)
    _costed_fill(root, pl.S01_BOOK, "2026-10-21", cost=0.02)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 0.98, 0.0)
    cum_cost, cum_turnover, gross = pl._cost_accumulators(root, pl.S01_BOOK, 0.98)
    assert cum_cost == pytest.approx(0.02)
    assert cum_turnover == pytest.approx(0.25)
    assert gross == pytest.approx(1.0, abs=1e-12)
    drag_pp = (gross - 0.98) / gross * 100.0
    assert drag_pp == pytest.approx(2.0, abs=1e-9)  # 0.02 charged on 1.0


def test_equity_gross_identity_gross_over_equity_is_cost_share_product(
    tmp_path: Path,
) -> None:
    """Algebraic chain identity: gross/equity == prod(1 + cost_t / equity_t).

    Costs charged mid-series compound forward with the subsequent market
    returns on the gross side — that is what makes the drag read the actual
    cost share on a moving market (the panel's cost_drag_pp input).
    """

    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    # market path with costs on two different steps, both books interleaved
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.0, 0.0)
    _costed_fill(root, pl.S01_BOOK, "2026-10-21", cost=0.01)
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-21", 1.0, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 0.99 * 1.05, 0.0)  # cost then +5%
    _mtm(root, pl.BOOK_C_BOOK, "2026-10-22", 1.008, 0.0)
    _costed_fill(root, pl.BOOK_C_BOOK, "2026-10-22", cost=0.004)
    _mtm(root, pl.S01_BOOK, "2026-10-23", 0.98 * 1.05, 0.0)  # no cost this step
    final_a = 1.038 * 0.97  # market -3% on 10-23, cost charged before it
    _costed_fill(root, pl.S01_BOOK, "2026-10-23", cost=0.005)
    cum_cost, _, gross_a = pl._cost_accumulators(root, pl.S01_BOOK, final_a)
    _, _, gross_c = pl._cost_accumulators(root, pl.BOOK_C_BOOK, 1.008 * 1.01)
    assert cum_cost == pytest.approx(0.015)
    # independent replay of prod(1 + c_t / E_t) per book, incl. the final step
    # (E_t = equity at the END of the step the cost was charged in)
    step_a2 = 0.99 * 1.05  # A's 0.01 sits in the step ending at mtm(10-22)
    prod_a = (1 + 0.01 / step_a2) * (1 + 0.005 / final_a)
    assert gross_a / final_a == pytest.approx(prod_a, rel=1e-12)
    final_c = 1.008 * 1.01  # C's 0.004 sits in the final step
    prod_c = 1 + 0.004 / final_c
    assert gross_c / final_c == pytest.approx(prod_c, rel=1e-12)
    # and the drag reads the cost share, not the market move: 1% + 0.5%
    # compound to ~1.44pp here — nowhere near the +5%/-3% market steps
    drag_a = (gross_a - final_a) / gross_a * 100.0
    assert drag_a == pytest.approx((1 - 1 / prod_a) * 100.0, rel=1e-9)
    assert 0.0 < drag_a < 1.5


def test_cost_drag_pp_reads_the_incurred_cost_not_the_market_move() -> None:
    """Panel regression (t_a2d67004): cost_drag_pp must match the cost share.

    With the fixed gross, a 2% cost on a flat market reads 2.0pp. The pre-fix
    gross (cost rates compounded from 1.0, market ignored) made the same
    journal read 3.92pp — the market drawdown mislabeled as cost.
    """

    from collections.abc import Mapping
    from typing import Any

    from yowayowa.paperloop_panel import _cost_drag_pp

    rows: list[Mapping[str, Any]] = [
        {"book": pl.S01_BOOK, "date": "2026-10-22", "equity": 0.98, "equity_gross": 1.0},
        {"book": pl.S01_BOOK, "date": "2026-10-23", "equity": 0.966553, "equity_gross": 0.968349},
    ]
    drag = _cost_drag_pp(rows, pl.S01_BOOK)
    assert drag is not None
    assert drag == pytest.approx((0.968349 - 0.966553) / 0.968349 * 100.0, rel=1e-12)
    assert 0.0 < drag < 1.0
    # the pre-fix value for the same journal (gross 1.000349 from 1.0) was
    # ~3.38pp — assert the fixed input, not the formula, removes that error
    old = (1.000349 - 0.966553) / 1.000349 * 100.0
    assert old > 3.0 and drag < 1.0


# --- C-02: week_return is the week's change, not the cumulative return -------------


def test_week_return_is_the_weeks_change_not_the_cumulative_return(
    tmp_path: Path, monkeypatch
) -> None:
    """C-02 (docs/PAPERLOOP_MONEY_REVIEW_20261023.md): week_return is per-week.

    The field used to be ``state["equity"] - 1.0`` (cumulative from the 1.0
    inception) while every neighbouring window column was scoped to
    ``week_start..week_end``. On the real journal that printed -3.3447% for a
    week whose actual move was -0.8421%. The regression below pins the chained
    window definition, the ignored mark after week_end (the weekly file is
    append-only, so a later mark must never leak into an older row), and the
    honest None when the week cannot be priced.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})

    def build(name: str, marks: list[tuple[str, float]]) -> Path:
        data_dir = tmp_path / name
        root = pl.loop_root(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        for day, equity in marks:
            _mtm(root, pl.S01_BOOK, day, equity, 0.0)
        return data_dir

    # week_end 2026-10-25 -> window 2026-10-19..2026-10-25
    data_dir = build(
        "chained",
        [
            ("2026-10-12", 1.10),  # base: last mark BEFORE the window
            ("2026-10-22", 1.144),
            ("2026-10-24", 1.155),  # last mark inside the window
            ("2026-10-27", 1.20),  # after week_end -> must be ignored
        ],
    )
    book = pl.weekly_summary(data_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["week_return"] == pytest.approx(1.155 / 1.10 - 1.0, rel=1e-12)
    assert book["week_return_base"] == "2026-10-12"
    assert book["week_return_as_of"] == "2026-10-24"
    assert book["week_return_reason"] is None
    # the number the pre-fix code wrote for this same journal (20%, not 5%)
    assert book["cum_return"] == pytest.approx(0.20, rel=1e-12)
    assert abs(book["week_return"] - book["cum_return"]) > 0.14

    # a book whose whole life is inside the window: inception (1.0) is the base
    only_window = build("inception", [("2026-10-20", 1.10), ("2026-10-23", 1.21)])
    book = pl.weekly_summary(only_window, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["week_return_base"] == "inception"
    assert book["week_return"] == pytest.approx(0.21, rel=1e-12)

    # no mark inside the window -> None + reason (missing data is not zero)
    stale = build("stale", [("2026-10-05", 1.05)])
    book = pl.weekly_summary(stale, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["week_return"] is None
    assert book["week_return_reason"] == "no_mark_in_window"
    assert book["week_return_as_of"] is None


