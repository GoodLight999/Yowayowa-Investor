# P5: screening候補一覧で実行日と資料基準日・取得時刻を区別する（実測記録）

- カード: t_8b9aa12f（worker-flash / 2026-10-23）
- 対象リビジョン: `agent/commercial-foundation` @ `fd9a1d0`（作業ツリーに他カードの未コミット差分あり。本カードの変更は下記の2ファイルのみ）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §2.2・§6 — run_date=2026-10-23 の163候補のうち EDINET 117行の資料は 2026-08-12〜10-22 提出で、実行日だけでは資料の新しさを判定できない。`docs/PRIVATE_OPERATOR_ROADMAP.md` P4（stale/incomplete coverage の明示）。

## 1. 日付契約（この変更で固定する見せ方）

| 列 | 意味 | 供給源 |
|---|---|---|
| `Run date` | パイプラインを実行した日 | `run_date`（候補抽出・分類の意味は変更しない） |
| `As of` | データ自身の基準時点 | `provenance.as_of` のみ |
| `Retrieved` | ソースを取得した時刻 | `provenance.retrieved_at` のみ |

- 欠落・legacy・不正な値は `unavailable` 表示。実行日・現在時刻・他フィールドで補わない。
- 取得時刻は永続化された ISO 文字列を検証後にそのまま表示（オフセット `Z` / `+09:00` を含め原文維持）。timezone-naive な値を UTC と解釈し直さない。
- 既存列 `Code` / `Source` / `Signal` / `Reason` と code の4桁/5桁表記は維持・補正しない。
- 候補抽出・ソート・分類・`run_date`・code の意味は一切変更しない。共有サービス `read_screening_candidates`（`services/screening_pipeline.py`）も API（`api/routes.py`）も無変更。

## 2. 入力の同定（hash実測）

| 対象 | hash / 実測 |
|---|---|
| 原本DB `data/yowayowa.db` (VPS) | sha256 `a4eb6c64cfb8f28e1ccbb825df0968d529437eefa49eb8ca78cfd5d5edc50540` |
| 読取用コピー (scratch `yw-readcopy.db`) | 同上（コピー前後で一致。実測後の原本DBも同hashで不変＝読取専用を実証） |
| `screening_cli.py` 変更前 (HEAD) | sha256 `8e7e5512d1feddafee667d5a02a06347bbb724425562cab8c9e47ce663b22d00` |
| `screening_cli.py` 変更後 (作業ツリー) | sha256 `a0c8560cc49b544196b79ddc7520a351339c391da56e6e86a1fc88c86fe75937` |
| `tests/test_screening_cli_dates.py` (新規) | sha256 `0080666ba937a1c49de7f380d1509d5906a0eae72a8d23508ba6483342f5d202` |
| TSUKUMO検証ツリーの素 (bundle) | sha256 `965f1f5a33ccfa99b64d01defcd516c3566b998515772510b6e781bdb4495822`（branch `agent/commercial-foundation` @ `fd9a1d0` のみ。他カードの未コミット差分は含まない） |

DBの中身（読取専用照会の実測）: `screening_candidates` 163行（run_date=2026-10-23: edinet_filing 117 / market_screener 46）。provenance null は 0行。EDINET 117行の `as_of` は 2026-08-12〜2026-10-22、`retrieved_at` は `Z` 付きUTCまたは `+09:00` 付き。market_screener 46行は `as_of` = `retrieved_at` = `2026-10-23T01:32:23.125925Z`（実行日内）。

## 3. 実測: 前後の出力（同一DBコピー・同一環境・COLUMNS=250）

実行方法: scratch の読取用DBコピーに `DATABASE_URL` を向けて、(変更前) `git worktree` に取り出した HEAD 版 `screening_cli.py`、(変更後) 作業ツリー版で同一コマンドを実行。原本DBへの書き込みコマンド（screening-run 等）は実行していない。

### 3.1 変更前（HEAD）: `screening-candidates ALL --limit 5` → rc=0

