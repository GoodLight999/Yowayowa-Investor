# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""キルスイッチ — fail-closed（先にロックを永続化してから取消を行う）。

設計正本 docs/CIO_HL_EXECUTION_TRACK_PLAN.md §2.4-2 の緊急停止シーケンス
（2026-11-02 CTO検収で修正された「ロック → 取消」の順を契約とする）:

  Step 1: 取引所との通信を行う前に、ローカルのキルスイッチファイルを
          アトミックに作成し fsync まで完了させる。以降の全プロセスは
          ``is_engaged()`` で新規発注を遮断される。ここで通信が切れても
          ロック状態は確実に残る（fail-closed）。
  Step 2: cancel_fn（未約定一括取消のコールバック。送信系実装は後段カード
          が担うため、本カードでは注入する形にする）を呼ぶ。
          例外・タイムアウトが起きてもロックは恒久維持され、結果は
          「未解決（Shutdown Unresolved）」として呼び出し側に返る。
          通信が失敗したからといって「完了」とみなすことは厳格に禁止。
  Step 3〜5（建玉全決済・注文0件/建玉0件の照合・完了判定と通知分岐）は
  送信系実装後の後段カードの範囲。本モジュールは Step 1〜2 の順序契約と
  照合に必要な ``is_engaged`` の安定した永続化だけを提供する。

ロックの解除はこのモジュールには用意しない（解除は人の手でファイルを
消す運用のみ。自動解除は fail-closed を壊す）。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_LOCK_PATH = "/root/.secrets/HL_KILL_SWITCH"

Clock = Callable[[], _dt.datetime]


class KillSwitchEngagedError(RuntimeError):
    """キルスイッチがロック中のため、新規の取引処理を拒否した。"""


@dataclasses.dataclass(frozen=True)
class ShutdownResult:
    """緊急停止の結果。steps は実行順序の記録（順序契約の証拠になる）。"""

    engaged: bool
    lock_path: str
    steps: tuple[str, ...]
    cancel_ok: bool
    error: str | None


def _utc_iso(now: _dt.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.UTC)
    return now.astimezone(_dt.UTC).isoformat(timespec="milliseconds")


def _fsync_dir(path: Path) -> None:
    """ディレクトリエントリ自体を fsync し、クラッシュ後も名前が残るようにする。"""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class KillSwitch:
    """ファイル存在判定のキルスイッチ（最優先のローカル遮断）。"""

    def __init__(self, lock_path: str | Path = DEFAULT_LOCK_PATH) -> None:
        self._lock_path = Path(lock_path)

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def is_engaged(self) -> bool:
        """ロックファイルが存在すれば遮断中（設計書 §2.4-2 の判定規約）。"""
        return self._lock_path.exists()

    def ensure_may_trade(self) -> None:
        """発注直前に呼ぶ。ロック中なら新規処理を拒否する。"""
        if self.is_engaged():
            raise KillSwitchEngagedError(
                f"kill switch engaged; refusing to proceed (lock: {self._lock_path})"
            )

    def read_lock(self) -> dict[str, Any] | None:
        """ロックファイルの中身（存在しなければ None）。"""
        if not self._lock_path.exists():
            return None
        data: Any = json.loads(self._lock_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"kill switch lock is not a JSON object: {self._lock_path}")
        return data

    def engage(
        self,
        *,
        reason: str,
        cancel_fn: Callable[[], Any] | None = None,
        now: _dt.datetime | None = None,
    ) -> ShutdownResult:
        """緊急停止を実行する。

        契約: cancel_fn を呼ぶ時点でロックは必ず既に永続化済みである。
        cancel_fn が例外を投げても本メソッドは例外を再送出せず、
        ``cancel_ok=False`` と ``error`` に載せて返す（ロックは維持、
        呼び出し側が「未解決」として管理者へ通知する）。
        """
        steps: list[str] = []
        if self.is_engaged():
            steps.append("already_locked")
        else:
            self._persist_lock(reason=reason, now=now)
            steps.append("lock_persisted")
        if not self.is_engaged():  # 永続化の自己検証（書けたのに無い、を許さない）
            raise RuntimeError(f"kill switch lock vanished right after creation: {self._lock_path}")

        cancel_ok = True
        error: str | None = None
        if cancel_fn is not None:
            steps.append("cancel_attempted")
            try:
                cancel_fn()
            except Exception as exc:  # 通信断・API拒否・タイムアウト等すべて
                cancel_ok = False
                error = f"{type(exc).__name__}: {exc}"
                steps.append("cancel_failed")
            else:
                steps.append("cancel_ok")
        return ShutdownResult(
            engaged=True,
            lock_path=str(self._lock_path),
            steps=tuple(steps),
            cancel_ok=cancel_ok,
            error=error,
        )

    def _persist_lock(self, *, reason: str, now: _dt.datetime | None) -> None:
        """ロックをアトミックに永続化する（tmp作成 → fsync → rename → dir fsync）。

        rename は同一ファイルシステム上でアトミックであるため、途中で
        プロセスが死んでも「半分書けたロック」や「ロック無し」の状態に
        ならない。tmp 作成から fsync まで行う点が単純な open("w") との差。
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "engaged_at": _utc_iso(now if now is not None else _dt.datetime.now()),
            "reason": reason,
            "pid": os.getpid(),
        }
        tmp_path = self._lock_path.with_name(self._lock_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self._lock_path)
        _fsync_dir(self._lock_path.parent)
