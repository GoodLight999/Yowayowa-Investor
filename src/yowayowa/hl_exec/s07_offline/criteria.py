# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""凍結された S-07 判定基準（設計書 §1・§6 S07-A09）。

このモジュールは **値を変更しない**。歴史資料
``docs/evidence/hl_testnet_s07_forward_20261103/S07_TESTNET_FORWARD_EVALUATION.md``
の 5 条件と、その退避評価器の比較演算子を写し取っただけである。ここを書き換える
ことは「成績基準の変更」に当たるため、独立裁可なしに行ってはならない。

判定の正式確定は保留する。歴史資料の評価窓が閉区間か半開区間かが未確認であり、
端点の扱いを勝手に決めると成績が変わり得るためである。したがって
``official_verdict`` は常に ``HELD_PENDING_ADJUDICATION`` を返し、テスト用の
``candidate_verdict`` だけを計算する。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

# --- 凍結窓（変更禁止） -----------------------------------------------------
WINDOW_START_UTC = _dt.datetime(2026, 11, 4, 0, 0, 0, tzinfo=_dt.UTC)
WINDOW_END_UTC = _dt.datetime(2026, 12, 2, 0, 0, 0, tzinfo=_dt.UTC)
WINDOW_DAYS = 28

# --- 凍結された 5 条件（変更禁止） -------------------------------------------
MIN_COMPLETED_ROUND_TRIPS = 8  # ① 完了往復 8 以上。8 未満は INCONCLUSIVE
NET_PNL_MUST_BE_POSITIVE = True  # ② 手数料・funding 後 net > 0（0 は不合格）
MAX_PEAK_DRAWDOWN_PCT = Decimal("10")  # ③ ピーク比 DD <= 10%
MAX_SLIPPAGE_MEDIAN_BP = Decimal("5")  # ④ slippage 中央値 <= 5bp
DAILY_END_FLAT_REQUIRED = True  # ⑤ 日次終了建玉 0
ALL_FILLS_REQUIRED = True  # ⑤ 全約定記録
LOCK_RECORD_REQUIRED = True  # ⑤ 全日ロック取得/解放記録
MAX_UNFILLED_RESIDUAL = 0  # ⑤ 未約定残留 0

# 窓は候補として半開区間 [start, end) を使う。歴史資料の閉区間明記は未確認。
WINDOW_SEMANTICS = "half_open_candidate"
WINDOW_SEMANTICS_SOURCE_CONFIRMED = False
OFFICIAL_VERDICT_HELD = "HELD_PENDING_ADJUDICATION"
OFFICIAL_VERDICT_REASON = (
    "historical source does not confirm whether the evaluation window is closed or "
    "half-open; endpoint differences must be adjudicated before an official verdict"
)


class Verdict(StrEnum):
    """判定値。INCONCLUSIVE は欠損・不明であり FAIL ではない。"""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


def _to_decimal(value: Any, *, what: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{what} must not be bool")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{what} is not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{what} must be finite: {value!r}")
    return result


@dataclass(frozen=True)
class Criterion:
    """1 条件分の判定結果。"""

    criterion_id: str
    requirement: str
    value: Any
    threshold: Any
    comparison: str
    verdict: Verdict
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "requirement": self.requirement,
            "value": self.value,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "verdict": str(self.verdict),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CriteriaMetrics:
    """判定に渡す実測値。未取得は ``None``（0 で補完しない）。"""

    completed_round_trips: int | None = None
    net_pnl: Decimal | None = None
    max_peak_drawdown_pct: Decimal | None = None
    slippage_median_bp: Decimal | None = None
    all_fills_recorded: bool | None = None
    daily_end_flat: bool | None = None
    all_days_lock_recorded: bool | None = None
    unfilled_residual: int | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CriteriaVerdict:
    candidate_verdict: Verdict
    official_verdict: str
    official_reason: str
    criteria: tuple[Criterion, ...]
    problems: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_verdict": str(self.candidate_verdict),
            "official_verdict": self.official_verdict,
            "official_reason": self.official_reason,
            "criteria": [c.as_dict() for c in self.criteria],
            "problems": list(self.problems),
        }


