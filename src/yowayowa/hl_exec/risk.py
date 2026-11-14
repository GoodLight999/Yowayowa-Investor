# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""リスク制限値の判定（純関数のみ・ネットワークなし・状態なし）。

数値の出典は設計正本 docs/CIO_HL_EXECUTION_TRACK_PLAN.md §2.4
（CTO承認済み CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md rev.2 準拠）。
すべての関数は引数から判定を返すだけで、グローバル状態も入出力も持たない。

境界規約（このモジュール全体で統一）:
  * 上限系（証拠金・建玉数・名目額・レバレッジ）: 上限ちょうどは許可、
    超えたら拒否（``value > cap`` で違反）。隔離証拠金は絶対額上限（20.0 USDC）と
    比率上限（当日開始残高の10.0%・1セント切り下げ）の小さい側を実効 cap とする。
  * 下限系（最小発注額・清算距離・事前残高）: ちょうどは許可、
    満たさなければ拒否。
  * 損失停止系（日次損失・DD）: 「閾値に達した瞬間」に発動（``<= 閾値``）。
    絶対額と比率のうち、早く発動する側（小さい側の閾値）を採用する
    （fail-safe。200 USDC の想定口座では両者が同値になる）。
"""

from __future__ import annotations

import dataclasses
import math

# ---------------------------------------------------------------- 設計書の数値
MICRO_PILOT_EQUITY_CAP_USDC = 200.0
ISOLATED_MARGIN_PER_POSITION_CAP_USDC = 20.0
ISOLATED_MARGIN_FRACTION_OF_EQUITY = 0.10
POSITION_NOTIONAL_CAP_USDC = 60.0
MIN_NOTIONAL_USDC = 10.0
LEVERAGE_CAP = 3.0
MAX_CONCURRENT_POSITIONS = 1
DAILY_LOSS_STOP_RATIO = 0.02
DAILY_LOSS_STOP_USDC = 4.0
DRAWDOWN_STOP_RATIO = 0.04
DRAWDOWN_STOP_USDC = 8.0
LIQUIDATION_DISTANCE_ALERT_RATIO = 0.15
MARGIN_PREFLIGHT_FEE_BUFFER_USDC = 5.0


class RiskInputError(ValueError):
    """判定への入力が不正（負の証拠金など）。判定前に即座に落とす。"""


@dataclasses.dataclass(frozen=True)
class RiskCheck:
    """1件分の判定結果。ok が False のときは reason に理由が入る。"""

    ok: bool
    rule: str
    value: float
    limit: float
    reason: str = ""


def _check(ok: bool, rule: str, value: float, limit: float, reason: str) -> RiskCheck:
    return RiskCheck(ok=ok, rule=rule, value=float(value), limit=float(limit), reason=reason)


# ---------------------------------------------------------------- 個別ゲート
def check_isolated_margin(
    margin_usdc: float,
    *,
    equity_usdc: float,
) -> RiskCheck:
    """1建玉あたりの隔離証拠金上限（20.0 USDC・口座の10.0%）。

    分離（is_cross=False）モードで清算枠を物理隔離し、auto-margin は禁止
    という設計の入口となる数値上限。実効 cap は min(20.0, 0.10 * equity_usdc)
    を1セント単位で切り下げて適用する。
    """
    if margin_usdc <= 0:
        raise RiskInputError("isolated margin must be positive")
    if not (math.isfinite(equity_usdc) and equity_usdc > 0):
        raise RiskInputError("equity_usdc must be finite and positive")
    effective_cap = (
        math.floor(
            min(
                ISOLATED_MARGIN_PER_POSITION_CAP_USDC,
                ISOLATED_MARGIN_FRACTION_OF_EQUITY * equity_usdc,
            )
            * 100.0
        )
        / 100.0
    )
    ok = margin_usdc <= effective_cap
    return _check(
        ok,
        rule="isolated_margin_per_position",
        value=margin_usdc,
        limit=effective_cap,
        reason="" if ok else f"isolated margin {margin_usdc} exceeds cap {effective_cap}",
    )


def check_concurrent_positions(
    open_count: int,
    *,
    cap: int = MAX_CONCURRENT_POSITIONS,
) -> RiskCheck:
    """同時建玉数（S-07 rev.1 は BTC 単一銘柄のみ・最大 1 建玉）。"""
    if open_count < 0:
        raise RiskInputError("open position count must be >= 0")
    ok = open_count <= cap
    return _check(
        ok,
        rule="max_concurrent_positions",
        value=float(open_count),
        limit=float(cap),
        reason="" if ok else f"{open_count} open positions exceeds cap {cap}",
    )


def check_position_notional(
    notional_usdc: float,
    *,
    cap: float = POSITION_NOTIONAL_CAP_USDC,
) -> RiskCheck:
    """1建玉（=1注文）の名目額上限（60.0 USDC・誤発注の物理遮断）。"""
    if notional_usdc <= 0:
        raise RiskInputError("notional must be positive")
    ok = notional_usdc <= cap
    return _check(
        ok,
        rule="position_notional_cap",
        value=notional_usdc,
        limit=cap,
        reason="" if ok else f"notional {notional_usdc} exceeds cap {cap}",
    )


def check_min_notional(
    notional_usdc: float,
    *,
    floor: float = MIN_NOTIONAL_USDC,
) -> RiskCheck:
    """最小発注額（全パーペチュアル共通 $10.0・満たさない発注は即時拒否）。"""
    if notional_usdc <= 0:
        raise RiskInputError("notional must be positive")
    ok = notional_usdc >= floor
    return _check(
        ok,
        rule="min_notional",
        value=notional_usdc,
        limit=floor,
        reason="" if ok else f"notional {notional_usdc} below floor {floor}",
    )


def check_leverage(
    notional_usdc: float,
    isolated_margin_usdc: float,
    *,
    cap: float = LEVERAGE_CAP,
) -> RiskCheck:
    """実効レバレッジ上限（設定3.0倍: 建玉60 USDC / 証拠金20 USDC）。"""
    if notional_usdc <= 0 or isolated_margin_usdc <= 0:
        raise RiskInputError("notional and margin must be positive")
    leverage = notional_usdc / isolated_margin_usdc
    ok = leverage <= cap
    return _check(
        ok,
        rule="leverage_cap",
        value=leverage,
        limit=cap,
        reason="" if ok else f"leverage {leverage:.4f} exceeds cap {cap}",
    )


# ---------------------------------------------------------------- 停止系
def daily_loss_stop_hit(
    day_realized_pnl_usdc: float,
    *,
    day_start_equity: float = MICRO_PILOT_EQUITY_CAP_USDC,
) -> bool:
    """日次損失停止（Circuit Breaker）。

    当日累計確定損益が閾値に達した瞬間に True。閾値は
    「絶対額 4.0 USDC」と「当日開始残高の 2.0%」のうち早く発動する側
    （小さい方）。設計の想定口座（200 USDC）ではどちらも 4.0 USDC。
    発動時は全建玉を成行決済し、翌日 00:00 UTC まで新規発注をロックする。
    """
    if day_start_equity <= 0:
        raise RiskInputError("day_start_equity must be positive")
    ratio_threshold = DAILY_LOSS_STOP_RATIO * day_start_equity
    threshold = min(DAILY_LOSS_STOP_USDC, ratio_threshold)
    return day_realized_pnl_usdc <= -threshold


def drawdown_stop_hit(current_equity: float, peak_equity: float) -> bool:
    """累積DD停止（Max Drawdown Stop）。

    ピークEquityからの下落が「絶対額 8.0 USDC」と「4.0%」のうち
    早く発動する側に達した時点で True。発動時は全決済・運用完全停止・
    設計再審査（自動再開はしない）。
    """
    if peak_equity <= 0:
        raise RiskInputError("peak_equity must be positive")
    if current_equity > peak_equity:
        return False  # ピーク更新中は下落でない
    drop_usdc = peak_equity - current_equity
    drop_ratio = drop_usdc / peak_equity
    return drop_ratio >= DRAWDOWN_STOP_RATIO or drop_usdc >= DRAWDOWN_STOP_USDC


def liquidation_distance_ok(distance_ratio: float) -> bool:
    """清算までの距離が安全か（15.0% 以上なら True・未満は異常事態）。

    設定レバレッジ3.0倍・BTC MMR 1.25%の理論清算逆行幅は 32.49%。
    通常損切り（1.8 x ATR14、約5〜6%）から清算までの距離が 15% 未満に
    近づいたら即時成行決済・プロセス終了という異常系の入口。
    """
    if distance_ratio < 0:
        raise RiskInputError("distance_ratio must be >= 0")
    return distance_ratio >= LIQUIDATION_DISTANCE_ALERT_RATIO


def margin_preflight_ok(
    withdrawable_usdc: float,
    required_isolated_margin_usdc: float,
    *,
    fee_buffer: float = MARGIN_PREFLIGHT_FEE_BUFFER_USDC,
) -> bool:
    """発注前の残高事前チェック（§2.7-4 残高不足）。

    出金可能残高が「要求分離証拠金 + 手数料バッファ 5.0 USDC」を満たすか。
    不足時は発注をバイパスし WARNING（自動リトライ禁止）。
    """
    if required_isolated_margin_usdc <= 0:
        raise RiskInputError("required margin must be positive")
    return withdrawable_usdc >= required_isolated_margin_usdc + fee_buffer


# ---------------------------------------------------------------- 集約ゲート
def evaluate_entry_gates(
    *,
    isolated_margin_usdc: float,
    open_position_count: int,
    notional_usdc: float,
    equity_usdc: float,
) -> tuple[RiskCheck, ...]:
    """新規エントリー前に通すべき静的ゲートの集約（順序固定）。

    日次損失・DD・清算距離は「口座状態」の停止系であり、呼び出し側が
    先に daily_loss_stop_hit / drawdown_stop_hit を見て止まる前提。
    ここでは1注文の組立に関わる静的上限を一括判定する。
    """
    return (
        check_isolated_margin(isolated_margin_usdc, equity_usdc=equity_usdc),
        check_concurrent_positions(open_position_count),
        check_position_notional(notional_usdc),
        check_min_notional(notional_usdc),
    )
