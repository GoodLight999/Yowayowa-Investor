# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""hl_exec.killswitch の単体テスト（fail-closed順序契約の証明を含む）。

最重要ケース: 通信例外を注入したとき
  * 取消を試みた時点でロックファイルが既に存在する
  * 取消が失敗してもロックは残り、結果は「未解決」として返る
の2つを順序アサーションで証明する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yowayowa.hl_exec.killswitch import (
    DEFAULT_LOCK_PATH,
    KillSwitch,
    KillSwitchEngagedError,
)


def test_default_lock_path_matches_design() -> None:
    assert str(KillSwitch().lock_path) == DEFAULT_LOCK_PATH


def test_engage_creates_lock_before_cancel_and_lock_survives_cancel_failure(
    tmp_path: Path,
) -> None:
    """【順序契約の本体】例外注入で「ロック作成が取消より前」を証明する。"""
    lock = tmp_path / "HL_KILL_SWITCH"
    ks = KillSwitch(lock)
    assert ks.is_engaged() is False

    def cancel_fn() -> None:
        # 取消を試みるこの時点で、ロックは必ず既に存在していなければならない
        assert lock.exists(), "lock file must exist before cancel is attempted"
        assert ks.is_engaged() is True, "kill switch must already be engaged before cancel"
        raise ConnectionError("network unreachable")

    result = ks.engage(reason="drill", cancel_fn=cancel_fn)

    # 実行順序の記録（実装側が残した証拠）でもロック先行を確認
    assert result.steps == ("lock_persisted", "cancel_attempted", "cancel_failed")
    assert result.engaged is True
    assert result.cancel_ok is False
    assert result.error is not None
    assert "ConnectionError" in result.error
    # 取消が通信例外で失敗しても、ロックは恒久維持される（fail-closed）
    assert lock.exists()
    assert ks.is_engaged() is True


def test_engage_success_path_steps(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    result = ks.engage(reason="drill", cancel_fn=lambda: None)
    assert result.steps == ("lock_persisted", "cancel_attempted", "cancel_ok")
    assert result.cancel_ok is True
    assert result.error is None


def test_engage_without_cancel_fn_only_persists_lock(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    result = ks.engage(reason="manual drill")
    assert result.steps == ("lock_persisted",)
    assert ks.is_engaged() is True


def test_lock_file_content_and_atomic_tmp_cleanup(tmp_path: Path) -> None:
    lock = tmp_path / "HL_KILL_SWITCH"
    ks = KillSwitch(lock)
    ks.engage(reason="test reason")
    assert not (tmp_path / "HL_KILL_SWITCH.tmp").exists()  # tmp は rename 後に残らない
    data = json.loads(lock.read_text(encoding="utf-8"))
    assert data["reason"] == "test reason"
    assert isinstance(data["pid"], int)
    assert "engaged_at" in data


def test_engage_twice_is_idempotent_and_keeps_first_reason(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    ks.engage(reason="first")
    result = ks.engage(reason="second", cancel_fn=lambda: None)
    assert result.steps == ("already_locked", "cancel_attempted", "cancel_ok")
    # 最初のロック理由が保持される（上書きしない）
    assert json.loads(ks.lock_path.read_text(encoding="utf-8"))["reason"] == "first"


def test_ensure_may_trade_blocks_after_engage(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    ks.ensure_may_trade()  # 未ロックでは通る
    ks.engage(reason="drill")
    with pytest.raises(KillSwitchEngagedError, match="kill switch engaged"):
        ks.ensure_may_trade()


def test_read_lock_returns_none_when_not_engaged(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    assert ks.read_lock() is None
    ks.engage(reason="drill")
    lock = ks.read_lock()
    assert lock is not None and lock["reason"] == "drill"


def test_persisted_lock_is_readable_json_written_once(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "HL_KILL_SWITCH")
    ks.engage(reason="once")
    first = (tmp_path / "HL_KILL_SWITCH").read_bytes()
    # 2回目の engage は already_locked なのでファイルを書き換えない
    ks.engage(reason="twice")
    assert (tmp_path / "HL_KILL_SWITCH").read_bytes() == first
