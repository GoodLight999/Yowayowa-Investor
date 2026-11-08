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
from typing import Any

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
        len(pl.CRYPTO_UNIVERSE) if book in pl.CRYPTO_BOOKS else len(pl.EQUITY_UNIVERSE)
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
    # v1.10 (t_52c4aa20): the three crypto books (A, the 1/N benchmark and the
    # 50% gross shadow book A_HALF) fill 2 assets each; the equity books have no
    # stock tape bars in this fixture.
    assert resolved == 2 * len(pl.CRYPTO_BOOKS)
    fills = [row for row in pl._load_journal(root) if row.get("event") == "fill"]
    assert all(row["side"] == "buy" for row in fills)
    # D2: traded notional = |target-held| * equity = 0.5 * 1.0 for A and the
    # benchmark, 0.25 * 1.0 for the half-gross shadow book
    for row in fills:
        expected_notional = 0.5 * pl.SHADOW_GROSS_CAP if row["book"] == pl.BOOK_A_HALF else 0.5
        assert abs(row["traded_notional"] - expected_notional) < 1e-9, row["book"]
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
    # v1.10: A rebalance 2 + A_HALF rebalance 2 + benchmark hold-only fills 2
    assert resolved2 == 2 * len(pl.CRYPTO_BOOKS)
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
    assert pl.resolve_fills(root, now + timedelta(hours=2)) == 2 * len(pl.CRYPTO_BOOKS)


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
    # v1.10 (t_52c4aa20): strategy books only (benchmark marks are excluded, D7)
    # — now A, C(no fill here) and the A_HALF shadow book, 2 crypto assets each.
    assert marked == 2 * len([b for b in pl.STRATEGY_BOOKS if b in pl.CRYPTO_BOOKS])
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
    assert pl.resolve_fills(root, buy_day + timedelta(hours=2)) == 2 * len(pl.CRYPTO_BOOKS)
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
    crypto_mtms = [row for row in mtms if row.get("book") in pl.CRYPTO_BOOKS]
    # v1.10 (t_52c4aa20): the FULL-gross crypto books breach the -20% line in
    # this slide; the 50% gross shadow book is expected to breach only half of
    # it, so it must NOT carry the warning. Pinned as a relation, not a value.
    full_gross = [row for row in crypto_mtms if row["book"] != pl.BOOK_A_HALF]
    half_gross = next(row for row in crypto_mtms if row["book"] == pl.BOOK_A_HALF)
    assert full_gross
    assert all(row["drawdown"] <= pl.HALT_DRAWDOWN for row in full_gross)
    assert all(row["drawdown_warning"] is True for row in full_gross)
    worst_full = min(row["drawdown"] for row in full_gross)
    assert half_gross["drawdown"] > pl.HALT_DRAWDOWN  # half the exposure, half the DD
    assert 0.40 <= half_gross["drawdown"] / worst_full <= 0.60
    assert half_gross["drawdown_warning"] is False
    assert all(row["stopped"] is False for row in mtms)  # v1.3: no stop
    assert pl.rebuild_state(root, pl.S01_BOOK)["stopped"] is False
    # the next decide is NOT halted by the drawdown: halt_check warns instead
    pl.run_daily_decide(root, slide_day)
    halt_checks = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "halt_check"
        and row.get("check_date") == slide_day.date().isoformat()
        and row.get("book") in pl.CRYPTO_BOOKS
    ]
    assert len(halt_checks) == len(pl.CRYPTO_BOOKS)
    for row in halt_checks:
        assert row["halted_after"] is False
        if row["book"] == pl.BOOK_A_HALF:
            assert row["drawdown_warning"] is False
        else:
            assert row["drawdown_warning"] is True
            assert row["action"] == "warn"
    day_decisions = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "decision"
        and row.get("decision_date") == slide_day.date().isoformat()
        and row.get("book") in pl.CRYPTO_BOOKS
    ]
    assert len(day_decisions) == 2 * len(pl.CRYPTO_BOOKS)
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
        for book in pl.CRYPTO_BOOKS
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
    assert resolved == 2 * len(pl.CRYPTO_BOOKS)  # only the NEW decisions fill
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
    import httpx

    from yowayowa.config import get_settings
    from yowayowa.providers.alpaca import AlpacaMarketDataProvider
    from yowayowa.stock_acquisition import StockOhlcvStore, fetch_stock_ohlcv

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
            {
                "t": "2026-10-16T04:00:00Z",
                "o": 100.0,
                "h": 102.0,
                "l": 99.0,
                "c": 101.0,
                "v": 1000,
                "n": 10,
                "vw": 100.5,
            },
            {
                "t": "2026-10-19T04:00:00Z",
                "o": 101.0,
                "h": 103.0,
                "l": 100.0,
                "c": 102.0,
                "v": 1000,
                "n": 10,
                "vw": 101.5,
            },
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
        len(pl.CRYPTO_UNIVERSE) if book in pl.CRYPTO_BOOKS else len(pl.EQUITY_UNIVERSE)
        for book in pl.BOOKS_ENABLED
    )
    assert pl.run_daily_decide(root, mon) == expected  # 6 crypto + 4 stock + 4 EW
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
    # v1.11 (t_264e0692): A_HALF has a SECOND frozen gate — the pre-registered
    # 20-business-day window (its first 20 weekdays, ~27 calendar days) — and
    # that window can close BEFORE the 4-week deadline. Its own record-once
    # record may then appear early; what may NOT appear before the deadline is
    # the WEEKLY milestone block (the "weeks" slots), and A/C must stay silent.
    early_milestone = out_early.get("milestone")
    if early_milestone is not None:
        assert set(early_milestone) == {pl.BOOK_A_HALF}, sorted(early_milestone)
        assert "weeks" not in early_milestone[pl.BOOK_A_HALF]
        assert "20bd" in early_milestone[pl.BOOK_A_HALF]
    else:
        assert "milestone" not in out_early
    assert len(pl._rows_from_jsonl(pl.summary_path(root))) == summary_rows + 1

    # at the deadline: one judgment per strategy book, on the frozen gates
    out = pl.weekly_summary(_data_dir, deadline)
    assert len(pl._load_journal(root)) == journal_rows  # record-only: journal untouched
    # v1.10 (t_52c4aa20): the shadow book A_HALF is a strategy book too, so it
    # carries its own 4-week judgment (same run rows, its own equity/cost).
    assert set(out["milestone"]) == set(pl.STRATEGY_BOOKS)
    assert set(out["milestone"]) >= {pl.S01_BOOK, pl.BOOK_C_BOOK}
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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
    c = out["milestone"][pl.BOOK_C_BOOK]["weeks"][4]
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
        row["milestone"][pl.S01_BOOK]["weeks"]["4"]
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
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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
    a = out["milestone"][pl.S01_BOOK]["weeks"][4]
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


# --- 12/26-week milestone wiring (t_0be71b95) ---------------------------------


def _long_milestone_row(root: Path, day: date, **row: object) -> None:
    """One hash-chained journal row with a sane UTC recorded_at."""

    row.setdefault("recorded_at", f"{day.isoformat()}T04:00:00+00:00")
    pl._append_row(root, row)  # type: ignore[arg-type]


def _long_milestone_journal(
    root: Path,
    *,
    start: date,
    run_days: int,
    book_marks: dict[int, float],
    bench_marks: dict[int, float] | None = None,
    shortfalls: dict[int, float] | None = None,
    book: str = pl.S01_BOOK,
    asset: str = "BTC",
    flip_every: int | None = None,
) -> None:
    """Synthetic journal for the long-horizon milestone tests: daily
    non-backfill decisions (stable RISK_ON, or alternating every
    ``flip_every`` days to manufacture regime flips) + ok daily run rows,
    mtm equity marks for the book and its benchmark, and mark rows with the
    given signed shortfalls."""

    for offset in range(run_days):
        day = start + timedelta(days=offset)
        if flip_every is not None:
            regime = "RISK_ON" if (offset // flip_every) % 2 == 0 else "RISK_OFF"
        else:
            regime = "RISK_ON"
        prev_regime = regime
        if flip_every is not None and offset % flip_every == 0 and offset > 0:
            prev_regime = "RISK_OFF" if regime == "RISK_ON" else "RISK_ON"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=book,
            decision_id=f"{book}:{asset}:{day.isoformat()}",
            decision_date=day.isoformat(),
            asset=asset,
            regime_state=regime,
            prev_regime_state=prev_regime,
            backfill=False,
        )
        _long_milestone_row(root, day, event="run", job="daily", phase="end", ok=True)
    for offset, equity in book_marks.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=book,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=-0.05,
            peak_equity=1.0,
        )
    bench = pl.BOOK_BENCHMARK.get(book)
    if bench_marks and bench:
        for offset, equity in bench_marks.items():
            day = start + timedelta(days=offset)
            _long_milestone_row(
                root,
                day,
                event="mtm",
                book=bench,
                mtm_date=day.isoformat(),
                equity=equity,
                cash=0.0,
                drawdown=-0.15,
                peak_equity=1.0,
            )
    for offset, shortfall in (shortfalls or {}).items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mark",
            book=book,
            decision_id=f"{book}:{asset}:{day.isoformat()}",
            asset=asset,
            bt_fill_price=100.0,
            recompute_match=True,
            turnover=0.0,
            shortfall_bps=shortfall,
        )


_DEEP_BENCH_MARKS = {
    0: 1.0,
    7: 0.95,
    14: 0.85,
    21: 0.86,
    28: 0.88,
    35: 0.9,
    42: 0.92,
    49: 0.94,
    56: 0.95,
    63: 0.96,
    70: 0.96,
    77: 0.97,
    83: 0.965,
    84: 0.97,
}
# 26-week windows need marks at their own frozen endpoints (0 and day 181):
# the rev2 dd gate compares the SAME frozen period on both sides, so a bench
# that stops at day 84 defers a 26-week judgment instead of grading a
# 12-week-long benchmark span under the 26-week label.
_DEEP_BENCH_MARKS_26W = {
    0: 1.0,
    7: 0.95,
    14: 0.85,
    83: 0.87,
    90: 0.85,
    175: 0.9,
    181: 0.86,
}


def test_milestone_12w_and_4w_record_in_one_summary_row(tmp_path: Path, monkeypatch) -> None:
    """(a) One weekly run past BOTH deadlines keeps 4w AND 12w in the same row;
    (b) the weekly run before the 12-week deadline judges no 12w (t_0be71b95).

    Weekly runs were paused between the two deadlines: the first run after the
    pause sits past day 84. The old single ``out[book]`` slot kept only the
    LAST judgment of the run, so the 4-week record vanished; the
    ``{"weeks": {...}}`` block keeps both — on the returned row and on the
    disk row (JSON stringifies the week keys) — and the next run dedupes both.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 6, 1)
    book_marks = {
        offset: (1.0 if offset < 28 else (0.97 if offset < 56 else 1.02))
        for offset in range(0, 85, 7)
    }
    # rev2 dd/cum gates compare the FULL frozen window: the book's last
    # in-window mark must sit on the window's last day (day 83), not day 77
    book_marks[83] = 1.02

    def build(root: Path) -> None:
        _long_milestone_journal(
            root,
            start=start,
            run_days=84,
            book_marks=book_marks,
            bench_marks=_DEEP_BENCH_MARKS,
            shortfalls={offset: 0.0 for offset in range(0, 84, 7)},
        )

    # --- (b) before the 12-week deadline: no 12w judgment, ever ---------------
    early_dir = tmp_path / "early" / "data"
    early_root = pl.loop_root(early_dir)
    early_root.mkdir(parents=True, exist_ok=True)
    build(early_root)
    out_early = pl.weekly_summary(early_dir, start + timedelta(days=83))
    early_weeks = out_early["milestone"][pl.S01_BOOK]["weeks"]
    assert 4 in early_weeks  # its own deadline (day 28) has passed
    assert 12 not in early_weeks  # deadline (day 84) not reached: never early
    assert 26 not in early_weeks

    # --- (a) the first run past both deadlines records BOTH in one row --------
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    build(root)
    deadline_12w = start + timedelta(days=84)
    out = pl.weekly_summary(data_dir, deadline_12w)
    weeks = out["milestone"][pl.S01_BOOK]["weeks"]
    assert set(weeks) == {4, 12}
    j12 = weeks[12]
    assert j12["milestone_date"] == deadline_12w.isoformat()
    assert j12["judged_at_week_end"] == deadline_12w.isoformat()
    assert j12["window_end"] == deadline_12w.isoformat()
    assert j12["window_days"] == 84
    assert j12["verdict"] == "PASS"
    assert j12["rules_source"] == "MILESTONE_RULES[12] + DEMO_LOOP_V1_OPS.md §4 (frozen)"
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["pass"] is True
    assert dd["benchmark_book"] == pl.BENCH_CRYPTO_BOOK
    assert dd["benchmark_max_drawdown"] == pytest.approx(-0.15, rel=1e-12)
    cum = j12["criteria"]["cum_pnl_min"]
    assert cum["pass"] is True
    assert cum["value"] == pytest.approx(0.02, rel=1e-12)
    sw = j12["criteria"]["switches_multiple"]
    assert sw["pass"] is True  # per asset: BTC 0 flips <= 2.0 * 8.8 * 84/365
    btc = sw["per_asset"]["BTC"]
    assert btc["expected"] == pytest.approx(8.8 * 84 / 365, rel=1e-12)
    assert btc["max"] == pytest.approx(2.0 * 8.8 * 84 / 365, rel=1e-12)
    assert btc["pass"] is True
    assert j12["switches_observed"] == 0
    # the 4-week block in the same row is the unchanged t_d33aef07 judgment
    j4 = weeks[4]
    assert j4["verdict"] == "PASS"
    assert set(j4["criteria"]) == {
        "loop_success_rate",
        "observed_cost_bps_mean",
        "recompute_match_rate",
        "orders",
        "guard_violations",
    }

    # the disk row carries both weeks too (JSON string keys)
    disk_rows = [
        json.loads(line) for line in pl.summary_path(root).read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(disk_rows[-1]["milestone"][pl.S01_BOOK]["weeks"]) == ["12", "4"]
    assert disk_rows[-1]["schema"] == "demo-loop-summary-v1.10"

    # dedupe: the next weekly run records neither week again
    out_again = pl.weekly_summary(data_dir, deadline_12w + timedelta(days=7))
    assert "milestone" not in out_again


def test_milestone_26w_shortfall_gate_is_signed_and_defers_until_facts(
    tmp_path: Path, monkeypatch
) -> None:
    """(c) With the shortfall facts missing at the 26-week deadline, NO
    judgment is recorded (no permanent INSUFFICIENT) and a later weekly run
    judges the same frozen window (t_0be71b95).

    The recorded gate is the SIGNED mean's one-sided 95% upper bound: three
    marks of -100 bps give bound -100 <= 15 (PASS), which the abs()-based
    4-week cost mean (100 bps) would have turned into a fake FAIL.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    deadline_26w = start + timedelta(days=182)
    book_marks = {
        offset: (1.0 if offset < 56 else (0.97 if offset < 112 else 1.03))
        for offset in range(0, 183, 7)
    }
    # rev2 full-window coverage: marks at BOTH windows' last days (83 for the
    # 12-week gates, 181 for the 26-week gates)
    book_marks[83] = 0.97
    book_marks[181] = 1.03

    def build(root: Path) -> None:
        _long_milestone_journal(
            root,
            start=start,
            run_days=182,
            book_marks=book_marks,
            bench_marks=_DEEP_BENCH_MARKS_26W,
            shortfalls={0: -100.0},
        )

    # --- at the deadline: one mark only -> n < 2 -> the 26w judgment defers ---
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    build(root)
    out_deadline = pl.weekly_summary(data_dir, deadline_26w)
    weeks = out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    assert 26 not in weeks, "no permanent INSUFFICIENT: nothing recorded without facts"
    assert 4 in weeks and 12 in weeks  # the other weeks still judge on their facts

    # --- facts arrive (their decisions' execution dates are in the window; ---
    # attribution is by decision date, recorded_at is provenance): judged once
    for offset in (91, 140):
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mark",
            book=pl.S01_BOOK,
            decision_id=f"A:BTC:{day.isoformat()}",
            asset="BTC",
            bt_fill_price=100.0,
            recompute_match=True,
            turnover=0.0,
            shortfall_bps=-100.0,
        )
    out_late = pl.weekly_summary(data_dir, deadline_26w + timedelta(days=7))
    weeks_late = out_late["milestone"][pl.S01_BOOK]["weeks"]
    assert 4 not in weeks_late and 12 not in weeks_late  # deduped
    j26 = weeks_late[26]
    assert j26["milestone_date"] == deadline_26w.isoformat()
    assert j26["window_end"] == deadline_26w.isoformat()  # frozen deadline window
    assert j26["judged_at_week_end"] == (deadline_26w + timedelta(days=7)).isoformat()
    gate = j26["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate["threshold"] == pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"]
    assert gate["value"] == pytest.approx(-100.0, rel=1e-12)  # SIGNED bound
    assert gate["pass"] is True  # abs() would read +100 bps -> fake FAIL
    assert gate["shortfall_n"] == 3
    assert gate["attribution"] == "decision_execution_date"
    assert j26["verdict"] == "PASS"
    assert j26["rules_source"] == "MILESTONE_RULES[26] + DEMO_LOOP_V1_OPS.md §4 (frozen)"
    assert j26["criteria"]["dd_vs_benchmark"]["pass"] is True


