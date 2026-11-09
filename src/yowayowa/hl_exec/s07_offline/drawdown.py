# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""ピーク比最大ドローダウンの候補計測（設計書 §4・S07-A04）。

凍結値は「ピーク比 10%」であり、このモジュールは**候補の明確化**であって
旧実装や成績基準の変更承認ではない。

* サンプルは凍結窓 [start, end) 内の日末（UTC）equity。
* ``peak_i = max(E_0, ..., E_i)``、``DD_i = (peak_i - E_i) / peak_i``、
  ``MaxDD = max(DD_i) * 100``。分母は固定 100 ではなく各時点の正の累積ピーク。
* 欠損・非有限・peak <= 0・外部資金移動で整合不能なら INCONCLUSIVE。
* 日末サンプルは日中最大 DD を保証しない。その事実を結果に明記する。
* 合成例 [100, 120, 108] の候補 DD は 10%（ピーク 120 が分母）。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from yowayowa.hl_exec.s07_offline.criteria import window_bounds_ms


class DrawdownVerdict(StrEnum):
    MEASURED = "MEASURED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class EquitySample:
    time_ms: int
    equity: Decimal
    external_flow: bool = False
    source: str = "synthetic"

    @property
    def utc(self) -> str:
        return _dt.datetime.fromtimestamp(self.time_ms / 1000, _dt.UTC).isoformat()


def _dec(value: Any, *, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{what} must not be bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{what} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{what} must be finite: {value!r}")
    return result


def parse_sample(row: Any) -> EquitySample:
    if not isinstance(row, dict):
        raise ValueError(f"equity sample must be an object, got {type(row).__name__}")
    for key in ("time", "equity"):
        if key not in row:
            raise ValueError(f"equity sample is missing {key!r}")
    time_ms = row["time"]
    if isinstance(time_ms, bool) or not isinstance(time_ms, int):
        raise ValueError(f"equity sample time must be epoch ms, got {time_ms!r}")
    return EquitySample(
        time_ms=time_ms,
        equity=_dec(row["equity"], what="equity"),
        external_flow=bool(row.get("external_flow", False)),
        source=str(row.get("source", "synthetic")),
    )


@dataclass(frozen=True)
class DrawdownPoint:
    time_ms: int
    equity: Decimal
    peak: Decimal
    drawdown_pct: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time_ms,
            "equity": str(self.equity),
            "peak": str(self.peak),
            "drawdown_pct": str(self.drawdown_pct),
        }


@dataclass
class DrawdownResult:
    verdict: DrawdownVerdict
    max_drawdown_pct: Decimal | None
    points: list[DrawdownPoint] = field(default_factory=list)
    sampled_days: int = 0
    frequency: str = "daily_utc_end_candidate"
    intraday_guaranteed: bool = False
    excluded_outside_window: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.verdict is DrawdownVerdict.MEASURED

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "max_drawdown_pct": (
                None if self.max_drawdown_pct is None else str(self.max_drawdown_pct)
            ),
            "points": [point.as_dict() for point in self.points],
            "sampled_days": self.sampled_days,
            "frequency": self.frequency,
            "intraday_guaranteed": self.intraday_guaranteed,
            "excluded_outside_window": self.excluded_outside_window,
            "problems": self.problems,
            "definition": "peak-relative: DD_i = (peak_i - E_i) / peak_i; peak_i = max(E_0..E_i)",
        }


def peak_drawdown(
    samples: Sequence[EquitySample],
    *,
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> DrawdownResult:
    """候補のピーク比最大 DD を計算する（公式判定には使わない）。"""
    if window_start_ms is None or window_end_ms is None:
        window_start_ms, window_end_ms = window_bounds_ms()
    if window_end_ms <= window_start_ms:
        raise ValueError("window_end_ms must be greater than window_start_ms")

    ordered = sorted(samples, key=lambda sample: sample.time_ms)
    inside = [sample for sample in ordered if window_start_ms <= sample.time_ms < window_end_ms]
    excluded = len(ordered) - len(inside)
    problems: list[str] = []
    if not inside:
        return DrawdownResult(
            verdict=DrawdownVerdict.INCONCLUSIVE,
            max_drawdown_pct=None,
            excluded_outside_window=excluded,
            problems=["no equity sample inside the frozen window"],
        )
    if any(sample.external_flow for sample in inside):
        problems.append(
            "external cash flow inside the window prevents a like-for-like peak comparison"
        )
    for sample in inside:
        if sample.equity <= 0:
            problems.append(f"non-positive equity at {sample.utc}: {sample.equity}")
    if problems:
        return DrawdownResult(
            verdict=DrawdownVerdict.INCONCLUSIVE,
            max_drawdown_pct=None,
            sampled_days=len(inside),
            excluded_outside_window=excluded,
            problems=problems,
        )

    peak = inside[0].equity
    points: list[DrawdownPoint] = []
    maximum = Decimal(0)
    for sample in inside:
        if sample.equity > peak:
            peak = sample.equity
        if peak <= 0:
            return DrawdownResult(
                verdict=DrawdownVerdict.INCONCLUSIVE,
                max_drawdown_pct=None,
                sampled_days=len(inside),
                excluded_outside_window=excluded,
                problems=[f"peak is non-positive at {sample.utc}"],
            )
        dd = (peak - sample.equity) / peak * Decimal(100)
        maximum = max(maximum, dd)
        points.append(
            DrawdownPoint(
                time_ms=sample.time_ms,
                equity=sample.equity,
                peak=peak,
                drawdown_pct=dd,
            )
        )
    return DrawdownResult(
        verdict=DrawdownVerdict.MEASURED,
        max_drawdown_pct=maximum,
        points=points,
        sampled_days=len(inside),
        excluded_outside_window=excluded,
    )
