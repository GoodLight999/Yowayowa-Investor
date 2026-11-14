# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""S-07 X1: 実データ真正性の検証（読み取り専用・実注文 0）。

保存済み台帳（``raw_events`` / ``execution_journal`` / ``trades`` /
``daily_ledger``）と公開アドレスの ``userFills`` / ``userFunding`` を突き合わせ、
OID の 1 対 1 対応・部分約定の集計・fee/funding と Δequity の整合・時刻の
単調性・重複を検証する。署名・鍵・送信の経路はこのパッケージに存在しない。
"""

from yowayowa.hl_exec.s07_authenticity.bookkeeping import (
    ChainReconstruction,
    OidAggregate,
    aggregate_by_oid,
    exact_realized_pnl,
    reconstruct_position_chain,
)
from yowayowa.hl_exec.s07_authenticity.models import (
    AuthenticityError,
    LiveFill,
    LiveSnapshot,
    OrderRecord,
    SourceFile,
)
from yowayowa.hl_exec.s07_authenticity.report import build_report, render_json, render_markdown
from yowayowa.hl_exec.s07_authenticity.sources import (
    SourceBundle,
    build_live_snapshot,
    load_bundle,
    load_live_from_files,
)
from yowayowa.hl_exec.s07_authenticity.verifier import (
    Check,
    Status,
    VerificationResult,
    verify,
)

__all__ = [
    "AuthenticityError",
    "ChainReconstruction",
    "Check",
    "LiveFill",
    "LiveSnapshot",
    "OidAggregate",
    "OrderRecord",
    "SourceBundle",
    "SourceFile",
    "Status",
    "VerificationResult",
    "aggregate_by_oid",
    "build_live_snapshot",
    "build_report",
    "exact_realized_pnl",
    "load_bundle",
    "load_live_from_files",
    "reconstruct_position_chain",
    "render_json",
    "render_markdown",
    "verify",
]