def test_milestone_12w_holds_while_benchmark_drawdown_is_shallow(
    tmp_path: Path, monkeypatch
) -> None:
    """Design §4-2a 判断材料なし: with the benchmark's max DD in the window
    below 10%, the 12-week DD ratio is not judged (保留) — the record defers
    instead of grading the ratio against a benchmark that never fell (and a
    real drawdown judges normally, same journal otherwise)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 6, 1)
    book_marks = {
        offset: (1.0 if offset < 28 else (0.99 if offset < 56 else 1.01))
        for offset in range(0, 85, 7)
    }
    book_marks[83] = 1.01  # rev2: the book spans to the window's last day
    # benchmark dips 3% and then recovers: a REAL drawdown, just shallower
    # than the design's 10% judgeability floor
    shallow_bench = {
        offset: (1.0 if offset == 0 else 0.97 + (offset - 7) * 0.0004) for offset in range(0, 85, 7)
    }
    shallow_bench[83] = 1.0004  # spans to the window's last day, still shallow
    deep_bench = dict(_DEEP_BENCH_MARKS)

    def build(root: Path, bench: dict[int, float]) -> None:
        _long_milestone_journal(
            root,
            start=start,
            run_days=84,
            book_marks=book_marks,
            bench_marks=bench,
            shortfalls={offset: 0.0 for offset in range(0, 84, 7)},
        )

    shallow_dir = tmp_path / "shallow" / "data"
    shallow_root = pl.loop_root(shallow_dir)
    shallow_root.mkdir(parents=True, exist_ok=True)
    build(shallow_root, shallow_bench)
    window_end = start + timedelta(days=84)
    # pin the hold branch itself: the ratio IS computable (bench_dd < 0) but
    # the shallow benchmark defers it
    dd_criterion = pl._milestone_dd_criterion(
        shallow_root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd_criterion["pass"] is None
    assert dd_criterion["reason"] == "benchmark_drawdown_shallow_hold"
    assert -pl.MILESTONE_DD_BENCH_MIN < dd_criterion["benchmark_max_drawdown"] < 0
    out = pl.weekly_summary(shallow_dir, window_end)
    weeks = out["milestone"][pl.S01_BOOK]["weeks"]
    assert 12 not in weeks, "shallow benchmark drawdown holds (保留), nothing recorded"
    assert 4 in weeks

    deep_dir = tmp_path / "deep" / "data"
    deep_root = pl.loop_root(deep_dir)
    deep_root.mkdir(parents=True, exist_ok=True)
    build(deep_root, deep_bench)
    out_deep = pl.weekly_summary(deep_dir, start + timedelta(days=84))
    j12 = out_deep["milestone"][pl.S01_BOOK]["weeks"][12]
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["pass"] is True
    assert dd["reason"] is None
    assert abs(dd["benchmark_max_drawdown"]) >= pl.MILESTONE_DD_BENCH_MIN


def test_milestone_12w_switches_gate_is_per_asset_and_book_c_judges(
    tmp_path: Path, monkeypatch
) -> None:
    """The frozen switches gate judges each asset against ITS OWN design §2-3
    backtest expectation (BTC 8.8/yr -> 12w expected ≈ 2.03, max 2x that ≈
    4.05): a whip-sawing BTC FAILS the record. Book C's S-02 rates are frozen
    too (t_be52f5bc decision A), so its 12-week judgment now RECORDS like any
    other book's — while no rate existed the required gate deferred the whole
    judgment every weekly run (AS-M126-01, t_0be71b95 rev2), and dropping the
    gate to pass on the rest was the fake PASS that contract closed."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 6, 1)
    book_marks = {
        offset: (1.0 if offset < 28 else (0.97 if offset < 56 else 1.02))
        for offset in range(0, 85, 7)
    }
    book_marks[83] = 1.02
    shortfalls = {offset: 0.0 for offset in range(0, 84, 7)}

    whip_dir = tmp_path / "whip" / "data"
    whip_root = pl.loop_root(whip_dir)
    whip_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        whip_root,
        start=start,
        run_days=84,
        book_marks=book_marks,
        bench_marks=_DEEP_BENCH_MARKS,
        shortfalls=shortfalls,
        flip_every=7,  # 11 RISK_ON <-> RISK_OFF flips inside the 12w window
    )
    out = pl.weekly_summary(whip_dir, start + timedelta(days=84))
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"  # a decided FAIL records; only 保留 defers
    assert j12["failed_criteria"] == ["switches_multiple"]
    sw = j12["criteria"]["switches_multiple"]
    assert sw["value"] == 11
    assert sw["pass"] is False
    btc = sw["per_asset"]["BTC"]
    assert btc["observed"] == 11
    assert btc["expected"] == pytest.approx(8.8 * 84 / 365, rel=1e-12)
    assert btc["max"] == pytest.approx(2.0 * 8.8 * 84 / 365, rel=1e-12)
    assert btc["pass"] is False
    assert j12["switches_by_asset"] == {"BTC": 11}

    c_dir = tmp_path / "bookc" / "data"
    c_root = pl.loop_root(c_dir)
    c_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        c_root,
        start=start,
        run_days=84,
        book=pl.BOOK_C_BOOK,
        asset="SPY",
        book_marks=book_marks,
        bench_marks=_DEEP_BENCH_MARKS,
        shortfalls=shortfalls,
    )
    out_c = pl.weekly_summary(c_dir, start + timedelta(days=84))
    # the freeze (t_be52f5bc decision A) unblocks Book C: the 12-week judgment
    # RECORDS once like any other book's (dd 3%/15% <= 0.6, cum +2%, switches 0)
    j12_c = out_c["milestone"][pl.BOOK_C_BOOK]["weeks"][12]
    assert j12_c["verdict"] == "PASS"
    assert pl._milestone_already_judged(c_root, pl.BOOK_C_BOOK, 12) is True
    switches_c = j12_c["criteria"]["switches_multiple"]
    # no deferred reason survives the freeze: the criterion is DECIDED and the
    # per-asset row carries the frozen SPY rate's own expectation and cap
    assert switches_c["pass"] is True
    assert switches_c["reason"] is None
    assert switches_c["per_asset"] == {
        "SPY": {
            "observed": 0,
            "expected": pytest.approx(5.6 * 84 / 365, rel=1e-12),
            "max": pytest.approx(2.0 * 5.6 * 84 / 365, rel=1e-12),
            "pass": True,
        }
    }
    # pin the decided criterion itself, judged straight from the journal
    switches_c2, _facts = pl._milestone_switches_criterion(
        pl._load_journal(c_root),
        pl.BOOK_C_BOOK,
        start,
        start + timedelta(days=84),
        84,
        float(pl.MILESTONE_RULES[12]["switches_multiple"]),
    )
    assert switches_c2["pass"] is True
    assert switches_c2["reason"] is None
    assert switches_c2["per_asset"] == switches_c["per_asset"]


def test_milestone_already_judged_reads_legacy_row_shape(tmp_path: Path) -> None:
    """The dedupe reader accepts BOTH summary shapes: the t_d33aef07 rows
    (``milestone[book]`` = the judgment dict, "weeks" an int) and the v1.10
    rows (``milestone[book]["weeks"]`` = {weeks: judgment}, string keys on
    disk). No production row ever carried the legacy shape; the reader just
    refuses to let an old file look un-judged."""

    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    legacy = {
        "schema": "demo-loop-summary-v1.9",
        "week_end": "2027-01-02",
        "milestone": {
            "A": {"weeks": 4, "book": "A", "verdict": "PASS", "criteria": {}},
        },
    }
    with pl.summary_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(legacy) + "\n")
    assert pl._milestone_already_judged(root, "A", 4) is True
    assert pl._milestone_already_judged(root, "A", 12) is False

    modern = {
        "schema": "demo-loop-summary-v1.10",
        "week_end": "2027-03-27",
        "milestone": {
            "A": {"weeks": {"12": {"weeks": 12, "book": "A", "verdict": "PASS"}}},
        },
    }
    with pl.summary_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(modern) + "\n")
    assert pl._milestone_already_judged(root, "A", 12) is True
    assert pl._milestone_already_judged(root, "A", 26) is False