def evaluate_frozen_criteria(metrics: CriteriaMetrics) -> CriteriaVerdict:
    """5 条件を凍結値のまま評価する（公式判定は常に保留）。"""
    problems = list(metrics.problems)
    out: list[Criterion] = []

    # ① 完了往復 >= 8。8 未満は FAIL ではなく INCONCLUSIVE（設計書 §1）。
    trips = metrics.completed_round_trips
    if trips is None:
        out.append(
            Criterion(
                "1",
                "completed round trips >= 8",
                None,
                MIN_COMPLETED_ROUND_TRIPS,
                ">=",
                Verdict.INCONCLUSIVE,
                "completed round trips were not measured",
            )
        )
        problems.append("round trips unmeasured")
    else:
        verdict = Verdict.INCONCLUSIVE if trips < MIN_COMPLETED_ROUND_TRIPS else Verdict.PASS
        out.append(
            Criterion(
                "1",
                "completed round trips >= 8",
                trips,
                MIN_COMPLETED_ROUND_TRIPS,
                ">=",
                verdict,
                "" if verdict is Verdict.PASS else "fewer than 8 completed round trips",
            )
        )

    # ② 手数料・funding 後 net > 0（厳密不等号。0 は不合格）。
    net = metrics.net_pnl
    if net is None:
        out.append(
            Criterion(
                "2",
                "net pnl after fees and funding > 0",
                None,
                "0",
                ">",
                Verdict.INCONCLUSIVE,
                "net pnl was not measured",
            )
        )
        problems.append("net pnl unmeasured")
    else:
        verdict = Verdict.PASS if net > 0 else Verdict.FAIL
        out.append(
            Criterion(
                "2",
                "net pnl after fees and funding > 0",
                str(net),
                "0",
                ">",
                verdict,
                "" if verdict is Verdict.PASS else "net pnl is not strictly positive",
            )
        )

    # ③ ピーク比最大 DD <= 10%（等号は合格）。
    dd = metrics.max_peak_drawdown_pct
    if dd is None:
        out.append(
            Criterion(
                "3",
                "peak-relative max drawdown <= 10%",
                None,
                str(MAX_PEAK_DRAWDOWN_PCT),
                "<=",
                Verdict.INCONCLUSIVE,
                "peak-relative drawdown was not measured",
            )
        )
        problems.append("drawdown unmeasured")
    else:
        verdict = Verdict.PASS if dd <= MAX_PEAK_DRAWDOWN_PCT else Verdict.FAIL
        out.append(
            Criterion(
                "3",
                "peak-relative max drawdown <= 10%",
                str(dd),
                str(MAX_PEAK_DRAWDOWN_PCT),
                "<=",
                verdict,
                "" if verdict is Verdict.PASS else "drawdown exceeds 10%",
            )
        )

    # ④ 全約定記録かつ slippage 中央値 <= 5bp。記録欠損は INCONCLUSIVE。
    all_fills = metrics.all_fills_recorded
    slip = metrics.slippage_median_bp
    if all_fills is None or slip is None:
        out.append(
            Criterion(
                "4",
                "all fills recorded and median slippage <= 5bp",
                None,
                str(MAX_SLIPPAGE_MEDIAN_BP),
                "<=",
                Verdict.INCONCLUSIVE,
                "fill completeness or slippage was not measured",
            )
        )
        problems.append("fill completeness/slippage unmeasured")
    elif not all_fills:
        out.append(
            Criterion(
                "4",
                "all fills recorded and median slippage <= 5bp",
                "incomplete",
                str(MAX_SLIPPAGE_MEDIAN_BP),
                "<=",
                Verdict.INCONCLUSIVE,
                "fill completeness cannot be proven",
            )
        )
    else:
        verdict = Verdict.PASS if slip <= MAX_SLIPPAGE_MEDIAN_BP else Verdict.FAIL
        out.append(
            Criterion(
                "4",
                "all fills recorded and median slippage <= 5bp",
                str(slip),
                str(MAX_SLIPPAGE_MEDIAN_BP),
                "<=",
                verdict,
                "" if verdict is Verdict.PASS else "median slippage exceeds 5bp",
            )
        )

    # ⑤ 日次終了建玉 0・全ロック記録・未約定残留 0。
    flat = metrics.daily_end_flat
    locks = metrics.all_days_lock_recorded
    residual = metrics.unfilled_residual
    if flat is None or locks is None or residual is None:
        out.append(
            Criterion(
                "5",
                "daily end flat, lock records complete, unfilled residual == 0",
                None,
                MAX_UNFILLED_RESIDUAL,
                "==",
                Verdict.INCONCLUSIVE,
                "daily flat/lock/residual was not measured",
            )
        )
        problems.append("daily flat/lock/residual unmeasured")
    elif not flat or not locks:
        out.append(
            Criterion(
                "5",
                "daily end flat, lock records complete, unfilled residual == 0",
                {"daily_end_flat": flat, "all_days_lock_recorded": locks},
                MAX_UNFILLED_RESIDUAL,
                "==",
                Verdict.FAIL if not flat else Verdict.INCONCLUSIVE,
                "a daily position was open" if not flat else "lock records are incomplete",
            )
        )
    elif residual != MAX_UNFILLED_RESIDUAL:
        out.append(
            Criterion(
                "5",
                "daily end flat, lock records complete, unfilled residual == 0",
                residual,
                MAX_UNFILLED_RESIDUAL,
                "==",
                Verdict.FAIL,
                "unfilled residual is not zero",
            )
        )
    else:
        out.append(
            Criterion(
                "5",
                "daily end flat, lock records complete, unfilled residual == 0",
                {"daily_end_flat": flat, "all_days_lock_recorded": locks},
                MAX_UNFILLED_RESIDUAL,
                "==",
                Verdict.PASS,
            )
        )

    if any(c.verdict is Verdict.FAIL for c in out):
        candidate = Verdict.FAIL
    elif any(c.verdict is Verdict.INCONCLUSIVE for c in out):
        candidate = Verdict.INCONCLUSIVE
    else:
        candidate = Verdict.PASS

    return CriteriaVerdict(
        candidate_verdict=candidate,
        official_verdict=OFFICIAL_VERDICT_HELD,
        official_reason=OFFICIAL_VERDICT_REASON,
        criteria=tuple(out),
        problems=tuple(dict.fromkeys(problems)),
    )


