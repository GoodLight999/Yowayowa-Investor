# ruff: noqa: RUF002, RUF003 — 日本語の句読点（全角）は本文テキストであり誤字ではない
"""S-07 オフライン試験レポートの renderer（設計書 §6・S07-A08）。

* JSON・Markdown の双方で ``data_kind`` を必須表示する。欠落・未知は拒否する。
* ``synthetic`` / ``mixed`` は「非公式・運用実績ではない」と明記する。
* 試験アサーションの PASS を運用成績の PASS に変換しない。``official_result`` は
  ``False`` に固定し、``test_status`` と分離して出力する。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

VALID_DATA_KINDS = ("synthetic", "mixed", "real")

DISCLAIMERS = {
    "synthetic": (
        "SYNTHETIC DATA / 合成データ — 本レポートは合成データによる試験であり、"
        "運用成績ではない。PASS は試験アサーションの PASS であり実績ではない。"
    ),
    "mixed": (
        "MIXED DATA / 合成混在 — 実取得データと合成データが混在しており、"
        "運用成績として使用できない。PASS は試験アサーションの PASS に限る。"
    ),
    "real": (
        "REAL DATA — 保存済みの実取得データ。それでも本レポートの PASS は"
        "オフライン契約試験の PASS であり、運用実績の証明ではない。"
    ),
}


class DataKindError(ValueError):
    """data_kind が欠落・未知（レポートを生成しない）。"""


def require_data_kind(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DataKindError("data_kind is required on every report (synthetic/mixed/real)")
    if not isinstance(value, str) or value not in VALID_DATA_KINDS:
        raise DataKindError(f"data_kind must be one of {list(VALID_DATA_KINDS)}, got {value!r}")
    return value


def _normalize_acceptance(
    acceptance: Sequence[Mapping[str, Any]] | Mapping[str, str] | None,
) -> list[dict[str, Any]]:
    if acceptance is None:
        return []
    if isinstance(acceptance, Mapping):
        return [
            {"id": str(key), "status": str(value), "reason": ""}
            for key, value in acceptance.items()
        ]
    rows: list[dict[str, Any]] = []
    for item in acceptance:
        if not isinstance(item, Mapping):
            raise DataKindError("acceptance rows must be objects")
        rows.append(
            {
                "id": str(item.get("id", "")),
                "status": str(item.get("status", "")),
                "reason": str(item.get("reason", "")),
            }
        )
    return rows


def build_payload(
    *,
    run: Mapping[str, Any],
    data_kind: str,
    acceptance: Sequence[Mapping[str, Any]] | Mapping[str, str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    problems: Sequence[str] = (),
) -> dict[str, Any]:
    """JSON/Markdown 共通のレポート dict を組み立てる。"""
    kind = require_data_kind(data_kind)
    return {
        "report_title": "S-07 offline verification report",
        "data_kind": kind,
        "disclaimer": DISCLAIMERS[kind],
        "official_result": False,
        "official_result_reason": (
            "offline acceptance assertions only; synthetic data cannot establish an "
            "operating result"
        ),
        "run": dict(run),
        "acceptance": _normalize_acceptance(acceptance),
        "metrics": dict(metrics or {}),
        "problems": list(problems),
    }


def render_json(
    *,
    run: Mapping[str, Any],
    data_kind: str,
    acceptance: Sequence[Mapping[str, Any]] | Mapping[str, str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    problems: Sequence[str] = (),
) -> str:
    payload = build_payload(
        run=run,
        data_kind=data_kind,
        acceptance=acceptance,
        metrics=metrics,
        problems=problems,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_markdown(
    *,
    run: Mapping[str, Any],
    data_kind: str,
    acceptance: Sequence[Mapping[str, Any]] | Mapping[str, str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    problems: Sequence[str] = (),
) -> str:
    payload = build_payload(
        run=run,
        data_kind=data_kind,
        acceptance=acceptance,
        metrics=metrics,
        problems=problems,
    )
    kind = payload["data_kind"]
    lines = [
        f"# {payload['report_title']} ({kind.upper()})",
        "",
        f"> **{payload['disclaimer']}**",
        ">",
        "> `official_result: false` — 試験の PASS は運用実績ではない。",
        "",
        f"- data_kind: `{kind}`",
        f"- run_id: `{payload['run'].get('run_id', '')}`",
        f"- generated_at: `{payload['run'].get('generated_at', '')}`",
        f"- mode: `{payload['run'].get('mode', '')}`",
        f"- network: `{payload['run'].get('network', '')}`",
        "",
    ]
    rows = payload["acceptance"]
    if rows:
        lines.extend(
            [
                "## Acceptance test status (test assertions only)",
                "",
                "| ID | status | reason |",
                "|---|---|---|",
            ]
        )
        for row in rows:
            reason = row["reason"].replace("|", "\\|")
            lines.append(f"| {row['id']} | {row['status']} | {reason} |")
        lines.append("")
    metrics_dict = payload["metrics"]
    if metrics_dict:
        lines.extend(["## Metrics", ""])
        for key in sorted(metrics_dict):
            lines.append(f"- {key}: `{metrics_dict[key]}`")
        lines.append("")
    if payload["problems"]:
        lines.extend(["## Problems", ""])
        lines.extend(f"- {problem}" for problem in payload["problems"])
        lines.append("")
    return "\n".join(lines)
