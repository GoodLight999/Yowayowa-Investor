#!/usr/bin/env python3
"""判定用ワンページ生成器（カード t_437a9f72）。

目的: cutover 以降に**確定した**前方局面だけを使い、
「1局面あたり期待値 / 95%信頼区間 / 累積独立局面数 / 最大単一局面損失 / 合否・キル」を1枚に出す。

凍結（このカードでは変更しない。変えるなら次の発散として別扱い）
- KILL_THRESHOLD_USD = -85.32   ← t_d4fdcfc4 で確定。測定時最悪局面 -42.657 USD の2倍。再導出しない。
- DEADLINE_UTC      = 2026-12-02 ← このUTC日以降の評価は「不合格」を含む最終判定になる。
- TARGET_INDEPENDENT_EPISODES = 20 ← 20独立局面に先に到達したら前倒しで判定する。
- CUTOVER_UTC = 2026-11-08T00:00:00Z ← 開始 < cutover は replay（累積・期待値・区間・キルに使わない）。
  boundary（開始 < cutover <= 終了）は exclusions.json が boundary_excluded として数えるだけで、判定に使わない。

合格 = 期待値 > 0 かつ 95%区間下限 > 0（トリガ到達後）。
キル = 確定した前方局面のいずれかで cluster_pnl_usd < -85.32 になった時点（期限を待たない）。
       評価は「データが届いた時点」で行い、evaluated_at_utc と latency_hours を decision_ledger.json に残す。
不合格 = 期限(2026-12-02)または20独立局面到達時に合格条件を満たさない。

読み取り: closed_episodes.jsonl / kill_ledger.json / exclusions.json / cumulative.json / episodes/*.json
書き込み: このディレクトリの decision_page.md / decision_page.json / decision_ledger.json のみ。
          既存の episodes/*.json・kill_ledger.json・cumulative.json は一切書き換えない。
発注・入金・口座操作はしない（HTTPクライアント・取引クライアントをimportしない）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cascade_sim import boot_ci  # noqa: E402  95%区間は測定と同一実装（2,000回・seed固定）

# --- 凍結定数（変更禁止。ここ以外に閾値を書かない） -------------------------
KILL_THRESHOLD_USD = -85.32
KILL_THRESHOLD_ORIGIN = (
    "測定時最悪局面 -42.657 USD（results_5m_primary.json 深さ2%/遅延30分/30独立局面）の2倍。"
    "カード t_d4fdcfc4 で確定（-85.32）。再導出・再調整しない"
)
DEADLINE_UTC = "2026-12-02"
TARGET_INDEPENDENT_EPISODES = 20
CUTOVER_UTC = "2026-11-08T00:00:00Z"
CI_MIN_N = 5
CI_LABEL = "bootstrap 2,000回・seed=20261107（cascade_sim.boot_ci）"

SCHEMA_VERSION = 1
CARD = "t_437a9f72"
# キル成立後の扱い（カード t_f2c6f343）。事前登録 t_0d06b1de の「この系統は全部終了」に対応し、
# キル発動時は判定資料そのものに「再挑戦しない・実資金/判定の候補から除外」を1行で残す。
KILL_DECISION_DOC = "docs/S07_ITEM5_KILL_DECISION_20261112.md"
TOP_DIR_NAME = "funding_top20"
OUT_DIR_NAME = "fwd_item5"
DEFAULT_DATA_DIR = Path("/root/current-work/yowayowa-investor/data/s07_paper")

PAGE_FILE = "decision_page.md"
JSON_FILE = "decision_page.json"
LEDGER_FILE = "decision_ledger.json"


class DecisionInputError(ValueError):
    """入力が凍結仕様と食い違う（判定を出してはいけない）状態。"""


# --------------------------------------------------------------------------- 時刻
def _ts(value, field: str) -> datetime:
    if not isinstance(value, str):
        raise DecisionInputError(f"{field}: 文字列のUTC時刻が必要: {value!r}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionInputError(f"{field}: 時刻として読めない: {value!r}") from exc
    if dt.tzinfo is None:
        raise DecisionInputError(f"{field}: タイムゾーンが無い: {value!r}")
    return dt.astimezone(timezone.utc)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CUTOVER = _ts(CUTOVER_UTC, "CUTOVER_UTC")
DEADLINE = date.fromisoformat(DEADLINE_UTC)


# --------------------------------------------------------------------------- 入力
def _is_number(value) -> bool:
    return (not isinstance(value, bool)) and isinstance(value, (int, float)) and math.isfinite(value)


def _read_json(path: Path):
    if not path.exists():
        raise DecisionInputError(f"入力が無い: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DecisionInputError(f"JSONとして読めない: {path}: {exc}") from exc


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(root) -> dict:
    """入力一式を読み、凍結仕様との不一致と再生分の混入を検出する（混入は例外）。"""
    root = Path(root)
    ledger = _read_json(root / "kill_ledger.json")
    exclusions = _read_json(root / "exclusions.json")

    if ledger.get("schema_version") != 2:
        raise DecisionInputError("kill_ledger.json: schema_version が 2 ではない")
    if ledger.get("threshold_usd") != KILL_THRESHOLD_USD:
        raise DecisionInputError(
            f"kill_ledger.json: threshold_usd {ledger.get('threshold_usd')!r} が凍結値 {KILL_THRESHOLD_USD} と違う")
    if _ts(ledger.get("cutover_utc"), "kill_ledger.cutover_utc") != CUTOVER:
        raise DecisionInputError("kill_ledger.json: cutover_utc が凍結値と違う")
    if ledger.get("status") not in ("ARMED", "KILLED"):
        raise DecisionInputError(f"kill_ledger.json: status が不正: {ledger.get('status')!r}")

    if exclusions.get("schema_version") != 2:
        raise DecisionInputError("exclusions.json: schema_version が 2 ではない")
    if _ts(exclusions.get("cutover_utc"), "exclusions.cutover_utc") != CUTOVER:
        raise DecisionInputError("exclusions.json: cutover_utc が凍結値と違う")

    events, seen, contaminated = [], set(), []
    ev_path = root / "closed_episodes.jsonl"
    if not ev_path.exists():
        raise DecisionInputError(f"入力が無い: {ev_path}")
    for lineno, line in enumerate(ev_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DecisionInputError(f"closed_episodes.jsonl:{lineno}: JSONとして読めない: {exc}") from exc
        eid = ev.get("episode_id")
        where = f"closed_episodes.jsonl:{lineno}({eid})"
        if ev.get("schema_version") != 2:
            raise DecisionInputError(f"{where}: schema_version が 2 ではない")
        if ev.get("type") != "episode_closed":
            raise DecisionInputError(f"{where}: type が episode_closed ではない")
        if ev.get("pnl_basis") != "all_trials_resolved":
            raise DecisionInputError(f"{where}: pnl_basis が all_trials_resolved ではない（未確定を混ぜない）")
        if ev.get("n_unresolved") != 0:
            raise DecisionInputError(f"{where}: n_unresolved が 0 ではない（未確定を混ぜない）")
        if not isinstance(ev.get("independent"), bool):
            raise DecisionInputError(f"{where}: independent が bool ではない")
        if not _is_number(ev.get("cluster_pnl_usd")):
            raise DecisionInputError(f"{where}: cluster_pnl_usd が有限の数値ではない")
        start = _ts(ev.get("episode_start_utc"), f"{where}.episode_start_utc")
        end = _ts(ev.get("episode_end_utc"), f"{where}.episode_end_utc")
        closed = _ts(ev.get("episode_closed_utc"), f"{where}.episode_closed_utc")
        if closed < start or end < start:
            raise DecisionInputError(f"{where}: 時刻の前後関係が壊れている")
        if start < CUTOVER:
            # COO 裁定: cutover 未満が1件でも累積に入っていたら不合格として差し戻す
            contaminated.append(eid)
            continue
        if not isinstance(eid, str) or not eid:
            raise DecisionInputError(f"{where}: episode_id が不正")
        if eid in seen:
            raise DecisionInputError(f"{where}: episode_id が重複（追記のみの台帳が壊れている）")
        seen.add(eid)
        events.append(ev)

    if contaminated:
        raise DecisionInputError(
            "再生分の混入: cutover未満の局面が closed_episodes.jsonl に "
            f"{len(contaminated)} 件ある（{', '.join(sorted(contaminated)[:5])}）"
            " — 累積に入れてはいけないため判定を出さない")

    events.sort(key=lambda e: (_ts(e["episode_closed_utc"], "closed"), e["episode_id"]))

    checks = _cross_check(root, events, exclusions, ledger)
    return {
        "root": root,
        "events": events,
        "kill_ledger": ledger,
        "exclusions": exclusions,
        "cumulative": _read_json(root / "cumulative.json") if (root / "cumulative.json").exists() else None,
        "checks": checks,
        "inputs": {
            "closed_episodes": {"path": str(ev_path), "sha256": _sha256(ev_path),
                                "lines": len(events)},
            "kill_ledger": {"path": str(root / "kill_ledger.json"),
                            "sha256": _sha256(root / "kill_ledger.json")},
            "exclusions": {"path": str(root / "exclusions.json"),
                           "sha256": _sha256(root / "exclusions.json")},
        },
    }


def _cross_check(root: Path, events: list[dict], exclusions: dict, ledger: dict) -> dict:
    """独立した成果物どうしを突き合わせる（再生分0件・当日分の扱いを隠さない）。"""
    event_ids = {ev["episode_id"] for ev in events}
    day_rows: dict[str, dict[str, dict]] = {}
    replay_ids, forward_in_files = [], 0
    for p in sorted((root / "episodes").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        rows = {}
        for e in rec.get("episodes") or []:
            rows[e.get("episode_id")] = e
            start = _ts(e.get("episode_start_utc"), f"{p.name}.episodes[].episode_start_utc")
            if start < CUTOVER:
                replay_ids.append(e.get("episode_id"))
            else:
                forward_in_files += 1
        day_rows[p.stem] = rows

    missing_in_day, pnl_mismatch, no_day_file = [], [], []
    for ev in events:
        rows = day_rows.get(ev["day_key"])
        if rows is None:
            no_day_file.append(ev["episode_id"])
            continue
        row = rows.get(ev["episode_id"])
        if row is None:
            missing_in_day.append(ev["episode_id"])
            continue
        if not _is_number(row.get("cluster_pnl_usd")) or abs(row["cluster_pnl_usd"] - ev["cluster_pnl_usd"]) > 1e-4:
            pnl_mismatch.append(ev["episode_id"])

    overlap = sorted(set(replay_ids) & event_ids)
    if missing_in_day:
        raise DecisionInputError(
            f"日次ファイルに無い確定イベントがある（追記のみの台帳が壊れている）: {missing_in_day[:5]}")
    if pnl_mismatch:
        raise DecisionInputError(f"日次ファイルと確定イベントの損益が食い違う: {pnl_mismatch[:5]}")
    if overlap:
        raise DecisionInputError(f"再生分が累積に混入している: {overlap[:5]}")

    return {
        "replay_ids_in_day_files": sorted(x for x in replay_ids if x),
        "replay_in_cumulative": 0,
        "day_file_episodes_forward": forward_in_files,
        "day_file_missing_for_events": no_day_file,
        "exclusions_forward_final": (exclusions.get("counts") or {}).get("forward_final"),
        "exclusions_episodes_total": (exclusions.get("counts") or {}).get("episodes_total"),
        "ledger_entries": len(ledger.get("entries") or []),
    }


# --------------------------------------------------------------------------- 評価
def evaluate(events: list[dict], *, kill_ledger_status: str, decision_status: str, as_of: date) -> dict:
    """合否・キルを決める唯一の関数。値の再解釈はしない（定数は上の1箇所だけ）。"""
    independent = [ev for ev in events if ev["independent"]]
    values = [float(ev["cluster_pnl_usd"]) for ev in independent]
    all_values = [float(ev["cluster_pnl_usd"]) for ev in events]

    mean = round(sum(values) / len(values), 4) if values else None
    ci = boot_ci(values) if len(values) >= CI_MIN_N else None
    worst = min(all_values) if all_values else None            # 最大単一局面損失（全確定局面）
    worst_independent = min(values) if values else None        # 独立局面だけの最悪
    killed = [ev for ev in events if float(ev["cluster_pnl_usd"]) < KILL_THRESHOLD_USD]

    kill = (kill_ledger_status == "KILLED" or decision_status == "KILLED" or bool(killed))
    reached_target = len(independent) >= TARGET_INDEPENDENT_EPISODES
    reached_deadline = as_of >= DEADLINE
    triggered = reached_target or reached_deadline

    if kill:
        verdict = "キル発動"
        reason = f"確定局面の損益が {KILL_THRESHOLD_USD} を下回った（期限を待たない）"
    elif not triggered:
        verdict = "判定待ち"
        reason = (f"独立局面 {len(independent)}/{TARGET_INDEPENDENT_EPISODES}、期限 {DEADLINE_UTC} 未到達")
    elif mean is not None and mean > 0 and ci is not None and ci[0] > 0:
        verdict = "合格"
        reason = f"期待値 {mean} > 0 かつ 95%区間下限 {ci[0]} > 0"
    else:
        verdict = "不合格"
        if not values:
            reason = "独立局面が0件で合格条件（期待値>0 かつ 区間下限>0）を満たせない"
        elif len(values) < CI_MIN_N:
            reason = f"独立局面が{len(values)}件で95%区間を算出できない（n<{CI_MIN_N}）"
        elif mean is not None and mean <= 0:
            reason = f"期待値 {mean} が正でない"
        else:
            lower = ci[0] if ci is not None else None
            reason = f"95%区間下限 {lower} が正でない"

    return {
        "verdict": verdict,
        "reason": reason,
        "mean_pnl_per_episode_usd": mean,
        "ci95": ci,
        "ci_method": CI_LABEL,
        "ci_available": ci is not None,
        "independent_episodes": len(independent),
        "episodes_final": len(events),
        "target_episodes": TARGET_INDEPENDENT_EPISODES,
        "reached_target": reached_target,
        "deadline_utc": DEADLINE_UTC,
        "deadline_reached": reached_deadline,
        "as_of_utc": as_of.isoformat(),
        "worst_episode_usd": worst,
        "worst_independent_episode_usd": worst_independent,
        "kill_threshold_usd": KILL_THRESHOLD_USD,
        "kill": kill,
        "kill_episode_ids": [ev["episode_id"] for ev in killed],
        "values": values,
    }


# --------------------------------------------------------------------------- 評価台帳
def _write_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def update_decision_ledger(root, events: list[dict], *, evaluated_at: datetime) -> dict:
    """届いた確定局面を「評価した時刻」つきで台帳に残す（追記のみ・KILLEDは単調）。

    収集側の kill_ledger.json は「確定→収集台帳」の遅延を記録する。
    こちらは「確定→判定評価」の遅延を記録し、遅延を隠さない（COO 裁定 2026-11-07）。
    """
    root = Path(root)
    path = root / LEDGER_FILE
    prev = {}
    if path.exists():
        prev = _read_json(path)
        if prev.get("threshold_usd") not in (None, KILL_THRESHOLD_USD):
            raise DecisionInputError("decision_ledger.json: 閾値が凍結値と違う")
        if prev.get("cutover_utc") not in (None, CUTOVER_UTC):
            raise DecisionInputError("decision_ledger.json: cutover が凍結値と違う")
    prev_entries = prev.get("entries") or []
    known = {e.get("episode_id") for e in prev_entries}
    prev_status = prev.get("status") or "ARMED"

    new_entries = []
    for ev in events:
        if ev["episode_id"] in known:
            continue
        closed = _ts(ev["episode_closed_utc"], "episode_closed_utc")
        end = _ts(ev["episode_end_utc"], "episode_end_utc")
        triggered = float(ev["cluster_pnl_usd"]) < KILL_THRESHOLD_USD
        new_entries.append({
            "episode_id": ev["episode_id"],
            "day_key": ev.get("day_key"),
            "episode_start_utc": ev["episode_start_utc"],
            "episode_end_utc": ev["episode_end_utc"],
            "episode_closed_utc": ev["episode_closed_utc"],
            "evaluated_at_utc": _utc(evaluated_at),
            # 確定（出口足終値＝損益確定）→ この判定評価
            "latency_hours": round((evaluated_at - closed).total_seconds() / 3600.0, 2),
            # 局面の最終事象 → この判定評価（COO 明示の episode_end_utc 基準も併記）
            "latency_from_end_hours": round((evaluated_at - end).total_seconds() / 3600.0, 2),
            "cluster_pnl_usd": ev["cluster_pnl_usd"],
            "n_filled": ev.get("n_filled"),
            "independent": bool(ev["independent"]),
            "threshold_usd": KILL_THRESHOLD_USD,
            "triggered": triggered,
        })
        known.add(ev["episode_id"])

    entries = prev_entries + new_entries
    triggered_entries = [e for e in entries if e.get("triggered")]
    status = "KILLED" if (prev_status == "KILLED" or triggered_entries) else "ARMED"
    if prev_status == "KILLED":
        killed_at = prev.get("killed_at_utc")
        trigger = prev.get("trigger")
    elif triggered_entries:
        first = triggered_entries[0]
        killed_at = first["evaluated_at_utc"]
        trigger = {k: first.get(k) for k in
                   ("episode_id", "day_key", "episode_end_utc", "episode_closed_utc",
                    "evaluated_at_utc", "latency_hours", "latency_from_end_hours",
                    "cluster_pnl_usd", "n_filled")}
    else:
        killed_at, trigger = None, None

    latencies = [e["latency_hours"] for e in entries if isinstance(e.get("latency_hours"), (int, float))]
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "card": CARD,
        "threshold_usd": KILL_THRESHOLD_USD,
        "threshold_origin": KILL_THRESHOLD_ORIGIN,
        "cutover_utc": CUTOVER_UTC,
        "deadline_utc": DEADLINE_UTC,
        "status": status,
        "killed_at_utc": killed_at,
        "trigger": trigger,
        "entries": entries,
        "counts": {"evaluated": len(entries), "triggered": len(triggered_entries)},
        "latency": {
            "definition": "局面損益の確定時刻(episode_closed_utc) → 判定評価時刻(evaluated_at_utc)",
            "max_hours": max(latencies) if latencies else None,
            "median_hours": round(statistics.median(latencies), 2) if latencies else None,
            "n": len(latencies),
        },
        "updated_at_utc": _utc(evaluated_at),
    }
    _write_atomic(path, ledger)
    return ledger


# --------------------------------------------------------------------------- 1ページ
def _fmt(value, unit="", nd=4, dash="n/a"):
    if value is None:
        return dash
    if isinstance(value, float):
        return f"{value:.{nd}f}{unit}"
    return f"{value}{unit}"


def _latency_line(ledger_entries: list[dict], decision_latency: dict) -> list[str]:
    """遅延を隠さない: 収集台帳（確定→台帳）と判定評価（確定→評価）を別々に出す。"""
    rec = [float(e["latency_s"]) for e in ledger_entries
           if isinstance(e.get("latency_s"), (int, float)) and not isinstance(e.get("latency_s"), bool)]
    lines = []
    if rec:
        hours = [round(v / 3600.0, 2) for v in rec]
        lines.append(
            f"- 観測遅延（確定→収集台帳）: 最大 **{max(hours):.2f}h** / 中央値 {statistics.median(hours):.2f}h / n={len(hours)}")
    else:
        lines.append("- 観測遅延（確定→収集台帳）: n=0（前方局面がまだ確定していない）。"
                     "参考実測: 1.05〜25.05h（先行カード t_25c7da31 の隔離実測）")
    if decision_latency.get("n"):
        lines.append(
            f"- 観測遅延（確定→判定評価）: 最大 **{decision_latency['max_hours']:.2f}h** / "
            f"中央値 {decision_latency['median_hours']:.2f}h / n={decision_latency['n']}")
    return lines


def render_page(result: dict, inputs: dict, decision_ledger: dict, *, generated_at: datetime) -> str:
    ex = inputs["exclusions"]
    counts = ex.get("counts") or {}
    checks = inputs["checks"]
    cum = inputs.get("cumulative") or {}
    ledger = decision_ledger or {}
    events = inputs["events"]

    # 凍結判定の後に観測された追加局面は、凍結判定と混ぜず1行で別掲する。
    # キー欠落・null は捏造せず N/A と明示する（0 や OK で埋めない）。
    add = result.get("additional_observations")
    add_lines: list[str] = []
    if isinstance(add, dict):
        ci = add.get("ci95")
        ci_txt = (f"[{_fmt(ci[0], ' USD')}, {_fmt(ci[1], ' USD')}]"
                  if isinstance(ci, (list, tuple)) and len(ci) == 2 and all(v is not None for v in ci)
                  else "N/A")
        add_lines = [
            "",
            "## 凍結後の追加観測",
            "",
            f"- 独立局面 {_fmt(add.get('independent_episodes'), '件', dash='N/A')}"
            f" / 期待値 {_fmt(add.get('mean_pnl_per_episode_usd'), ' USD', dash='N/A')}"
            f" / 95%区間 {ci_txt} / 仮判定 {_fmt(add.get('verdict'), dash='N/A')}",
        ]

    verdict = result["verdict"]
    lines = [
        "# 前方局面 判定ページ（項目5）",
        "",
        f"**判定: {verdict}** — {result['reason']}",
        "",
        # キル確定後は、この判定資料自体に「除外・再挑戦しない」を残す（カード t_f2c6f343）。
        *([f"- **扱い（KILL 確定）: この系統（深さ2%・遅延30分および深さ・遅延の他の組み合わせ）は"
           f"再挑戦しない。実資金・判定の候補から除外**（判定文書: {KILL_DECISION_DOC}）",
           ""] if verdict == "キル発動" else []),
        f"- 判定日（as_of, UTC）: {result['as_of_utc']}／期限: {result['deadline_utc']}"
        f"（期限到達: {'はい' if result['deadline_reached'] else 'いいえ'}）",
        f"- トリガ: 期限 or 独立局面 {result['target_episodes']}件（到達: "
        f"{'はい' if result['reached_target'] else 'いいえ'}）",
        f"- cutover: {CUTOVER_UTC}（開始がこれ未満の局面は累積に入れない）",
        f"- キル閾値: **{KILL_THRESHOLD_USD} USD**（{KILL_THRESHOLD_ORIGIN}）",
        "",
        "## 指標",
        "",
        "| 指標 | 値 |",
        "|---|---|",
        f"| 1局面あたり期待値 | {_fmt(result['mean_pnl_per_episode_usd'], ' USD')} |",
        f"| 95%信頼区間（下限／上限） | "
        f"{('[' + _fmt(result['ci95'][0], ' USD') + ', ' + _fmt(result['ci95'][1], ' USD') + ']') if result['ci95'] else 'n/a（n<' + str(CI_MIN_N) + '）'} |",
        f"| 累積の独立局面数 | {result['independent_episodes']} / {result['target_episodes']} |",
        f"| 確定局面数（独立でない局面を含む） | {result['episodes_final']} |",
        f"| 最大単一局面損失（全確定局面） | {_fmt(result['worst_episode_usd'], ' USD')} |",
        f"| 最大単一局面損失（独立局面のみ） | {_fmt(result['worst_independent_episode_usd'], ' USD')} |",
        f"| 判定結果 | **{verdict}** |",
        "",
        f"95%区間の方法: {result['ci_method']}。",
        "",
        *add_lines,
        "## 判定ロジック（凍結）",
        "",
        f"1. 合格 = 期待値 > 0 かつ 95%区間下限 > 0（トリガ到達後に評価）。",
        f"2. キル = 確定した前方局面のいずれかで cluster_pnl_usd < {KILL_THRESHOLD_USD} になった時点で即終了（判定日を待たない）。",
        f"3. 不合格 = 期限 {DEADLINE_UTC} または独立局面 {TARGET_INDEPENDENT_EPISODES} 件到達時に合格条件を満たさない。",
        "4. 未確定（censored を含む局面）は pending として損益 null・キル未評価・累積/キルに不使用。",
        "",
        "## 除外（隠さない）",
        "",
        f"- {ex.get('one_line', '(exclusions.json に one_line が無い)')}",
        f"- boundary_excluded（開始 < cutover <= 終了）: **{counts.get('boundary_excluded', 'n/a')} 件**"
        " — 期待値・区間・局面数・キル判定のいずれにも使わない",
        f"- pending（全trial未確定）: **{counts.get('pending', 'n/a')} 件** — 累積にもキルにも使わない",
        f"- replay（開始・終了とも cutover 前）: **{counts.get('replay', 'n/a')} 件** — 記録のみ、累積は0件",
        "",
        "## 遅延（確定→評価）",
        "",
        *_latency_line(ledger.get("entries") or [], (ledger.get("latency") or {})),
        "",
        "## キル",
        "",
    ]

    trig = ledger.get("trigger")
    if result["kill"] and trig:
        lines += [
            f"- **キル記録**: episode={trig.get('episode_id')} 損益 {_fmt(trig.get('cluster_pnl_usd'), ' USD')}"
            f" / 確定 {trig.get('episode_closed_utc')} → 評価 {trig.get('evaluated_at_utc')}"
            f"（差 {trig.get('latency_hours')}h）",
        ]
    elif result["kill"]:
        lines += [f"- **キル判定**: 収集台帳の状態が KILLED（{inputs['kill_ledger'].get('killed_at_utc')}）",
                  f"  - 対象: {', '.join(result['kill_episode_ids']) or '台帳に記録済み'}"]
    else:
        lines += ["- キル: 未発動（閾値を下回った確定局面は0件）"]

    lines += [
        "",
        "## データ検算",
        "",
        f"- 再生分の混入チェック: closed_episodes.jsonl 内の cutover 未満 = **0 件**"
        f"（day ファイル側の cutover 未満 {len(checks['replay_ids_in_day_files'])} 件は累積未使用）",
        f"- 日次ファイル照合: 確定イベント {len(events)} 件中 "
        f"{len(events) - len(checks['day_file_missing_for_events'])} 件が episodes/<day_key>.json と一致"
        f"（未生成 {len(checks['day_file_missing_for_events'])} 件 = 当日分など）",
        f"- exclusions.forward_final = {checks['exclusions_forward_final']} / "
        f"closed_episodes.jsonl = {len(events)}",
        f"- 収集台帳の累積独立局面 = {cum.get('cumulative_independent_episodes', 'n/a')} / "
        f"判定入力の独立局面 = {result['independent_episodes']}",
        f"- 収集側キル台帳: status={inputs['kill_ledger'].get('status')} / "
        f"entries={len(inputs['kill_ledger'].get('entries') or [])}",
        "",
        "## 出所",
        "",
        f"- closed_episodes.jsonl: `{inputs['inputs']['closed_episodes']['path']}`"
        f"（sha256 {inputs['inputs']['closed_episodes']['sha256']}）",
        f"- kill_ledger.json: `{inputs['inputs']['kill_ledger']['path']}`（sha256 {inputs['inputs']['kill_ledger']['sha256']}）",
        f"- exclusions.json: `{inputs['inputs']['exclusions']['path']}`（sha256 {inputs['inputs']['exclusions']['sha256']}）",
        f"- 生成: {_utc(generated_at)} / カード {CARD} / 仕様は t_d4fdcfc4（cascade_sim.py 流用・深さ2%/遅延30分/待ち30/保有60/往復9bps/1000USD）",
        f"- 実弾0・入金0・注文0・口座操作0（このスクリプトは取引クライアントを呼ばない）",
    ]

    if events:
        lines += ["", "## 確定局面（独立フラグ付き）", "",
                  "| episode_id | 確定UTC | 損益 USD | 独立 | kill |",
                  "|---|---|---|---|---|"]
        for ev in events:
            lines.append(
                f"| {ev['episode_id']} | {ev['episode_closed_utc']} | {ev['cluster_pnl_usd']:.4f} | "
                f"{'yes' if ev['independent'] else 'no'} | "
                f"{'KILL' if float(ev['cluster_pnl_usd']) < KILL_THRESHOLD_USD else '-'} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI
def run(root, *, as_of: date, now: datetime, write: bool = True) -> dict:
    inputs = load_inputs(root)
    prev_decision = {}
    ledger_path = Path(root) / LEDGER_FILE
    if ledger_path.exists():
        prev_decision = _read_json(ledger_path)
    if write:
        decision_ledger = update_decision_ledger(root, inputs["events"], evaluated_at=now)
    else:
        decision_ledger = prev_decision
    computed = evaluate(inputs["events"],
                        kill_ledger_status=inputs["kill_ledger"].get("status", "ARMED"),
                        decision_status=decision_ledger.get("status", "ARMED"),
                        as_of=as_of)
    out = Path(root)
    frozen_path = out / "decision_frozen.json"
    frozen_hash_path = out / "decision_frozen.sha256"
    frozen = _read_json(frozen_path) if frozen_path.exists() else None
    first_formal = frozen.get("result") if frozen else None
    if first_formal is None and (computed["reached_target"] or computed["deadline_reached"] or computed["kill"]):
        first_formal = computed
        frozen = {"schema_version": 1, "frozen_at_utc": _utc(now), "result": computed,
                  "input_sha256": inputs["inputs"]["closed_episodes"]["sha256"]}
        if write:
            frozen_bytes = json.dumps(frozen, ensure_ascii=False, indent=1) + "\n"
            frozen_hash = hashlib.sha256(frozen_bytes.encode("utf-8")).hexdigest()
            try:
                with frozen_path.open("x", encoding="utf-8") as fh:
                    fh.write(frozen_bytes)
                with frozen_hash_path.open("x", encoding="utf-8") as fh:
                    fh.write(frozen_hash + "\n")
            except FileExistsError:
                frozen = _read_json(frozen_path)
                first_formal = frozen["result"]
    if frozen and not frozen_hash_path.exists():
        raise DecisionInputError(
            f"{frozen_path.name} は存在するが {frozen_hash_path.name} が存在しない"
            f"（{frozen_path} / {frozen_hash_path}）ため、凍結結果を検証できません")
    if frozen and frozen_hash_path.exists():
        recorded_hash = frozen_hash_path.read_text(encoding="utf-8").strip()
        if recorded_hash != hashlib.sha256(frozen_path.read_bytes()).hexdigest():
            raise DecisionInputError("decision_frozen.json のsha256不一致")
        first_formal = frozen["result"]
    additional = computed if first_formal and len(computed["values"]) > len(first_formal["values"]) else None
    result = first_formal or computed
    page_result = dict(result)
    page_result["first_formal_decision"] = first_formal
    page_result["additional_observations"] = ({"independent_episodes": computed["independent_episodes"],
        "mean_pnl_per_episode_usd": computed["mean_pnl_per_episode_usd"], "ci95": computed["ci95"],
        "verdict": computed["verdict"]} if additional else None)
    page = render_page(page_result, inputs, decision_ledger, generated_at=now)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "card": CARD,
        "generated_at_utc": _utc(now),
        "cutover_utc": CUTOVER_UTC,
        "kill_threshold_usd": KILL_THRESHOLD_USD,
        "deadline_utc": DEADLINE_UTC,
        "target_independent_episodes": TARGET_INDEPENDENT_EPISODES,
        "result": result,
        "first_formal_decision": first_formal,
        "additional_observations": page_result["additional_observations"],
        "exclusions": inputs["exclusions"].get("counts"),
        "exclusions_one_line": inputs["exclusions"].get("one_line"),
        "checks": inputs["checks"],
        "kill_ledger": {"status": inputs["kill_ledger"].get("status"),
                        "killed_at_utc": inputs["kill_ledger"].get("killed_at_utc")},
        "decision_ledger": {"status": decision_ledger.get("status"),
                            "killed_at_utc": decision_ledger.get("killed_at_utc"),
                            "trigger": decision_ledger.get("trigger"),
                            "counts": decision_ledger.get("counts"),
                            "latency": decision_ledger.get("latency")},
        "inputs": inputs["inputs"],
    }
    if write:
        (out / PAGE_FILE).write_text(page, encoding="utf-8")
        _write_atomic(out / JSON_FILE, payload)
    return {"page": page, "payload": payload, "decision_ledger": decision_ledger}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="前方局面の判定を1ページに出す（実弾0・読み取りのみ）")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--top-dir-name", default=TOP_DIR_NAME)
    ap.add_argument("--out-dir-name", default=OUT_DIR_NAME)
    ap.add_argument("--as-of", default=None, help="判定日（YYYY-MM-DD, UTC）。省略時は今日")
    ap.add_argument("--now", default=None, help="評価時刻（ISO8601, UTC）。判定時点を固定したいとき")
    ap.add_argument("--no-write", action="store_true", help="読み取りのみ（decision_* を書かない）")
    ap.add_argument("--json", action="store_true", help="要約JSONを出す")
    args = ap.parse_args(argv)

    now = _ts(args.now, "--now") if args.now else datetime.now(timezone.utc)
    as_of = date.fromisoformat(args.as_of) if args.as_of else now.date()
    root = Path(args.data_dir) / args.top_dir_name / args.out_dir_name
    try:
        out = run(root, as_of=as_of, now=now, write=not args.no_write)
    except DecisionInputError as exc:
        print(f"判定不能（入力が凍結仕様と食い違う）: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out["payload"], ensure_ascii=False))
    else:
        print(out["page"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
