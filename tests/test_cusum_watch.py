"""Unit tests for the CUSUM edge-lifetime monitor (t_7dcc16ea, prereg §5).

Covers: the frozen registration values, ISO-week aggregation (backtest points
and mtm equity), the no-reset recursion, FAR behaviour (monotone grid + the
baseline_ref regression FAR(4.8)=0% / FAR(4.7)=21/172), a registration row the
existing verifier accepts, and the live weekly path (insufficient history,
crash -> alarm -> PAUSE recorded, constants taken from the registered row).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_paperloop import _now_of, _prepare
from yowayowa import cusum_watch as cw
from yowayowa import paperloop as pl
from yowayowa import paperloop_verify as pv

REG_AT = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)


# --- frozen registration -------------------------------------------------------


def test_registered_spec_matches_prereg_section5() -> None:
    spec = cw.cusum_spec()
    assert spec["type"] == "one_sided_downward_cusum_weekly"
    assert spec["kappa0"] == 0.5
    assert spec["k"] == 0.5
    assert spec["h_star"] == 4.8
    assert spec["window_weeks"] == 26
    assert spec["scale"] == {
        "method": "stationary_bootstrap_weekly_sigma",
        "B": 2000,
        "mean_block_weeks": 4,
        "seed": 20260930,
    }
    baseline = spec["baseline_ref"]
    assert baseline["sha256"] == "1cdca730152f697acae252b8bb609ff30f4a1ab13fe393f650849bccf8cc720b"
    assert baseline["weekly_mean"] == 0.004933
    assert baseline["weekly_sigma_hat"] == 0.048186
    assert baseline["weekly_se_of_mean"] == 0.004124
    assert cw.cusum_spec_hash() == cw._sha256(json.dumps(spec, sort_keys=True))


# --- series construction --------------------------------------------------------


def test_weekly_from_points_iso_compound_and_labels() -> None:
    points = [
        {"date": "2026-01-05", "daily_return": 0.10},  # Mon, ISO 2026-W2
        {"date": "2026-01-06", "daily_return": -0.10},  # compound -> 0.988*1.1-... = -0.01
        {"date": "2026-01-13", "daily_return": 0.05},  # Tue, ISO 2026-W3
    ]
    weekly = cw.weekly_returns_from_points(points)
    assert weekly == [("2026-W2", pytest.approx(1.1 * 0.9 - 1.0)), ("2026-W3", pytest.approx(0.05))]
    assert weekly[0][1] == pytest.approx(-0.01)


def test_weekly_from_mtm_and_incomplete_week_drop() -> None:
    rets = cw.daily_returns_from_mtm(
        [
            {"mtm_date": "2026-10-07", "equity": 1.0},
            {"mtm_date": "2026-10-07", "equity": 1.0},  # duplicate day: last wins
            {"mtm_date": "2026-10-09", "equity": 0.70},
            {"mtm_date": "2026-10-12", "equity": 0.71},
        ]
    )
    weekly = cw.weekly_returns_from_daily(rets)
    assert weekly == [
        ("2026-W41", pytest.approx(-0.30)),
        ("2026-W42", pytest.approx(0.71 / 0.70 - 1.0)),
    ]
    # the first observation only establishes the base: no return for it
    assert len(cw.daily_returns_from_mtm([{"mtm_date": "2026-10-07", "equity": 1.0}])) == 0
    with pytest.raises(pl.ChainError, match="not positive"):
        cw.daily_returns_from_mtm(
            [
                {"mtm_date": "2026-10-07", "equity": 1.0},
                {"mtm_date": "2026-10-08", "equity": 0.0},
            ]
        )
    now = _now_of(date(2026, 10, 13))  # Tuesday of ISO week 42
    assert cw.drop_incomplete_weeks(weekly, now) == [("2026-W41", pytest.approx(-0.30))]


# --- recursion: alarm + never reset ----------------------------------------------


def test_cusum_recursion_alarm_and_no_reset() -> None:
    weekly = [("2026-W41", -0.30), ("2026-W42", 0.05), ("2026-W43", 0.0)]
    traj = cw.cusum_trajectory(weekly, mean=0.004933, sigma=0.048186)
    # hand-checked reference values (registered constants)
    assert traj[0][1] == pytest.approx(-6.277061802)
    assert traj[0][2] == pytest.approx(5.777061802)
    assert traj[1][2] == pytest.approx(4.290603080)
    assert traj[2][2] == pytest.approx(3.841790147)
    # the crash week alarms; the following weeks CARRY S (never reset to 0)
    assert cw.alarm_weeks(traj) == ["2026-W41"]
    assert traj[1][2] > 3.0
    assert traj[2][2] > 3.0
    with pytest.raises(ValueError, match="sigma"):
        cw.cusum_trajectory(weekly, mean=0.0, sigma=0.0)


def test_far_monotone_in_h() -> None:
    weekly = [
        ("w", v)
        for v in [
            0.01,
            -0.02,
            0.03,
            -0.05,
            0.02,
            -0.10,
            0.04,
            -0.03,
            0.01,
            -0.04,
            0.06,
            -0.02,
            0.00,
            -0.08,
            0.05,
            0.01,
            -0.06,
            0.03,
            -0.01,
            0.02,
            -0.12,
            0.04,
            0.02,
            -0.03,
            0.01,
            0.05,
            -0.04,
            0.00,
        ]
    ]
    curve = cw.far_curve(weekly, mean=0.004933, sigma=0.048186, grid=(1.0, 2.0, 3.0, 4.8))
    values = [curve[h] for h in sorted(curve)]
    assert values == sorted(values, reverse=True)
    with pytest.raises(ValueError, match="weeks"):
        cw.far_curve(weekly[:10], mean=0.0, sigma=1.0)


def test_baseline_far_regression() -> None:
    """FAR(h=4.8)=0% and FAR(4.7)=21/172 on the registered baseline_ref series."""

    path = Path(__file__).resolve().parents[1] / "crypto_backtest_results.json"
    if not path.exists():  # pragma: no cover
        pytest.skip("baseline_ref file not present")
    data = json.loads(path.read_text())
    points = next(
        r["equity_curve"] for r in data["results"] if r["strategy"]["id"] == "crypto_trend_dual_ma"
    )
    weekly = cw.weekly_returns_from_points(points, start=date(2023, 1, 1), end=date(2026, 9, 29))
    values = [v for _, v in weekly]
    assert len(values) == 197
    mean = sum(values) / len(values)
    sigma = cw.boot_sigma(values, b=cw.BOOT_B, block=cw.BOOT_MEAN_BLOCK_WEEKS, seed=cw.BOOT_SEED)
    assert round(mean * 100, 4) == 0.4933
    assert round(sigma * 100, 4) == 4.8186
    curve = cw.far_curve(weekly, mean=mean, sigma=sigma, grid=(4.7, 4.8))
    assert curve[4.8] == 0.0
    assert curve[4.7] == 21 / 172


# --- prereg registration row + verifier ------------------------------------------


def test_registration_row_chain_and_verifier_accepts(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    rows_before = pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
    assert [str(r["book_id"]) for r in rows_before] == ["A", "CRYPTO_1N", "__regime_spec__"]

    row = cw.append_registration(
        root, registered_at=now + timedelta(hours=1), registered_by="test:t"
    )
    assert row["prev_hash"] == rows_before[-1]["row_hash"]
    expected = cw._sha256(
        json.dumps({k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    assert row["row_hash"] == expected
    rows = pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
    assert len(rows) == 4 and rows[-1]["book_id"] == cw.CUSUM_BOOK
    with pytest.raises(pl.ChainError, match="already registered"):
        cw.append_registration(root, registered_at=now + timedelta(hours=2), registered_by="test:t")

    result = pv.verify(data_dir, now.date() - timedelta(days=1), now.date())
    assert result["exit_code"] == 0, json.dumps(result["checks"], default=str)
    # the tz-aware row keeps L1 clean (the v1.1 rows are legacy findings, not failures)
    assert result["checks"][pv.L1]["ok"] is True


# --- live weekly path -------------------------------------------------------------


def _append_mtm(root: Path, day: date, equity: float, now: datetime) -> None:
    pl._append_row(
        root,
        {
            "event": "mtm",
            "book": "A",
            "mtm_date": day.isoformat(),
            "recorded_at": now.isoformat(),
            "price_bar_date": (day - timedelta(days=1)).isoformat(),
            "equity": equity,
            "cash": 0.0,
        },
    )


def test_run_weekly_requires_registration(tmp_path: Path, monkeypatch) -> None:
    data_dir, _root, now = _prepare(tmp_path, monkeypatch)
    with pytest.raises(pl.ChainError, match="refuses to run unregistered"):
        cw.run_weekly(data_dir, now)


def test_run_weekly_insufficient_history_and_log_chain(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    cw.append_registration(root, registered_at=now, registered_by="test:t")
    # the day's mtm lands in the SAME ISO week as the run clock -> no complete week
    row = cw.run_weekly(data_dir, now, repo_root=tmp_path)
    assert row["weeks_observed"] == 0
    assert row["week"] is None and row["z"] is None
    assert row["S"] == 0.0 and row["alarm"] is False and row["action"] == "continue"
    assert row["baseline_sha_match"] is None  # repo_root has no baseline file
    log_rows = pl._rows_from_jsonl(root / "cusum_A.jsonl", strict=True)
    assert len(log_rows) == 1
    row2 = cw.run_weekly(data_dir, now + timedelta(minutes=5), repo_root=tmp_path)
    assert row2["prev_hash"] == row["row_hash"]
    # constants come from the registered row, not from code drift
    assert row["constants"]["sigma_hat"] == 0.048186
    assert row["constants"]["mean"] == 0.004933
    assert row["constants"]["h_star"] == 4.8


def test_run_weekly_crash_alarms_and_pause_carries(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    cw.append_registration(root, registered_at=now, registered_by="test:t")
    # a -30% week (W41), run on Tue of W42 while W42 is still incomplete:
    # the CUSUM alarms (PAUSE) on the crash week
    later = _now_of(date(2026, 10, 13))  # Tue of ISO 2026-W42
    _append_mtm(root, date(2026, 10, 5), 1.00, now)  # Mon W41
    _append_mtm(root, date(2026, 10, 9), 0.70, now)  # Fri W41: -30%
    row = cw.run_weekly(data_dir, later, repo_root=tmp_path)
    assert row["week"] == "2026-W41"
    assert row["weeks_observed"] == 1
    assert row["S"] == pytest.approx(5.777061802)
    assert row["alarm"] is True
    assert row["action"] == "PAUSE"
    # ...and one week later the recursion CARRIES S (never reset): W42 = +2.14%
    _append_mtm(root, date(2026, 10, 12), 0.71, now)  # Mon W42
    _append_mtm(root, date(2026, 10, 16), 0.715, now)  # Fri W42
    row2 = cw.run_weekly(data_dir, _now_of(date(2026, 10, 20)), repo_root=tmp_path)
    assert row2["week"] == "2026-W42"  # W42 is the newest complete week
    assert row2["weeks_observed"] == 2
    assert row2["S"] == pytest.approx(4.883543531)
    assert row2["S"] > 4.0  # carried, not reset
    log_rows = pl._rows_from_jsonl(root / "cusum_A.jsonl", strict=True)
    assert len(log_rows) == 2 and log_rows[1]["prev_hash"] == log_rows[0]["row_hash"]


def test_run_weekly_uses_registered_row_constants(tmp_path: Path, monkeypatch) -> None:
    data_dir, root, now = _prepare(tmp_path, monkeypatch)
    pl.run_daily_decide(root, now)
    cw.append_registration(root, registered_at=now, registered_by="test:t")
    _append_mtm(root, date(2026, 10, 5), 1.00, now)
    _append_mtm(root, date(2026, 10, 9), 0.70, now)
    later = _now_of(date(2026, 10, 13))
    row = cw.run_weekly(data_dir, later, repo_root=tmp_path)
    assert row["constants"]["sigma_hat"] == 0.048186
    # a SECOND registration row wins and changes the scale -> z changes with it
    prior = pl._rows_from_jsonl(pl.prereg_path(root), strict=True)
    override = cw.build_registration_row(
        registered_at=(later + timedelta(minutes=1)).isoformat(), registered_by="test:t"
    )
    override["cusum_spec"]["baseline_ref"]["weekly_sigma_hat"] = 0.01
    override["spec_hash"] = cw._sha256(json.dumps(override["cusum_spec"], sort_keys=True))
    override["prev_hash"] = prior[-1]["row_hash"]
    override["row_hash"] = cw._sha256(
        json.dumps(
            {k: v for k, v in override.items() if k != "row_hash"}, sort_keys=True, default=str
        )
    )
    with pl.prereg_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(override) + "\n")
    row2 = cw.run_weekly(data_dir, later + timedelta(minutes=2), repo_root=tmp_path)
    assert row2["constants"]["sigma_hat"] == 0.01
    weekly_return = -0.30
    assert row2["z"] == pytest.approx((weekly_return - 0.5 * 0.004933) / 0.01)
