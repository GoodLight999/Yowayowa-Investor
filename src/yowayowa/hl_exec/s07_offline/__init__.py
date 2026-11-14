# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 復帰候補のオフライン検証（隔離モジュール）。

設計書 ``docs/design/S07_REENTRY_DESIGN_20261109.md`` の受入試験のうち、
オフラインで検証可能な契約だけを実装する。送信・署名・鍵生成・取消の経路は
このパッケージに存在しない。停止標識には触れない。
"""

from yowayowa.hl_exec.s07_offline.criteria import (
    MAX_PEAK_DRAWDOWN_PCT,
    MAX_SLIPPAGE_MEDIAN_BP,
    MIN_COMPLETED_ROUND_TRIPS,
    OFFICIAL_VERDICT_HELD,
    WINDOW_END_UTC,
    WINDOW_START_UTC,
    CriteriaMetrics,
    CriteriaVerdict,
    Verdict,
    evaluate_frozen_criteria,
)
from yowayowa.hl_exec.s07_offline.drawdown import DrawdownResult, EquitySample, peak_drawdown
from yowayowa.hl_exec.s07_offline.evidence import EvidenceWriteError, RunRecorder
from yowayowa.hl_exec.s07_offline.fills import (
    Correction,
    Fill,
    FillIntegrityError,
    OrderReplay,
    OrderStatus,
    account_net,
    apply_corrections,
    completed_round_trip,
    ingest_fills,
    replay_order,
)
from yowayowa.hl_exec.s07_offline.isolation import IsolatedRunRoot, IsolationError
from yowayowa.hl_exec.s07_offline.manifest import (
    BarBatch,
    ManifestRejected,
    load_batch,
    partition_by_window,
)
from yowayowa.hl_exec.s07_offline.report import (
    DataKindError,
    render_json,
    render_markdown,
)

__all__ = [
    "MAX_PEAK_DRAWDOWN_PCT",
    "MAX_SLIPPAGE_MEDIAN_BP",
    "MIN_COMPLETED_ROUND_TRIPS",
    "OFFICIAL_VERDICT_HELD",
    "WINDOW_END_UTC",
    "WINDOW_START_UTC",
    "BarBatch",
    "Correction",
    "CriteriaMetrics",
    "CriteriaVerdict",
    "DataKindError",
    "DrawdownResult",
    "EquitySample",
    "EvidenceWriteError",
    "Fill",
    "FillIntegrityError",
    "IsolatedRunRoot",
    "IsolationError",
    "ManifestRejected",
    "OrderReplay",
    "OrderStatus",
    "RunRecorder",
    "Verdict",
    "account_net",
    "apply_corrections",
    "completed_round_trip",
    "evaluate_frozen_criteria",
    "ingest_fills",
    "load_batch",
    "partition_by_window",
    "peak_drawdown",
    "render_json",
    "render_markdown",
    "replay_order",
]
