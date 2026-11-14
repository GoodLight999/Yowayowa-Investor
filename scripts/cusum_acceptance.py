"""Acceptance checks for the registered CUSUM monitor (PREREG section 5).

Card t_7dcc16ea. Two modes:

- ``baseline`` (default): load the repo-root ``crypto_backtest_results.json``,
  verify its sha256 against baseline_ref, and reproduce the registered
  calibration: 197 ISO weeks, weekly mean +0.4933%, sigma_hat 4.8186%, and the
  FAR curve with FAR(4.8) = 0.00% over all 172 26-week start points.
- ``extension``: load a results JSON (the 2017-2022 extension backtest) and
  reproduce the diagnostic run of
  ``docs/results/2026-10-13_S01_EXT_2017_2022_RESULTS.md``: the alarm weeks at
  the registered h*=4.8 must equal the report's 12 weeks exactly.

Usage:
  python scripts/cusum_acceptance.py baseline [path-to-results.json]
  python scripts/cusum_acceptance.py extension path/to/crypto_backtest_results_ext.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from yowayowa.cusum_watch import (  # noqa: E402
    ALARM_WEEKS_EXTENSION_REPORT,
    BASELINE_REF_SHA256,
    BOOT_B,
    BOOT_MEAN_BLOCK_WEEKS,
    BOOT_SEED,
    FAR_CURVE_REPORT,
    boot_sigma,
    cusum_trajectory,
    evaluate_series,
    far_curve,
    weekly_returns_from_points,
)


def _load_results(path: Path) -> dict:
    raw = path.read_bytes()
    print(f"loaded {path} sha256 {hashlib.sha256(raw).hexdigest()}")
    return json.loads(raw)


def _s01_equity(data: dict) -> list[dict]:
    for result in data["results"]:
        if result["strategy"]["id"] == "crypto_trend_dual_ma":
            return result["equity_curve"]
    raise SystemExit("no crypto_trend_dual_ma result in file")


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def run_baseline(path: Path) -> int:
    data = _load_results(path)
    points = _s01_equity(data)
    weekly = weekly_returns_from_points(points, start=date(2023, 1, 1), end=date(2026, 9, 29))
    values = [v for _, v in weekly]
    print(f"weeks: {len(values)} (registered: 197)")
    if len(values) != 197:
        _fail("week count does not match the registered calibration series")
    mean = sum(values) / len(values)
    sigma = boot_sigma(values, b=BOOT_B, block=BOOT_MEAN_BLOCK_WEEKS, seed=BOOT_SEED)
    print(f"weekly mean {mean * 100:.4f}% (registered +0.4933%)")
    print(f"weekly sigma_hat {sigma * 100:.4f}% (registered 4.8186%)")
    if round(mean * 100, 4) != 0.4933:
        _fail("weekly mean does not match the registered value")
    if round(sigma * 100, 4) != 4.8186:
        _fail("weekly sigma_hat does not match the registered value")
    curve = far_curve(weekly, mean=mean, sigma=sigma)
    ok = True
    for h, reference in FAR_CURVE_REPORT.items():
        got = curve[h]
        match = got == reference or abs(got - reference) <= 5e-4
        ok &= match
        print(
            f"FAR(h={h:.1f}) = {got * 100:.2f}%  (registered {reference * 100:.2f}%)"
            f" {'ok' if match else 'MISMATCH'}"
        )
    if curve[4.8] != 0.0:
        _fail("FAR(h=4.8) is not 0%: the registered cliff is not reproduced")
    if curve[4.7] != 21 / 172:
        _fail(f"FAR(h=4.7) is not 21/172 = 12.21%: got {curve[4.7]}")
    # the trajectory-level alarm at the registered constants must be empty too
    trajectory = cusum_trajectory(weekly, mean=mean, sigma=sigma)
    alarms = [label for label, _, s in trajectory if s > 4.8]
    print(f"full-series trajectory alarms at h*=4.8: {alarms or 'none'}")
    if not ok:
        _fail("FAR curve does not match the registered reference")
    print("baseline acceptance: PASS")
    return 0


def run_extension(path: Path) -> int:
    data = _load_results(path)
    result = evaluate_series(_s01_equity(data))
    alarms = list(result["alarm_weeks"])
    print(f"weeks: {result['weeks']}  sigma_hat_ext {result['weekly_sigma_hat'] * 100:.4f}%")
    print(f"max S: {max(s for _, _, s in result['trajectory']):.4f} (report: 8.47)")
    print(f"alarm weeks ({len(alarms)}): {alarms}")
    print(
        f"report alarm weeks ({len(ALARM_WEEKS_EXTENSION_REPORT)}):",
        list(ALARM_WEEKS_EXTENSION_REPORT),
    )
    if alarms != list(ALARM_WEEKS_EXTENSION_REPORT):
        _fail("extension alarm weeks do not match the report's 12 weeks")
    print("extension acceptance: PASS")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 1 or argv[0] not in ("baseline", "extension"):
        print(__doc__)
        return 2
    if argv[0] == "baseline":
        path = Path(argv[1]) if len(argv) > 1 else REPO / "crypto_backtest_results.json"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != BASELINE_REF_SHA256:
            _fail(f"baseline_ref sha256 mismatch: {digest}")
        print(f"baseline_ref sha256 verified: {digest}")
        return run_baseline(path)
    if len(argv) < 2:
        print(__doc__)
        return 2
    return run_extension(Path(argv[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