def test_milestone_dd_gate_requires_the_frozen_window_on_both_sides(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-02 (t_0be71b95 rev2): the DD ratio grades the SAME frozen
    period on both sides — a common origin (the first mark day both sides
    share inside the window, recorded as ``comparison_start``) and marks at
    the frozen window's last day on BOTH the book and the benchmark. A side
    that stops mid-window defers the judgment (nothing recorded, retried
    later); when the missing mark arrives later (attributed by its own
    mtm_date, written with an honest late recorded_at) the SAME frozen window
    is judged — the rev1 premature PASS (book day 7 vs benchmark day 83) can
    no longer exist, and the honest numbers landing late actually decide.
    Normal same-span windows judge at the deadline."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)

    def bench_deep() -> dict[int, float]:
        return dict(_DEEP_BENCH_MARKS)

    # --- normal: both sides span 0..83 -> judged PASS at the deadline --------
    normal_dir = tmp_path / "normal" / "data"
    normal_root = pl.loop_root(normal_dir)
    normal_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        normal_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 41: 0.985, 83: 1.0},
        bench_marks=bench_deep(),
        shortfalls={offset: 0.0 for offset in (0, 41, 83)},
    )
    out = pl.weekly_summary(normal_dir, window_end)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["pass"] is True
    assert dd["comparison_start"] == start.isoformat()
    assert dd["comparison_end"] == (window_end - timedelta(days=1)).isoformat()
    assert dd["book_mark_span"] == [start.isoformat(), (start + timedelta(days=83)).isoformat()]
    assert dd["benchmark_mark_span"] == dd["book_mark_span"]

    # --- mismatch: the book stops at day 7, the benchmark spans to 83 -------
    # (the rev1 repro: this graded book[-3% over 7 days] against
    # bench[-20% over 83 days] and recorded a permanent PASS)
    gap_dir = tmp_path / "gap" / "data"
    gap_root = pl.loop_root(gap_dir)
    gap_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        gap_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 7: 0.97},
        bench_marks={0: 1.0, 7: 0.99, 83: 0.8},
        shortfalls={offset: 0.0 for offset in (0, 7)},
    )
    out_gap = pl.weekly_summary(gap_dir, window_end)
    assert 12 not in out_gap["milestone"][pl.S01_BOOK]["weeks"], "mid-window span defers"
    dd_gap = pl._milestone_dd_criterion(
        gap_root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd_gap["pass"] is None
    assert dd_gap["reason"] == "book_no_mark_at_window_end"
    assert dd_gap["book_mark_span"] == [start.isoformat(), (start + timedelta(days=7)).isoformat()]
    assert dd_gap["benchmark_mark_span"] == [
        start.isoformat(),
        (start + timedelta(days=83)).isoformat(),
    ]

    # the honest late mark arrives (recorded_at AFTER the deadline, its own
    # mtm_date inside the window): the next weekly run judges -0.15 vs -0.20
    _long_milestone_row(
        gap_root,
        start + timedelta(days=91),  # recorded late: day 91
        event="mtm",
        book=pl.S01_BOOK,
        mtm_date=(start + timedelta(days=83)).isoformat(),
        equity=0.85,
        cash=0.0,
        drawdown=-0.15,
        peak_equity=1.0,
    )
    out_recovered = pl.weekly_summary(gap_dir, start + timedelta(days=91))
    j12r = out_recovered["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12r["verdict"] == "FAIL"  # the honest numbers decide, once
    assert j12r["judged_at_week_end"] == (start + timedelta(days=91)).isoformat()
    assert j12r["milestone_date"] == window_end.isoformat()  # still the frozen window
    assert set(j12r["failed_criteria"]) == {"cum_pnl_min", "dd_vs_benchmark"}
    assert j12r["criteria"]["dd_vs_benchmark"]["value"] == pytest.approx(0.75, rel=1e-12)
    assert j12r["criteria"]["cum_pnl_min"]["value"] == pytest.approx(-0.15, rel=1e-12)
    assert pl.weekly_summary(gap_dir, start + timedelta(days=98)).get("milestone") is None

    # --- late-marking benchmark: the shared origin is recorded, not guessed --
    # Book C's real case: the equity benchmark starts marking days after the
    # book. The origin becomes the first day BOTH sides mark (v1.9's shared
    # base_day, here day 7) and is recorded in comparison_start; the gate
    # stays judgeable instead of deferring forever. The book's
    # inception-to-origin move stays with the principal-anchored cum gate.
    origin_dir = tmp_path / "origin" / "data"
    origin_root = pl.loop_root(origin_dir)
    origin_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        origin_root,
        start=start,
        run_days=84,
        book_marks={7: 0.97, 83: 0.99},
        bench_marks=bench_deep(),
        shortfalls={offset: 0.0 for offset in (7, 83)},
    )
    out_origin = pl.weekly_summary(origin_dir, window_end)
    j12o = out_origin["milestone"][pl.S01_BOOK]["weeks"][12]
    dd_origin = j12o["criteria"]["dd_vs_benchmark"]
    assert dd_origin["pass"] is True
    assert dd_origin["comparison_start"] == (start + timedelta(days=7)).isoformat()
    assert dd_origin["comparison_end"] == (window_end - timedelta(days=1)).isoformat()
    # cum still anchors to the 1.0 inception principal: 0.99 - 1.0
    assert j12o["criteria"]["cum_pnl_min"]["value"] == pytest.approx(-0.01, rel=1e-12)

    # --- 26 weeks: same contract, same recovery ------------------------------
    deadline_26w = start + timedelta(days=182)
    gap26_dir = tmp_path / "gap26" / "data"
    gap26_root = pl.loop_root(gap26_dir)
    gap26_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        gap26_root,
        start=start,
        run_days=182,
        book_marks={0: 1.0, 150: 0.9},
        bench_marks={0: 1.0, 90: 0.85, 181: 0.8},
        shortfalls={offset: 0.0 for offset in (10, 20)},
    )
    out_gap26 = pl.weekly_summary(gap26_dir, deadline_26w)
    assert 26 not in out_gap26["milestone"][pl.S01_BOOK]["weeks"]
    dd26 = pl._milestone_dd_criterion(
        gap26_root,
        pl.S01_BOOK,
        start,
        deadline_26w,
        float(pl.MILESTONE_RULES[26]["dd_vs_benchmark"]),
        shallow_bench_hold=False,
    )
    assert dd26["pass"] is None
    assert dd26["reason"] == "book_no_mark_at_window_end"
    _long_milestone_row(
        gap26_root,
        start + timedelta(days=189),  # recorded late
        event="mtm",
        book=pl.S01_BOOK,
        mtm_date=(start + timedelta(days=181)).isoformat(),
        equity=0.85,
        cash=0.0,
        drawdown=-0.15,
        peak_equity=1.0,
    )
    out_recovered26 = pl.weekly_summary(gap26_dir, start + timedelta(days=189))
    j26r = out_recovered26["milestone"][pl.S01_BOOK]["weeks"][26]
    assert j26r["verdict"] == "FAIL"
    # the 26-week frozen rules carry dd + shortfall only (no cum gate)
    assert j26r["failed_criteria"] == ["dd_vs_benchmark"]
    assert j26r["criteria"]["dd_vs_benchmark"]["value"] == pytest.approx(0.75, rel=1e-12)
    gate26 = j26r["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate26["pass"] is True  # the two on-time shortfall facts decided
    assert gate26["shortfall_n"] == 2


def test_milestone_cum_pnl_anchors_to_inception_principal(tmp_path: Path, monkeypatch) -> None:
    """AS-M126-03 (t_0be71b95 rev2): the cumulative-PnL gate is anchored to
    the 1.0 inception principal — the weekly cum_return contract — so a
    first-day loss (first mtm != 1.0) cannot be rebased away. With first mtm
    0.99 and last 0.895 the frozen -10% gate reads -10.5% (FAIL); the rev1
    first-mark rebasing read -9.596% (PASS) and hid the loss. The DD gate's
    own footing stays the reviewed t_dddae39e baseline (peak starts at the
    day-0 mark on both sides); the inception-to-day-0 move is cum's job."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 0.99, 83: 0.895},
        # benchmark falls 17%: the DD ratio (0.0960/0.17 ≈ 0.56) passes so the
        # cum gate is the ONLY failing criterion in the recorded verdict
        bench_marks={0: 1.0, 20: 0.83, 83: 0.85},
        shortfalls={offset: 0.0 for offset in (0, 83)},
    )
    out = pl.weekly_summary(data_dir, window_end)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    cum = j12["criteria"]["cum_pnl_min"]
    assert cum["value"] == pytest.approx(-0.105, rel=1e-12)
    assert cum["pass"] is False
    assert cum["value_basis"] == "inception_principal_1.0"
    assert cum["window_base_equity"] == 1.0
    assert cum["window_end_equity"] == pytest.approx(0.895, rel=1e-12)
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["cum_pnl_min"]
    assert j12["criteria"]["dd_vs_benchmark"]["pass"] is True
    # same contract as the weekly row's cum_return (equity - 1.0)
    assert out["books"][pl.S01_BOOK]["cum_return"] == pytest.approx(-0.105, rel=1e-12)


def test_milestone_12w_switches_per_asset_no_cross_asset_offset(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-04 (t_0be71b95 rev2): the switches gate aggregates PER-ASSET
    verdicts, so one asset's excess reversals cannot pass on another asset's
    quiet headroom. BTC flips 6 times (its own 2x cap is 2.0 * 8.8 * 84/365 ≈
    4.05 — over the design §2-3 warning at 5), ETH stays silent: the rev1
    combined expectation (8.8 + 6.7)/yr would read 6 <= 7.13 and PASS."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    flip_offsets = {7, 21, 35, 49, 63, 77}  # 6 BTC flips, ETH never flips
    for offset in range(84):
        day = start + timedelta(days=offset)
        for asset in ("BTC", "ETH"):
            regime = "RISK_ON"
            prev = regime
            if asset == "BTC" and offset in flip_offsets:
                regime = "RISK_OFF"
                prev = "RISK_ON"
            elif asset == "BTC" and offset - 1 in flip_offsets and offset > 0:
                regime, prev = "RISK_OFF", "RISK_OFF"  # stay flipped until the next flip
            _long_milestone_row(
                root,
                day,
                event="decision",
                book=pl.S01_BOOK,
                decision_id=f"A:{asset}:{day.isoformat()}",
                decision_date=day.isoformat(),
                asset=asset,
                regime_state=regime,
                prev_regime_state=prev,
                backfill=False,
            )
        _long_milestone_row(root, day, event="run", job="daily", phase="end", ok=True)
    for offset, equity in {0: 1.0, 83: 1.01}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=0.0,
            peak_equity=1.0,
        )
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=0.85 if offset else 1.0,
            cash=0.0,
            drawdown=-0.15,
            peak_equity=1.0,
        )
    out = pl.weekly_summary(tmp_path / "data", start + timedelta(days=84))
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["switches_multiple"]
    per_asset = j12["criteria"]["switches_multiple"]["per_asset"]
    assert per_asset["BTC"]["observed"] == 6
    assert per_asset["BTC"]["max"] == pytest.approx(2.0 * 8.8 * 84 / 365, rel=1e-12)
    assert per_asset["BTC"]["pass"] is False  # 6 > 4.05: BTC's OWN cap
    assert per_asset["ETH"]["pass"] is True  # quiet, but it cannot offset BTC
    assert j12["criteria"]["switches_multiple"]["value"] == 6


def test_milestone_26w_shortfall_late_marks_arrive_via_real_writer(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-05 (t_0be71b95 rev2): the recovery path is the REAL writer.
    ``mark_up`` stamps each mark with the measurement time (2026-07-03, AFTER
    the 2026-07-02 deadline) and never backdates; the 26-week gate attributes
    marks by the execution date of the decision they measure, so the two late
    measurements of in-window executions complete the sample (n=1 -> n=3) and
    the next weekly run records the judgment. Marks of out-of-window
    executions stay out; the rev1 recorded_at filter kept the gate at n=1
    forever no matter how often the honest marking job retried."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    deadline = start + timedelta(days=182)  # 2026-07-02
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    marked_early = 0  # the day-0 decision is measured on time (day 1)
    for offset in (0, 80, 81, 183):  # 183 executes OUTSIDE the frozen window
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            weight_before=0.0,
            weight_after=1.0,
            cash_after=0.0,
            equity_at_fill=1.0,
        )
        if offset == 0:
            _long_milestone_row(
                root,
                start + timedelta(days=1),
                event="mark",
                book=pl.S01_BOOK,
                decision_id=did,
                asset="BTC",
                shortfall_bps=-100.0,
                recompute_match=True,
                turnover=1.0,
            )
            marked_early = 1
    for offset, equity in {0: 1.0, 181: 0.97}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=0.8 if offset else 1.0,
            cash=0.0,
            drawdown=-0.2,
            peak_equity=1.0,
        )
    assert marked_early == 1

    # at the deadline: the in-window sample is still n=1 -> defer, no record
    out_deadline = pl.weekly_summary(tmp_path / "data", deadline)
    assert 26 not in out_deadline["milestone"][pl.S01_BOOK]["weeks"]

    # the REAL writer measures the day-80/81 executions honestly late: the
    # execution-day bars landed after the deadline, recorded_at is NOW
    bars = {start + timedelta(days=d): {"open": 100.0} for d in (0, 80, 81, 183)}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    monkeypatch.setattr(pl, "_engine_target", lambda *a, **k: {"BTC": 1.0, "ETH": 0.0})
    late_time = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)  # deadline + 1 day
    written = pl.mark_up(root, late_time)
    assert written == 3  # decisions of day 80, 81 and 183 get measured
    marks = [row for row in pl._load_journal(root) if row.get("event") == "mark"]
    assert len(marks) == 4  # 1 on-time + 3 honest late marks
    late = [row for row in marks if str(row["recorded_at"])[:10] == "2026-07-03"]
    assert len(late) == 3
    # recorded_at keeps the honest measurement time (deadline + 1 day) for
    # every late mark — no backdating, the journal is append-only truth
    assert all(str(row["recorded_at"]).startswith("2026-07-03") for row in late)
    late_decision_days = sorted(str(row["decision_id"])[-10:] for row in late)
    assert late_decision_days == ["2026-03-22", "2026-03-23", "2026-07-03"]

    # the next weekly run judges the SAME frozen window: the two late marks
    # of in-window executions count (n=3), the out-of-window one does not
    out_recovered = pl.weekly_summary(tmp_path / "data", start + timedelta(days=189))
    j26 = out_recovered["milestone"][pl.S01_BOOK]["weeks"][26]
    gate = j26["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate["shortfall_n"] == 3
    assert gate["late_recorded"] == 2  # provenance: measured after the window
    assert gate["shortfall_n_excluded"] == 0
    assert gate["value"] == pytest.approx(-100.0, rel=1e-12)  # SIGNED bound
    assert gate["pass"] is True
    assert j26["verdict"] == "PASS"
    assert j26["criteria"]["dd_vs_benchmark"]["pass"] is True
    assert set(j26["criteria"]) == set(pl.MILESTONE_RULES[26])  # every frozen gate
    assert j26["milestone_date"] == deadline.isoformat()  # the frozen window
    assert j26["judged_at_week_end"] == (start + timedelta(days=189)).isoformat()


def _26w_completeness_journal(root: Path, *, start: date, measured: tuple[int, ...]) -> None:
    """A 26-week window with three in-window executions (days 0/80/81),
    shortfall marks for the ``measured`` days only, and full-window equity
    marks on both sides (dd ratio 0.15, judgeable)."""

    for offset in (0, 80, 81):
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            weight_before=0.0,
            weight_after=1.0,
            cash_after=0.0,
            equity_at_fill=1.0,
        )
        if offset in measured:
            _long_milestone_row(
                root,
                day + timedelta(days=1),
                event="mark",
                book=pl.S01_BOOK,
                decision_id=did,
                asset="BTC",
                shortfall_bps=-100.0,
                recompute_match=True,
                turnover=1.0,
            )
    for offset, equity in {0: 1.0, 181: 0.97}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=0.8 if offset else 1.0,
            cash=0.0,
            drawdown=-0.2 if offset else 0.0,
            peak_equity=1.0,
        )


def test_milestone_26w_holds_while_a_target_fill_is_unmeasured(tmp_path: Path, monkeypatch) -> None:
    """AS-M126-06 (t_0be71b95 rev3): ``n >= 2`` is the bound's minimum
    sample, not proof that the window's executions are all measured. Three
    in-window fills with two marks (-100 bps each) give a deciding bound of
    -100 <= 15 — a PASS written from a partial sample at the deadline, which
    the third fill's honest late measurement (+1000 bps -> bound ~869.8 bps)
    could never correct (already_judged dedupes). The gate now matches the
    window's fill set against the marks: while a target fill is unmeasured
    the judgment records NOTHING (never deduped) and the criterion carries
    the measured/unmeasured/unusable completeness."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    deadline = start + timedelta(days=182)  # window's last day: day 181
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _26w_completeness_journal(root, start=start, measured=(0, 80))

    out = pl.weekly_summary(tmp_path / "data", deadline)
    assert 26 not in out["milestone"][pl.S01_BOOK]["weeks"]
    gate = pl._milestone_shortfall_criterion(
        pl._load_journal(root),
        pl.S01_BOOK,
        start,
        deadline,
        float(pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"]),
    )
    assert gate["value"] == pytest.approx(-100.0, rel=1e-12)  # the partial bound: a fact
    assert gate["pass"] is None
    assert gate["reason"] == "shortfall_unmeasured_fills"
    assert gate["completeness"]["attribution"] == "decision_execution_date"
    assert gate["completeness"]["target_fills"] == 3
    assert gate["completeness"]["measured"] == 2
    assert gate["completeness"]["unmeasured"] == [
        f"A:BTC:{(start + timedelta(days=81)).isoformat()}"
    ]
    assert gate["completeness"]["unusable"] == []
    # the deferral is not a record: a fact-less retry re-judges, never dedupes
    out_retry = pl.weekly_summary(tmp_path / "data", deadline + timedelta(days=7))
    milestone_retry = out_retry.get("milestone") or {}
    assert 26 not in milestone_retry.get(pl.S01_BOOK, {}).get("weeks", {})
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 26)


def test_milestone_26w_unmeasured_fill_completes_via_real_writer_then_judges_once(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-06 recovery: the missing measurement arrives through the REAL
    ``mark_up`` writer — the execution day's bar landed the day after the
    deadline, so the shortfall is measured NOW with an honest recorded_at
    (never backdated). The SAME frozen window is then judged exactly ONCE,
    on the FULL sample: three signed shortfalls (-100, -100, +1000 bps) give
    a one-sided 95% upper bound of ~869.797 bps > 15 -> FAIL. No later
    weekly run writes a second record for (book, 26)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    deadline = start + timedelta(days=182)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _26w_completeness_journal(root, start=start, measured=(0, 80))

    # the real writer measures the day-81 execution honestly late
    bars = {start + timedelta(days=d): {"open": 90.0 if d == 81 else 100.0} for d in (0, 80, 81)}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    monkeypatch.setattr(pl, "_engine_target", lambda *a, **k: {"BTC": 1.0, "ETH": 0.0})
    late_time = datetime.combine(start + timedelta(days=183), datetime.min.time(), UTC)
    assert pl.mark_up(root, late_time) == 1  # decisions 0/80 are already measured
    marks = [row for row in pl._load_journal(root) if row.get("event") == "mark"]
    assert len(marks) == 3
    assert str(marks[-1]["recorded_at"]).startswith((start + timedelta(days=183)).isoformat())
    assert marks[-1]["decision_id"] == f"A:BTC:{(start + timedelta(days=81)).isoformat()}"

    out = pl.weekly_summary(tmp_path / "data", start + timedelta(days=189))
    j26 = out["milestone"][pl.S01_BOOK]["weeks"][26]
    gate = j26["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate["completeness"]["target_fills"] == 3
    assert gate["completeness"]["measured"] == 3
    assert gate["completeness"]["unmeasured"] == []
    assert gate["completeness"]["unusable"] == []
    assert gate["shortfall_n"] == 3
    assert gate["late_recorded"] == 1  # only the day-81 measurement is post-window
    assert gate["value"] == pytest.approx(869.7966666666675, rel=1e-9)
    assert gate["pass"] is False
    assert j26["verdict"] == "FAIL"
    assert j26["failed_criteria"] == ["shortfall_bps_mean_upper95_max"]
    assert j26["milestone_date"] == deadline.isoformat()  # the SAME frozen window
    assert j26["judged_at_week_end"] == (start + timedelta(days=189)).isoformat()
    # exactly one record for (book, 26) — ever
    assert (
        pl.weekly_summary(tmp_path / "data", start + timedelta(days=196)).get("milestone") is None
    )
    recorded_rows = [
        row
        for row in pl._rows_from_jsonl(pl.summary_path(root), strict=True)
        if isinstance(row.get("milestone"), dict)
        and isinstance(row["milestone"].get(pl.S01_BOOK), dict)
        and (
            26 in row["milestone"][pl.S01_BOOK].get("weeks", {})
            or "26" in row["milestone"][pl.S01_BOOK].get("weeks", {})
        )
    ]
    assert len(recorded_rows) == 1


def test_milestone_26w_pass_control_when_every_target_fill_is_measured(
    tmp_path: Path, monkeypatch
) -> None:
    """Control for AS-M126-06: completeness holds back only UNMEASURED facts.
    The same window with all three fills measured on time records its PASS
    at the deadline exactly once — an honest complete sample must never turn
    into a permanent deferral."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    deadline = start + timedelta(days=182)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _26w_completeness_journal(root, start=start, measured=(0, 80, 81))

    out = pl.weekly_summary(tmp_path / "data", deadline)
    j26 = out["milestone"][pl.S01_BOOK]["weeks"][26]
    gate = j26["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate["completeness"]["target_fills"] == 3
    assert gate["completeness"]["measured"] == 3
    assert gate["completeness"]["unmeasured"] == []
    assert gate["completeness"]["unusable"] == []
    assert gate["shortfall_n"] == 3
    assert gate["late_recorded"] == 0  # every measurement landed inside the window
    assert gate["value"] == pytest.approx(-100.0, rel=1e-12)  # SIGNED bound
    assert gate["pass"] is True
    assert j26["verdict"] == "PASS"
    assert j26["milestone_date"] == deadline.isoformat()
    assert (
        pl.weekly_summary(tmp_path / "data", deadline + timedelta(days=7)).get("milestone") is None
    )


def test_run_daily_mtm_recovers_an_unpriced_milestone_window_end_12w(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-02-R2 (t_0be71b95 rev3): the unpriced window-end mark is
    recovered by the REAL writer. ``run_daily_mtm`` writes ``mtm_day =
    now.date()`` only, so the operational loop never produced the missing
    day again and the frozen 12-week window stayed unjudgeable forever (the
    rev2 regression recovered with a hand-written row). Here the real writer
    records the day as unpriced (no bar for the holding), the bars land
    later, and the NEXT honest daily run — the very call the cron makes —
    re-prices that day AS OF THAT DAY (the day-0 fill's units priced with
    that day's own finalized bar) and stamps it with the recovering run's
    recorded_at. The judgment then records the SAME frozen window once."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)  # the 12-week deadline
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 7: 0.97},  # day 83: the mark that never happened
        bench_marks={0: 1.0, 7: 0.99, 83: 0.8},
        shortfalls={},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    # --- the operational failure: the writer runs on the window's last day
    # with no bar for the holding -> the real mtm_unpriced row, no judgment ---
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(last_day)) == 3  # A unpriced; 3 books mark
    unpriced = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
    ]
    assert [row["mtm_date"] for row in unpriced] == [last_day.isoformat()]
    assert unpriced[0]["unpriced_assets"] == ["BTC"]
    deadline_out = pl.weekly_summary(tmp_path / "data", window_end)
    weeks = deadline_out["milestone"][pl.S01_BOOK]["weeks"]
    assert 12 not in weeks and 4 in weeks  # the gate holds; 4w keeps its own record
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 12)

    # --- the bars land later; the next honest daily run recovers the day ----
    bars = {
        start + timedelta(days=82): {"open": 85.0, "close": 85.0},  # prices day 83
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},  # prices day 91
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    recovery_day = start + timedelta(days=91)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6  # A: recovered + today
    recovered = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    ]
    assert len(recovered) == 1
    row = recovered[0]
    assert row["recovered_from_unpriced"] is True
    # honest recorded_at: the RECOVERING run's time — never the target day
    assert str(row["recorded_at"]).startswith(recovery_day.isoformat())
    assert row["equity"] == pytest.approx(0.01 * 85.0, rel=1e-12)
    assert row["drawdown"] == pytest.approx(-0.15, rel=1e-12)
    assert row["peak_equity"] == pytest.approx(1.0, rel=1e-12)
    assert row["price_bar_date"] == (start + timedelta(days=82)).isoformat()
    # B2: a later run neither re-recovers the day nor duplicates anything
    assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=92))) == 5

    # --- the SAME frozen window is now judgeable — and judged exactly once ---
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is False
    assert dd["value"] == pytest.approx(0.75, rel=1e-12)  # the honest numbers decide
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["milestone_date"] == window_end.isoformat()
    assert j12["judged_at_week_end"] == recovery_day.isoformat()
    assert pl.weekly_summary(tmp_path / "data", start + timedelta(days=98)).get("milestone") is None


def test_run_daily_mtm_recovers_an_unpriced_milestone_window_end_26w(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-02-R2 on the 26-week window, composed with AS-M126-06: the
    real writer records the window's last day unpriced AND the day-81
    execution is still unmeasured — the judgment holds for BOTH reasons
    (nothing recorded). The bars land, the next honest daily run re-prices
    the past day AS OF THAT DAY, the real ``mark_up`` measures the missing
    execution with an honest post-deadline recorded_at, and the SAME frozen
    window is then judged exactly once — FAIL on the full evidence (dd ratio
    0.75, signed shortfall bound ~869.797 bps > 15)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=182)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    for offset in (0, 80, 81):
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            cash_after=0.0,
        )
        if offset in (0, 80):
            _long_milestone_row(
                root,
                day + timedelta(days=1),
                event="mark",
                book=pl.S01_BOOK,
                decision_id=did,
                asset="BTC",
                shortfall_bps=-100.0,
                recompute_match=True,
                turnover=1.0,
            )
    for offset, equity in {0: 1.0, 7: 0.97}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
    for offset, equity in {0: 1.0, 7: 0.99, 181: 0.8}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )

    # --- at the deadline: an unpriced window-end AND an unmeasured fill -----
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(last_day)) == 3
    out_deadline = pl.weekly_summary(tmp_path / "data", window_end)
    assert 26 not in out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    rows = pl._load_journal(root)
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["dd_vs_benchmark"]),
        shallow_bench_hold=False,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "book_no_mark_at_window_end"
    gate = pl._milestone_shortfall_criterion(
        rows,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"]),
    )
    assert gate["pass"] is None
    assert gate["reason"] == "shortfall_unmeasured_fills"
    assert gate["completeness"]["unmeasured"] == [
        f"A:BTC:{(start + timedelta(days=81)).isoformat()}"
    ]

    # --- recovery: one honest daily run + one honest mark_up ----------------
    bars = {
        start + timedelta(days=0): {"open": 100.0, "close": 100.0},
        start + timedelta(days=80): {"open": 100.0, "close": 100.0},
        start + timedelta(days=81): {"open": 90.0, "close": 90.0},  # the execution bar
        start + timedelta(days=180): {"open": 85.0, "close": 85.0},  # prices day 181
        start + timedelta(days=188): {"open": 85.0, "close": 85.0},  # prices day 189
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    monkeypatch.setattr(pl, "_engine_target", lambda *a, **k: {"BTC": 1.0, "ETH": 0.0})
    recovery_day = start + timedelta(days=189)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6
    assert pl.mark_up(root, datetime.combine(recovery_day, datetime.min.time(), UTC)) == 1

    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j26 = out["milestone"][pl.S01_BOOK]["weeks"][26]
    assert j26["verdict"] == "FAIL"
    assert j26["milestone_date"] == window_end.isoformat()
    assert j26["judged_at_week_end"] == recovery_day.isoformat()
    gate = j26["criteria"]["shortfall_bps_mean_upper95_max"]
    assert gate["completeness"]["measured"] == 3
    assert gate["completeness"]["unmeasured"] == []
    assert gate["shortfall_n"] == 3
    assert gate["late_recorded"] == 1
    assert gate["value"] == pytest.approx(869.7966666666675, rel=1e-9)
    assert gate["pass"] is False
    dd26 = j26["criteria"]["dd_vs_benchmark"]
    assert dd26["value"] == pytest.approx(0.75, rel=1e-12)
    assert dd26["pass"] is False
    assert (
        pl.weekly_summary(tmp_path / "data", start + timedelta(days=196)).get("milestone") is None
    )


def test_run_daily_mtm_recovery_values_the_failed_snapshot_not_same_day_later_fills(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-02-R3 (t_0be71b95 rev4): the recovery values the FAILED
    evaluation's own snapshot, never the end-of-calendar-day holdings.

    The real writer records the window's last day unpriced at 00:00 (holdings
    BTC 0.01 / cash 0); a decision at 01:00 resolves through the REAL fill
    path at 03:00 into a half sell (BTC 0.005 / cash 0.49925). The recovered
    mark must price the 00:00 snapshot at the previous close — equity 0.85,
    FAIL — not the afternoon holdings (0.92425, a fabricated PASS). The
    no-outage control runs the identical MTM-before-sell day with the price
    available: the 00:00 mark is 0.85 and the later sell does not rewrite it
    — the recovery must land on the SAME number, and the sale's effect first
    shows in the NEXT day's mark on both paths.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    def same_day_sell(journal_root: Path, day: date) -> None:
        """A real pending decision at 01:00 resolved by the real fill path."""
        _long_milestone_row(
            journal_root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            asset="BTC",
            decision_id=f"A:BTC:{day.isoformat()}",
            decision_date=day.isoformat(),
            recorded_at=f"{day.isoformat()}T01:00:00+00:00",
            backfill=False,
            fill_status="pending",
            side="sell",
            delta_weight=-0.5,
            target_weight=0.5,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
        )
        assert pl.resolve_fills(
            journal_root,
            datetime.combine(day, datetime.min.time(), UTC).replace(hour=3),
        )

    def journal_rows(data_dir: Path) -> list[dict[str, object]]:
        return pl._load_journal(pl.loop_root(data_dir))  # type: ignore[arg-type]

    # --- the failure: the real writer cannot price the window's last day ----
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(last_day)) == 3  # A unpriced; 3 books mark
    unpriced = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
    ]
    assert len(unpriced) == 1
    assert unpriced[0]["mtm_date"] == last_day.isoformat()
    assert unpriced[0]["units"] == {"BTC": 0.01}
    assert unpriced[0]["cash"] == 0.0

    # --- a REAL sell fills at 03:00, AFTER the failed 00:00 write -----------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
        m.setattr(
            pl,
            "_first_fill_candidate",
            lambda *a, **k: {
                "open_time": datetime.combine(last_day, datetime.min.time(), UTC)
                .replace(hour=2)
                .isoformat(),
                "open": 100.0,
                "close": 100.0,
            },
        )
        same_day_sell(root, last_day)
    sold = next(
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "fill" and row.get("decision_id") == f"A:BTC:{last_day.isoformat()}"
    )
    assert sold["side"] == "sell" and sold["guard_applied"] is True
    assert sold["units_after"] == pytest.approx(0.005, rel=1e-12)
    assert not any(
        row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
        for row in journal_rows(tmp_path / "data")
    )

    # --- the bars land; the next honest run recovers AT THE SNAPSHOT --------
    bars = {
        start + timedelta(days=82): {"open": 85.0, "close": 85.0},  # prices day 83
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},  # prices day 91
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    recovery_day = start + timedelta(days=91)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6  # recovered + 5 own rows
    recovered = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    ]
    assert len(recovered) == 1
    # the 00:00 snapshot priced at the previous close: 0.01 * 85 + 0.0 — NOT
    # the afternoon's 0.005 * 85 + 0.49925 = 0.92425
    assert recovered[0]["equity"] == pytest.approx(0.85, rel=1e-12)
    assert recovered[0]["units"] == {"BTC": 0.01}
    assert recovered[0]["cash"] == 0.0
    assert recovered[0]["recovered_from_unpriced"] is True
    assert str(recovered[0]["recorded_at"]).startswith(recovery_day.isoformat())
    # the sale's effect first shows in the NEXT mark (as in a normal day)
    day91 = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == recovery_day.isoformat()
    ]
    assert len(day91) == 1
    assert day91[0]["units"] == {"BTC": 0.005}
    assert day91[0]["equity"] == pytest.approx(0.005 * 85.0 + 0.49925, rel=1e-12)

    # --- the honest numbers decide: FAIL, exactly once -----------------------
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["cum_pnl_min", "dd_vs_benchmark"]
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["value"] == pytest.approx(0.75, rel=1e-12)
    assert dd["unresolved_mtm_gaps"] == []
    assert pl.weekly_summary(tmp_path / "data", start + timedelta(days=98)).get("milestone") is None

    # --- no-outage control: same day, price available from the start --------
    control_dir = tmp_path / "control" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        control_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        control_root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    assert pl.run_daily_mtm(control_root, _now_of(last_day)) == 4  # A marks at 00:00
    normal = next(
        row
        for row in journal_rows(control_dir)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    )
    assert normal["equity"] == pytest.approx(0.85, rel=1e-12)
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
        m.setattr(
            pl,
            "_first_fill_candidate",
            lambda *a, **k: {
                "open_time": datetime.combine(last_day, datetime.min.time(), UTC)
                .replace(hour=2)
                .isoformat(),
                "open": 100.0,
                "close": 100.0,
            },
        )
        same_day_sell(control_root, last_day)
    # the sell does NOT rewrite the existing 00:00 mark (B2), and the recovery
    # equivalence holds: the outage path records the SAME numbers
    control_day83 = [
        row
        for row in journal_rows(control_dir)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    ]
    assert len(control_day83) == 1 and control_day83[0]["equity"] == pytest.approx(0.85, rel=1e-12)
    assert pl.run_daily_mtm(control_root, _now_of(recovery_day)) == 5
    control_out = pl.weekly_summary(control_dir, recovery_day)
    control_j12 = control_out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert control_j12["verdict"] == "FAIL" == j12["verdict"]
    control_dd = control_j12["criteria"]["dd_vs_benchmark"]
    assert control_dd["value"] == pytest.approx(dd["value"], rel=1e-12)
    assert control_j12["criteria"]["cum_pnl_min"]["value"] == pytest.approx(
        j12["criteria"]["cum_pnl_min"]["value"], rel=1e-12
    )


def test_unresolved_interior_mtm_gap_defers_12w_until_the_writer_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-07 (t_0be71b95 rev4), 12 weeks: a KNOWN unresolved interior
    valuation hole holds the judgment — matching spans are not wholeness.

    The real writer records day 40 unpriced; at the frozen deadline the last
    day's own close IS available, so both spans end on the same day and the
    partial series would show a shallow drawdown (ratio 0.15 -> a permanent
    PASS). The gate must defer instead. When the historical day-39 close
    arrives, the normal daily writer recovers day 40 honestly (equity 0.70)
    and the SAME frozen window records its real verdict (ratio 1.5, FAIL)
    exactly once. A complete-data control records PASS through the same
    gate with no holes.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    # --- day 40: the real writer records a KNOWN hole ------------------------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=40))) == 4
    # --- the deadline: endpoints complete, interior still holed -------------
    deadline_bars = {start + timedelta(days=82): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(deadline_bars))
    assert pl.run_daily_mtm(root, _now_of(last_day)) == 4  # A marks 0.97; day 40 unrecovered
    out_deadline = pl.weekly_summary(tmp_path / "data", window_end)
    weeks = out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    assert 12 not in weeks and 4 in weeks  # the gate holds; 4w keeps its own record
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 12)
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [
        {"book": pl.S01_BOOK, "mtm_date": (start + timedelta(days=40)).isoformat()}
    ]

    # --- the late price; the NORMAL writer recovers the hole -----------------
    late_bars = {
        start + timedelta(days=39): {"open": 70.0, "close": 70.0},  # prices day 40
        start + timedelta(days=82): {"open": 97.0, "close": 97.0},
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(late_bars))
    recovery_day = start + timedelta(days=91)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6  # recovered + 5 own rows
    recovered = [
        row
        for row in pl._load_journal(root)
        if row.get("recovered_from_unpriced") and row.get("book") == pl.S01_BOOK
    ]
    assert len(recovered) == 1
    assert recovered[0]["mtm_date"] == (start + timedelta(days=40)).isoformat()
    assert recovered[0]["equity"] == pytest.approx(0.70, rel=1e-12)

    # --- the SAME frozen window records its real verdict, exactly once -------
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["dd_vs_benchmark"]
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["value"] == pytest.approx(1.5, rel=1e-12)
    assert dd["unresolved_mtm_gaps"] == []
    assert j12["milestone_date"] == window_end.isoformat()
    assert j12["judged_at_week_end"] == recovery_day.isoformat()
    assert pl.weekly_summary(tmp_path / "data", start + timedelta(days=98)).get("milestone") is None

    # --- complete-data control: no holes, the gate records PASS -------------
    control_dir = tmp_path / "control" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        control_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 83: 0.97},
        bench_marks={0: 1.0, 83: 0.8},
    )
    control_out = pl.weekly_summary(control_dir, window_end)
    control_j12 = control_out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert control_j12["verdict"] == "PASS"
    control_dd = control_j12["criteria"]["dd_vs_benchmark"]
    assert control_dd["pass"] is True
    assert control_dd["unresolved_mtm_gaps"] == []
    assert pl.weekly_summary(control_dir, start + timedelta(days=91)).get("milestone") is None


def test_unresolved_interior_mtm_gap_defers_26w_until_the_writer_recovers(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-07 (t_0be71b95 rev4), 26 weeks: the same interior-hole hold on
    the half-year window, composed with a DECIDED shortfall gate — the drawdown
    hold alone must keep the judgment unrecorded, and the honest recovery must
    complete the very same frozen window exactly once (ratio 1.5, FAIL). A
    complete-data control records PASS through the same gate.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=182)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    for offset in (0, 80, 81):
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            cash_after=0.0,
        )
        _long_milestone_row(
            root,
            day + timedelta(days=1),
            event="mark",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            shortfall_bps=-100.0,
            recompute_match=True,
            turnover=1.0,
        )
    for offset, equity in {0: 1.0}.items():
        _long_milestone_row(
            root,
            start + timedelta(days=offset),
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=(start + timedelta(days=offset)).isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
    for offset, equity in {0: 1.0, 181: 0.8}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )

    # --- the hole, then a deadline whose endpoints complete anyway -----------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=40))) == 4
    deadline_bars = {start + timedelta(days=180): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(deadline_bars))
    assert pl.run_daily_mtm(root, _now_of(last_day)) == 4
    # the cost gate is fully decided — the drawdown hole alone holds the record
    gate = pl._milestone_shortfall_criterion(
        pl._load_journal(root),
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"]),
    )
    assert gate["pass"] is True
    out_deadline = pl.weekly_summary(tmp_path / "data", window_end)
    weeks = out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    assert 26 not in weeks and 4 in weeks
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["dd_vs_benchmark"]),
        shallow_bench_hold=False,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [
        {"book": pl.S01_BOOK, "mtm_date": (start + timedelta(days=40)).isoformat()}
    ]
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 26)

    # --- the late price; the NORMAL writer completes the same window ---------
    late_bars = {
        start + timedelta(days=39): {"open": 70.0, "close": 70.0},
        start + timedelta(days=180): {"open": 97.0, "close": 97.0},
        start + timedelta(days=188): {"open": 85.0, "close": 85.0},
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(late_bars))
    recovery_day = start + timedelta(days=189)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6
    recovered = [
        row
        for row in pl._load_journal(root)
        if row.get("recovered_from_unpriced") and row.get("book") == pl.S01_BOOK
    ]
    assert len(recovered) == 1
    assert recovered[0]["equity"] == pytest.approx(0.70, rel=1e-12)
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j26 = out["milestone"][pl.S01_BOOK]["weeks"][26]
    assert j26["verdict"] == "FAIL"
    assert j26["failed_criteria"] == ["dd_vs_benchmark"]
    assert j26["criteria"]["dd_vs_benchmark"]["value"] == pytest.approx(1.5, rel=1e-12)
    assert j26["criteria"]["dd_vs_benchmark"]["unresolved_mtm_gaps"] == []
    assert j26["criteria"]["shortfall_bps_mean_upper95_max"]["pass"] is True
    assert j26["milestone_date"] == window_end.isoformat()
    assert j26["judged_at_week_end"] == recovery_day.isoformat()
    assert (
        pl.weekly_summary(tmp_path / "data", start + timedelta(days=196)).get("milestone") is None
    )

    # --- complete-data control -----------------------------------------------
    control_dir = tmp_path / "control" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    for offset in (0, 80, 81):
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            control_root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            control_root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            cash_after=0.0,
        )
        _long_milestone_row(
            control_root,
            day + timedelta(days=1),
            event="mark",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            shortfall_bps=-100.0,
            recompute_match=True,
            turnover=1.0,
        )
    for offset, equity in {0: 1.0, 181: 0.97}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            control_root,
            day,
            event="mtm",
            book=pl.S01_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
    for offset, equity in {0: 1.0, 181: 0.8}.items():
        day = start + timedelta(days=offset)
        _long_milestone_row(
            control_root,
            day,
            event="mtm",
            book=pl.BENCH_CRYPTO_BOOK,
            mtm_date=day.isoformat(),
            equity=equity,
            cash=0.0,
            drawdown=equity - 1.0,
            peak_equity=1.0,
        )
    control_out = pl.weekly_summary(control_dir, window_end)
    control_j26 = control_out["milestone"][pl.S01_BOOK]["weeks"][26]
    assert control_j26["verdict"] == "PASS"
    assert control_j26["criteria"]["dd_vs_benchmark"]["pass"] is True
    assert control_j26["criteria"]["dd_vs_benchmark"]["unresolved_mtm_gaps"] == []
    assert pl.weekly_summary(control_dir, start + timedelta(days=189)).get("milestone") is None


def test_unpriced_snapshot_refusal_keeps_the_window_unresolved(tmp_path: Path, monkeypatch) -> None:
    """AS-M126-02-R3's integrity refusal (t_0be71b95 rev4): a snapshot the
    journal prefix cannot reproduce means the journal describes no single
    state boundary — the recovery refuses instead of picking a side, and the
    AS-M126-07 hole-hold keeps the judgment unrecorded (fail-closed on both
    layers; nothing is guessed between the two disagreeing holdings).
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )
    # a corrupted/tampered snapshot: the prefix replay says BTC 0.01 / cash 0
    tampered_day = start + timedelta(days=40)
    _long_milestone_row(
        root,
        tampered_day,
        event="mtm_unpriced",
        book=pl.S01_BOOK,
        mtm_date=tampered_day.isoformat(),
        reason="unpriced_positions",
        unpriced_assets=["BTC"],
        units={"BTC": 999.0},
        cash=0.0,
    )
    # every price the honest snapshot needs is now available — and still the
    # recovery must refuse: the disagreement is unresolvable from the journal
    bars = {
        start + timedelta(days=39): {"open": 85.0, "close": 85.0},
        start + timedelta(days=82): {"open": 97.0, "close": 97.0},
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},
    }
    deadline_bars = {start + timedelta(days=82): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(deadline_bars))
    last_day = window_end - timedelta(days=1)
    assert pl.run_daily_mtm(root, _now_of(last_day)) == 4  # A marks 0.97; hole refuses
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    recovery_day = start + timedelta(days=91)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 5  # still NO recovered row
    assert not any(row.get("recovered_from_unpriced") for row in pl._load_journal(root))
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [
        {"book": pl.S01_BOOK, "mtm_date": tampered_day.isoformat()}
    ]
    assert (
        pl.weekly_summary(tmp_path / "data", recovery_day)["milestone"][pl.S01_BOOK]["weeks"].get(
            12
        )
        is None
    )


def test_same_day_price_restoration_completes_the_failed_snapshot_not_current_holdings(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-02-R4 (t_0be71b95 rev5): a SAME-DAY second run after prices
    arrive completes the FAILED evaluation's own snapshot, never the current
    holdings.

    The audit repro: the real writer records the window's last day unpriced
    at 00:00 (BTC 0.01 / cash 0), a 01:00 decision resolves through the REAL
    fill path at 03:00 into a half sell (BTC 0.005 / cash 0.49925), and the
    previous close (85) lands at 06:00 the SAME day. The ordinary day pass
    valued the CURRENT holdings — equity 0.92425, a recorded 12-week PASS —
    while the no-outage control marked 0.85 (FAIL) at 00:00 and let the sale
    first show in the NEXT mark: the arrival date of a price was choosing
    which snapshot gets priced. The same-day completion must write 0.85 with
    the recovering run's honest recorded_at, exactly once (B2), and the
    weekly judgment must equal the control's FAIL.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    def same_day_sell(journal_root: Path, day: date) -> None:
        """A real pending decision at 01:00 resolved by the real fill path."""
        _long_milestone_row(
            journal_root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            asset="BTC",
            decision_id=f"A:BTC:{day.isoformat()}",
            decision_date=day.isoformat(),
            recorded_at=f"{day.isoformat()}T01:00:00+00:00",
            backfill=False,
            fill_status="pending",
            side="sell",
            delta_weight=-0.5,
            target_weight=0.5,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
        )
        assert pl.resolve_fills(
            journal_root,
            datetime.combine(day, datetime.min.time(), UTC).replace(hour=3),
        )

    def journal_rows(data_dir: Path) -> list[dict[str, object]]:
        return pl._load_journal(pl.loop_root(data_dir))  # type: ignore[arg-type]

    # --- the failure: the real writer cannot price the window's last day ----
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(last_day)) == 3  # A unpriced; 3 books mark
    unpriced = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
    ]
    assert len(unpriced) == 1
    assert unpriced[0]["units"] == {"BTC": 0.01}
    assert unpriced[0]["cash"] == 0.0

    # --- a REAL sell fills at 03:00, AFTER the failed 00:00 write -----------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
        m.setattr(
            pl,
            "_first_fill_candidate",
            lambda *a, **k: {
                "open_time": datetime.combine(last_day, datetime.min.time(), UTC)
                .replace(hour=2)
                .isoformat(),
                "open": 100.0,
                "close": 100.0,
            },
        )
        same_day_sell(root, last_day)
    sold = next(
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "fill" and row.get("decision_id") == f"A:BTC:{last_day.isoformat()}"
    )
    assert sold["side"] == "sell" and sold["units_after"] == pytest.approx(0.005, rel=1e-12)

    # --- prices land at 06:00 the SAME day: the re-run must complete the ----
    # --- FAILED SNAPSHOT (0.85), not the afternoon holdings (0.92425) -------
    bars = {
        start + timedelta(days=82): {"open": 85.0, "close": 85.0},  # prices day 83
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},  # prices day 91
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=360)) == 1  # only A's recovery
    day_marks = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    ]
    assert len(day_marks) == 1  # B2: exactly one mark for the day
    recovered = day_marks[0]
    assert recovered["equity"] == pytest.approx(0.85, rel=1e-12)  # 0.01 * 85, NOT 0.92425
    assert recovered["units"] == {"BTC": 0.01}
    assert recovered["cash"] == 0.0
    assert recovered["recovered_from_unpriced"] is True
    # honest recorded_at: THIS run's wall time on the SAME day — never backdated
    assert str(recovered["recorded_at"]).startswith(last_day.isoformat())
    # the failed write's own record still stands beside the mark (append-only)
    unpriced_after = [
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
    ]
    assert len(unpriced_after) == 1
    # a third same-day run writes nothing (B2)
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=420)) == 0
    # the sale's effect first shows in the NEXT mark (as on a no-outage day)
    assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=91))) == 5
    day91 = next(
        row
        for row in journal_rows(tmp_path / "data")
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == (start + timedelta(days=91)).isoformat()
    )
    assert day91["units"] == {"BTC": 0.005}
    assert day91["equity"] == pytest.approx(0.005 * 85.0 + 0.49925, rel=1e-12)

    # --- the honest numbers decide: FAIL, identical to the no-outage control -
    out = pl.weekly_summary(tmp_path / "data", window_end)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["cum_pnl_min", "dd_vs_benchmark"]
    dd = j12["criteria"]["dd_vs_benchmark"]
    assert dd["value"] == pytest.approx(0.75, rel=1e-12)
    assert dd["unresolved_mtm_gaps"] == []

    control_dir = tmp_path / "control" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        control_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0},
        bench_marks={0: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        control_root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )
    assert pl.run_daily_mtm(control_root, _now_of(last_day)) == 4  # A marks 0.85 at 00:00
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
        m.setattr(
            pl,
            "_first_fill_candidate",
            lambda *a, **k: {
                "open_time": datetime.combine(last_day, datetime.min.time(), UTC)
                .replace(hour=2)
                .isoformat(),
                "open": 100.0,
                "close": 100.0,
            },
        )
        same_day_sell(control_root, last_day)
    # the sell does NOT rewrite the 00:00 mark (B2)
    control_day83 = [
        row
        for row in journal_rows(control_dir)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == last_day.isoformat()
    ]
    assert len(control_day83) == 1 and control_day83[0]["equity"] == pytest.approx(0.85, rel=1e-12)
    assert pl.run_daily_mtm(control_root, _now_of(start + timedelta(days=91))) == 5
    control_out = pl.weekly_summary(control_dir, window_end)
    control_j12 = control_out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert control_j12["verdict"] == "FAIL" == j12["verdict"]
    assert control_j12["criteria"]["dd_vs_benchmark"]["value"] == pytest.approx(
        dd["value"], rel=1e-12
    )
    assert control_j12["criteria"]["cum_pnl_min"]["value"] == pytest.approx(
        j12["criteria"]["cum_pnl_min"]["value"], rel=1e-12
    )


