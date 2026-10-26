#!/usr/bin/env python3
"""yowayowa データ源台帳（config/data_sources.json）の lint — R1〜R10（検出のみ）。

設計: t_b2d13c2d DATA_SOURCE_REGISTRY_DESIGN.md §6（台帳の検査）。
方針:
- 本スクリプトは読み取り専用。データの削除・移動・取得 job の追加は一切しない。
- R1〜R10 の区分は設計 §6 の表にそのまま対応させる。
- 終了コード: 0=検出なし / 10=AMBER のみ / 30=RED あり / 20=lint 自体が実行不能（fail-closed）。
  （0/10/20/30 はリポジトリの終了コード規約に合わせる。30 が「全体 RED」。）

R2 の読み（実装上の決め）:
- 「license_class が licensing.py に存在」= LicenseClass 列挙（src/yowayowa/domain.py）の値と完全一致。
- 「public_mode が licensing.py の判定と一致」= その区分の SourceLicensePolicy が licensing.py 内に
  少なくとも1件あり、その全件が公開可（commercial_use && public_display && public_api &&
  derived_analysis_public）のときだけ allowed。区分の実績が無いか、1件でも公開不可なら denied（保守側）。
- provider と policy の突合はしない（設計§5が FRED を personal_only と書くため。区分単位の照合のみ）。

R1 の読み（設計§5の内容が通るように）:
- cadence は lifecycle が active / not_collected のとき必須（one_shot は valid_until 判定、retired は不要）。
- grace は active のとき必須。valid_until は R8（one_shot の必須欄）で見る。

実行例: .venv/bin/python scripts/lint_data_sources.py [--json]
（R2 で licensing.py を import するため pydantic が必要。リポジトリの .venv 推奨。）
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# 日本語のメッセージ・注記はこの台帳運用の正文（全角括弧・読点等は誤字ではない）。
# pyproject の per-file-ignores と同じ理屈だが、共有設定を触らず本ファイル内で完結させる。
# ruff: noqa: E501, RUF001, RUF002, RUF003


RED = "RED"
AMBER = "AMBER"

EXIT_OK = 0
EXIT_AMBER = 10
EXIT_INFRA = 20
EXIT_RED = 30

LIFECYCLE_VALUES = {"active", "not_collected", "one_shot", "retired"}
PUBLIC_MODE_VALUES = {"allowed", "denied"}
STORE_KIND_VALUES = {"jsonl_per_symbol", "jsonl", "sqlite_table", "json_snapshot"}
TIER_VALUES = {"critical", "important", "context"}
CADENCE_KIND_VALUES = {"session", "calendar_day", "business_day_report", "weekly", "interval", "release_schedule"}

AUTH_RE = re.compile(r"^(none|api_key:[A-Z][A-Z0-9_]*|session:.*)$")
COST_RE = re.compile(r"^(free|paid:.+|metered)$")
ACQUIRER_RE = re.compile(r"^(none|hermes_cron:[^/\s]+/\S+|systemd:\S+|manual:.*)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# R8: grace の単位（設計§4: h / sessions / days / releases のみ）
GRACE_UNIT_RE = re.compile(r"^(\d+)\s*(h|hours?|sessions?|days?|releases?)$")
UNIT_CANON = {
    "h": "h", "hour": "h", "hours": "h",
    "session": "sessions", "sessions": "sessions",
    "day": "days", "days": "days",
    "release": "releases", "releases": "releases",
}
CADENCE_GRACE_UNITS = {
    "session": {"h", "sessions"},
    "calendar_day": {"h", "days"},
    "business_day_report": {"h", "days"},
    "weekly": {"h", "days"},
    "interval": {"h"},
    "release_schedule": {"h", "days", "releases"},
}

# R6: 台帳が「db:<table>」で受けている DB（単一 sqlite。2026-10-13 実測）。
DB_REL = "data/yowayowa.db"
# R6: データ源ではないアプリ内部テーブル（UI 状態など。データ源台帳の対象外とするもの）。
# 根拠: 設計§2-G5 が「データ系の0行テーブル」だけを問題視しており、アプリ状態テーブルは挙げていない。
INTERNAL_APP_TABLES = {
    "portfolios", "positions", "portfolio_snapshots",
    "watchlists", "watchlist_items",
    "price_alerts", "price_alert_notifications",
    "event_subscriptions", "event_inbox",
    "broker_execution_applications", "investment_hypotheses", "research_presets",
}

# 派生物の区分継承（厳しさの順位。数字が大きいほど厳しい）
CLASS_STRICTNESS = {
    "official_public": 0,
    "licensed_redistributable": 1,
    "user_key": 2,
    "personal_only": 3,
}


class Findings:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, rule: str, severity: str, dataset: str | None, message: str) -> None:
        self.items.append({"rule": rule, "severity": severity, "dataset": dataset, "message": message})

    def has_red(self) -> bool:
        return any(f["severity"] == RED for f in self.items)

    def has_any(self) -> bool:
        return bool(self.items)


def load_registry(repo: Path) -> dict:
    path = repo / "config" / "data_sources.json"
    if not path.exists():
        raise SystemExitWithCode(f"台帳が見つからない: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExitWithCode(f"台帳が JSON として読めない: {path}: {e}") from e


class SystemExitWithCode(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _resolve_hermes_home(explicit: str | None) -> Path:
    """名簿と cron fleet（default + worker-flash）は Hermes ルート直下にある。
    profile セッションの HERMES_HOME は profile 配下を指すことがあるため、
    明示指定が無い場合は /root/.hermes を優先する。"""
    if explicit:
        return Path(explicit)
    for candidate in (Path("/root/.hermes"), os.environ.get("HERMES_HOME")):
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return Path("/root/.hermes")


# ---------------------------------------------------------------- R1

def r1_structure(reg: dict, findings: Findings) -> None:
    if reg.get("schema_version") != 1:
        findings.add("R1", RED, None, f"schema_version は 1 のみ（実値 {reg.get('schema_version')!r}）")
    ro = reg.get("registry_owner")
    if not isinstance(ro, dict) or not ro.get("maintainer") or not ro.get("approver"):
        findings.add("R1", RED, None, "registry_owner に maintainer/approver が要る")
    if not isinstance(reg.get("calendars"), dict):
        findings.add("R1", RED, None, "calendars が無い（名前→ファイルパスの対応）")
    datasets = reg.get("datasets")
    if not isinstance(datasets, dict) or not datasets:
        findings.add("R1", RED, None, "datasets が空")
        return

    for ds_id, ds in datasets.items():
        where = ds_id
        if not isinstance(ds, dict):
            findings.add("R1", RED, where, "dataset の中身がオブジェクトでない")
            continue
        # 識別・契約
        _req_str(findings, "R1", where, ds, ["title", "provider", "license_class", "auth", "cost", "acquirer", "last_reviewed"])
        if ds.get("lifecycle") not in LIFECYCLE_VALUES:
            findings.add("R1", RED, where, f"lifecycle が列挙外: {ds.get('lifecycle')!r}")
        if ds.get("public_mode") not in PUBLIC_MODE_VALUES:
            findings.add("R1", RED, where, f"public_mode は allowed/denied（実値 {ds.get('public_mode')!r}）")
        if isinstance(ds.get("auth"), str) and not AUTH_RE.match(ds["auth"]):
            findings.add("R1", RED, where, f"auth の形式が不正（none / api_key:<ENV変数名> / session:<文書>: {ds['auth']!r}。値そのものは書かない）")
        if isinstance(ds.get("cost"), str) and not COST_RE.match(ds["cost"]):
            findings.add("R1", RED, where, f"cost は free / paid:<契約名> / metered（実値 {ds['cost']!r}）")
        if isinstance(ds.get("last_reviewed"), str) and not DATE_RE.match(ds["last_reviewed"]):
            findings.add("R1", RED, where, f"last_reviewed は YYYY-MM-DD（実値 {ds['last_reviewed']!r}）")
        # store
        store = ds.get("store")
        if not isinstance(store, dict):
            findings.add("R1", RED, where, "store が無い")
        else:
            if store.get("kind") not in STORE_KIND_VALUES:
                findings.add("R1", RED, where, f"store.kind が列挙外: {store.get('kind')!r}")
            _req_str(findings, "R1", where, store, ["path", "as_of_field", "retrieved_field"], prefix="store.")
            if isinstance(store.get("path"), str) and store["path"].startswith("db:") and len(store["path"]) <= 3:
                findings.add("R1", RED, where, "store.path の db: にテーブル名が無い")
        # acquirer 形式
        if isinstance(ds.get("acquirer"), str) and not ACQUIRER_RE.match(ds["acquirer"]):
            findings.add("R1", RED, where, f"acquirer の形式が不正: {ds['acquirer']!r}")
        # cadence / grace（active, not_collected で必須）
        lifecycle = ds.get("lifecycle")
        cadence = ds.get("cadence")
        if lifecycle in ("active", "not_collected"):
            if not isinstance(cadence, dict):
                findings.add("R1", RED, where, f"lifecycle={lifecycle} には cadence が必須")
            else:
                if cadence.get("kind") not in CADENCE_KIND_VALUES:
                    findings.add("R1", RED, where, f"cadence.kind は6種類のみ（実値 {cadence.get('kind')!r}）")
        elif cadence is not None and isinstance(cadence, dict) and cadence.get("kind") not in CADENCE_KIND_VALUES:
            findings.add("R1", RED, where, f"cadence.kind が列挙外: {cadence.get('kind')!r}")
        grace = ds.get("grace")
        if lifecycle == "active":
            if not isinstance(grace, dict) or not grace.get("late_after") or not grace.get("stale_after"):
                findings.add("R1", RED, where, "lifecycle=active には grace（late_after/stale_after）が必須")
        elif grace is not None and not isinstance(grace, dict):
            findings.add("R1", RED, where, "grace はオブジェクト（late_after/stale_after）")
        # owner（3役）
        owner = ds.get("owner")
        if not isinstance(owner, dict):
            findings.add("R1", RED, where, "owner が無い（3役）")
        else:
            missing = [k for k in ("operator", "maintainer", "contract_holder") if not owner.get(k)]
            if missing:
                findings.add("R1", RED, where, f"owner に必須の役が無い: {','.join(missing)}")
        # tier / consumers
        if ds.get("tier") not in TIER_VALUES:
            findings.add("R1", RED, where, f"tier は critical/important/context（実値 {ds.get('tier')!r}）")
        if not isinstance(ds.get("consumers"), list):
            findings.add("R1", RED, where, "consumers はリスト（無消費なら空リスト）")


def _req_str(findings: Findings, rule: str, where: str, obj: dict, keys: list[str], prefix: str = "") -> None:
    for key in keys:
        v = obj.get(key)
        if not isinstance(v, str) or not v.strip():
            findings.add(rule, RED, where, f"{prefix}{key} が空または文字列でない")


# ---------------------------------------------------------------- R2

def r2_license(reg: dict, repo: Path, findings: Findings) -> bool:
    """licensing.py と照合。import できない場合は infra 失敗（False を返す）。"""
    sys.path.insert(0, str(repo / "src"))
    try:
        from yowayowa.domain import LicenseClass
        from yowayowa.services import licensing
    except Exception as e:
        findings.add("R2", AMBER, None, f"licensing.py を import できず照合できなかった（.venv で実行せよ）: {e}")
        return False

    class_values = {member.value for member in LicenseClass}
    policies_by_class: dict[str, list] = {}
    for policy in licensing.SOURCE_POLICIES:
        policies_by_class.setdefault(policy.license_class.value, []).append(policy)

    def class_public_ok(cls: str) -> bool:
        pols = policies_by_class.get(cls, [])
        return bool(pols) and all(licensing._policy_public_api_allowed(p) for p in pols)

    datasets = reg.get("datasets", {})
    for ds_id, ds in datasets.items():
        if not isinstance(ds, dict):
            continue
        cls = ds.get("license_class")
        if cls not in class_values:
            findings.add("R2", RED, ds_id, f"license_class が licensing.py の区分に無い: {cls!r}（区分値: {sorted(class_values)}）")
            continue
        expected_mode = "allowed" if class_public_ok(cls) else "denied"
        if ds.get("public_mode") != expected_mode:
            findings.add("R2", RED, ds_id,
                         f"public_mode={ds.get('public_mode')!r} は licensing.py の判定（{cls} → {expected_mode}）と不一致")
        # 派生物は入力の最も厳しい区分を継承しているか（設計§5）
        derived_from = ds.get("derived_from")
        if derived_from:
            input_classes: list[tuple[str, str]] = []
            for ref in derived_from:
                ref_ds = datasets.get(ref)
                if not isinstance(ref_ds, dict):
                    findings.add("R2", RED, ds_id, f"derived_from の参照先が台帳に無い: {ref}")
                    continue
                input_classes.append((ref, ref_ds.get("license_class")))
            known = [(ref, c) for ref, c in input_classes if c in CLASS_STRICTNESS]
            if known and cls in CLASS_STRICTNESS:
                strictest_ref, strictest_cls = max(known, key=lambda x: CLASS_STRICTNESS[x[1]])
                if cls != strictest_cls:
                    findings.add("R2", RED, ds_id,
                                 f"派生物の区分は入力の最も厳しい区分を継承するはず（{strictest_ref} の {strictest_cls}）が、{cls} になっている")
    return True


# ---------------------------------------------------------------- R3

def r3_no_acquirer_active(reg: dict, findings: Findings) -> None:
    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        if ds.get("lifecycle") == "active" and ds.get("acquirer") == "none":
            findings.add("R3", RED, ds_id,
                         "lifecycle=active なのに acquirer=none（取得 job が無い。G1 型。retired への変更か取得の復活が必要）")


# ---------------------------------------------------------------- R4

def r4_acquirer_exists(reg: dict, hermes_home: Path, findings: Findings) -> None:
    jobs_cache: dict[str, dict | None] = {}

    def load_jobs(profile: str) -> dict | None:
        if profile in jobs_cache:
            return jobs_cache[profile]
        if profile == "default":
            path = hermes_home / "cron" / "jobs.json"
        else:
            path = hermes_home / "profiles" / profile / "cron" / "jobs.json"
        data = None
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = None
        jobs_cache[profile] = data
        return data

    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        acq = ds.get("acquirer")
        if not isinstance(acq, str):
            continue
        if acq.startswith("hermes_cron:"):
            spec = acq[len("hermes_cron:"):]
            profile, _, job = spec.partition("/")
            jobs = load_jobs(profile)
            if jobs is None:
                findings.add("R4", RED, ds_id, f"profile {profile} の jobs.json が読めない（{spec}）")
                continue
            entries = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
            if isinstance(entries, dict):
                entries = list(entries.values())
            found = next((j for j in entries if isinstance(j, dict) and j.get("name") == job), None)
            if found is None:
                findings.add("R4", RED, ds_id, f"job が実在しない: {spec}")
            elif found.get("enabled") is not True:
                findings.add("R4", RED, ds_id, f"job は存在するが無効（enabled != true）: {spec}")
        elif acq.startswith("systemd:"):
            unit = acq[len("systemd:"):]
            candidates = [Path("/etc/systemd/system") / unit, Path("/usr/lib/systemd/system") / unit,
                          Path("/lib/systemd/system") / unit]
            if not any(p.exists() for p in candidates):
                findings.add("R4", RED, ds_id, f"systemd unit が実在しない: {unit}")
        # none / manual: は対象外


# ---------------------------------------------------------------- R5

def r5_owners_in_roster(reg: dict, hermes_home: Path, findings: Findings) -> None:
    roster_path = hermes_home / "CSUITE_ROSTER.md"
    handles: set[str] = set()
    if roster_path.exists():
        for line in roster_path.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells:
                continue
            token = re.sub(r"[（(].*?$", "", cells[0]).strip()
            if token and token not in ("handle", "---", ":", "-"):
                handles.add(token)
    else:
        findings.add("R5", AMBER, None, f"名簿が読めない: {roster_path}")
    valid = handles | {"master"}

    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        owner = ds.get("owner")
        if not isinstance(owner, dict):
            continue
        for role in ("operator", "maintainer", "contract_holder", "escalation"):
            who = owner.get(role)
            if who is None:
                continue
            if who not in valid:
                findings.add("R5", RED, ds_id, f"owner.{role}={who!r} が名簿（CSUITE_ROSTER.md）に実在しない")


# ---------------------------------------------------------------- R6

def _glob_match(pattern: str, rel: str) -> bool:
    if pattern == rel:
        return True
    pp, rp = pattern.split("/"), rel.split("/")

    def rec(pi: int, ri: int) -> bool:
        while pi < len(pp):
            seg = pp[pi]
            if seg == "**":
                return any(rec(pi + 1, nxt) for nxt in range(ri, len(rp) + 1))
            if ri >= len(rp):
                return False
            if seg == rp[ri] or (("*" in seg or "?" in seg or "[" in seg) and fnmatch.fnmatch(rp[ri], seg)):
                pi += 1
                ri += 1
                continue
            return False
        return ri == len(rp)

    return rec(0, 0)


def r6_stray_data(reg: dict, repo: Path, findings: Findings) -> None:
    datasets = {k: v for k, v in reg.get("datasets", {}).items() if isinstance(v, dict)}
    patterns = []
    db_tables: set[str] = set()
    for ds_id, ds in datasets.items():
        store = ds.get("store") or {}
        path = store.get("path")
        if not isinstance(path, str) or not path:
            continue
        if path.startswith("db:"):
            db_tables.add(path[3:])
        else:
            patterns.append((ds_id, path))

    data_dir = repo / "data"
    stray_files: list[str] = []
    if data_dir.is_dir():
        for p in sorted(data_dir.rglob("*.jsonl")):
            if any(part.startswith(".") for part in p.relative_to(repo).parts):
                continue
            rel = p.relative_to(repo).as_posix()
            if not any(_glob_match(pat, rel) for _, pat in patterns):
                stray_files.append(rel)
    for rel in stray_files:
        findings.add("R6", AMBER, None,
                     f"野良データ（台帳に無い jsonl）: {rel}（新しいデータ源なら台帳に登録、不要なら削除裁可を）")

    db_path = repo / DB_REL
    if db_path.exists():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = con.execute("select name from sqlite_master where type='table' order by name").fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            findings.add("R6", AMBER, None, f"DB を読めず野良テーブル検査をスキップ: {e}")
            rows = []
        for (table,) in rows:
            if table in db_tables or table in INTERNAL_APP_TABLES:
                continue
            findings.add("R6", AMBER, None,
                         f"野良データ（台帳に無い DB テーブル）: db:{table}（内部アプリ状態なら lint の INTERNAL_APP_TABLES へ、データ源なら台帳へ）")
    else:
        findings.add("R6", AMBER, None, f"DB が見つからない: {DB_REL}")


# ---------------------------------------------------------------- R7

def r7_filter_overlap(reg: dict, findings: Findings) -> None:
    by_path: dict[str, list[str]] = {}
    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        store = ds.get("store") or {}
        path = store.get("path")
        if isinstance(path, str) and path:
            by_path.setdefault(path, []).append(ds_id)
    for path, ids in sorted(by_path.items()):
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = reg["datasets"][ids[i]], reg["datasets"][ids[j]]
                fa = (a.get("store") or {}).get("filter")
                fb = (b.get("store") or {}).get("filter")
                if fa is None and fb is None:
                    findings.add("R7", RED, f"{ids[i]},{ids[j]}", f"同じ保存先 {path} に filter 無しの項目が2つ（行が重なる）")
                elif fa is None or fb is None:
                    findings.add("R7", RED, f"{ids[i]},{ids[j]}", f"同じ保存先 {path} で filter 有りと無しが混在（無し側が全行を覆う）")
                else:
                    common = set(fa) & set(fb)
                    dup = [k for k in common if fa[k] == fb[k]]
                    if dup:
                        findings.add("R7", RED, f"{ids[i]},{ids[j]}", f"同じ保存先 {path} で filter の値が重複: {dup}")


# ---------------------------------------------------------------- R8

def r8_cadence_consistency(reg: dict, repo: Path, today: date, findings: Findings) -> None:
    calendars = reg.get("calendars") or {}
    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        lifecycle = ds.get("lifecycle")
        cadence = ds.get("cadence") if isinstance(ds.get("cadence"), dict) else {}
        kind = cadence.get("kind")

        # (a) grace の単位が cadence に合う
        #     設計§4は単位を h/sessions/days/releases に限定し、「weekly に sessions」のような
        #     不一致を落とす。設計§5の表そのものが session 系で late_after=1 day を使うため、
        #     late_after は実時間（h/days）を常に許し、stale_after を cadence 自然な単位に限定する。
        grace = ds.get("grace")
        if isinstance(grace, dict) and kind:
            allowed = CADENCE_GRACE_UNITS.get(kind)
            for field in ("late_after", "stale_after"):
                raw = grace.get(field)
                if not isinstance(raw, str):
                    continue
                m = GRACE_UNIT_RE.match(raw.strip())
                if not m:
                    findings.add("R8", RED, ds_id, f"grace.{field} の形式が不正: {raw!r}（例: 6h / 2 sessions / 3 days / 1 release）")
                    continue
                unit = UNIT_CANON[m.group(2)]
                if field == "late_after":
                    if unit not in {"h", "days"}:
                        findings.add("R8", RED, ds_id, f"grace.late_after の単位 {unit} は実時間（h/days）に限る")
                elif allowed is not None and unit not in allowed:
                    findings.add("R8", RED, ds_id, f"grace.stale_after の単位 {unit} は cadence={kind} に合わない（許容: {sorted(allowed)}）")

        # (b) one_shot には valid_until が要る（G7）
        if lifecycle == "one_shot" and not _parse_date(ds.get("valid_until")):
            findings.add("R8", RED, ds_id, "lifecycle=one_shot に有効な valid_until が無い（いつまで使ってよいか未決。G7）")

        # (c) release_schedule の公表予定表（無い/切れたら判定不能）
        if kind == "release_schedule":
            cal_name = cadence.get("calendar")
            rel = calendars.get(cal_name) if isinstance(calendars, dict) else None
            if not isinstance(rel, str):
                findings.add("R8", RED, ds_id, f"release_schedule の公表予定表 {cal_name!r} が台帳の calendars に無い")
                continue
            cal_path = repo / rel
            if not cal_path.exists():
                findings.add("R8", RED, ds_id,
                             f"公表予定表が無い（判定不能・RED）: {rel}。年1回、公式の公表予定を写すこと（設計§4）")
                continue
            try:
                with open(cal_path, encoding="utf-8") as f:
                    cal = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                findings.add("R8", RED, ds_id, f"公表予定表を読めない: {rel}: {e}")
                continue
            years = _collect_years(cal)
            need = {str(today.year)}
            if (today.month, today.day) >= (12, 1):
                need.add(str(today.year + 1))
            missing = sorted(need - years)
            if missing:
                findings.add("R8", RED, ds_id, f"公表予定表 {rel} に {missing} 年分が無い（写し忘れ＝判定不能）")


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _collect_years(cal) -> set[str]:
    years: set[str] = set()
    if isinstance(cal, dict):
        for key, val in cal.items():
            if re.fullmatch(r"\d{4}", str(key)):
                years.add(str(key))
            if str(key) == "years" and isinstance(val, list):
                years.update(str(v) for v in val if re.fullmatch(r"\d{4}", str(v)))
            if str(key) == "releases" and isinstance(val, list):
                for row in val:
                    if isinstance(row, dict):
                        for v in row.values():
                            m = re.match(r"^(\d{4})-", str(v))
                            if m:
                                years.add(m.group(1))
    return years


# ---------------------------------------------------------------- R9

def r9_contract_review(reg: dict, today: date, findings: Findings) -> None:
    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        cost = ds.get("cost") or ""
        auth = ds.get("auth") or ""
        needs_review = (isinstance(cost, str) and cost.startswith("paid:")) or auth.startswith("api_key:")
        if not needs_review:
            continue
        due = _parse_date(ds.get("contract_review_due"))
        if due is None:
            findings.add("R9", AMBER, ds_id, "有料・鍵ありなのに contract_review_due が無い（年1回の規約見直しが予定されていない。契約者＝マスターへ）")
        elif due < today:
            findings.add("R9", AMBER, ds_id, f"contract_review_due を過ぎた（{due}。契約者＝マスターへの通知対象）")


# ---------------------------------------------------------------- R10

def r10_last_reviewed(reg: dict, today: date, findings: Findings) -> None:
    threshold = today - timedelta(days=180)
    for ds_id, ds in reg.get("datasets", {}).items():
        if not isinstance(ds, dict):
            continue
        reviewed = _parse_date(ds.get("last_reviewed"))
        if reviewed is not None and reviewed < threshold:
            findings.add("R10", AMBER, ds_id, f"last_reviewed が古い（{reviewed}。180日より前）")


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="データ源台帳の lint（R1〜R10・検出のみ・読み取り専用）")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent), help="リポジトリのルート")
    parser.add_argument("--hermes-home", default=None, help="Hermes ホーム（jobs.json と名簿の場所）。省略時は /root/.hermes を優先し、無ければ HERMES_HOME")
    parser.add_argument("--json", action="store_true", help="JSON で出力")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    hermes_home = _resolve_hermes_home(args.hermes_home)
    today = date.today()
    findings = Findings()

    try:
        reg = load_registry(repo)
    except SystemExitWithCode as e:
        print(f"lint 実行不能（fail-closed）: {e.message}", file=sys.stderr)
        return EXIT_INFRA

    r1_structure(reg, findings)
    r2_ok = r2_license(reg, repo, findings)
    r3_no_acquirer_active(reg, findings)
    r4_acquirer_exists(reg, hermes_home, findings)
    r5_owners_in_roster(reg, hermes_home, findings)
    r6_stray_data(reg, repo, findings)
    r7_filter_overlap(reg, findings)
    r8_cadence_consistency(reg, repo, today, findings)
    r9_contract_review(reg, today, findings)
    r10_last_reviewed(reg, today, findings)

    if args.json:
        print(json.dumps({"date": today.isoformat(), "findings": findings.items}, ensure_ascii=False, indent=1))
    else:
        rules = [f"R{i}" for i in range(1, 11)]
        dataset_count = len(reg.get("datasets", {}))
        print(f"データ源台帳 lint（検出のみ・読み取り専用） repo={repo} date={today.isoformat()} datasets={dataset_count}")
        for rule in rules:
            rows = [f for f in findings.items if f["rule"] == rule]
            if not rows:
                print(f"  {rule}: OK（検出なし）")
                continue
            for f in rows:
                target = f["dataset"] or "-"
                print(f"  {rule} {f['severity']}  {target}  {f['message']}")
        reds = sum(1 for f in findings.items if f["severity"] == RED)
        ambers = sum(1 for f in findings.items if f["severity"] == AMBER)
        print(f"summary: RED={reds} AMBER={ambers} total={len(findings.items)}")
        if not r2_ok:
            print("note: R2 の照合が実行できていないため、fail-closed としては扱うこと。")

    if not r2_ok:
        return EXIT_INFRA
    if findings.has_red():
        return EXIT_RED
    if findings.has_any():
        return EXIT_AMBER
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
