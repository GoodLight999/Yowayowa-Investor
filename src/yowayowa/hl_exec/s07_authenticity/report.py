# ruff: noqa: RUF001, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない

"""S-07 X1 真正性レポートの JSON / Markdown 出力。

``data_kind`` は必須で、``synthetic`` と ``real`` を混ぜない。合成データの実行は
「検証器の単体動作」であり、実データ真正性の証明ではないことをバナーで明示する。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from yowayowa.hl_exec.s07_authenticity.verifier import VerificationResult, jsonable
from yowayowa.hl_exec.s07_offline.report import DISCLAIMERS, require_data_kind

TOLERANCE_SPEC = {
    "unit": "USDC（Hyperliquid の fee/closedPnl/accountValue は 10 進 6 桁）",
    "fee_quantization": "fill ごとに 1e-6 USDC の量子化（切り捨て）",
    "equity_quantization": "accountValue は 1e-6 USDC",
    "cycle_fill_residual": "(fill 数 + funding 行数 + 1) × 1e-6 USDC",
    "entry_px_display": "ジャーナル/台帳の fill_px・entryPx は 0.1 USDC 切り捨て",
    "journal_net": "(fill 数 + 1) × 1e-6 + 2 × 0.1 × 数量 USDC",
    "ledger_display": "daily_ledger の equity/daily_pnl は 1e-4 USDC（差 <= 5e-5）",
    "rounded_values_in_report": "なし。残差は Decimal のまま文字列化する",
}


def build_report(
    *,
    result: VerificationResult,
    data_kind: str,
    run: Mapping[str, Any],
) -> dict[str, Any]:
    kind = require_data_kind(data_kind)
    return {
        "report_title": "S-07 X1 real-data authenticity report",
        "data_kind": kind,
        "disclaimer": DISCLAIMERS[kind],
        "official_result": False,
        "official_result_reason": (
            "this report verifies stored data authenticity; it is not an operating result"
        ),
        "verdict": result.verdict,
        "run": dict(run),
        "summary": _summary(result),
        "checks": [check.as_dict() for check in result.checks],
        "metrics": result.as_dict()["metrics"],
        "inputs": [item.as_dict() for item in result.inputs],
        "live_provenance": result.as_dict()["live_provenance"],
        "tolerance_spec": TOLERANCE_SPEC,
        "safety": {
            "orders_sent": 0,
            "signatures_created": 0,
            "keys_read": 0,
            "endpoints_used": ["POST /info (public address only)"],
            "writes_to_data_dir": 0,
        },
        "problems": list(result.problems),
    }


def _summary(result: VerificationResult) -> dict[str, Any]:
    statuses = [str(check.status) for check in result.checks]
    counts: dict[str, Any] = {
        "checks": len(result.checks),
        "pass": statuses.count("PASS"),
        "fail": statuses.count("FAIL"),
        "warn": statuses.count("WARN"),
        "blocked": statuses.count("BLOCKED"),
    }
    for check_id in (
        "oid-journal-live",
        "fill-aggregation",
        "partial-fills",
        "cycle-equity",
        "reported-closedpnl",
        "journal-net-precision",
        "funding",
        "time-order",
        "position-chain",
        "ledger-vs-raw",
    ):
        try:
            counts[check_id] = dict(result.check(check_id).counts)
        except KeyError:  # pragma: no cover - 防御的
            continue
    metrics = result.metrics
    counts["ledger_orders"] = metrics.get("ledger_orders")
    counts["live_fills"] = metrics.get("live_fills")
    counts["multi_fill_orders"] = metrics.get("multi_fill_orders")
    counts["cycles"] = metrics.get("cycles")
    return cast(dict[str, Any], jsonable(counts))


def render_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={_fmt(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(item) for item in value) + "]"
    return str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    data_kind = str(report["data_kind"])
    lines: list[str] = [
        "# S-07 X1 実データ真正性レポート",
        "",
        f"- データ種別: **{data_kind}**",
        f"- 判定: **{report['verdict']}**",
        f"- 生成: {report['run'].get('generated_at', '')}",
        f"- 実行: `{report['run'].get('command', '')}`",
        f"- 口座: `{report['run'].get('account', '')}`（公開アドレスのみ）",
        "",
        f"> {report['disclaimer']}",
        "",
        "**実注文 0・署名 0・鍵非接触。** 使用したのは `POST /info` の読み取りのみ。",
        "",
        "## 1. 判定内訳",
        "",
        "| check | status | 主な件数 |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        key_counts = {
            key: value
            for key, value in check["counts"].items()
            if not isinstance(value, (dict, list))
        }
        lines.append(f"| `{check['id']}` | {check['status']} | {_fmt(key_counts)} |")
    lines.extend(
        [
            "",
            "## 2. 実測件数",
            "",
            "```json",
            json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## 3. サイクル別の残差（丸めなし・USDC）",
            "",
            "| day | fills | Δequity | fill由来net | fill残差 | "
            "報告closedPnl残差 | entryPx量子化 | 帰属後残差 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    cycles = report["metrics"].get("cycles_detail", {})
    for day in sorted(cycles):
        row = cycles[day]
        lines.append(
            f"| {day} | {row['fills']} | {row['delta_equity']} | "
            f"{row['exact_pnl_fills']} − {row['fees']} + {row['funding']} | "
            f"{row['fill_derived_residual']} | {row['reported_residual']} | "
            f"{row['entry_px_quantization']} | {row['attributed_residual']} |"
        )
    lines.extend(
        [
            "",
            "## 4. 不合格・要確認の詳細",
            "",
        ]
    )
    any_detail = False
    for check in report["checks"]:
        if check["status"] in ("PASS",) or not check["details"]:
            continue
        any_detail = True
        lines.append(f"### `{check['id']}` ({check['status']})")
        lines.extend(f"- {detail}" for detail in check["details"])
        lines.append("")
    if not any_detail:
        lines.append("- なし")
        lines.append("")
    lines.extend(
        [
            "## 5. 許容範囲（単位と根拠）",
            "",
        ]
    )
    lines.extend(f"- {key}: {value}" for key, value in report["tolerance_spec"].items())
    lines.extend(
        [
            "",
            "## 6. 入力と sha256",
            "",
            "| file | rows | size | sha256 |",
            "|---|---|---|---|",
        ]
    )
    for item in report["inputs"]:
        lines.append(f"| `{item['path']}` | {item['rows']} | {item['size']} | `{item['sha256']}` |")
    lines.extend(
        [
            "",
            "## 7. 実データの出典",
            "",
            "```json",
            json.dumps(report["live_provenance"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## 8. 限界",
            "",
            "- `/info` の `userFills` は既定で直近の約定を返す。本検証は口座の全履歴 16 fill を",
            "  取得できたことを応答件数と台帳件数の一致で確認している。",
            "- 判定は保存済み台帳と公開オンチェーン約定の突合であり、戦略の収益性や",
            "  本番環境の執行品質を証明しない。",
            "",
        ]
    )
    return "\n".join(lines)