def test_prebaseline_unresolved_gap_holds_the_window_until_the_writer_recovers_12w(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-07-R4 (t_0be71b95 rev5), 12 weeks: a KNOWN unresolved hole
    BEFORE the provisional shared origin holds the judgment — the origin is
    not a safe lower bound, because recovering the hole can move the first
    common mark itself.

    The audit repro: the real writer records day 0 unpriced (the book's very
    first valuation), day 7 and the window end mark honestly. At the frozen
    deadline both endpoints exist and the provisional origin is day 7 — the
    rev4 scan (origin..end) saw no hole and recorded a PASS off ratio ~0.
    When the historical day-1 close arrives and the real writer recovers
    day 0 (equity 1.2), the origin moves to day 0 and the SAME frozen window
    is a FAIL (ratio .958 > .6) — but the PASS was already deduped. The gate
    must defer while the day-0 hole is open, then judge the window exactly
    once on the completed series.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={},  # the book's own marks all come from the real writer
        bench_marks={0: 1.0, 7: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"A:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    # --- day 0: the real writer records a KNOWN hole at the window's start --
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(start)) == 3
    unpriced = [
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
    ]
    assert [row["mtm_date"] for row in unpriced] == [start.isoformat()]
    assert unpriced[0]["units"] == {"BTC": 0.01} and unpriced[0]["cash"] == 0.0

    # --- days 7 and 83 mark honestly; day 0 stays a hole ---------------------
    day7_bars = {start + timedelta(days=6): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(day7_bars))
    assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=7))) == 4
    deadline_bars = dict(day7_bars)
    deadline_bars[start + timedelta(days=82)] = {"open": 97.0, "close": 97.0}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(deadline_bars))
    assert pl.run_daily_mtm(root, _now_of(last_day)) == 4  # recovery of day 0 still impossible
    book_marks = {
        row["mtm_date"]: row["equity"]
        for row in pl._load_journal(root)
        if row.get("event") == "mtm" and row.get("book") == pl.S01_BOOK
    }
    assert set(book_marks) == {(start + timedelta(days=7)).isoformat(), last_day.isoformat()}

    # --- the deadline: endpoints complete, the PRE-ORIGIN hole still open ----
    out_deadline = pl.weekly_summary(tmp_path / "data", window_end)
    weeks = out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    assert 12 not in weeks and 4 in weeks  # the gate holds; 4w keeps its own record
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 12)
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [{"book": pl.S01_BOOK, "mtm_date": start.isoformat()}]
    assert dd["comparison_start"] == (start + timedelta(days=7)).isoformat()  # provisional

    # --- the historical price arrives; the NORMAL writer recovers day 0 -----
    late_bars = {
        start - timedelta(days=1): {"open": 120.0, "close": 120.0},  # prices day 0
        start + timedelta(days=6): {"open": 97.0, "close": 97.0},
        start + timedelta(days=82): {"open": 97.0, "close": 97.0},
        start + timedelta(days=90): {"open": 85.0, "close": 85.0},
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(late_bars))
    recovery_day = start + timedelta(days=91)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6  # recovered + 5 own rows
    recovered = next(
        row
        for row in pl._load_journal(root)
        if row.get("recovered_from_unpriced") and row.get("book") == pl.S01_BOOK
    )
    assert recovered["mtm_date"] == start.isoformat()
    assert recovered["equity"] == pytest.approx(1.2, rel=1e-12)  # 0.01 * 120, the snapshot
    assert str(recovered["recorded_at"]).startswith(recovery_day.isoformat())

    # --- the SAME frozen window records its real verdict, exactly once ------
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is False
    assert dd["comparison_start"] == start.isoformat()  # the origin moved onto the window
    assert dd["value"] == pytest.approx((1.0 - 0.97 / 1.2) / 0.2, rel=1e-12)
    assert dd["unresolved_mtm_gaps"] == []
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "FAIL"
    assert j12["failed_criteria"] == ["dd_vs_benchmark"]
    assert j12["milestone_date"] == window_end.isoformat()
    assert j12["judged_at_week_end"] == recovery_day.isoformat()
    assert pl._milestone_already_judged(root, pl.S01_BOOK, 12)
    assert pl.weekly_summary(tmp_path / "data", start + timedelta(days=98)).get("milestone") is None