```
┏━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Run        ┃ Code  ┃ Source        ┃ Signal                   ┃ Reason                                     ┃
┡━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2026-10-23 │ 29010 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正臨時報告書                 │
│ 2026-10-23 │ 60890 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正臨時報告書                 │
│ 2026-10-23 │ 59870 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（組込方式） │
│ 2026-10-23 │ 87980 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（通常方式） │
│ 2026-10-23 │ 73200 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（通常方式） │
└────────────┴───────┴───────────────┴──────────────────────────┴────────────────────────────────────────────┘
```

→ 実行日 2026-10-23 のみが見え、資料が 2026-09-24 提出分であることは表示上判別不能（監査§2.2の指摘どおり）。

### 3.2 変更後: `screening-candidates ALL --limit 5` → rc=0

```
┏━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Run date  ┃ Code  ┃ Source        ┃ Signal                   ┃ Reason                                     ┃ As of      ┃ Retrieved                   ┃
┡━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2026-10-23 │ 29010 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正臨時報告書                 │ 2026-09-24 │ 2026-09-24T11:49:11.304750Z │
│ 2026-10-23 │ 60890 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正臨時報告書                 │ 2026-09-24 │ 2026-09-24T11:49:11.306673Z │
│ 2026-10-23 │ 59870 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（組込方式） │ 2026-09-24 │ 2026-09-24T11:49:11.307220Z │
│ 2026-10-23 │ 87980 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（通常方式） │ 2026-09-24 │ 2026-09-24T11:49:11.308518Z │
│ 2026-10-23 │ 73200 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券届出書（通常方式） │ 2026-09-24 │ 2026-09-24T11:49:11.308710Z │
└───────────┴───────┴───────────────┴──────────────────────────┴────────────────────────────────────────────┴────────────┴─────────────────────────────┘
```

→ 実行日と資料基準日が異なる実例を表示上確認できる: Run date 2026-10-23 に対し As of 2026-09-24（約1ヶ月古い）・Retrieved 2026-09-24T11:49:11Z（Z付きUTCを原文のまま）。

### 3.3 変更後: `screening-candidates 14140`（コード指定・1件）→ rc=0

```
┃ Run date   ┃ Code  ┃ Source        ┃ Signal                   ┃ Reason                                                         ┃ As of      ┃ Retrieved                        ┃
┃ 2026-10-23 │ 14140 │ edinet_filing │ filing_forecast_revision │ EDINET提出: 訂正有価証券報告書－第19期(2025/07/01－2026/06/30) │ 2026-09-30 │ 2026-10-11T14:06:26.467891+09:00 │
```

→ 3つの日時がすべて異なる実例。`+09:00` オフセットが原文のまま表示される（UTCへの読み替え無し）。

### 3.4 変更後: `screening-candidates 9984`（market_screener・実行日内の実例）→ rc=0

```
┃ Run date   ┃ Code ┃ Source          ┃ Signal          ┃ Reason                                 ┃ As of                       ┃ Retrieved                   ┃
│ 2026-10-23 │ 9984 │ market_screener │ screener_low_pe │ Yahooスクリーナー: PER(TTM)が8.0倍未満 │ 2026-10-23T01:32:23.125925Z │ 2026-10-23T01:32:23.125925Z │
```

→ market_screener は as_of = retrieved = 実行日内。EDINET 行（§3.2）と見比べることで「候補の鮮度が source で異なる」こと読み取れる。

### 3.5 欠損: `screening-candidates 99840`（実在しないコード）→ rc=1

```
No screening candidates persisted for 99840
```

→ 空結果時の exit 1 は既存どおり。

## 4. `unavailable` の実測について（実データに欠損が無い事実と fixture 検証の分離）

現在の実DBには provenance 欠損行が 0行のため、`unavailable` 表示は実データからは観測できない。このため fixture による専用テスト（`tests/test_screening_cli_dates.py`・5件）で検証した:

- `provenance=None`（legacy行）/ 空dict / 日付キーが None / 非dict（"not-a-dict"）/ 不正形式（`24/09/2026`・`soon`）→ すべて `unavailable`（5行×2列=10セル）。実行日・現在時刻・隣接列での補完が無いことをセル数で検証。
- timezone-naive な取得時刻（`2026-10-22T09:30:00`）が `+00:00` / `Z` に読み替えられないこと（原文維持）。date-only の as_of もそのまま。
- オフセット付き（`Z`・`+09:00`）の原文維持、Run date / As of / Retrieved の3列が別値を示すこと、既存列の内容維持、コード絞り込み・`--limit` の既存挙動維持、空結果 exit 1。

## 5. 差分（本カードの全変更）

対象は `src/yowayowa/screening_cli.py`（変更）と `tests/test_screening_cli_dates.py`（新規）のみ。`cli.py` / `research_ask.py` / `research_brief.py` / `api/routes.py` / `services/screening_pipeline.py` は触っていない。

```diff
--- a/src/yowayowa/screening_cli.py
+++ b/src/yowayowa/screening_cli.py
@@ -11,6 +11,9 @@
 from __future__ import annotations

+from datetime import datetime
+from typing import Any
+
 import typer
 from rich import print
 from rich.table import Table
@@ -24,6 +27,39 @@
     run_screening_pipeline,
 )

+_UNAVAILABLE = "unavailable"
+
+
+def _provenance_date(provenance: Any, key: str) -> str:
+    """Render one provenance date field for display, or ``unavailable``.
+
+    Date contract for the candidates table (roadmap P4: expose stale and
+    incomplete coverage explicitly):
+
+    - ``Run date`` is when the pipeline ran; it says nothing about how fresh
+      the underlying data is.
+    - ``As of`` is the data's own as-of timestamp (``provenance.as_of``).
+    - ``Retrieved`` is when the source was fetched (``provenance.retrieved_at``).
+
+    Only the candidate's own persisted provenance is shown. A missing, legacy
+    or unparseable value degrades to ``unavailable`` — it is never backfilled
+    from the run date, the current time or any other field. The persisted ISO
+    string is echoed verbatim after validation, so an offset (or the absence
+    of one) is preserved exactly as captured; naive values are never silently
+    relabelled as UTC.
+    """
+
+    if not isinstance(provenance, dict):
+        return _UNAVAILABLE
+    raw = provenance.get(key)
+    if not isinstance(raw, str) or not raw.strip():
+        return _UNAVAILABLE
+    try:
+        datetime.fromisoformat(raw)
+    except ValueError:
+        return _UNAVAILABLE
+    return raw
+

 def screening_run() -> None:
@@ -85,13 +121,16 @@
     if not rows:
         print(f"No screening candidates persisted for {code}")
         raise typer.Exit(code=1)
-    table = Table("Run", "Code", "Source", "Signal", "Reason")
+    table = Table("Run date", "Code", "Source", "Signal", "Reason", "As of", "Retrieved")
     for row in rows:
+        provenance = row.get("provenance")
         table.add_row(
             str(row["run_date"]),
             str(row["code"]),
             str(row["source"]),
             str(row["signal"]),
             str(row["reason"]),
+            _provenance_date(provenance, "as_of"),
+            _provenance_date(provenance, "retrieved_at"),
         )
     print(table)
```

（新規テスト `tests/test_screening_cli_dates.py` は5件。CliRunner で in-memory SQLite をシードし表出力を検証する。）

## 6. 制約遵守の実測

- 原本DB `data/yowayowa.db` は全工程で読取専用。実測前後で sha256 不変（§2）。`screening-run` 等の書き込みコマンドは実行していない。
- commit / push はしていない。他カードの未コミット差分は取り込んでいない（TSUKUMO検証ツリーは `fd9a1d0` + 本カード2ファイルのみで構成）。
- API応答は不変（`api/routes.py` 無変更・共有リーダー `read_screening_candidates` 無変更・抽出件数は変更前後で同一: ALL --limit 5 の5行が同順で同一code/source/signal/reason）。
- 内蔵LLM・外部送信・新データソースなし。

## 7. 検証結果（host と rc を明記）