def window_bounds_ms() -> tuple[int, int]:
    """凍結窓の半開区間 [start, end) を epoch ミリ秒で返す。"""
    return (
        int(WINDOW_START_UTC.timestamp() * 1000),
        int(WINDOW_END_UTC.timestamp() * 1000),
    )


def in_window(time_ms: int) -> bool:
    """候補窓 [start, end) の所属判定（端点: start は含む・end は含まない）。"""
    start, end = window_bounds_ms()
    return start <= time_ms < end


def classify_window(time_ms: int) -> str:
    """``inside`` / ``before_start`` / ``at_or_after_end`` の 3 値。"""
    start, end = window_bounds_ms()
    if time_ms < start:
        return "before_start"
    if time_ms >= end:
        return "at_or_after_end"
    return "inside"


def endpoint_sensitive(time_ms: int) -> bool:
    """閉区間解釈と半開区間解釈で所属が変わる端点かどうか。

    歴史資料の窓が閉区間だった場合、end ちょうどのイベントは含まれる。
    半開区間では含まれない。この差は独立裁可まで公式判定に使わない。
    """
    _, end = window_bounds_ms()
    return time_ms == end


def to_decimal(value: Any, *, what: str) -> Decimal:
    """外部公開用の安全な Decimal 変換（テスト・呼出側の共通入口）。"""
    return _to_decimal(value, what=what)