def test_prebaseline_unresolved_gap_holds_the_window_until_the_writer_recovers_26w(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-07-R4 (t_0be71b95 rev5), 26 weeks: the same pre-origin hold on
    the half-year window, composed with a DECIDED shortfall gate — the DD
    hold alone must keep the judgment unrecorded, and the honest recovery of
    the window's first day must complete the very same frozen window exactly
    once (origin day 0, ratio .958, FAIL).
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=182)
    last_day = window_end - timedelta(days=1)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    for offset in (0, 80, 81):
        day = start + timedelta(days=offset)
        did = f"A:BTC:{day.isoformat()}"
        _long_milestone_row(
            root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            decision_id=did,
            decision_date=day.isoformat(),
            asset="BTC",
            target_weight=1.0,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
            backfill=False,
        )
        _long_milestone_row(
            root,
            day,
            event="fill",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            paper_fill_price=99.0,
            side="buy",
            turnover=1.0,
            units_before=0.0,
            units_after=0.01,
            cash_after=0.0,
        )
        # every execution measured: the shortfall gate is DECIDED, so the DD
        # hold is the only thing that can (and must) block the judgment
        _long_milestone_row(
            root,
            day + timedelta(days=1),
            event="mark",
            book=pl.S01_BOOK,
            decision_id=did,
            asset="BTC",
            shortfall_bps=-100.0,
            recompute_match=True,
            turnover=1.0,
        )
    _long_milestone_journal(
        root,
        start=start,
        run_days=0,
        book_marks={},
        bench_marks={0: 1.0, 7: 1.0, 181: 0.8},
    )

    # --- day 0: the real writer records the window's first day as a hole ----
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        assert pl.run_daily_mtm(root, _now_of(start)) == 3
    # --- days 7 and 181 mark honestly ----------------------------------------
    day7_bars = {start + timedelta(days=6): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(day7_bars))
    assert pl.run_daily_mtm(root, _now_of(start + timedelta(days=7))) == 4
    deadline_bars = dict(day7_bars)
    deadline_bars[start + timedelta(days=180)] = {"open": 97.0, "close": 97.0}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(deadline_bars))
    assert pl.run_daily_mtm(root, _now_of(last_day)) == 4

    # --- the deadline: shortfall DECIDED, DD held on the pre-origin hole -----
    out_deadline = pl.weekly_summary(tmp_path / "data", window_end)
    assert 26 not in out_deadline["milestone"][pl.S01_BOOK]["weeks"]
    rows = pl._load_journal(root)
    gate = pl._milestone_shortfall_criterion(
        rows,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["shortfall_bps_mean_upper95_max"]),
    )
    assert gate["pass"] is True  # decided — not the blocker
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[26]["dd_vs_benchmark"]),
        shallow_bench_hold=False,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [{"book": pl.S01_BOOK, "mtm_date": start.isoformat()}]
    assert dd["comparison_start"] == (start + timedelta(days=7)).isoformat()
    assert not pl._milestone_already_judged(root, pl.S01_BOOK, 26)

    # --- the historical price arrives; the NORMAL writer recovers day 0 -----
    late_bars = {
        start - timedelta(days=1): {"open": 120.0, "close": 120.0},  # prices day 0
        start + timedelta(days=6): {"open": 97.0, "close": 97.0},
        start + timedelta(days=180): {"open": 97.0, "close": 97.0},
        start + timedelta(days=188): {"open": 85.0, "close": 85.0},
    }
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(late_bars))
    recovery_day = start + timedelta(days=189)
    assert pl.run_daily_mtm(root, _now_of(recovery_day)) == 6  # recovered + 5 own rows
    recovered = next(
        row
        for row in pl._load_journal(root)
        if row.get("recovered_from_unpriced") and row.get("book") == pl.S01_BOOK
    )
    assert recovered["mtm_date"] == start.isoformat()
    assert recovered["equity"] == pytest.approx(1.2, rel=1e-12)

    # --- the SAME frozen window records its real verdict, exactly once ------
    out = pl.weekly_summary(tmp_path / "data", recovery_day)
    j26 = out["milestone"][pl.S01_BOOK]["weeks"][26]
    assert j26["verdict"] == "FAIL"
    assert j26["failed_criteria"] == ["dd_vs_benchmark"]
    dd26 = j26["criteria"]["dd_vs_benchmark"]
    assert dd26["value"] == pytest.approx((1.0 - 0.97 / 1.2) / 0.2, rel=1e-12)
    assert dd26["comparison_start"] == start.isoformat()
    assert dd26["unresolved_mtm_gaps"] == []
    assert j26["milestone_date"] == window_end.isoformat()
    assert j26["judged_at_week_end"] == recovery_day.isoformat()
    assert pl._milestone_already_judged(root, pl.S01_BOOK, 26)
    assert (
        pl.weekly_summary(tmp_path / "data", start + timedelta(days=196)).get("milestone") is None
    )


def test_benchmark_side_prebaseline_gap_holds_and_a_late_common_base_still_judges(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-07-R4 (t_0be71b95 rev5): the whole-window hole scan covers BOTH
    origin candidates, and a legitimately late common origin stays judgeable.

    A benchmark-side hole before the provisional origin holds the judgment
    exactly like a book-side one (the scan is symmetric over book and
    benchmark); once the benchmark's historical day-0 mark arrives honestly
    (mtm_date day 0, recorded_at the arrival day — never backdated), the
    window judges once. The control pins the Book C contract: a benchmark
    that only STARTS marking later carries no unpriced record, is not a
    known failure, and must not hold — the judgment records off the late
    common origin (comparison_start day 7).
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    window_end = start + timedelta(days=84)
    bench = pl.BOOK_BENCHMARK[pl.S01_BOOK]

    # --- the bench-side hole: day 0 failed and was never completed ----------
    root = pl.loop_root(tmp_path / "bench-gap" / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 7: 0.97, 83: 0.97},
        bench_marks={7: 1.0, 83: 0.8},
    )
    _long_milestone_row(
        root,
        start,
        event="mtm_unpriced",
        book=bench,
        mtm_date=start.isoformat(),
        reason="unpriced_positions",
        unpriced_assets=["BTC"],
        units={},
        cash=0.0,
    )
    dd = pl._milestone_dd_criterion(
        root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is None
    assert dd["reason"] == "unresolved_mtm_gap_in_window"
    assert dd["unresolved_mtm_gaps"] == [{"book": bench, "mtm_date": start.isoformat()}]
    assert dd["comparison_start"] == (start + timedelta(days=7)).isoformat()
    assert (
        pl.weekly_summary(tmp_path / "bench-gap" / "data", window_end)["milestone"][pl.S01_BOOK][
            "weeks"
        ].get(12)
        is None
    )
    # the benchmark's historical day-0 mark arrives honestly, late
    arrival = start + timedelta(days=91)
    _long_milestone_row(
        root,
        start,
        event="mtm",
        book=bench,
        mtm_date=start.isoformat(),
        equity=1.0,
        cash=0.0,
        drawdown=0.0,
        peak_equity=1.0,
        recorded_at=f"{arrival.isoformat()}T04:00:00+00:00",
    )
    out = pl.weekly_summary(tmp_path / "bench-gap" / "data", arrival)
    j12 = out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert j12["verdict"] == "PASS"
    assert j12["criteria"]["dd_vs_benchmark"]["comparison_start"] == start.isoformat()
    assert j12["criteria"]["dd_vs_benchmark"]["unresolved_mtm_gaps"] == []
    assert (
        pl.weekly_summary(tmp_path / "bench-gap" / "data", start + timedelta(days=98)).get(
            "milestone"
        )
        is None
    )

    # --- control: a late-STARTING benchmark is not a hole — it judges -------
    control_dir = tmp_path / "late-base" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        control_root,
        start=start,
        run_days=84,
        book_marks={0: 1.0, 7: 0.97, 83: 0.97},
        bench_marks={7: 1.0, 83: 0.8},  # the bench simply starts at day 7
    )
    dd = pl._milestone_dd_criterion(
        control_root,
        pl.S01_BOOK,
        start,
        window_end,
        float(pl.MILESTONE_RULES[12]["dd_vs_benchmark"]),
        shallow_bench_hold=True,
    )
    assert dd["pass"] is True  # no known failure — the late common origin judges
    assert dd["comparison_start"] == (start + timedelta(days=7)).isoformat()
    assert dd["unresolved_mtm_gaps"] == []
    control_out = pl.weekly_summary(control_dir, window_end)
    control_j12 = control_out["milestone"][pl.S01_BOOK]["weeks"][12]
    assert control_j12["verdict"] == "PASS"
    assert pl.weekly_summary(control_dir, start + timedelta(days=91)).get("milestone") is None


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
    # v1.8 (C-05): the cumulative column is closed at week_end too — 1.155 on
    # 10-24, not the 1.20 mark written after it (that is what the pre-fix code
    # read, and 20% is what it wrote on this same journal)
    assert book["cum_return"] == pytest.approx(1.155 - 1.0, rel=1e-12)
    assert book["cum_return_as_of"] == "2026-10-24"
    assert book["cum_return_reason"] is None
    assert book["cum_return"] != pytest.approx(0.20, rel=1e-9)
    assert abs(book["week_return"] - book["cum_return"]) > 0.09

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


# --- C-03: weekly shortfall is the week's, and one bad row cannot stop the week ----


def _mark(
    root: Path,
    day: str,
    *,
    book: str = pl.S01_BOOK,
    asset: str = "BTC",
    shortfall: float | None = 0.0,
    with_key: bool = True,
) -> None:
    """One mark row; ``with_key=False`` writes a row with NO shortfall_bps key."""

    row: dict[str, object] = {
        "event": "mark",
        "book": book,
        "recorded_at": f"{day}T00:20:00+00:00",
        "decision_id": f"{book}:{asset}:{day}",
        "asset": asset,
        "bt_fill_price": 100.0,
        "recompute_match": True,
        "turnover": 0.0,
    }
    if with_key:
        row["shortfall_bps"] = shortfall
    _append_row(root, **row)


