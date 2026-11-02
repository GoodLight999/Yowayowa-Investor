# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.risk の単体テスト（設計書 §2.4 の数値上限・境界値）。

境界規約の確認が主目的:
  * 上限系: 上限ちょうどは許可、超えたら拒否
  * 停止系: 「閾値に達した瞬間」に発動
"""

from __future__ import annotations

import pytest

from yowayowa.hl_exec.risk import (
    DAILY_LOSS_STOP_RATIO,
    RiskInputError,
    check_concurrent_positions,
    check_isolated_margin,
    check_leverage,
    check_min_notional,
    check_position_notional,
    daily_loss_stop_hit,
    drawdown_stop_hit,
    evaluate_entry_gates,
    liquidation_distance_ok,
    margin_preflight_ok,
)


def test_isolated_margin_boundary_at_cap() -> None:
    assert check_isolated_margin(20.0).ok is True
    assert check_isolated_margin(19.99).ok is True
    assert check_isolated_margin(20.0001).ok is False


def test_isolated_margin_reason_and_rule_names() -> None:
    check = check_isolated_margin(21.0)
    assert check.rule == "isolated_margin_per_position"
    assert "exceeds cap" in check.reason


def test_concurrent_positions_cap_is_one() -> None:
    assert check_concurrent_positions(0).ok is True
    assert check_concurrent_positions(1).ok is True
    assert check_concurrent_positions(2).ok is False


def test_position_notional_cap_boundary() -> None:
    assert check_position_notional(60.0).ok is True
    assert check_position_notional(60.0001).ok is False


def test_min_notional_boundary() -> None:
    assert check_min_notional(10.0).ok is True
    assert check_min_notional(9.99).ok is False


def test_leverage_cap_boundary() -> None:
    assert check_leverage(60.0, 20.0).ok is True  # 設定3.0倍ちょうど
    assert check_leverage(60.001, 20.0).ok is False


def test_daily_loss_stop_hits_exactly_at_threshold() -> None:
    assert daily_loss_stop_hit(-3.99) is False
    assert daily_loss_stop_hit(-4.0) is True  # 「達した瞬間」発動
    assert daily_loss_stop_hit(-4.01) is True
    assert daily_loss_stop_hit(0.0) is False
    assert daily_loss_stop_hit(2.0) is False  # 利益は停止しない


def test_daily_loss_stop_ratio_side_fires_earlier_on_small_equity() -> None:
    # 当日開始残高 100 USDC なら比率側（2%）が 2.0 USDC で先に発動する
    assert pytest.approx(2.0) == DAILY_LOSS_STOP_RATIO * 100.0
    assert daily_loss_stop_hit(-1.99, day_start_equity=100.0) is False
    assert daily_loss_stop_hit(-2.0, day_start_equity=100.0) is True


def test_drawdown_stop_boundary() -> None:
    assert drawdown_stop_hit(200.0, 200.0) is False
    assert drawdown_stop_hit(192.01, 200.0) is False
    assert drawdown_stop_hit(192.0, 200.0) is True  # 下落4.0%（8.0 USDC）で発動
    assert drawdown_stop_hit(191.99, 200.0) is True


def test_drawdown_stop_ignores_new_peak() -> None:
    assert drawdown_stop_hit(210.0, 200.0) is False


def test_liquidation_distance_boundary() -> None:
    assert liquidation_distance_ok(0.15) is True  # ちょうど15%はまだ安全側
    assert liquidation_distance_ok(0.149999) is False
    assert liquidation_distance_ok(0.0) is False
    assert liquidation_distance_ok(0.5) is True


def test_margin_preflight_boundary() -> None:
    assert margin_preflight_ok(25.0, 20.0) is True  # 20 + 5バッファちょうど
    assert margin_preflight_ok(24.99, 20.0) is False


def test_evaluate_entry_gates_all_pass() -> None:
    checks = evaluate_entry_gates(
        isolated_margin_usdc=20.0,
        open_position_count=1,
        notional_usdc=60.0,
    )
    assert all(c.ok for c in checks)
    assert [c.rule for c in checks] == [
        "isolated_margin_per_position",
        "max_concurrent_positions",
        "position_notional_cap",
        "min_notional",
    ]


def test_evaluate_entry_gates_flags_margin_breach_only() -> None:
    checks = evaluate_entry_gates(
        isolated_margin_usdc=25.0,
        open_position_count=1,
        notional_usdc=60.0,
    )
    failed = [c for c in checks if not c.ok]
    assert len(failed) == 1
    assert failed[0].rule == "isolated_margin_per_position"


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        (check_isolated_margin, (0.0,)),
        (check_position_notional, (-1.0,)),
        (check_min_notional, (0.0,)),
        (check_leverage, (10.0, 0.0)),
    ],
)
def test_invalid_inputs_rejected(fn: object, args: tuple) -> None:
    with pytest.raises(RiskInputError):
        fn(*args)  # type: ignore[operator]


def test_drawdown_rejects_nonpositive_peak() -> None:
    with pytest.raises(RiskInputError):
        drawdown_stop_hit(10.0, 0.0)


def test_daily_loss_rejects_nonpositive_equity() -> None:
    with pytest.raises(RiskInputError):
        daily_loss_stop_hit(-1.0, day_start_equity=0.0)
