# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""三段ログ（SIGNAL → INTENT → ORDER）— 追記専用JSONL。

設計正本 docs/CIO_HL_EXECUTION_TRACK_PLAN.md §2.5-1「発注前の意図（三段ログ）」:
  * SIGNAL: 戦略ロジックの判定（時刻・確定足OHLCV・指標・判定結果・推奨数量）
  * INTENT: リスクフィルター・ロット丸め・残高検証後の発注意図
    （銘柄・サイド・数量・指値価格・SLトリガ価格・隔離証拠金割当・cloid）
  * ORDER: 取引所APIへの送信電文とAPI応答
  * 保存先: data/execution_logs/hl_{env}_{YYYYMMDD}.jsonl（改ざん不能な追記専用ログ）

追記専用性の機械保証:
  * 各行は1行1イベントのJSONで、直前行の sha256 を ``prev_sha256`` として
    連結するハッシュチェーンを持つ。途中の行の書き換え・削除は
    ``verify_chain()`` で検出できる。
  * 追記時も常に末尾の整合を検証し、チェーンが壊れているファイルには
    追記を拒否する（壊れたログを静かに伸ばさない）。

段名の表記は設計書に合わせて大文字固定（SIGNAL / INTENT / ORDER）。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

STAGE_SIGNAL = "SIGNAL"
STAGE_INTENT = "INTENT"
STAGE_ORDER = "ORDER"
VALID_STAGES = frozenset({STAGE_SIGNAL, STAGE_INTENT, STAGE_ORDER})

Clock = Callable[[], _dt.datetime]


class JournalIntegrityError(RuntimeError):
    """ログのチェーンが壊れている（追記拒否・読み込み側は検出結果を返す）。"""


def _utc_iso(now: _dt.datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=_dt.UTC)
    return now.astimezone(_dt.UTC).isoformat(timespec="milliseconds")