def test_weekly_shortfall_is_scoped_to_the_week_and_survives_bad_rows(
    tmp_path: Path, monkeypatch
) -> None:
    """C-03 (docs/PAPERLOOP_MONEY_REVIEW_20261023.md): weekly cost, not all-period.

    Two defects in one place: (a) the four ``shortfall_*`` columns were
    computed over EVERY mark the book ever wrote while the neighbouring
    columns were weekly, and (b) ``row["shortfall_bps"]`` was a direct dict
    lookup, so one mark row without the column raised KeyError and
    ``weekly_summary`` died before writing the row — the whole weekly report,
    milestone judgment included. On the real journal (week 2026-10-20..26)
    the pre-fix row printed -10.2291 bps / n=26 for a week that had 8 marks
    (-11.2105 bps).

    Pins: the week's values only, an explicit record of the excluded rows
    (no silent drop), the row still being written when the week contains
    anomalies, and None — never 0 — when no usable value exists.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)

    # week_end 2026-10-25 -> window 2026-10-19..2026-10-25
    _mark(root, "2026-10-05", asset="BTC", shortfall=100.0)  # older week
    _mark(root, "2026-10-12", asset="ETH", shortfall=-50.0)  # older week
    _mark(root, "2026-10-20", asset="BTC", shortfall=10.0)
    _mark(root, "2026-10-22", asset="ETH", shortfall=20.0)
    _mark(root, "2026-10-25", asset="BTC", shortfall=30.0)

    book = pl.weekly_summary(data_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["shortfall_scope"] == "week"
    assert book["shortfall_window_start"] == "2026-10-19"
    assert book["shortfall_n"] == 3
    assert book["shortfall_bps_mean_signed"] == pytest.approx(20.0, rel=1e-12)
    assert book["shortfall_bps_mean_abs"] == pytest.approx(20.0, rel=1e-12)
    assert book["shortfall_n_excluded"] == 0
    assert book["shortfall_errors"] == []
    # the number the pre-fix code wrote for this same journal: (100-50+10+20+30)/5
    old_all_period_mean = (100.0 - 50.0 + 10.0 + 20.0 + 30.0) / 5
    assert old_all_period_mean == pytest.approx(22.0)
    assert book["shortfall_bps_mean_signed"] != pytest.approx(22.0)

    # --- mixed anomalies in the same week: the week still gets its row --------
    mixed_dir = tmp_path / "mixed" / "data"
    mixed_root = pl.loop_root(mixed_dir)
    mixed_root.mkdir(parents=True, exist_ok=True)
    _mark(mixed_root, "2026-10-20", asset="BTC", shortfall=10.0)
    _mark(mixed_root, "2026-10-22", asset="ETH", shortfall=30.0)
    _mark(mixed_root, "2026-10-21", asset="BTC", with_key=False)
    _mark(mixed_root, "2026-10-23", asset="ETH", shortfall=None)
    _mark(mixed_root, "2026-10-24", asset="BTC", shortfall="n/a")  # type: ignore[arg-type]
    mixed = pl.weekly_summary(mixed_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert mixed["shortfall_n"] == 2  # only the two usable marks
    assert mixed["shortfall_bps_mean_signed"] == pytest.approx(20.0, rel=1e-12)
    assert mixed["shortfall_n_excluded"] == 3
    assert [error["reason"] for error in mixed["shortfall_errors"]] == [
        "missing_shortfall_bps",  # 2026-10-21, key absent
        "missing_shortfall_bps",  # 2026-10-23, value null
        "non_numeric_shortfall_bps",  # 2026-10-24, "n/a"
    ]
    # each excluded row is named, so the journal row can be found again
    assert [error["asset"] for error in mixed["shortfall_errors"]] == ["BTC", "ETH", "BTC"]
    assert all(error["decision_id"] and error["recorded_at"] for error in mixed["shortfall_errors"])
    # ... and the weekly row was actually appended (the pre-fix KeyError killed
    # the function before this file could be written)
    written = [
        json.loads(line)
        for line in pl.summary_path(mixed_root).read_text(encoding="utf-8").splitlines()
    ]
    assert written[-1]["books"][pl.S01_BOOK]["shortfall_n"] == 2
    assert written[-1]["schema"] == "demo-loop-summary-v1.10"

    # --- a week with only unusable values: None, never 0 ----------------------
    empty = tmp_path / "all_bad" / "data"
    empty_root = pl.loop_root(empty)
    empty_root.mkdir(parents=True, exist_ok=True)
    _mark(empty_root, "2026-10-20", asset="BTC", with_key=False)
    bad = pl.weekly_summary(empty, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert bad["shortfall_n"] == 0
    assert bad["shortfall_bps_mean_signed"] is None
    assert bad["shortfall_bps_mean_abs"] is None
    assert bad["shortfall_bps_mean_upper95"] is None
    assert bad["shortfall_n_excluded"] == 1
    assert bad["shortfall_errors"][0]["reason"] == "missing_shortfall_bps"


# --- C-05: the weekly row is closed at week_end --------------------------------


def test_C05_equity_columns_are_closed_at_week_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-05 (docs/PAPERLOOP_MONEY_REVIEW_20261023.md): no future mark in a row.

    ``cum_return`` / ``drawdown_now`` came from ``rebuild_state`` — the journal's
    LATEST mark — so a row generated for ``week_end`` carried any mark written
    after it. The real file shows it: the last row says ``week_end=2026-10-26``
    while every book's last mark is 10-23. The file is append-only, so such a
    value can never be corrected.

    Here 10-30 (a -22% crash) sits after week_end 10-25: the row must read the
    10-24 mark (+15.5%, no drawdown) and name it, not the crash.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "closed" / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _mtm(root, pl.S01_BOOK, "2026-10-12", 1.10, -0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.144, -0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-24", 1.155, -0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-30", 0.90, 0.90 / 1.155 - 1.0)

    book = pl.weekly_summary(data_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["cum_return"] == pytest.approx(1.155 - 1.0, rel=1e-12)
    assert book["cum_return_as_of"] == "2026-10-24"
    assert book["cum_return_reason"] is None
    # peak_equity is 1.0 in this fixture, so the as-of drawdown is +15.5% - 1
    # (measured, not the crash's -22.08%); what matters is the DATE it is
    # measured at: the same mark as cum_return, before the future mark
    assert book["drawdown_as_of"] == "2026-10-24"
    assert book["drawdown_now"] != pytest.approx(0.90 / 1.155 - 1.0, rel=1e-9)

    # a book whose only mark is AFTER week_end: unknown, with a reason
    late_dir = tmp_path / "late" / "data"
    late_root = pl.loop_root(late_dir)
    late_root.mkdir(parents=True, exist_ok=True)
    _mtm(late_root, pl.S01_BOOK, "2026-10-27", 1.20, 0.0)
    late = pl.weekly_summary(late_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert late["cum_return"] is None
    assert late["cum_return_as_of"] is None
    assert late["cum_return_reason"] == "no_mark_at_or_before_week_end"
    assert late["drawdown_now"] is None
    assert late["drawdown_reason"] == "no_mark_at_or_before_week_end"


def test_C05_comparison_line_is_closed_at_week_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-05: the comparison line ends at week_end, both sides alike.

    The benchmark fills on 10-21 and both books carry a 10-21 mark, so the shared
    baseline is 10-21. A 10-30 mark (+30% on both sides) is AFTER week_end
    2026-10-25; the line must read the 10-22 marks (+5% / +2% -> +3pp) and name
    its endpoint. Before the fix the state's latest mark (10-30) produced +0pp
    while ``comparison_start`` still said 10-21 — a row mixing two dates.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "cmp" / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _fill(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.00, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.05, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.02, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-30", 1.30, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-30", 1.30, 0.0)

    book = pl.weekly_summary(data_dir, date(2026, 10, 25))["books"][pl.S01_BOOK]
    assert book["comparison_start"] == "2026-10-21"
    assert book["comparison_as_of"] == "2026-10-22"
    assert book["benchmark_as_of"] == "2026-10-22"
    assert book["cum_pnl"] == pytest.approx(0.05, rel=1e-12)
    assert book["benchmark_cum_pnl"] == pytest.approx(0.02, rel=1e-12)
    assert book["vs_benchmark_pp_cum"] == pytest.approx(3.0, rel=1e-12)
    # the value the pre-fix code wrote from the 10-30 marks
    assert book["vs_benchmark_pp_cum"] != pytest.approx(0.0, abs=1e-9)
    # ... and the same line computed WITHOUT as_of still rides the latest mark
    line = pl._comparison_line(
        root,
        pl.S01_BOOK,
        pl.BENCH_CRYPTO_BOOK,
        {
            pl.S01_BOOK: pl.rebuild_state(root, pl.S01_BOOK),
            pl.BENCH_CRYPTO_BOOK: pl.rebuild_state(root, pl.BENCH_CRYPTO_BOOK),
        },
    )
    assert line["vs_benchmark_pp_cum"] == pytest.approx(0.0, abs=1e-9)
    assert line["comparison_as_of"] == "2026-10-30"


# --- C-06: switches are a per-asset state -------------------------------------


def _decision(
    root: Path,
    *,
    asset: str,
    regime: str,
    prev: str | None = None,
    day: str = "2026-10-22",
    book: str = pl.S01_BOOK,
    backfill: bool = False,
    with_prev: bool = True,
) -> None:
    """One decision row, optionally LEGACY (no ``prev_regime_state`` key)."""

    row: dict[str, object] = {
        "event": "decision",
        "book": book,
        "decision_id": f"{book}:{asset}:{day}:{regime}",
        "decision_date": day,
        "asset": asset,
        "regime_state": regime,
        "backfill": backfill,
    }
    if with_prev:
        row["prev_regime_state"] = prev
    _append_row(root, **row)


def _switches(data_dir: Path, week_end: date = date(2026, 10, 25)) -> tuple[int, dict, int]:
    """(switches, switches_by_asset, the pre-fix book-wide count) for book A."""

    out = pl.weekly_summary(data_dir, week_end)["books"][pl.S01_BOOK]
    # the pre-fix algorithm, re-implemented here to show the difference:
    # ONE cursor across every judged decision row of the week, whatever asset
    old = 0
    prev: str | None = None
    for row in pl._load_journal(pl.loop_root(data_dir)):
        if row.get("event") != "decision" or row.get("book") != pl.S01_BOOK:
            continue
        if row.get("backfill"):
            continue
        if not (
            week_end - timedelta(days=6)
            <= date.fromisoformat(str(row["decision_date"]))
            <= week_end
        ):
            continue
        regime = str(row.get("regime_state"))
        if regime in ("RISK_ON", "RISK_OFF") and prev is not None and prev != regime:
            old += 1
        if regime in ("RISK_ON", "RISK_OFF"):
            prev = regime
    return out["switches"], out["switches_by_asset"], old


def _switch_dir(tmp_path: Path, name: str):
    data_dir = tmp_path / name / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return data_dir, root


def test_C06_two_assets_in_different_regimes_are_not_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-06: BTC=RISK_ON / ETH=RISK_OFF in one week is zero switches.

    The pre-fix count kept one cursor for the book, so the second asset's row was
    compared against the FIRST asset's regime: BTC-vs-ETH in, ETH-vs-BTC out =
    two fake switches in a week where neither asset changed its own state.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir, root = _switch_dir(tmp_path, "split")
    _decision(root, asset="BTC", regime="RISK_ON", prev=None, day="2026-10-21")
    _decision(root, asset="ETH", regime="RISK_OFF", prev=None, day="2026-10-21")
    _decision(root, asset="BTC", regime="RISK_ON", prev="RISK_ON", day="2026-10-22")
    _decision(root, asset="ETH", regime="RISK_OFF", prev="RISK_OFF", day="2026-10-22")

    switches, by_asset, old = _switches(data_dir)
    assert switches == 0
    assert by_asset == {"BTC": 0, "ETH": 0}
    assert old >= 1  # the pre-fix count for the same rows
    assert sum(by_asset.values()) == switches


def test_C06_one_assets_flip_counts_once_and_the_sum_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-06: ETH turning RISK_OFF -> RISK_ON is exactly one switch (BTC stays)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir, root = _switch_dir(tmp_path, "flip")
    _decision(root, asset="BTC", regime="RISK_ON", prev="RISK_ON", day="2026-10-21")
    _decision(root, asset="ETH", regime="RISK_OFF", prev="RISK_OFF", day="2026-10-21")
    _decision(root, asset="BTC", regime="RISK_ON", prev="RISK_ON", day="2026-10-22")
    _decision(root, asset="ETH", regime="RISK_ON", prev="RISK_OFF", day="2026-10-22")

    switches, by_asset, old = _switches(data_dir)
    assert switches == 1
    assert by_asset == {"BTC": 0, "ETH": 1}
    assert sum(by_asset.values()) == switches
    # the pre-fix count read 2 for these same rows: one real flip plus the
    # BTC-vs-ETH comparison the fake switch came from
    assert old == 2


def test_C06_legacy_rows_without_prev_regime_state_fall_back_per_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-06: rows predating ``prev_regime_state`` count the same way.

    The fallback is the previous decision for the SAME (book, asset) in journal
    order — the rule the writer used — so a backfilled journal is not read
    differently from a fresh one.

    Split assets stay at 0; one asset reversing stays at 1; and the reversal is
    still one switch when the earlier row is the previous WEEK's (the state
    carries, the count does not: only in-window rows are counted, but the row
    that opens the window is compared against the older one).
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})

    split_dir, split_root = _switch_dir(tmp_path, "legacy_split")
    _decision(split_root, asset="BTC", regime="RISK_ON", day="2026-10-21", with_prev=False)
    _decision(split_root, asset="ETH", regime="RISK_OFF", day="2026-10-21", with_prev=False)
    _decision(split_root, asset="BTC", regime="RISK_ON", day="2026-10-22", with_prev=False)
    _decision(split_root, asset="ETH", regime="RISK_OFF", day="2026-10-22", with_prev=False)
    switches, by_asset, old = _switches(split_dir)
    assert switches == 0
    assert by_asset == {"BTC": 0, "ETH": 0}
    assert old >= 1

    flip_dir, flip_root = _switch_dir(tmp_path, "legacy_flip")
    _decision(flip_root, asset="BTC", regime="RISK_ON", day="2026-10-21", with_prev=False)
    _decision(flip_root, asset="ETH", regime="RISK_OFF", day="2026-10-21", with_prev=False)
    _decision(flip_root, asset="BTC", regime="RISK_ON", day="2026-10-22", with_prev=False)
    _decision(flip_root, asset="ETH", regime="RISK_ON", day="2026-10-22", with_prev=False)
    switches, by_asset, _old = _switches(flip_dir)
    assert switches == 1
    assert by_asset == {"BTC": 0, "ETH": 1}

    # the reversal happens on the week's FIRST judged row: the chain still
    # reaches back to the previous week's decision for the same asset
    carry_dir, carry_root = _switch_dir(tmp_path, "legacy_carry")
    _decision(carry_root, asset="BTC", regime="RISK_ON", day="2026-10-18", with_prev=False)
    _decision(carry_root, asset="BTC", regime="RISK_OFF", day="2026-10-20", with_prev=False)
    switches, by_asset, _old = _switches(carry_dir)
    assert switches == 1
    assert by_asset == {"BTC": 1}

    # a HALT row neither counts nor resets the asset's cursor
    halt_dir, halt_root = _switch_dir(tmp_path, "legacy_halt")
    _decision(halt_root, asset="BTC", regime="RISK_ON", day="2026-10-20", with_prev=False)
    _decision(halt_root, asset="BTC", regime="HALT", day="2026-10-21", with_prev=False)
    _decision(halt_root, asset="BTC", regime="RISK_OFF", day="2026-10-22", with_prev=False)
    switches, by_asset, _old = _switches(halt_dir)
    assert switches == 1
    assert by_asset == {"BTC": 1}


# --- C-07: one name, one definition -------------------------------------------


def test_C07_top_level_vs_benchmark_is_the_shared_baseline_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-07: the top-level column repeats ``books[A]``; the old one is renamed.

    Book A is at 1.10 when CRYPTO_1N fills at 1.00, so the shared baseline is
    10-21 with two DIFFERENT baseline equities (1.10 vs 1.00). A closes at 1.155
    while the benchmark stays at 1.00:

    * shared-baseline line: (1.155/1.10 - 1) - 0 = +5.0pp
    * the old baseline-less difference: (1.155 - 1.00) x 100 = +15.5pp

    Two definitions under one name is the defect (§C-07: up to ¥900,000 of
    misreading); the number the operator reads as ``vs_benchmark_pp_cum`` must be
    the one every per-book line uses, and the old formula keeps its own name.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir = tmp_path / "c07" / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _mtm(root, pl.S01_BOOK, "2026-10-20", 1.10, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-20", 1.00, 0.0)
    _fill(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.10, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00, 0.0)
    _mtm(root, pl.S01_BOOK, "2026-10-22", 1.155, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-22", 1.00, 0.0)

    out = pl.weekly_summary(data_dir, date(2026, 10, 25))
    book_a = out["books"][pl.S01_BOOK]
    assert book_a["comparison_start"] == "2026-10-21"
    assert book_a["comparison_as_of"] == "2026-10-22"
    assert book_a["vs_benchmark_pp_cum"] == pytest.approx(5.0, rel=1e-12)
    # one name, one definition: the top level IS the per-book line, same date
    assert out["vs_benchmark_pp_cum"] == book_a["vs_benchmark_pp_cum"]
    assert out["vs_benchmark_pp_cum_as_of"] == book_a["comparison_as_of"]
    # the old formula keeps its own name (and its own, weaker baseline rule)
    assert out["vs_benchmark_pp_cum_legacy_nobaseline"] == pytest.approx(15.5, rel=1e-12)
    # the two definitions really do disagree on this journal (10.5pp = ¥105,000
    # on a ¥10,000,000 base) — that is why one of them had to be renamed
    assert out["vs_benchmark_pp_cum_legacy_nobaseline"] - out["vs_benchmark_pp_cum"] == (
        pytest.approx(10.5, rel=1e-12)
    )
    # neither column is quietly absent, and the legacy one is not on the books
    assert "vs_benchmark_pp_cum_legacy_nobaseline" not in book_a


# --- t_94687d03: a comparison needs ONE end day for both sides ----------------


def _endpoint_dir(tmp_path: Path, name: str, *, who_is_ahead: str) -> tuple[Path, Path]:
    """Journal where one side carries a mark on 10-24 and the other does not.

    Shared baseline is 10-21 (benchmark's fill day, both books marked). The book
    is at 1.00 on the baseline; whichever side is ``who_is_ahead`` marks 1.10 on
    10-24, the other side stops at 10-21.
    """

    data_dir = tmp_path / name / "data"
    root = pl.loop_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    _fill(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21")
    _mtm(root, pl.S01_BOOK, "2026-10-21", 1.00, 0.0)
    _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-21", 1.00, 0.0)
    if who_is_ahead == "book":
        _mtm(root, pl.S01_BOOK, "2026-10-24", 1.10, 0.0)
    else:
        _mtm(root, pl.BENCH_CRYPTO_BOOK, "2026-10-24", 1.10, 0.0)
    return data_dir, root


def test_C05_comparison_line_is_null_when_the_two_endpoints_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t_94687d03: a 3-day move is never reported against a 0-day move.

    Closing the line at ``week_end`` left each side ending at its own last mark
    on/before that day. With A marking 10-24 (1.10) and the benchmark stopping
    at 10-21 (1.00), the row still carried ``vs_benchmark_pp_cum=+10.0pp``
    labelled ``comparison_as_of=2026-10-24`` / ``benchmark_as_of=2026-10-21``:
    A's three days of P&L against the benchmark's zero days. Fail closed on the
    numbers, keep BOTH as-ofs so the row still records which marks existed.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})

    for who in ("book", "benchmark"):
        data_dir, root = _endpoint_dir(tmp_path, f"mismatch_{who}", who_is_ahead=who)
        out = pl.weekly_summary(data_dir, date(2026, 10, 25))
        book_a = out["books"][pl.S01_BOOK]

        # the endpoints really are two different days, and both are reported
        behind, ahead = (
            ("2026-10-21", "2026-10-24") if who == "book" else ("2026-10-24", "2026-10-21")
        )
        assert book_a["comparison_start"] == "2026-10-21"
        assert book_a["comparison_as_of"] == ahead
        assert book_a["benchmark_as_of"] == behind
        assert book_a["comparison_reason"] == "endpoint_mismatch"
        for key in (
            "cum_pnl",
            "max_drawdown",
            "benchmark_cum_pnl",
            "benchmark_max_drawdown",
            "vs_benchmark_pp_cum",
            "dd_vs_benchmark_ratio",
        ):
            assert book_a[key] is None, f"{key} must be None when the endpoints differ"
        # the number the row used to emit for this journal (+/-10% over the
        # window only one side traded); reached again below through the no-as_of
        # path, i.e. the pre-fix behaviour is still callable but is no longer
        # what the weekly row writes.
        expected_legacy = 10.0 if who == "book" else -10.0

        # one name, one definition: the top level refuses too, and a refused
        # value carries no date (the per-book line still names both marks)
        assert out["vs_benchmark_pp_cum"] is None
        assert out["vs_benchmark_pp_cum_as_of"] is None
        # the legacy baseline-less difference subtracted two equities measured
        # on different days; same endpoint rule, same refusal, with a reason
        assert out["vs_benchmark_pp_cum_legacy_nobaseline"] is None
        assert out["vs_benchmark_pp_cum_legacy_nobaseline_reason"] == "endpoint_mismatch"

        # requirement: omitting as_of keeps the previous meaning for callers
        line = pl._comparison_line(
            root,
            pl.S01_BOOK,
            pl.BENCH_CRYPTO_BOOK,
            {
                pl.S01_BOOK: pl.rebuild_state(root, pl.S01_BOOK),
                pl.BENCH_CRYPTO_BOOK: pl.rebuild_state(root, pl.BENCH_CRYPTO_BOOK),
            },
        )
        assert line["comparison_reason"] is None
        assert line["vs_benchmark_pp_cum"] == pytest.approx(expected_legacy, rel=1e-12)


def test_C05_comparison_line_keeps_its_value_when_both_endpoints_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t_94687d03 control: same marks on both sides -> yesterday's numbers.

    Both books mark 10-24 (1.10 / 1.00), so the two endpoints are the same day
    and every value the v1.8 row carried must survive unchanged.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    data_dir, _root = _endpoint_dir(tmp_path, "matched", who_is_ahead="book")
    _mtm(pl.loop_root(data_dir), pl.BENCH_CRYPTO_BOOK, "2026-10-24", 1.00, 0.0)

    out = pl.weekly_summary(data_dir, date(2026, 10, 25))
    book_a = out["books"][pl.S01_BOOK]
    assert book_a["comparison_start"] == "2026-10-21"
    assert book_a["comparison_as_of"] == "2026-10-24"
    assert book_a["benchmark_as_of"] == "2026-10-24"
    assert book_a["comparison_reason"] is None
    assert book_a["cum_pnl"] == pytest.approx(0.10, rel=1e-12)
    assert book_a["benchmark_cum_pnl"] == pytest.approx(0.0, abs=1e-12)
    assert book_a["vs_benchmark_pp_cum"] == pytest.approx(10.0, rel=1e-12)
    assert out["vs_benchmark_pp_cum"] == book_a["vs_benchmark_pp_cum"]
    assert out["vs_benchmark_pp_cum_as_of"] == "2026-10-24"
    assert out["vs_benchmark_pp_cum_legacy_nobaseline"] == pytest.approx(10.0, rel=1e-12)
    assert out["vs_benchmark_pp_cum_legacy_nobaseline_reason"] is None


# --- C-04: an unpriceable mtm day is recorded, never zero-filled ---------------


def test_C04_unpriced_mtm_is_recorded_and_never_zero_filled(tmp_path: Path, monkeypatch) -> None:
    """C-04 (docs/PAPERLOOP_MONEY_REVIEW_20261023.md): no crash, no 0 valuation.

    Pre-fix behaviour, both measured in the review: a held symbol with no
    finalized bar left ``None`` in ``prices`` and ``units * None`` raised
    TypeError, aborting the pass after the earlier books had already been
    appended; a held symbol outside the book's universe fell through
    ``prices.get(asset, 0.0)`` and was valued at 0, so equity dropped by the
    whole position (w=25% -> a fake -25% drawdown).

    Pins: the missing-price day is recorded as ``mtm_unpriced`` with the
    missing assets and NO ``mtm`` row (0 is never substituted), the other books
    still mark, consecutive missing days are each recorded, the same day is not
    re-recorded, and the equity written after recovery includes every holding —
    universe member or not.
    """

    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    _inject_bar(root, "BTC", now + timedelta(hours=1), 101.0)
    _inject_bar(root, "ETH", now + timedelta(hours=1), 60.0)
    assert pl.resolve_fills(root, now + timedelta(hours=2)) == 2 * len(pl.CRYPTO_BOOKS)
    state_a = pl.rebuild_state(root, pl.S01_BOOK)
    assert {asset for asset, units in state_a["units"].items() if units} == {"BTC", "ETH"}

    missing_day = now.date() + timedelta(days=1)
    next_day = missing_day + timedelta(days=1)
    (data_dir / "crypto-ohlcv" / "BTC" / "ohlcv.jsonl").unlink()

    # --- one missing day: the books that hold BTC record "cannot value" ------
    assert pl.run_daily_mtm(root, _now_of(missing_day)) == 2  # only the equity books
    journal = pl._load_journal(root)
    mtm_books = {
        row["book"]
        for row in journal
        if row.get("event") == "mtm" and row["mtm_date"] == missing_day.isoformat()
    }
    assert mtm_books == {pl.BOOK_C_BOOK, pl.BENCH_EQUITY_BOOK}
    unpriced = [
        row
        for row in journal
        if row.get("event") == "mtm_unpriced" and row["mtm_date"] == missing_day.isoformat()
    ]
    assert {row["book"] for row in unpriced} == set(pl.CRYPTO_BOOKS)
    assert all(row["reason"] == "unpriced_positions" for row in unpriced)
    assert all(row["unpriced_assets"] == ["BTC"] for row in unpriced)
    assert all("equity" not in row for row in unpriced)
    # the 0 the old code stored: no mtm row anywhere carries a 0 equity
    assert not [row for row in journal if row.get("event") == "mtm" and not row.get("equity")]

    # --- consecutive missing days: each day is recorded on its own ------------
    assert pl.run_daily_mtm(root, _now_of(next_day)) == 2
    journal = pl._load_journal(root)
    assert [
        row["book"]
        for row in journal
        if row.get("event") == "mtm_unpriced" and row["mtm_date"] == next_day.isoformat()
    ] == [book for book in pl.BOOKS_ENABLED if book in pl.CRYPTO_BOOKS]
    assert not [
        row
        for row in journal
        if row.get("event") == "mtm"
        and row["mtm_date"] == next_day.isoformat()
        and row["book"] in pl.CRYPTO_BOOKS
    ]

    # --- the same day is never recorded twice (B2 idempotency) ---------------
    assert pl.run_daily_mtm(root, _now_of(missing_day)) == 0
    journal = pl._load_journal(root)
    same_day = [
        row
        for row in journal
        if row.get("event") == "mtm_unpriced" and row["mtm_date"] == missing_day.isoformat()
    ]
    assert len(same_day) == len(pl.CRYPTO_BOOKS)

    # --- tape comes back: the real mtm is written, with every holding priced --
    _write_crypto_days(data_dir, "BTC", [(now.date().isoformat(), 101.0, 101.0)])
    assert pl.run_daily_mtm(root, _now_of(missing_day)) == len(pl.CRYPTO_BOOKS)
    journal = pl._load_journal(root)
    recovered = [
        row
        for row in journal
        if row.get("event") == "mtm"
        and row["book"] == pl.S01_BOOK
        and row["mtm_date"] == missing_day.isoformat()
    ]
    assert len(recovered) == 1
    row = recovered[0]
    eth_close = pl._latest_finalized_close(root, "ETH", missing_day)[1]
    units = row["units"]
    assert row["equity"] == pytest.approx(
        row["cash"] + units["BTC"] * row["prices"]["BTC"] + units["ETH"] * eth_close, rel=1e-12
    )
    assert row["equity"] > 0

    # --- a holding the universe no longer lists is priced, not zeroed --------
    eth_next = pl._latest_finalized_close(root, "ETH", next_day)[1]
    monkeypatch.setattr(pl, "CRYPTO_UNIVERSE", ("BTC",))
    assert pl.run_daily_mtm(root, _now_of(next_day)) == len(pl.CRYPTO_BOOKS)
    journal = pl._load_journal(root)
    shrunk = [
        row
        for row in journal
        if row.get("event") == "mtm"
        and row["book"] == pl.S01_BOOK
        and row["mtm_date"] == next_day.isoformat()
    ]
    assert len(shrunk) == 1
    row2 = shrunk[0]
    assert "ETH" not in row2["prices"]  # the row's universe field keeps its shape
    zero_filled = row2["cash"] + row2["units"]["BTC"] * row2["prices"]["BTC"]
    # the pre-fix equity: ETH valued at 0 (which is also how 0 got into equity)
    assert row2["equity"] == pytest.approx(zero_filled + row2["units"]["ETH"] * eth_next, rel=1e-12)
    assert row2["equity"] - zero_filled == pytest.approx(row2["units"]["ETH"] * eth_next, rel=1e-12)


@pytest.mark.parametrize("recovery_close", [70.0, 120.0])
def test_recovered_past_mark_does_not_rollback_current_state_or_fill_sizing(
    tmp_path: Path, monkeypatch, recovery_close: float
) -> None:
    """AS-M126-08 (t_0be71b95 rev6): a recovered PAST mark must not roll the
    CURRENT state (and the next real fill sized on it) backwards.

    The audit repro, every step through the REAL writers: book A holds BTC
    0.01 / cash 0; day 40 fails unpriced; day 83 marks 0.97 normally at
    00:00; the day-39 close arrives 06:00 the SAME day and the recovery
    honestly completes day 40 (0.70 low / 1.20 high — both parametrized),
    appended at the journal END with the OLDER target day. rebuild_state
    adopted the last append, so the current equity rolled back to the
    recovered value and a 07:00 sell decision resolved by the real fill path
    at 09:00 traded notional 0.35 instead of 0.485 (27.8% less paper sold
    than the no-outage control). The consumer contract (state by target day,
    chain never steps at recovered marks) keeps current equity 0.97 and the
    fill identical to the control, while the recovered row stays and stays
    faithful.
    """

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    start = date(2026, 1, 1)
    day40 = start + timedelta(days=40)
    window_end = start + timedelta(days=84)
    last_day = window_end - timedelta(days=1)  # day 83
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        root, start=start, run_days=84, book_marks={0: 1.0}, bench_marks={0: 1.0}
    )
    _long_milestone_row(
        root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"{pl.S01_BOOK}:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )

    def sell_fill(journal_root: Path, day: date) -> dict[str, Any]:
        """A real pending decision at 07:00 resolved by the real fill path."""
        _long_milestone_row(
            journal_root,
            day,
            event="decision",
            book=pl.S01_BOOK,
            asset="BTC",
            decision_id=f"{pl.S01_BOOK}:BTC:{day.isoformat()}",
            decision_date=day.isoformat(),
            recorded_at=f"{day.isoformat()}T07:00:00+00:00",
            backfill=False,
            fill_status="pending",
            side="sell",
            delta_weight=-0.5,
            target_weight=0.5,
            regime_state="RISK_ON",
            prev_regime_state="RISK_ON",
        )
        with monkeypatch.context() as m:
            m.setattr(pl, "_daily_map", lambda root_, asset: {})
            m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
            m.setattr(
                pl,
                "_first_fill_candidate",
                lambda *a, **k: {
                    "open_time": datetime.combine(day, datetime.min.time(), UTC)
                    .replace(hour=8)
                    .isoformat(),
                    "open": 100.0,
                    "close": 100.0,
                },
            )
            assert pl.resolve_fills(
                journal_root, datetime.combine(day, datetime.min.time(), UTC).replace(hour=9)
            )
        return next(
            row
            for row in pl._load_journal(journal_root)
            if row.get("event") == "fill"
            and row.get("decision_id") == f"{pl.S01_BOOK}:BTC:{day.isoformat()}"
        )

    def a_marks(data_root: Path, mtm_day: date) -> list[dict[str, Any]]:
        return [
            row
            for row in pl._load_journal(data_root)
            if row.get("event") == "mtm"
            and row.get("book") == pl.S01_BOOK
            and row.get("mtm_date") == mtm_day.isoformat()
        ]

    # --- day 40: the real writer cannot price the holding -------------------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        pl.run_daily_mtm(root, _now_of(day40))
    assert len(a_marks(root, day40)) == 0

    # --- day 83 00:20: the normal mark (0.01 BTC at the day-82 close 97) ----
    bars = {start + timedelta(days=82): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    pl.run_daily_mtm(root, _now_of(last_day))
    day83 = a_marks(root, last_day)
    assert len(day83) == 1 and day83[0]["equity"] == pytest.approx(0.97, rel=1e-12)
    assert pl.rebuild_state(root, pl.S01_BOOK)["equity"] == pytest.approx(0.97, rel=1e-12)

    # --- 06:00 SAME day: the day-39 close arrives; the recovery completes ---
    # --- day 40's FAILED snapshot, appended at the journal end -------------
    bars[start + timedelta(days=39)] = {"open": recovery_close, "close": recovery_close}
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=360)) == 1  # only A's recovery
    recovered = a_marks(root, day40)
    assert len(recovered) == 1
    rec = recovered[0]
    assert rec["equity"] == pytest.approx(0.01 * recovery_close, rel=1e-12)
    assert rec["recovered_from_unpriced"] is True
    assert rec["recorded_at"].startswith(last_day.isoformat())  # honest, never backdated
    assert rec["price_bar_date"] == (start + timedelta(days=39)).isoformat()
    # B2: the day-83 mark was not rewritten, and the failed write's record stands
    assert len(a_marks(root, last_day)) == 1
    assert any(
        row.get("event") == "mtm_unpriced" and row.get("book") == pl.S01_BOOK
        for row in pl._load_journal(root)
    )
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=420)) == 0  # B2: nothing twice

    # --- THE contract: the current state did NOT roll back to the recovery --
    state = pl.rebuild_state(root, pl.S01_BOOK)
    assert state["equity"] == pytest.approx(0.97, rel=1e-12)

    # --- the 07:00 decision's real fill is sized on the CURRENT equity ------
    sold = sell_fill(root, last_day)
    assert sold["equity_at_fill"] == pytest.approx(0.97, rel=1e-12)
    assert sold["traded_notional"] == pytest.approx(0.485, rel=1e-12)
    assert sold["units_after"] == pytest.approx(0.00515, rel=1e-12)
    assert sold["cash_after"] == pytest.approx(0.485 * (1 - pl.COST_RATE), rel=1e-12)

    # --- no-outage control: day 40 marked normally, everything else equal ---
    control_dir = tmp_path / "control" / "data"
    control_root = pl.loop_root(control_dir)
    control_root.mkdir(parents=True, exist_ok=True)
    _long_milestone_journal(
        control_root, start=start, run_days=84, book_marks={0: 1.0}, bench_marks={0: 1.0}
    )
    _long_milestone_row(
        control_root,
        start,
        event="fill",
        book=pl.S01_BOOK,
        decision_id=f"{pl.S01_BOOK}:BTC:{start.isoformat()}",
        asset="BTC",
        paper_fill_price=99.0,
        side="buy",
        turnover=1.0,
        units_before=0.0,
        units_after=0.01,
        cash_after=0.0,
    )
    control_bars = {start + timedelta(days=39): {"open": recovery_close, "close": recovery_close}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(control_bars))
    pl.run_daily_mtm(control_root, _now_of(day40))  # day 40 marks honestly at 00:20
    control_bars[start + timedelta(days=82)] = {"open": 97.0, "close": 97.0}
    pl.run_daily_mtm(control_root, _now_of(last_day))
    control_sold = sell_fill(control_root, last_day)
    assert control_sold["equity_at_fill"] == pytest.approx(0.97, rel=1e-12)
    assert control_sold["traded_notional"] == pytest.approx(sold["traded_notional"], rel=1e-12)
    assert control_sold["units_after"] == pytest.approx(sold["units_after"], rel=1e-12)

    # --- the recovered row is as faithful as the control's own day-40 mark --
    control_rec = [
        row
        for row in pl._load_journal(control_root)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == day40.isoformat()
    ]
    assert len(control_rec) == 1
    c40 = control_rec[0]
    for column in ("equity", "equity_gross", "cum_cost_assumed", "cum_turnover", "peak_equity"):
        assert rec[column] == pytest.approx(c40[column], rel=1e-12), column

    # --- the NEXT day's real writer: identical row in both directions -------
    # (the gross chain must not step at the recovered mark — with the rev5
    # append-order walk the repro's next row carried a corrupted chain)
    next_day = window_end
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    pl.run_daily_mtm(root, _now_of(next_day))
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(control_bars))
    pl.run_daily_mtm(control_root, _now_of(next_day))
    repro_next = a_marks(root, next_day)
    control_next = [
        row
        for row in pl._load_journal(control_root)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == next_day.isoformat()
    ]
    assert len(repro_next) == 1 and len(control_next) == 1
    rn, cn = repro_next[0], control_next[0]
    for column in ("equity", "units", "cash", "peak_equity", "halted", "cum_turnover"):
        assert rn[column] == cn[column], column
    assert rn["cum_cost_assumed"] == pytest.approx(cn["cum_cost_assumed"], rel=1e-12)
    assert rn["cum_cost_assumed"] > 0  # the real sell's cost: not a vacuous comparison
    assert rn["equity_gross"] == pytest.approx(cn["equity_gross"], rel=1e-12)
    assert rn["equity"] == pytest.approx(0.485 * (1 - pl.COST_RATE) + 0.00515 * 97.0, rel=1e-12)


def test_unpriced_boundary_chain_branches_for_recovered_marks() -> None:
    """AS-M126-08 (rev6) + AS-M126-10 (rev7): the boundary replay mirrors the
    consumer chain contract for recovered marks, in BOTH branches.

    A recovery that was the chain's NEWEST target day when appended IS a
    gross step (nothing was written across the hole yet — skipping it would
    re-charge the hole's costs at face value on the final step); a recovery
    that arrived after a LATER as-written mark is NOT a step (that written
    step already compounded across the hole). Neither rolls the boundary's
    "now" (halted) backwards. Non-zero costs make the branches
    distinguishable: the two prefixes chain differently.
    """

    book = pl.S01_BOOK

    def prefix(*, late: bool) -> list[dict[str, Any]]:
        recovery_row = {
            "event": "mtm",
            "book": book,
            "mtm_date": "2026-02-10",
            "equity": 0.70,
            "peak_equity": 1.0,
            "halted": True,  # the recovered day's honest PAST halt flag
            "recovered_from_unpriced": True,
        }
        later_mark = {
            "event": "mtm",
            "book": book,
            "mtm_date": "2026-02-11",
            "equity": 0.97,
            "peak_equity": 1.0,
            "halted": False,
        }
        return [
            {
                "event": "fill",
                "book": book,
                "asset": "BTC",
                "units_after": 0.01,
                "cash_after": 0.0,
                "cost_assumed": 0.0015,
                "traded_notional": 1.0,
            },
            later_mark if late else recovery_row,
            recovery_row if late else later_mark,
        ]

    # latest-day recovery (no later mark existed when it was appended): a step
    state = pl._unpriced_boundary(prefix(late=False), book, boundary=3, equity=0.80)
    assert state is not None
    assert state["halted"] is False  # still the newest target day's flag
    assert state["equity_gross"] == pytest.approx(
        ((0.70 + 0.0015) / 1.0) * (0.97 / 0.70) * (0.80 / 0.97), rel=1e-12
    )
    # late recovery (the later mark was already written): not a step
    late_state = pl._unpriced_boundary(prefix(late=True), book, boundary=3, equity=0.80)
    assert late_state is not None
    assert late_state["halted"] is False
    assert late_state["equity_gross"] == pytest.approx(
        ((0.97 + 0.0015) / 1.0) * (0.80 / 0.97), rel=1e-12
    )
    assert state["equity_gross"] != pytest.approx(late_state["equity_gross"], rel=1e-4)


# --- rev7 (AS-M126-09 / AS-M126-10): as-of reads and the chain branch ---------


def _real_market_fill(
    monkeypatch,
    root: Path,
    day: date,
    *,
    side: str,
    delta_weight: float,
    target_weight: float,
    price: float,
) -> dict[str, Any]:
    """A pending decision resolved through the REAL fill path (the audit
    probes' shape): decision 07:00, bar 08:00 at ``price``, resolve_fills
    09:00. Returns the fill row."""

    book = pl.S01_BOOK
    _long_milestone_row(
        root,
        day,
        event="decision",
        book=book,
        asset="BTC",
        decision_id=f"{book}:BTC:{day.isoformat()}",
        decision_date=day.isoformat(),
        recorded_at=f"{day.isoformat()}T07:00:00+00:00",
        backfill=False,
        fill_status="pending",
        side=side,
        delta_weight=delta_weight,
        target_weight=target_weight,
        regime_state="RISK_ON",
        prev_regime_state="RISK_ON",
    )
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        m.setattr(pl, "_fetch_crypto_1h", lambda *a, **k: [])
        m.setattr(
            pl,
            "_first_fill_candidate",
            lambda *a, **k: {
                "open_time": datetime.combine(day, datetime.min.time(), UTC)
                .replace(hour=8)
                .isoformat(),
                "open": price,
                "close": price,
            },
        )
        assert pl.resolve_fills(
            root, datetime.combine(day, datetime.min.time(), UTC).replace(hour=9)
        )
    return next(
        row
        for row in pl._load_journal(root)
        if row.get("event") == "fill" and row.get("decision_id") == f"{book}:BTC:{day.isoformat()}"
    )


def _a_mtm(root: Path, day: date) -> dict[str, Any]:
    return next(
        row
        for row in pl._load_journal(root)
        if row.get("event") == "mtm"
        and row.get("book") == pl.S01_BOOK
        and row.get("mtm_date") == day.isoformat()
    )


def test_cost_accumulators_step_branch_for_recovered_marks(tmp_path: Path) -> None:
    """AS-M126-10 (rev7): the writer-side gross chain steps at a recovery
    only while it is the chain's newest target day. Skipping a latest-day
    recovery (rev6's blanket rule) loses the hole day's first cost step and
    everything compounding after it — the audited day-41 row read gross
    1.25961 instead of 1.26081. A recovery appended after a later as-written
    mark stays out of that written chain (AS-M126-08)."""

    book = pl.S01_BOOK

    def journal(*, late: bool) -> Path:
        root = pl.loop_root(tmp_path / ("late" if late else "latest") / "data")
        root.mkdir(parents=True, exist_ok=True)

        def append(row: dict[str, Any]) -> None:
            row.setdefault("recorded_at", "2026-03-26T00:00:00+00:00")
            pl._append_row(root, row)

        append(
            {
                "event": "fill",
                "book": book,
                "asset": "BTC",
                "units_after": 0.009985,
                "cash_after": 0.0,
                "cost_assumed": 0.0015,
                "traded_notional": 1.0,
            }
        )
        recovery_row = {
            "event": "mtm",
            "book": book,
            "mtm_date": "2026-02-10",
            "equity": 0.69895,
            "equity_gross": 0.70045,
            "recovered_from_unpriced": True,
        }
        later_mark = {
            "event": "mtm",
            "book": book,
            "mtm_date": "2026-03-25",
            "equity": 0.968545,
            "equity_gross": 0.970045,
        }
        rows = [later_mark, recovery_row] if late else [recovery_row, later_mark]
        for row in rows:
            append(row)
        append(
            {
                "event": "fill",
                "book": book,
                "asset": "BTC",
                "units_after": 0.0049925,
                "cash_after": 0.349475 * (1 - pl.COST_RATE),
                "cost_assumed": 0.0005242125,
                "traded_notional": 0.349475,
            }
        )
        return root

    equity_now = 0.349475 * (1 - pl.COST_RATE) + 0.0049925 * 140.0
    # latest-day recovery: the chain runs inception -> recovery -> now, each
    # step adding back only its own segment's costs (the independent formula
    # (E40+Cbefore40)/E40*(Enow+C40tonow)/E40 with the recovery as day 40)
    cum_cost, cum_turnover, gross = pl._cost_accumulators(journal(late=False), book, equity_now)
    assert cum_cost == pytest.approx(0.0015 + 0.0005242125, rel=1e-12)
    assert cum_turnover == pytest.approx(1.0 + 0.349475, rel=1e-12)
    assert gross == pytest.approx(
        ((0.69895 + 0.0015) / 1.0) * ((equity_now + 0.0005242125) / 0.69895), rel=1e-12
    )
    # late recovery: the written 03-25 step already compounded across the
    # hole, so the recovery stays out and the chain runs over that mark
    _, _, late_gross = pl._cost_accumulators(journal(late=True), book, equity_now)
    assert late_gross == pytest.approx(
        ((0.968545 + 0.0015) / 1.0) * ((equity_now + 0.0005242125) / 0.968545), rel=1e-12
    )
    assert gross != pytest.approx(late_gross, rel=1e-4)


def test_recovery_before_the_next_mark_keeps_the_next_days_gross_step(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-10 (t_0be71b95 rev7), the audited repro end to end through the
    REAL writers: day 40 fails unpriced at 00:00, the day-39 close arrives
    06:00 the SAME day and the recovery honestly completes day 40, a 07:00
    decision sells through the real fill path at 09:00, and day 41 marks at
    a changed price. The recovery happened BEFORE the next mark, so it is
    the chain's newest day and the day-41 row must chain THROUGH it —
    identical to the no-outage control and to the independent formula (with
    rev6's blanket skip the walk chained from inception and read gross
    1.25961 vs the control's 1.26081, understating cost_drag by 37.16%)."""

    monkeypatch.setattr(pl, "_eval_blocks", lambda *a, **k: {"stub": True})
    from yowayowa import paperloop_panel

    start = date(2026, 1, 1)
    day39 = start + timedelta(days=39)
    day40 = start + timedelta(days=40)
    day41 = start + timedelta(days=41)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)

    bought = _real_market_fill(
        monkeypatch, root, start, side="buy", delta_weight=1.0, target_weight=1.0, price=100.0
    )
    assert bought["cost_assumed"] == pytest.approx(0.0015, rel=1e-12)
    assert bought["units_after"] == pytest.approx(0.009985, rel=1e-12)

    # --- day 40 00:20: the real writer cannot price the holding -------------
    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        pl.run_daily_mtm(root, _now_of(day40))
    with pytest.raises(StopIteration):
        _a_mtm(root, day40)  # unpriced, honestly

    # --- 06:00 SAME day: the day-39 close arrives; the recovery completes ---
    bars = {day39: {"open": 70.0, "close": 70.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    assert pl.run_daily_mtm(root, _now_of(day40, minute=360)) == 1  # only A's recovery
    rec = _a_mtm(root, day40)
    assert rec["recovered_from_unpriced"] is True
    assert rec["equity"] == pytest.approx(0.009985 * 70.0, rel=1e-12)
    assert rec["equity_gross"] == pytest.approx(0.69895 + 0.0015, rel=1e-12)
    assert pl.rebuild_state(root, pl.S01_BOOK)["equity"] == pytest.approx(0.69895, rel=1e-12)

    # --- 07:00 decision -> 09:00 real sell, sized on the CURRENT state ------
    sold = _real_market_fill(
        monkeypatch, root, day40, side="sell", delta_weight=-0.5, target_weight=0.5, price=70.0
    )
    assert sold["equity_at_fill"] == pytest.approx(0.69895, rel=1e-12)
    assert sold["traded_notional"] == pytest.approx(0.349475, rel=1e-12)
    assert sold["cost_assumed"] == pytest.approx(0.0005242125, rel=1e-12)

    # --- day 41: the real writer marks at the changed price -----------------
    bars[day40] = {"open": 140.0, "close": 140.0}
    pl.run_daily_mtm(root, _now_of(day41))
    nxt = _a_mtm(root, day41)
    expected_equity = sold["cash_after"] + sold["units_after"] * 140.0
    assert nxt["equity"] == pytest.approx(expected_equity, rel=1e-12)
    assert nxt["cum_cost_assumed"] == pytest.approx(0.0015 + 0.0005242125, rel=1e-12)
    # THE contract: the chain runs through the recovery, not from inception
    expected_gross = ((rec["equity"] + 0.0015) / 1.0) * (
        (expected_equity + sold["cost_assumed"]) / rec["equity"]
    )
    assert nxt["equity_gross"] == pytest.approx(expected_gross, rel=1e-12)
    assert pl.run_daily_mtm(root, _now_of(day41, minute=60)) == 0  # B2: nothing twice

    # --- no-outage control: day 40 marked normally, everything else equal ---
    control_root = pl.loop_root(tmp_path / "control" / "data")
    control_root.mkdir(parents=True, exist_ok=True)
    control_bought = _real_market_fill(
        monkeypatch,
        control_root,
        start,
        side="buy",
        delta_weight=1.0,
        target_weight=1.0,
        price=100.0,
    )
    assert control_bought["units_after"] == pytest.approx(bought["units_after"], rel=1e-12)
    control_bars = {day39: {"open": 70.0, "close": 70.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(control_bars))
    pl.run_daily_mtm(control_root, _now_of(day40))  # day 40 marks honestly at 00:20
    c40 = _a_mtm(control_root, day40)
    for column in ("equity", "equity_gross", "cum_cost_assumed", "cum_turnover", "peak_equity"):
        assert rec[column] == pytest.approx(c40[column], rel=1e-12), column
    control_sold = _real_market_fill(
        monkeypatch,
        control_root,
        day40,
        side="sell",
        delta_weight=-0.5,
        target_weight=0.5,
        price=70.0,
    )
    assert control_sold["traded_notional"] == pytest.approx(sold["traded_notional"], rel=1e-12)
    assert control_sold["units_after"] == pytest.approx(sold["units_after"], rel=1e-12)
    control_bars[day40] = {"open": 140.0, "close": 140.0}
    pl.run_daily_mtm(control_root, _now_of(day41))
    nxt_c = _a_mtm(control_root, day41)
    assert nxt_c["equity"] == pytest.approx(nxt["equity"], rel=1e-12)
    assert nxt_c["cum_cost_assumed"] == pytest.approx(nxt["cum_cost_assumed"], rel=1e-12)
    assert nxt_c["equity_gross"] == pytest.approx(nxt["equity_gross"], rel=1e-12)

    # --- cost_drag reads the same share on both paths (Q3's window metric) --
    panel_r = paperloop_panel.build_panel(tmp_path / "data", start=day40, end=day41)
    panel_c = paperloop_panel.build_panel(tmp_path / "control" / "data", start=day40, end=day41)
    drag_r = paperloop_panel._cost_drag_pp(panel_r["rows"], pl.S01_BOOK)
    drag_c = paperloop_panel._cost_drag_pp(panel_c["rows"], pl.S01_BOOK)
    assert drag_r is not None and drag_r > 0
    assert drag_r == pytest.approx(drag_c, rel=1e-12)


def test_panel_as_of_reads_target_day_order_not_append_order(tmp_path: Path, monkeypatch) -> None:
    """AS-M126-09 (t_0be71b95 rev7): the panel's as-of equity columns adopt
    the newest TARGET day's mark, never the last append. The audited repro,
    real writers throughout: day 40 fails unpriced, day 83 marks 0.968545
    normally, the day-39 close lands 06:00 and day 40 is honestly recovered
    (0.69895) at the journal's END, then a real decision/fill lands on day
    83. The public build_panel row for day 83 must read day 83's OWN mark —
    rev6's append-order read put the recovered past value there (26.9595pp
    wrong) and leaked it into weekly Q4."""

    start = date(2026, 1, 1)
    day39 = start + timedelta(days=39)
    day40 = start + timedelta(days=40)
    last_day = start + timedelta(days=83)  # 2026-03-25
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)

    bought = _real_market_fill(
        monkeypatch, root, start, side="buy", delta_weight=1.0, target_weight=1.0, price=100.0
    )
    assert bought["units_after"] == pytest.approx(0.009985, rel=1e-12)

    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        pl.run_daily_mtm(root, _now_of(day40))
    bars = {start + timedelta(days=82): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    pl.run_daily_mtm(root, _now_of(last_day))
    day83 = _a_mtm(root, last_day)
    assert day83["equity"] == pytest.approx(0.009985 * 97.0, rel=1e-12)

    bars[day39] = {"open": 70.0, "close": 70.0}
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=360)) == 1
    rec = _a_mtm(root, day40)
    assert rec["recovered_from_unpriced"] is True
    assert rec["equity"] == pytest.approx(0.009985 * 70.0, rel=1e-12)
    assert rec["recorded_at"].startswith(last_day.isoformat())  # honest, never backdated

    repro_sold = _real_market_fill(
        monkeypatch,
        root,
        last_day,
        side="sell",
        delta_weight=-0.5,
        target_weight=0.5,
        price=100.0,
    )
    assert pl.rebuild_state(root, pl.S01_BOOK)["equity"] == pytest.approx(0.968545, rel=1e-12)

    from yowayowa.paperloop_panel import build_panel

    panel = build_panel(tmp_path / "data", start=last_day, end=last_day)
    rows = [row for row in panel["rows"] if row.get("book") == pl.S01_BOOK]
    assert len(rows) == 1
    row = rows[0]
    # the queried day's OWN mark — not the trailing recovered past value
    assert row["equity"] == pytest.approx(day83["equity"], rel=1e-12)
    assert row["equity"] == pytest.approx(0.968545, rel=1e-12)
    assert row["equity_gross"] == pytest.approx(day83["equity_gross"], rel=1e-12)
    assert row["equity"] != pytest.approx(rec["equity"], rel=1e-3)
    assert row["equity_gross"] != pytest.approx(rec["equity_gross"], rel=1e-3)
    assert row["bench_equity"] == pytest.approx(1.0, rel=1e-12)

    # --- the no-outage control reads the same day-83 equity -----------------
    control_root = pl.loop_root(tmp_path / "control" / "data")
    control_root.mkdir(parents=True, exist_ok=True)
    _real_market_fill(
        monkeypatch,
        control_root,
        start,
        side="buy",
        delta_weight=1.0,
        target_weight=1.0,
        price=100.0,
    )
    control_bars = {day39: {"open": 70.0, "close": 70.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(control_bars))
    pl.run_daily_mtm(control_root, _now_of(day40))
    control_bars[start + timedelta(days=82)] = {"open": 97.0, "close": 97.0}
    pl.run_daily_mtm(control_root, _now_of(last_day))
    control_sold = _real_market_fill(
        monkeypatch,
        control_root,
        last_day,
        side="sell",
        delta_weight=-0.5,
        target_weight=0.5,
        price=100.0,
    )
    assert control_sold["traded_notional"] == pytest.approx(
        repro_sold["traded_notional"], rel=1e-12
    )
    assert control_sold["units_after"] == pytest.approx(repro_sold["units_after"], rel=1e-12)
    control_panel = build_panel(tmp_path / "control" / "data", start=last_day, end=last_day)
    control_rows = [row for row in control_panel["rows"] if row.get("book") == pl.S01_BOOK]
    assert len(control_rows) == 1
    assert control_rows[0]["equity"] == pytest.approx(row["equity"], rel=1e-12)
    assert control_rows[0]["equity_gross"] == pytest.approx(
        _a_mtm(control_root, last_day)["equity_gross"], rel=1e-12
    )


def test_weekly_q4_excess_reads_the_marked_day_not_the_recovered_past(
    tmp_path: Path, monkeypatch
) -> None:
    """AS-M126-09 (t_0be71b95 rev7) on the public weekly row: with day 40's
    recovery appended AFTER day 83's own mark, weekly_summary's Q4 must read
    the marked day's equity — mean excess equal to the row's books
    cum_return (-0.031455 in the audited numbers) — not the recovered past
    value (-0.30105). Every fact through the real writers; the no-outage
    control produces the identical Q4 block."""

    start = date(2026, 1, 1)
    day39 = start + timedelta(days=39)
    day40 = start + timedelta(days=40)
    last_day = start + timedelta(days=83)
    window_end = start + timedelta(days=84)
    root = pl.loop_root(tmp_path / "data")
    root.mkdir(parents=True, exist_ok=True)

    bought = _real_market_fill(
        monkeypatch, root, start, side="buy", delta_weight=1.0, target_weight=1.0, price=100.0
    )
    assert bought["units_after"] == pytest.approx(0.009985, rel=1e-12)

    with monkeypatch.context() as m:
        m.setattr(pl, "_daily_map", lambda root_, asset: {})
        pl.run_daily_mtm(root, _now_of(day40))
    bars = {start + timedelta(days=82): {"open": 97.0, "close": 97.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(bars))
    pl.run_daily_mtm(root, _now_of(last_day))
    bars[day39] = {"open": 70.0, "close": 70.0}
    assert pl.run_daily_mtm(root, _now_of(last_day, minute=360)) == 1
    rec = _a_mtm(root, day40)
    assert rec["equity"] == pytest.approx(0.009985 * 70.0, rel=1e-12)

    # a live-schema decision on the marked day: the panel row Q4 judges
    _long_milestone_row(
        root,
        last_day,
        event="decision",
        book=pl.S01_BOOK,
        asset="BTC",
        decision_id=f"{pl.S01_BOOK}:BTC:{last_day.isoformat()}",
        decision_date=last_day.isoformat(),
        recorded_at=f"{last_day.isoformat()}T07:00:00+00:00",
        schema=pl.SCHEMA_V13,
        backfill=False,
        regime_state="RISK_ON",
        prev_regime_state="RISK_ON",
        close=97.0,
        regime_tag={"vol_bucket": "low", "trend_bucket": "up"},
    )
    bars[start + timedelta(days=83)] = {"open": 97.0, "close": 97.0}
    pl.run_daily_mtm(root, _now_of(window_end))  # next day's mark: ret_next exists

    out = pl.weekly_summary(tmp_path / "data", window_end)
    expected = 0.009985 * 97.0 - 1.0
    q4 = out["eval_by_book"][pl.S01_BOOK]["eval"]["Q4"]
    assert q4["n"] == 1
    assert q4["n_excluded"]["unjudgeable"] == 0
    bucket = next(iter(q4["by_bucket"].values()))
    assert bucket["n"] == 1
    # the marked day's equity vs the benchmark — not the recovered past value
    assert bucket["mean_excess"] == pytest.approx(expected, rel=1e-12)
    assert out["books"][pl.S01_BOOK]["cum_return"] == pytest.approx(expected, rel=1e-12)
    assert bucket["mean_excess"] != pytest.approx(-0.30105, rel=1e-3)

    # --- no-outage control: the identical Q4 block ---------------------------
    control_root = pl.loop_root(tmp_path / "control" / "data")
    control_root.mkdir(parents=True, exist_ok=True)
    _real_market_fill(
        monkeypatch,
        control_root,
        start,
        side="buy",
        delta_weight=1.0,
        target_weight=1.0,
        price=100.0,
    )
    control_bars = {day39: {"open": 70.0, "close": 70.0}}
    monkeypatch.setattr(pl, "_daily_map", lambda root_, asset: dict(control_bars))
    pl.run_daily_mtm(control_root, _now_of(day40))
    control_bars[start + timedelta(days=82)] = {"open": 97.0, "close": 97.0}
    pl.run_daily_mtm(control_root, _now_of(last_day))
    _long_milestone_row(
        control_root,
        last_day,
        event="decision",
        book=pl.S01_BOOK,
        asset="BTC",
        decision_id=f"{pl.S01_BOOK}:BTC:{last_day.isoformat()}",
        decision_date=last_day.isoformat(),
        recorded_at=f"{last_day.isoformat()}T07:00:00+00:00",
        schema=pl.SCHEMA_V13,
        backfill=False,
        regime_state="RISK_ON",
        prev_regime_state="RISK_ON",
        close=97.0,
        regime_tag={"vol_bucket": "low", "trend_bucket": "up"},
    )
    control_bars[start + timedelta(days=83)] = {"open": 97.0, "close": 97.0}
    pl.run_daily_mtm(control_root, _now_of(window_end))
    control_out = pl.weekly_summary(tmp_path / "control" / "data", window_end)
    control_q4 = control_out["eval_by_book"][pl.S01_BOOK]["eval"]["Q4"]
    assert control_q4["by_bucket"] == q4["by_bucket"]
    assert control_q4["n"] == q4["n"]
