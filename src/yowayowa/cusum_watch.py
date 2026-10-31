"""CUSUM edge-lifetime monitor for demo book A (S-01).

Registered in ``docs/prereg/2026-10-13_S01_EXT_2017_2022_PREREG.md`` section 5
(commit c0f110f6ce4bf3206710dbb88b08abeb74b14ab2) and mirrored as one hash-
chained row (``book_id="__cusum_A__"``) in ``data/demo-loop/prereg.jsonl``:

- weekly vital ``z_w = (r_w - kappa0 * mean) / sigma_hat``, ``kappa0 = 0.5``;
- one-sided downward CUSUM ``S_w = max(0, S_{w-1} - z_w - k)``, ``k = 0.5``;
- alarm (PAUSE) when ``S_w > h*`` with ``h* = 4.8``; S is NEVER reset after an
  alarm (design t_9e1b26e2 section 3-3);
- scale ``sigma_hat`` is the per-week standard deviation estimated with a
  stationary bootstrap (B=2000, mean block 4 weeks, seed 20260930) on the
  calibration series: the S-01 equity curve of ``crypto_backtest_results.json``
  (sha256 1cdca730..., 197 ISO weeks, 2023-01-01..2026-09-29) whose weekly mean
  is +0.4933% and ``sigma_hat`` 4.8186%. h* = 4.8 is the smallest 0.1-grid
  threshold with FAR <= 10% over all 172 26-week start points (FAR(4.7) =
  12.21%, FAR(4.8) = 0.00%).

Two parameterizations, both per the prereg:

- diagnostic/calibration (``evaluate_series``): expectation and sigma are
  estimated from the series being fed. That is exactly how h* was calibrated
  on baseline_ref and how the extension diagnostic in
  ``docs/results/2026-10-13_S01_EXT_2017_2022_RESULTS.md`` was run (its 12
  alarm weeks are a regression target in ``scripts/cusum_acceptance.py``).
- live (``run_weekly``): the FROZEN registered constants are read from the
  ``__cusum_A__`` prereg row (single source of truth; fail-closed when the row
  is missing or malformed), and the weekly returns come from book A's mtm
  equity history in the demo-loop journal. The CUSUM is recomputed from
  scratch over all complete ISO weeks on every run, so the reported S is a
  pure function of the data and the registered constants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from yowayowa.paperloop import (
    S01_BOOK,
    ChainError,
    _load_journal,
    _require_utc,
    _rows_from_jsonl,
    _sha256,
    loop_root,
    prereg_path,
)

# --- frozen registered values (PREREG_S01_EXT_2017_2022 section 5) -----------

KAPPA0 = 0.5
K_DOWN = 0.5
H_STAR = 4.8
WINDOW_WEEKS = 26
BOOT_B = 2000
BOOT_MEAN_BLOCK_WEEKS = 4
BOOT_SEED = 20260930
BASELINE_REF_SHA256 = "1cdca730152f697acae252b8bb609ff30f4a1ab13fe393f650849bccf8cc720b"
BASELINE_WEEKLY_MEAN = 0.004933  # +0.4933% (section 5, as registered)
BASELINE_WEEKLY_SIGMA = 0.048186  # 4.8186% (section 5, as registered)
BASELINE_WEEKLY_SE_OF_MEAN = 0.004124  # 0.4124% reference value (section 5)
BASELINE_WEEKS = 197
CUSUM_BOOK = "__cusum_A__"  # prereg book_id of this registration (not a ledger)
LOG_SCHEMA = "cusum-watch-v1"
ALARM_WEEKS_EXTENSION_REPORT = (
    # docs/results/2026-10-13_S01_EXT_2017_2022_RESULTS.md, observation 3
    "2018-W11",
    "2020-W11",
    "2020-W12",
    "2020-W13",
    "2020-W14",
    "2020-W15",
    "2020-W16",
    "2020-W17",
    "2020-W18",
    "2020-W19",
    "2021-W20",
    "2021-W25",
)
FAR_CURVE_REPORT = {  # section 5 reference FAR curve (fraction)
    1.0: 0.779,
    1.5: 0.506,
    2.0: 0.506,
    2.5: 0.302,
    3.0: 0.297,
    3.5: 0.262,
    4.0: 0.145,
    4.5: 0.140,
    4.7: 0.1221,
    4.8: 0.0,
    5.0: 0.0,
}


def cusum_spec() -> dict[str, Any]:
    """The frozen section-5 spec exactly as registered (single source)."""

    return {
        "type": "one_sided_downward_cusum_weekly",
        "book": S01_BOOK,
        "kappa0": KAPPA0,
        "k": K_DOWN,
        "h_star": H_STAR,
        "window_weeks": WINDOW_WEEKS,
        "scale": {
            "method": "stationary_bootstrap_weekly_sigma",
            "B": BOOT_B,
            "mean_block_weeks": BOOT_MEAN_BLOCK_WEEKS,
            "seed": BOOT_SEED,
        },
        "baseline_ref": {
            "file": "crypto_backtest_results.json",
            "sha256": BASELINE_REF_SHA256,
            "weekly_mean": BASELINE_WEEKLY_MEAN,
            "weekly_sigma_hat": BASELINE_WEEKLY_SIGMA,
            "weekly_se_of_mean": BASELINE_WEEKLY_SE_OF_MEAN,
            "series": (
                "S-01 equity curve weekly returns; ISO weeks; 197 weeks; 2023-01-01..2026-09-29"
            ),
        },
        "far_check": {
            "grid_step": 0.1,
            "start_points": 172,
            "far_h_4_7": 0.1221,
            "far_h_4_8": 0.0,
        },
        "prereg_source": (
            "PREREG_S01_EXT_2017_2022 section 5 (commit c0f110f6ce4bf3206710dbb88b08abeb74b14ab2)"
        ),
        "alert_rule": "S_w > h* records PAUSE; S is never reset after an alarm",
    }


def cusum_spec_hash() -> str:
    return _sha256(json.dumps(cusum_spec(), sort_keys=True))


# --- series construction ------------------------------------------------------


def week_label(iso_year: int, iso_week: int) -> str:
    """Label format matches the prereg diagnostic (no zero padding)."""

    return f"{iso_year}-W{iso_week}"


def weekly_returns_from_points(
    points: Sequence[Mapping[str, Any]],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[str, float]]:
    """Compound ``daily_return`` per ISO week from backtest equity-curve points.

    Same aggregation as the prereg calibration/diagnostic scripts (the return
    of day t belongs to t's ISO week).
    """

    def _day(point: Mapping[str, Any]) -> date:
        return date.fromisoformat(str(point["date"])[:10])

    weeks: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    for point in points:
        day = _day(point)
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        iso = day.isocalendar()
        key = (iso[0], iso[1])
        if key not in weeks:
            weeks[key] = 1.0
            order.append(key)
        weeks[key] *= 1.0 + float(point["daily_return"])
    return [(week_label(key[0], key[1]), weeks[key] - 1.0) for key in order]


def daily_returns_from_mtm(rows: Sequence[Mapping[str, Any]]) -> list[tuple[date, float]]:
    """Daily equity returns of book A from journal mtm rows.

    Rows are deduplicated per mtm_date (the last row wins) and returns are
    taken over consecutive observed dates (``E_t / E_{t-1} - 1``).
    """

    by_day: dict[date, float] = {}
    for row in rows:
        day = date.fromisoformat(str(row["mtm_date"])[:10])
        equity = float(row["equity"])
        if not math.isfinite(equity) or equity <= 0.0:
            raise ChainError(f"mtm equity for {day} is not positive/finite: {equity}")
        by_day[day] = equity  # journal order: the last row per day is the state
    out: list[tuple[date, float]] = []
    prev_day: date | None = None
    prev_equity = 1.0
    for day in sorted(by_day):
        if prev_day is not None:
            out.append((day, by_day[day] / prev_equity - 1.0))
        prev_day, prev_equity = day, by_day[day]
    return out


def weekly_returns_from_daily(rets: Sequence[tuple[date, float]]) -> list[tuple[str, float]]:
    """Compound daily returns into ISO weeks (same aggregation as above)."""

    weeks: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    for day, ret in rets:
        iso = day.isocalendar()
        key = (iso[0], iso[1])
        if key not in weeks:
            weeks[key] = 1.0
            order.append(key)
        weeks[key] *= 1.0 + ret
    return [(week_label(key[0], key[1]), weeks[key] - 1.0) for key in order]


def drop_incomplete_weeks(
    weekly: Sequence[tuple[str, float]], now: datetime
) -> list[tuple[str, float]]:
    """Keep only complete ISO weeks (strictly before the run clock's week)."""

    iso = now.isocalendar()
    current = (iso[0], iso[1])
    out = []
    for label, value in weekly:
        year_text, week_text = label.split("-W")
        if (int(year_text), int(week_text)) < current:
            out.append((label, value))
    return out


# --- CUSUM core (identical math to the prereg calibration) --------------------


def stationary_block_draw(rng: random.Random, n: int, mean_block: int) -> list[int]:
    idx: list[int] = []
    i = rng.randrange(n)
    for _ in range(n):
        idx.append(i)
        i = rng.randrange(n) if rng.random() < 1.0 / mean_block else (i + 1) % n
    return idx


def boot_sigma(vals: Sequence[float], *, b: int, block: int, seed: int) -> float:
    """Stationary-bootstrap estimate of the per-week standard deviation."""

    n = len(vals)
    rng = random.Random(seed)
    stds: list[float] = []
    for _ in range(b):
        draw = [vals[i] for i in stationary_block_draw(rng, n, block)]
        m = sum(draw) / n
        stds.append(math.sqrt(sum((x - m) ** 2 for x in draw) / (n - 1)))
    return sum(stds) / len(stds)


def _cusum_values(
    values: Sequence[float],
    *,
    mean: float,
    sigma: float,
    kappa0: float = KAPPA0,
    k: float = K_DOWN,
    s0: float = 0.0,
) -> list[float]:
    """Recursion over bare weekly values; S is never reset (max(0, .) floor only)."""

    if sigma <= 0.0 or not math.isfinite(sigma):
        raise ValueError(f"sigma must be positive/finite, got {sigma}")
    out: list[float] = []
    s = s0
    for value in values:
        z = (value - kappa0 * mean) / sigma
        s = max(0.0, s - z - k)
        out.append(s)
    return out


def cusum_trajectory(
    weekly: Sequence[tuple[str, float]],
    *,
    mean: float,
    sigma: float,
    kappa0: float = KAPPA0,
    k: float = K_DOWN,
    s0: float = 0.0,
) -> list[tuple[str, float, float]]:
    """One-sided downward CUSUM: returns ``(week, z_w, S_w)``; S is never reset."""

    values = [v for _, v in weekly]
    states = _cusum_values(values, mean=mean, sigma=sigma, kappa0=kappa0, k=k, s0=s0)
    return [
        (label, (value - kappa0 * mean) / sigma, s)
        for (label, value), s in zip(weekly, states, strict=True)
    ]


def alarm_weeks(trajectory: Sequence[tuple[str, float, float]], h: float = H_STAR) -> list[str]:
    return [label for label, _, s in trajectory if s > h]


def far_curve(
    weekly: Sequence[tuple[str, float]],
    *,
    mean: float,
    sigma: float,
    grid: Sequence[float] = tuple(sorted(FAR_CURVE_REPORT)),
    window: int = WINDOW_WEEKS,
) -> dict[float, float]:
    """FAR per threshold: share of window starts whose CUSUM alarms inside."""

    values = [v for _, v in weekly]
    starts = len(values) - window + 1
    if starts <= 0:
        raise ValueError(f"series has {len(values)} weeks; need at least {window}")
    out: dict[float, float] = {}
    for h in grid:
        alarmed = 0
        for i in range(starts):
            states = _cusum_values(values[i : i + window], mean=mean, sigma=sigma)
            if any(s > h for s in states):
                alarmed += 1
        out[h] = alarmed / starts
    return out


def calibrate(weekly: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """Diagnostic parameterization: mean/sigma from the fed series itself."""

    values = [v for _, v in weekly]
    mean = sum(values) / len(values)
    sigma = boot_sigma(values, b=BOOT_B, block=BOOT_MEAN_BLOCK_WEEKS, seed=BOOT_SEED)
    return {
        "weeks": len(values),
        "weekly_mean": mean,
        "weekly_sigma_hat": sigma,
        "trajectory": cusum_trajectory(weekly, mean=mean, sigma=sigma),
    }


def evaluate_series(points: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calibrate + run on a backtest equity curve (diagnostic path)."""

    weekly = weekly_returns_from_points(points)
    result = calibrate(weekly)
    result["alarm_weeks"] = alarm_weeks(result["trajectory"])
    return result


# --- prereg registration row ---------------------------------------------------


def build_registration_row(*, registered_at: str, registered_by: str) -> dict[str, Any]:
    """One hash-chained prereg.jsonl row carrying the frozen section-5 spec."""

    row: dict[str, Any] = {
        "book_id": CUSUM_BOOK,
        "spec_hash": cusum_spec_hash(),
        "hypothesis": (
            "Edge-lifetime monitor for book A (S-01): one-sided downward weekly "
            "CUSUM of ledger returns against the frozen backtest baseline; "
            "S_w > h* indicates edge-death risk and records PAUSE"
        ),
        "judgment_rule": (
            "z_w=(r_w-kappa0*mean)/sigma_hat, S_w=max(0,S_{w-1}-z_w-k); "
            "S_w>4.8 records PAUSE; S is never reset after an alarm"
        ),
        "registered_at": registered_at,
        "registered_by": registered_by,
        "cusum_spec": cusum_spec(),
    }
    return row


def append_registration(
    root: Path, *, registered_at: datetime, registered_by: str
) -> dict[str, Any]:
    """Append the registration row to ``<root>/prereg.jsonl`` (chain convention)."""

    path = prereg_path(root)
    prior = _rows_from_jsonl(path, strict=True) if path.exists() else []
    for entry in prior:
        if str(entry.get("book_id")) == CUSUM_BOOK:
            raise ChainError(f"{CUSUM_BOOK} is already registered in {path}")
    row = build_registration_row(
        registered_at=_require_utc(registered_at.isoformat(), "registered_at").isoformat(),
        registered_by=registered_by,
    )
    row["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    row["row_hash"] = _sha256(
        json.dumps({k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def load_registration(root: Path) -> dict[str, Any]:
    """Fail-closed: return the registered row, or raise."""

    path = prereg_path(root)
    found: dict[str, Any] | None = None
    for entry in _rows_from_jsonl(path, strict=True):
        if str(entry.get("book_id")) == CUSUM_BOOK:
            found = entry  # the last registration wins
    if found is None:
        raise ChainError(
            f"{path}: no {CUSUM_BOOK} row; the CUSUM monitor refuses to run unregistered"
        )
    spec = found.get("cusum_spec")
    if not isinstance(spec, dict):
        raise ChainError(f"{path}: {CUSUM_BOOK} row has no cusum_spec")
    baseline = spec.get("baseline_ref")
    scale = spec.get("scale")
    if not isinstance(baseline, dict) or not isinstance(scale, dict):
        raise ChainError(f"{path}: {CUSUM_BOOK} cusum_spec is malformed")
    try:
        constants = {
            "kappa0": float(spec["kappa0"]),
            "k": float(spec["k"]),
            "h_star": float(spec["h_star"]),
            "mean": float(baseline["weekly_mean"]),
            "sigma": float(baseline["weekly_sigma_hat"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ChainError(f"{path}: {CUSUM_BOOK} cusum_spec is missing numbers: {exc}") from exc
    if min(constants.values()) <= 0.0:
        raise ChainError(f"{path}: {CUSUM_BOOK} cusum_spec constants must be positive")
    found["_constants"] = constants
    return found


# --- live weekly run ------------------------------------------------------------


def baseline_sha_of_repo(repo_root: Path) -> str | None:
    path = repo_root / "crypto_backtest_results.json"
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mtm_rows_for_book(journal: Sequence[Mapping[str, Any]], book: str) -> list[Mapping[str, Any]]:
    return [row for row in journal if row.get("event") == "mtm" and str(row.get("book")) == book]


def run_weekly(
    data_dir: Path,
    now: datetime,
    *,
    repo_root: Path | None = None,
    book: str = S01_BOOK,
) -> dict[str, Any]:
    """Compute z_w/S_w for ``book`` over all complete ISO weeks; log; PAUSE flag.

    The CUSUM is recomputed from scratch each run from book A's mtm history
    with the registered constants, so the reported S_w is deterministic and S
    is never reset (an alarm carries into every later week via the recursion).
    """

    now_utc = _require_utc(now.isoformat(), "now")
    root = loop_root(data_dir)
    registration = load_registration(root)
    constants: dict[str, float] = registration["_constants"]
    journal = _load_journal(root)  # strict: fail-closed on a broken journal
    daily = daily_returns_from_mtm(_mtm_rows_for_book(journal, book))
    weekly = drop_incomplete_weeks(weekly_returns_from_daily(daily), now_utc)
    trajectory = cusum_trajectory(
        weekly,
        mean=constants["mean"],
        sigma=constants["sigma"],
        kappa0=constants["kappa0"],
        k=constants["k"],
    )
    if trajectory:
        week_label_last, z_last, s_last = trajectory[-1]
        weeks_observed: int = len(trajectory)
    else:
        week_label_last, z_last, s_last = None, None, 0.0
        weeks_observed = 0
    alarm = bool(s_last > constants["h_star"]) if trajectory else False
    observed_sha = baseline_sha_of_repo(repo_root or Path(__file__).resolve().parents[2])
    row: dict[str, Any] = {
        "schema": LOG_SCHEMA,
        "book_id": book,
        "computed_at": now_utc.isoformat(),
        "prereg_row_hash": str(registration.get("row_hash")),
        "baseline_ref_sha256": BASELINE_REF_SHA256,
        "baseline_sha_match": (
            None if observed_sha is None else observed_sha == BASELINE_REF_SHA256
        ),
        "constants": {
            "kappa0": constants["kappa0"],
            "k": constants["k"],
            "mean": constants["mean"],
            "sigma_hat": constants["sigma"],
            "h_star": constants["h_star"],
        },
        "week": week_label_last,
        "weeks_observed": weeks_observed,
        "z": z_last,
        "S": s_last,
        "alarm": alarm,
        "action": "PAUSE" if alarm else "continue",
        "note": None if trajectory else "insufficient_history: no complete ISO week yet",
    }
    log_path = root / "cusum_A.jsonl"
    prior = _rows_from_jsonl(log_path, strict=True) if log_path.exists() else []
    row["prev_hash"] = str(prior[-1]["row_hash"]) if prior else ""
    row["row_hash"] = _sha256(
        json.dumps({k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str)
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="demo-loop CUSUM edge-lifetime monitor (book A)")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--now", type=str, default=None, help="ISO clock override (tests)")
    args = parser.parse_args(argv)
    now = datetime.now(UTC) if args.now is None else datetime.fromisoformat(args.now)
    try:
        row = run_weekly(args.data_dir, now)
    except ChainError as exc:
        print(json.dumps({"ok": False, "error": f"ChainError: {exc}"}), file=sys.stderr)
        return 2
    print(json.dumps(row, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
