# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""dry-run の失敗証跡（設計書 §3・S07-A07）。

* 進行イベントは ``events.jsonl`` へ逐次追記し、毎行 flush + fsync する。
* 終了時 ``report.json`` に run の全必須項目を書く。
* 書込に失敗したら :class:`EvidenceWriteError` を送出し、呼出側は非 zero で停止する。
  「証跡を保存できた」と装ってはいけない。
* 例外本文は安全化する。秘密鍵らしき 64 hex、token、署名、Authorization は伏せる。
* 既存 ``ExecutionJournal``（SIGNAL/INTENT/ORDER 三段）は一般失敗イベントに
  流用せず、この JSONL を別に持つ。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from yowayowa.hl_exec.s07_offline.isolation import IsolatedRunRoot, IsolationError

SCHEMA_VERSION = "s07-offline/1"
MODE_DRY_RUN = "dry_run"
REQUIRED_REPORT_KEYS = (
    "run_id",
    "schema_version",
    "mode",
    "network",
    "data_kind",
    "started_at",
    "finished_at",
    "stage",
    "input_sha256",
    "window",
    "error_class",
    "error_safe_message",
    "applied_count",
    "excluded_count",
    "state_sha256_before",
    "state_sha256_after",
    "reconciliation_status",
    "retryable",
    "retry_basis",
    "unresolved_order_ids",
)

_SECRET_KEYS = frozenset(
    {
        "private_key",
        "privatekey",
        "secret",
        "seed",
        "signature",
        "sig",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "agent_key",
        "wallet_key",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"0x[0-9a-fA-F]{64}"),  # 32-byte private key 形式
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(private[_-]?key|secret|token|signature)\s*[=:]\s*\S+"),
)


class EvidenceWriteError(RuntimeError):
    """証跡の書込に失敗した。成功を装わず、呼出側は非 zero で停止する。"""


def _canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def state_sha256(state: Any) -> str:
    import hashlib

    return hashlib.sha256(_canonical_bytes(state)).hexdigest()


def redact_text(text: str) -> str:
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """秘密を落とした JSON 安全な値へ変換する。"""
    if key is not None and key.lower() in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, _dt.datetime):
        return value.astimezone(_dt.UTC).isoformat()
    return redact_text(str(value))


def safe_exception(exc: BaseException) -> tuple[str, str]:
    """例外を分類名と安全化メッセージに分ける（traceback は保存しない）。"""
    return type(exc).__name__, redact_text(str(exc))[:500]


def _default_clock() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


@dataclass
class RunRecorder:
    """1 run 分の逐次証跡。run ごとに新規 root を要求する。"""

    root: IsolatedRunRoot
    run_id: str
    clock: Callable[[], _dt.datetime]
    mode: str
    network: str
    data_kind: str
    input_sha256: str
    window: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION
    started_at: str = ""
    seq: int = 0
    _events: Any = field(default=None, repr=False)
    _closed: bool = False

    @classmethod
    def begin(
        cls,
        root: IsolatedRunRoot,
        *,
        run_id: str,
        clock: Callable[[], _dt.datetime] | None = None,
        mode: str = MODE_DRY_RUN,
        network: str = "testnet",
        data_kind: str,
        input_sha256: str,
        window: Mapping[str, Any],
    ) -> RunRecorder:
        recorder = cls(
            root=root,
            run_id=run_id,
            clock=clock or _default_clock,
            mode=mode,
            network=network,
            data_kind=data_kind,
            input_sha256=input_sha256,
            window=window,
            started_at=(clock or _default_clock)().astimezone(_dt.UTC).isoformat(),
        )
        try:
            recorder._events = root.open_exclusive("events.jsonl")
        except Exception as exc:
            raise EvidenceWriteError(
                f"failed to open events.jsonl under the isolated run root: {exc}"
            ) from exc
        recorder.event(
            "run_started",
            mode=mode,
            network=network,
            data_kind=data_kind,
            input_sha256=input_sha256,
        )
        return recorder

    def event(self, kind: str, **fields: Any) -> None:
        """1 イベントを追記し、flush + fsync する。失敗は握り潰さない。"""
        if self._closed or self._events is None:
            raise EvidenceWriteError("event stream is already closed")
        self.seq += 1
        row = {
            "seq": self.seq,
            "ts": self.clock().astimezone(_dt.UTC).isoformat(),
            "kind": kind,
            **sanitize(fields),
        }
        try:
            self._events.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            self._events.flush()
            import os

            os.fsync(self._events.fileno())
        except OSError as exc:
            raise EvidenceWriteError(f"failed to append {kind!r} to events.jsonl: {exc}") from exc

    def finish(
        self,
        *,
        status: str,
        stage: str,
        applied_count: int,
        excluded_count: int,
        state_before: Any,
        state_after: Any,
        reconciliation_status: str,
        retryable: bool,
        retry_basis: str,
        unresolved_order_ids: list[Any] | None = None,
        error: BaseException | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``report.json`` を書き、report dict を返す（失敗時は例外）。"""
        error_class = "" if error is None else type(error).__name__
        error_message = "" if error is None else redact_text(str(error))[:500]
        report: dict[str, Any] = {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "mode": self.mode,
            "network": self.network,
            "data_kind": self.data_kind,
            "started_at": self.started_at,
            "finished_at": self.clock().astimezone(_dt.UTC).isoformat(),
            "status": status,
            "stage": stage,
            "input_sha256": self.input_sha256,
            "window": dict(self.window),
            "error_class": error_class,
            "error_safe_message": error_message,
            "applied_count": applied_count,
            "excluded_count": excluded_count,
            "state_sha256_before": state_sha256(state_before),
            "state_sha256_after": state_sha256(state_after),
            "reconciliation_status": reconciliation_status,
            "retryable": retryable,
            "retry_basis": retry_basis,
            "unresolved_order_ids": list(unresolved_order_ids or []),
        }
        if extra:
            report.update(sanitize(extra))
        missing = [key for key in REQUIRED_REPORT_KEYS if key not in report]
        if missing:
            raise EvidenceWriteError(f"report is missing required keys: {missing}")
        try:
            with self.root.open_exclusive("report.json") as handle:
                handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
                handle.write("\n")
                handle.flush()
                import os

                os.fsync(handle.fileno())
        except IsolationError as exc:
            raise EvidenceWriteError(f"failed to write report.json: {exc}") from exc
        except OSError as exc:
            raise EvidenceWriteError(f"failed to write report.json: {exc}") from exc
        self.close()
        return report

    def close(self) -> None:
        if self._events is not None and not self._closed:
            try:
                self._events.close()
            finally:
                self._closed = True

    def __enter__(self) -> RunRecorder:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
