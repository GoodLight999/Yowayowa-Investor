# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
r"""S-07 オフライン検証の別入口（隔離モジュール・dry-run 専用）。

この runner は **ネットワーク・署名器・注文クライアントを一切持たない**。
入力は呼出側が明示したローカル JSON（合成 fixture）だけで、出力は
``<base>/dry-run/<run_id>/`` の 4 ファイルに限定する。実注文・取消・署名・
鍵生成・mainnet 送信は行わない。停止標識 ``scripts/hl_testnet_s07/s07_daily_forward.py``
には触れない。

CLI::

    # --input は絶対パス必須。相対パスは IsolationError: input path must be absolute
    # （exit 2）で拒否される。--out のベースディレクトリは事前に存在が必要で
    # 自動作成されない（fail closed）。同じ run-id の再実行も
    # 「run root already exists」で拒否されるため、毎回新しいベースを作る。
    OUT=$(mktemp -d /tmp/s07-offline.XXXXXX)
    python -m yowayowa.hl_exec.s07_offline.runner \
        --input "$PWD/tests/fixtures/s07_offline_synthetic/run_synthetic_A02_A07_A08.json" \
        --out "$OUT" --run-id synthetic-a02-a07-a08 \
        --repo-root "$PWD"

終了コード:

* 0: 全 stage 成功・report.json/report.md 保存済み
* 2: 隔離違反・証跡書込失敗（成功を装わず stderr に最小診断）
* 3: 入力検証/照合の失敗（証跡は保存済み・再送なし）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yowayowa.hl_exec.s07_offline import criteria as frozen
from yowayowa.hl_exec.s07_offline.drawdown import parse_sample, peak_drawdown
from yowayowa.hl_exec.s07_offline.evidence import EvidenceWriteError, RunRecorder
from yowayowa.hl_exec.s07_offline.fills import (
    FillIntegrityError,
    FillPage,
    account_net,
    adverse_slippage_bp,
    assess_page_completeness,
    completed_round_trip,
    ingest_fills,
    median_slippage_bp,
    parse_funding,
    replay_order,
)
from yowayowa.hl_exec.s07_offline.isolation import (
    IsolatedRunRoot,
    IsolationError,
    default_forbidden_roots,
    guard_input_file,
)
from yowayowa.hl_exec.s07_offline.manifest import (
    BarBatch,
    ManifestRejected,
    load_batch,
    partition_by_window,
)
from yowayowa.hl_exec.s07_offline.report import build_payload, render_markdown

EXIT_OK = 0
EXIT_ISOLATION_OR_EVIDENCE = 2
EXIT_FAILED_EVIDENCE_SAVED = 3


@dataclass
class OfflineRunResult:
    exit_code: int
    status: str
    report: dict[str, Any] | None = None
    report_md: str = ""
    problems: list[str] = field(default_factory=list)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _window_payload() -> dict[str, Any]:
    start, end = frozen.window_bounds_ms()
    return {
        "start_ms": start,
        "end_ms": end,
        "start_utc": frozen.WINDOW_START_UTC.isoformat(),
        "end_utc": frozen.WINDOW_END_UTC.isoformat(),
        "semantics": frozen.WINDOW_SEMANTICS,
        "days": frozen.WINDOW_DAYS,
        "official_semantics_confirmed": frozen.WINDOW_SEMANTICS_SOURCE_CONFIRMED,
    }


def _retry_policy(error: BaseException) -> tuple[bool, str]:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True, "read-only transient failure; a bounded retry may be attempted"
    if isinstance(error, FillIntegrityError):
        return False, "validation/integrity failure; fix the input and start a new run"
    if isinstance(error, ManifestRejected):
        return False, "manifest/provenance rejection; fix the input and start a new run"
    return False, "unrecognized failure; do not retry automatically"


def run_cycle(
    payload: Mapping[str, Any],
    *,
    out_base: str | Path,
    run_id: str,
    clock: Callable[[], Any],
    forbidden_roots: Sequence[str | Path] = (),
) -> OfflineRunResult:
    """1 回の dry-run を実行する。送信は存在しない。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    input_hash = _sha256_bytes(raw)
    data_kind = payload.get("data_kind")
    root = IsolatedRunRoot.create(out_base, run_id, forbidden_roots=forbidden_roots)
    recorder = RunRecorder.begin(
        root,
        run_id=run_id,
        clock=clock,
        network="testnet",
        data_kind=str(data_kind),
        input_sha256=input_hash,
        window=_window_payload(),
    )
    state_before = {"orders": [], "positions": [], "status": "initial"}
    state_after: dict[str, Any] = {"orders": [], "positions": [], "status": "unknown"}
    problems: list[str] = []
    metrics: dict[str, Any] = {}
    acceptance = payload.get("acceptance", [])
    applied = 0
    excluded = 0

    try:
        batch_data = payload.get("bars")
        if not isinstance(batch_data, Mapping):
            raise ManifestRejected("payload.bars must be a manifest object")
        recorder.event("stage_started", stage="bars")
        batch: BarBatch = load_batch(batch_data)
        if batch.provenance.data_kind != data_kind and data_kind != "mixed":
            raise ManifestRejected(
                "payload.data_kind does not match the bar manifest; mixed must be declared"
            )
        partition = partition_by_window(batch.accepted)
        applied += len(batch.accepted)
        excluded += len(batch.excluded) + len(batch.gaps)
        recorder.event(
            "bars_validated",
            accepted=len(batch.accepted),
            excluded=len(batch.excluded),
            duplicates=batch.duplicate_count,
            gaps=len(batch.gaps),
            warmup=len(partition.warmup),
            active=len(partition.active),
            post=len(partition.post),
            complete=batch.complete,
        )
        if batch.problems:
            problems.extend(batch.problems)

        inject_stage = str((payload.get("inject") or {}).get("stage", ""))
        inject_error = str((payload.get("inject") or {}).get("error", ""))
        if inject_stage == "fills" and inject_error == "timeout":
            raise TimeoutError("synthetic timeout injection (offline; no network was used)")

        fills_section = payload.get("fills") or {}
        fill_rows = fills_section.get("rows", []) if isinstance(fills_section, Mapping) else []
        start, end = frozen.window_bounds_ms()
        recorder.event("stage_started", stage="fills")
        ingest = ingest_fills(
            fill_rows,
            network=str(fills_section.get("network", "testnet")),
            window_start_ms=start,
            window_end_ms=end,
        )
        funding = [parse_funding(row) for row in payload.get("funding", [])]
        net = account_net(fills=ingest.accepted, funding=funding)
        if not net.complete:
            problems.extend(net.problems)

        page_reports = []
        fills_complete: bool | None = payload.get("fills_complete")
        if payload.get("pages"):
            pages = [
                FillPage(
                    index=int(page["index"]),
                    rows=tuple(page.get("rows", [])),
                    limit=int(page.get("limit", 2000)),
                    next_page_first_time_ms=page.get("next_page_first_time_ms"),
                )
                for page in payload["pages"]
            ]
            completeness = assess_page_completeness(pages)
            fills_complete = (
                bool(fills_complete) and completeness.complete
                if fills_complete
                else (completeness.complete)
            )
            page_reports.append(completeness.as_dict())
            recorder.event("fill_pages_assessed", **completeness.as_dict())

        by_tid = {fill.tid: fill for fill in ingest.accepted}
        order_replays = []
        unresolved_ids: list[Any] = []
        for order in payload.get("orders", []):
            order_fills = [by_tid[tid] for tid in order.get("fill_tids", []) if tid in by_tid]
            replay = replay_order(
                cloid=str(order["cloid"]),
                requested_sz=order["requested_sz"],
                fills=order_fills,
                transport_failure=bool(order.get("transport_failure", False)),
                rejected=bool(order.get("rejected", False)),
                cancel_after_ms=order.get("cancel_after_ms"),
            )
            order_replays.append(replay)
            if replay.status.value == "UNKNOWN":
                unresolved_ids.append(replay.cloid)
            recorder.event("order_replayed", **replay.as_dict())

        round_trips = []
        for trip in payload.get("round_trips", []):
            entry = [by_tid[tid] for tid in trip.get("entry_tids", []) if tid in by_tid]
            exit_ = [by_tid[tid] for tid in trip.get("exit_tids", []) if tid in by_tid]
            funding_by_coin = [
                payment for payment in funding if payment.coin in (None, trip.get("coin"))
            ]
            round_trips.append(
                completed_round_trip(
                    coin=str(trip.get("coin", "BTC")),
                    entry_fills=entry,
                    exit_fills=exit_,
                    position_sz=trip.get("position_sz", 0),
                    open_order_count=int(trip.get("open_order_count", 0)),
                    funding=funding_by_coin,
                    reconciled=bool(trip.get("reconciled", True)),
                    realized_gross=trip.get("realized_gross"),
                )
            )
        completed_trips = sum(1 for trip in round_trips if trip.complete)

        samples = [parse_sample(row) for row in payload.get("equity", [])]
        drawdown = peak_drawdown(samples)
        metrics["drawdown"] = drawdown.as_dict()
        if not drawdown.complete:
            problems.extend(drawdown.problems)

        slippage_samples = []
        for item in payload.get("reference_prices", []):
            fill = by_tid.get(item.get("tid"))
            if fill is None:
                continue
            slippage_samples.append(
                adverse_slippage_bp(
                    side=fill.side,
                    fill_px=fill.px,
                    reference_px=frozen.to_decimal(item["reference_px"], what="reference_px"),
                )
            )
        slippage = median_slippage_bp(slippage_samples)

        criteria_metrics = frozen.CriteriaMetrics(
            completed_round_trips=completed_trips if round_trips else None,
            net_pnl=net.net if net.complete and ingest.accepted else None,
            max_peak_drawdown_pct=drawdown.max_drawdown_pct,
            slippage_median_bp=slippage,
            all_fills_recorded=fills_complete,
            daily_end_flat=payload.get("daily_end_flat"),
            all_days_lock_recorded=payload.get("all_days_lock_recorded"),
            unfilled_residual=payload.get("unfilled_residual"),
            problems=tuple(problems),
        )
        verdict = frozen.evaluate_frozen_criteria(criteria_metrics)
        metrics["net"] = net.as_dict()
        metrics["criteria"] = verdict.as_dict()
        metrics["fills"] = ingest.as_dict()
        metrics["round_trips"] = [trip.__dict__ for trip in round_trips]
        metrics["slippage_median_bp"] = None if slippage is None else str(slippage)

        state_after = {
            "orders": [replay.as_dict() for replay in order_replays],
            "positions": payload.get("positions", []),
            "unfilled_residual": payload.get("unfilled_residual"),
            "status": "dry_run_complete",
        }
        report_payload = build_payload(
            run={
                "run_id": run_id,
                "mode": "dry_run",
                "network": "testnet",
                "generated_at": clock().isoformat(),
                "input_sha256": input_hash,
            },
            data_kind=str(data_kind),
            acceptance=acceptance,
            metrics=metrics,
            problems=problems,
        )
        with root.open_exclusive("state.json") as handle:
            handle.write(json.dumps(state_after, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
        markdown = render_markdown(
            run=report_payload["run"],
            data_kind=str(data_kind),
            acceptance=acceptance,
            metrics=metrics,
            problems=problems,
        )
        with root.open_exclusive("report.md") as handle:
            handle.write(markdown)
        recorder.event("run_completed", status="COMPLETED")
        report = recorder.finish(
            status="COMPLETED",
            stage="report",
            applied_count=applied + len(ingest.accepted),
            excluded_count=excluded + len(ingest.quarantined),
            state_before=state_before,
            state_after=state_after,
            reconciliation_status="unresolved" if unresolved_ids else "reconciled",
            retryable=not unresolved_ids,
            retry_basis=(
                "order state UNKNOWN; automatic resend is prohibited"
                if unresolved_ids
                else "offline dry-run completed; no send path exists"
            ),
            unresolved_order_ids=unresolved_ids,
            extra=report_payload,
        )
        return OfflineRunResult(
            exit_code=EXIT_OK,
            status="COMPLETED",
            report=report,
            report_md=markdown,
            problems=problems,
        )
    except EvidenceWriteError:
        recorder.close()
        raise
    except (
        ManifestRejected,
        FillIntegrityError,
        IsolationError,
        TimeoutError,
        ConnectionError,
        ValueError,
    ) as exc:
        retryable, basis = _retry_policy(exc)
        try:
            recorder.event(
                "stage_failed",
                stage="offline",
                error_class=type(exc).__name__,
                error_safe_message=str(exc),
                retryable=retryable,
            )
            report = recorder.finish(
                status="FAILED",
                stage="offline",
                applied_count=applied,
                excluded_count=excluded,
                state_before=state_before,
                state_after=state_after,
                reconciliation_status="not_reached",
                retryable=retryable,
                retry_basis=basis,
                error=exc,
            )
        except EvidenceWriteError:
            recorder.close()
            raise
        return OfflineRunResult(
            exit_code=EXIT_FAILED_EVIDENCE_SAVED,
            status="FAILED",
            report=report,
            problems=[f"{type(exc).__name__}: {exc}"],
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="S-07 offline dry-run verification (no send path)")
    parser.add_argument("--input", required=True, help="synthetic manifest JSON")
    parser.add_argument("--out", required=True, help="temp dir; dry-run/<run_id> is created below")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--repo-root",
        default=".",
        help="repo root whose data/ is forbidden for dry-run output and input",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    forbidden = default_forbidden_roots(args.repo_root)
    try:
        input_path = guard_input_file(args.input, forbidden_roots=forbidden)
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("input must be a JSON object")
        if payload.get("data_kind") not in ("synthetic", "mixed", "real"):
            raise ValueError("data_kind must be explicitly synthetic/mixed/real")
        import datetime as _dt

        result = run_cycle(
            payload,
            out_base=args.out,
            run_id=args.run_id,
            clock=lambda: _dt.datetime.now(_dt.UTC),
            forbidden_roots=forbidden,
        )
    except EvidenceWriteError as exc:
        print(
            f"S07_OFFLINE_EVIDENCE_WRITE_FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_ISOLATION_OR_EVIDENCE
    except (IsolationError, OSError, ValueError) as exc:
        print(f"S07_OFFLINE_ABORTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ISOLATION_OR_EVIDENCE
    print(
        json.dumps(
            {
                "status": result.status,
                "exit_code": result.exit_code,
                "run_id": args.run_id,
                "problems": result.problems,
            },
            ensure_ascii=False,
        )
    )
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    sys.exit(main())