| 検証 | host | 結果 |
|---|---|---|
| 対象テスト（本カード5件 + 既存 `test_screening_pipeline.py` 26件、TZ=UTC） | VPS (ubuntu, Python 3.12 venv) | **31件パス rc=0** |
| 対象テスト（同2ファイル、makeと同一の `PYTHONPATH=<tree>/src`） | TSUKUMO-KUN (Python 3.12.3 venv) | **31件パス rc=0**（ドット31 `[100%]`） |
| ruff（本カード2ファイルのみ） | VPS / TSUKUMO 両方 | **パス rc=0** |
| `make openapi` | TSUKUMO-KUN | **rc=0**（`Wrote openapi.json`） |
| `make verify`（lint段階） | TSUKUMO-KUN | **rc=1**（既存の全リポlint違反で停止・§7.1） |
| `make typecheck` | TSUKUMO-KUN | **rc=2**（HEAD既存の mypy 違反・§7.2。本カードファイルへの指摘 0件） |
| `make test` | TSUKUMO-KUN | **rc=2**（HEAD既存の収集 ImportError・§7.3。本カード2ファイルは収集・実行とも正常） |

### 7.1 既存の全リポlint違反（本カードと無関係・両hostで再現）

- VPS（作業ツリー・他カードWIP込み）: `ruff check src tests` → 34 errors。TSUKUMO（HEAD `fd9a1d0` + 本カード2ファイルのみ）: 33 errors。
- 内訳は `src/yowayowa/loop_cli.py`（I001 import順）、`src/yowayowa/paperloop.py`（SIM113 ほか）、`src/yowayowa/paperloop_panel.py`、`src/yowayowa/services/jp_value_quality.py` 等の**既存コミット済みファイル**で、本カードの2ファイルへの指摘は 0件。
- したがって `make verify` 全量の lint 合格は現行 HEAD では達成できず、本カードの範囲（screening CLI の表示のみ）を超えるため本カードでは手を入れない。

### 7.2 `make typecheck` rc=2（HEAD既存・本カードファイルへの指摘0件）

- TSUKUMO（HEAD+本カード2ファイル、mypy strict）: `src/yowayowa/services/jp_value_quality.py:346/431`、`src/yowayowa/paperloop.py:1603`、`src/yowayowa/paperloop_panel.py:115〜181` 等の既存コミット済みファイルに error。`screening_cli.py` への指摘は 0件（ログ内に該当行なし）。
- 初回は venv が CPython 3.14.7 で作成され、3.12 での作り直し後も同じ既存ファイルで rc=2（＝Pythonバージョン非依存の既存違反）。

### 7.3 `make test` rc=2（HEAD既存の収集 ImportError）

- TSUKUMO: `tests/test_cusum_watch.py:18` の `from tests.test_paperloop import _now_of, _prepare` が `No module named 'tests'` で収集失敗（`tests/test_paperloop_v12.py` も同様）。pytest が収集段階で中断するため全テストは未実行。
- 同一ファイルは VPS 作業ツリーでは収集可能（11件を実測）。HEAD のみの TSUKUMO 環境で壊れている既存問題であり、本カードの変更範囲外。
- 本カードの対象2ファイルは TSUKUMO でも収集・実行とも正常（対象テスト rc=0・上表）。

### 7.4 全量の到達度

`make verify` 全量合格は現行 HEAD の既存違反（lint 33件・mypy 複数件・test 収集 ImportError）により TSUKUMO では到達不能だったため、rc を上記のとおり正実測で記録した（捏造なし）。本カードの変更がこれらの既存違反を追加・悪化していないことは、(a) 本カード2ファイルの ruff/mypy/pytest が両hostで正常、(b) TSUKUMO検証ツリーが HEAD+本カード2ファイルのみ、(c) lint/mypy/test の失敗箇所リストに本カードファイルが含まれないこと、で示す。

## 8. 既知の表示制限（正直な記録）

- 7列の表はターミナル幅が狭い（例: 80列）と rich が各セルを `…` 付きで切り詰める。テストでは `COLUMNS=220` を与えて全文一致を検証しており、実運用は広めの端末を推奨。表示のみの問題で、永続化データ・API・抽出件数には影響しない。
- 変更前の5列表も同一の幅依存挙動だったものが7列で強まったもの。切り詰め防止は別カード（表示幅設計）に分離するのが妥当と判断した。