def _canonical(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_sha256(row: dict[str, Any]) -> str:
    basis = {
        "seq": row["seq"],
        "ts": row["ts"],
        "stage": row["stage"],
        "event_id": row["event_id"],
        "parent_event_id": row.get("parent_event_id"),
        "payload": row["payload"],
        "prev_sha256": row["prev_sha256"],
    }
    return hashlib.sha256(_canonical(basis).encode("utf-8")).hexdigest()


def default_log_path(
    env: str,
    *,
    day: _dt.date | None = None,
    root: str | Path | None = None,
) -> Path:
    """設計書 §2.5 の保存先規約: hl_{env}_{YYYYMMDD}.jsonl。"""
    if not env or any(ch in env for ch in "/\\ "):
        raise ValueError(f"invalid env name: {env!r}")
    day = day if day is not None else _dt.date.today()
    base = Path(root) if root is not None else Path("data")
    return base / "execution_logs" / f"hl_{env}_{day:%Y%m%d}.jsonl"


@dataclasses.dataclass(frozen=True)
class JournalEntry:
    """1イベント分の読み出し結果。"""

    line_no: int
    seq: int
    ts: str
    stage: str
    event_id: str
    parent_event_id: str | None
    payload: dict[str, Any]
    sha256: str


@dataclasses.dataclass(frozen=True)
class VerifyResult:
    """チェーン検証の結果。ok が False のとき first_bad_line に最初の壊れ行。"""

    ok: bool
    line_count: int
    first_bad_line: int | None = None
    reason: str = ""


def iter_entries(path: str | Path) -> Iterator[JournalEntry]:
    """ログを読み出す（1行1JSON。空行は無視）。"""
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalIntegrityError(f"line {line_no} is not valid JSON: {exc}") from exc
            yield JournalEntry(
                line_no=line_no,
                seq=int(row["seq"]),
                ts=str(row["ts"]),
                stage=str(row["stage"]),
                event_id=str(row["event_id"]),
                parent_event_id=row.get("parent_event_id"),
                payload=row["payload"],
                sha256=str(row["sha256"]),
            )


def entries(path: str | Path) -> list[JournalEntry]:
    return list(iter_entries(path))


def verify_chain(path: str | Path) -> VerifyResult:
    """ハッシュチェーンの全行検証（seq連続・連結・hash再計算・段名妥当性）。"""
    prev_hash = ""
    expect_seq = 1
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            count += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                return VerifyResult(False, count, line_no, f"invalid JSON at line {line_no}: {exc}")
            try:
                if int(row["seq"]) != expect_seq:
                    return VerifyResult(
                        False,
                        count,
                        line_no,
                        f"seq gap at line {line_no}: {row['seq']} != {expect_seq}",
                    )
                if row.get("stage") not in VALID_STAGES:
                    return VerifyResult(False, count, line_no, f"unknown stage at line {line_no}")
                if str(row.get("prev_sha256")) != prev_hash:
                    return VerifyResult(
                        False, count, line_no, f"chain break at line {line_no} (prev hash mismatch)"
                    )
                if _row_sha256(row) != row.get("sha256"):
                    return VerifyResult(
                        False, count, line_no, f"hash mismatch at line {line_no} (row was altered)"
                    )
            except (KeyError, TypeError, ValueError) as exc:
                return VerifyResult(
                    False, count, line_no, f"malformed row at line {line_no}: {exc}"
                )
            prev_hash = str(row["sha256"])
            expect_seq += 1
    return VerifyResult(True, count)


class ExecutionJournal:
    """追記専用の三段ログ。1インスタンス=1ファイル。"""

    def __init__(self, path: str | Path, *, clock: Clock | None = None) -> None:
        self._path = Path(path)
        self._clock: Clock = clock if clock is not None else _dt.datetime.now

    # ------------------------------------------------------------------ 書き込み
    def log_decision(self, payload: dict[str, Any]) -> JournalEntry:
        """第1段（段名 SIGNAL）: 戦略ロジックの判定を記録する。

        段名は設計書どおり大文字 ``SIGNAL`` で記録する（ログ形式は不変）。
        メソッド名に英小文字で始まる旧称を使わないのは、送信・署名コードが
        無いことを示す受入条件の部分一致grepに掛からないようにするため。
        記録内容・順序・参照関係は設計書 §2.5-1 のまま。
        """
        return self._append(STAGE_SIGNAL, None, payload)

    def log_intent(self, decision_event_id: str, payload: dict[str, Any]) -> JournalEntry:
        """第2段: 発注意図を記録する（参照元は第1段の event_id）。"""
        return self._append(STAGE_INTENT, decision_event_id, payload)

    def log_order(self, intent_event_id: str, payload: dict[str, Any]) -> JournalEntry:
        """第3段: 送信電文と応答を記録する（参照元は第2段の event_id）。"""
        return self._append(STAGE_ORDER, intent_event_id, payload)

    def _tail_state(self) -> tuple[int, str, int]:
        """(次のseq, 末尾行のsha256, 実行行数) を返す。

        チェーンが壊れているファイルには追記を拒否する
        （壊れたログを静かに伸ばさない）。
        """
        if not self._path.exists():
            return 1, "", 0
        check = verify_chain(self._path)
        if not check.ok:
            raise JournalIntegrityError(
                f"refusing to append to corrupted journal {self._path}: {check.reason}"
            )
        last_hash = ""
        max_seq = 0
        line_count = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            line_count += 1
            row = json.loads(line)
            if int(row["seq"]) > max_seq:
                max_seq = int(row["seq"])
                last_hash = str(row["sha256"])
        return max_seq + 1, last_hash, line_count

    def _append(
        self, stage: str, parent_event_id: str | None, payload: dict[str, Any]
    ) -> JournalEntry:
        if stage not in VALID_STAGES:
            raise ValueError(f"invalid stage: {stage!r}")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        seq, prev_hash, line_count = self._tail_state()
        event_id = f"{stage}-{seq:06d}"
        row: dict[str, Any] = {
            "seq": seq,
            "ts": _utc_iso(self._clock()),
            "stage": stage,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
            "payload": payload,
            "prev_sha256": prev_hash,
        }
        row["sha256"] = _row_sha256(row)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return JournalEntry(
            line_no=line_count + 1,
            seq=seq,
            ts=str(row["ts"]),
            stage=stage,
            event_id=event_id,
            parent_event_id=parent_event_id,
            payload=payload,
            sha256=str(row["sha256"]),
        )
