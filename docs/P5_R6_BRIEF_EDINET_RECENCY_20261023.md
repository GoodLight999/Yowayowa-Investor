# P5 R6 — 朝ブリーフが最古のEDINET提出を掲載する（選択順の修正と実測）

- 実施: 2026-10-23 JST（カード `t_3320c1ae`）
- 作業ツリー: `/root/current-work/yowayowa-investor`（`agent/commercial-foundation`）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 の R6（P0）
- 変更対象: `src/yowayowa/services/research_brief.py` の `assemble_edinet_summary` 周辺のみ
  （同ファイルにある他カードの未コミット差分＝プロンプト予算 364 行には触れていない）

## 0. 結論

`assemble_edinet_summary` は jsonl を**追記順**に読み、`filings[:_MAX_EDINET_SIGNALS]`（=12）で
先頭から切っていた。EDINET daily の jsonl は日付ごとの取得分が追記されるため先頭が最古になり、
毎日 2026-09-24 分が選ばれていた。**選択順を `submitDateTime` 降順に変える**最小修正を行い、
同じ入力（sha256 `fc71256a…3cb2719`、6,176 行）で修正前後を実測比較した。

| 指標 | 修正前 | 修正後 |
|---|---|---|
| `filings[0]` | `2026-09-24 09:00` | `2026-10-22 16:00` |
| 選ばれた提出の日付 | **2026-09-24 のみ**（12/12） | **2026-10-22 のみ**（12/12） |
| `citations[0].as_of` / `source_url` | `2026-09-24` / `…?date=2026-09-24&type=2` | `2026-10-22` / `…?date=2026-10-22&type=2` |
| 修正前後の docID 重複 | — | 0 件（完全に入れ替わり） |

10-13〜10-22 の分類対象行は 488 件（シグナル分類 202／大口提出 286）あり、
修正前はこのうち**1件も選ばれていなかった**（監査の「約300件」は別母数の丸め。実測値を上に置く）。

## 1. 入力（実測）

```
path        /root/current-work/yowayowa-investor/data/edinet-daily.jsonl
sha256      fc71256aa68ec4a8d6a4775ad8eed43d62816564c76e58dc76e7bc05e3cb2719
bytes       2,533,478
lines       6,176   （全て JSON オブジェクト・空行0・壊れ行0）
mtime       2026-10-23 06:31 JST
row dates   2026-09-18 … 2026-10-22（先頭ブロック＝2026-09-24、末尾＝2026-10-22）
coverage    row_count=6176 / classified=1170 / large_filing_count=671
```

追記順は `submitDateTime` と一致しない（日付ブロックが 7 箇所で巻き戻る）。
実測の先頭 3 行と末尾 3 行:

```
1     2026-09-24 09:00
2     2026-09-24 09:00
3     2026-09-24 09:00
6174  2026-10-22 16:45
6175  2026-10-22 16:55
6176  2026-10-22 17:00
```

`submitDateTime` が null の行は 1 行だけ（2,827 行目 `S100Z4JX`）。ただし
`docDescription` / `docTypeCode` / `filerName` も総て null の取得ノイズ行で、
分類対象にならないため選択には影響しない（修正後 `coverage["undated_rows"] = 0`）。

## 2. 症状の再現（修正前・同一入力）

```
$ .venv/bin/python <probe>/r6_measure_before_after.py before
coverage= {"row_count": 6176, "classified": 1170, "large_filing_count": 671}
filings submit_date_time order:
  ['2026-09-24 09:00', '2026-09-24 09:00', '2026-09-24 09:01', '2026-09-24 09:18',
   '2026-09-24 09:22', '2026-09-24 09:23', '2026-09-24 09:24', '2026-09-24 09:38',
   '2026-09-24 09:45', '2026-09-24 09:48', '2026-09-24 10:30', '2026-09-24 12:25']
citation0.source_url = https://api.edinet-fsa.go.jp/api/v2/documents.json?date=2026-09-24&type=2
```

永続化済み brief（読み取りのみ・再生成していない）でも同じ症状が残っている:

```
sqlite3 data/yowayowa.db (mode=ro)
select run_date, json_extract(payload,'$.citations[0].source_url')
  from research_briefs where run_date='2026-10-23'
-> 2026-10-23 | …?date=2026-09-24&type=2     （brief_rows=25、最新 generated_at 2026-10-23 00:01:27）
```

## 3. 原因（コードの実体）

`assemble_edinet_summary` は行を読みながら `filings` / `large_filings` に**append し、
最後に先頭 12 件で切る**だけだった。並べ替えは存在せず、順序＝ファイルの物理順。
したがってコレクタが最初に書いた日（2026-09-24）が、そのまま毎日の「本日の提出」になる。

## 4. 修正（並び順の仕様変更を明記）

**並び順の仕様を変更した**（従来: ファイル追記順 → 新: `submitDateTime` 降順）。

- `_sort_edinet_rows_by_recency(entries)` を追加し、`filings` と `large_filings` の
  **両方**を cap 適用前に整列（大口提出リストも 12 件上限で同じ欠陥クラスだったため）。
- キーは `(submitDateTime が有効か, submitDateTime)` の降順。`submitDateTime` は
  `YYYY-MM-DD HH:MM` 固定なので文字列比較＝時系列比較。
- **時刻が無い行は最下位**（未知は「最新」ではないので、日付のある提出を押し出さない）。
- 同一時刻の順序は `docID` 昇順（事前に `docID` で安定ソートしてから降順ソート。
  Python の sort は安定で `reverse=True` でも同値キーの相対順を保つ）。これにより
  同一時刻内の並びがコレクタの追記順に依存しなくなる。
- 観測用に `coverage["undated_rows"]`（時刻欠落の分類対象行数）と
  `coverage["selection_order"] = "submit_date_time_desc"` を追加。数値・上限・分類ロジックは不変。

差分（`assemble_edinet_summary` 周辺のみ、+50/−1）:

```diff
+def _sort_edinet_rows_by_recency(entries):   # docID 安定ソート → (有効か, 時刻) 降順
+    entries.sort(key=lambda entry: str(entry.get("doc_id") or ""))
+    entries.sort(key=lambda entry: (_normalized_submit_date_time(entry) != "",
+                                    _normalized_submit_date_time(entry)), reverse=True)
+    return entries
...
         coverage["classified"] = classified
         coverage["large_filing_count"] = len(large_filings)
+        coverage["undated_rows"] = undated
+        coverage["selection_order"] = _DEFAULT_EDINET_SELECTION_ORDER
+        _sort_edinet_rows_by_recency(filings)
+        _sort_edinet_rows_by_recency(large_filings)
         return {"filings": filings[:_MAX_EDINET_SIGNALS], ...}
```

## 5. 実測（修正前後の選択行・同一入力）

### 5.1 `filings`（シグナル分類・上限12）

| # | 修正後の docID | 修正後の submitDateTime | signal | 修正前の同順位（docID / 時刻） |
|---|---|---|---|---|
| 1 | S100Z9KF | 2026-10-22 16:00 | filing_forecast_revision | S100Z3JP / 2026-09-24 09:00 |
| 2 | S100Z9KE | 2026-10-22 15:30 | filing_forecast_revision | S100Z2CL / 2026-09-24 09:00 |
| 3 | S100Z9KL | 2026-10-22 15:30 | filing_forecast_revision | S100Z2AS / 2026-09-24 09:01 |
| 4 | S100Z9KG | 2026-10-22 15:17 | filing_forecast_revision | S100Z2HQ / 2026-09-24 09:18 |
| 5 | S100Z9K8 | 2026-10-22 15:11 | filing_forecast_revision | S100Z2US / 2026-09-24 09:22 |
| 6 | S100Z9D3 | 2026-10-22 15:03 | filing_forecast_revision | S100Z2V7 / 2026-09-24 09:23 |
| 7 | S100Z9E7 | 2026-10-22 15:03 | filing_forecast_revision | S100Z17U / 2026-09-24 09:24 |
| 8 | S100Z9E4 | 2026-10-22 14:56 | filing_forecast_revision | S100YWQL / 2026-09-24 09:38 |
| 9 | S100Z9I7 | 2026-10-22 14:24 | filing_forecast_revision | S100Z39U / 2026-09-24 09:45 |
| 10 | S100Z8N4 | 2026-10-22 13:08 | filing_forecast_revision | S100Z3KU / 2026-09-24 09:48 |
| 11 | S100Z9H4 | 2026-10-22 13:00 | filing_forecast_revision | S100Z3CM / 2026-09-24 10:30 |
| 12 | S100Z96W | 2026-10-22 12:07 | filing_forecast_revision | S100Z3PI / 2026-09-24 12:25 |

修正後の `source_url` は 12/12 が `…documents.json?date=2026-10-22&type=2`
（修正前は 12/12 が `date=2026-09-24`）。

### 5.2 `large_filings`（docTypeCode 030・上限12）

| # | 修正後の docID | 修正後の submitDateTime | 修正前の同順位（docID / 時刻） |
|---|---|---|---|
| 1 | S100YRI5 | 2026-10-22 09:41 | S100Z3EZ / 2026-09-24 09:00 |
| 2 | S100Z83C | 2026-10-22 09:29 | S100Z0QW / 2026-09-24 09:03 |
| 3 | S100Z86T | 2026-10-22 09:24 | S100YLAU / 2026-09-24 09:03 |
| 4 | S100YRI3 | 2026-10-22 09:23 | S100YYXD / 2026-09-24 09:04 |
| 5 | S100Z5VR | 2026-10-22 09:21 | S100YLAY / 2026-09-24 09:04 |
| 6 | S100Z0VO | 2026-10-22 09:17 | S100YYI4 / 2026-09-24 09:05 |
| 7 | S100Z5VP | 2026-10-22 09:16 | S100YYFB / 2026-09-24 09:10 |
| 8 | S100Z5VN | 2026-10-22 09:15 | S100YYXH / 2026-09-24 09:11 |
| 9 | S100Z73S | 2026-10-22 09:13 | S100YYYL / 2026-09-24 09:13 |
| 10 | S100Z5VK | 2026-10-22 09:11 | S100YYZY / 2026-09-24 09:14 |
| 11 | S100Z1O6 | 2026-10-22 09:10 | S100YYXK / 2026-09-24 09:15 |
| 12 | S100Z5VI | 2026-10-22 09:10 | S100YZ04 / 2026-09-24 09:16 |

### 5.3 citation（brief が最初に引用する提出）

```
修正前: as_of=2026-09-24 / code_or_series=S100Z3JP /
        source_url=…?date=2026-09-24&type=2 / retrieved_at=2026-09-24T11:49:11.291548+00:00
修正後: as_of=2026-10-22 / code_or_series=S100Z9KF /
        source_url=…?date=2026-10-22&type=2 / retrieved_at=2026-10-23T06:31:00.869041+09:00
```

### 5.4 coverage

```
修正前: {"path": ".../data/edinet-daily.jsonl", "row_count": 6176,
         "classified": 1170, "large_filing_count": 671}
修正後: {"path": ".../data/edinet-daily.jsonl", "row_count": 6176,
         "classified": 1170, "large_filing_count": 671,
         "undated_rows": 0, "selection_order": "submit_date_time_desc"}
```

計数（row_count / classified / large_filing_count）と上限（12/12）は不変。
変わったのは**選ばれる行**だけ。

## 6. 回帰試験（追加4件）

`tests/test_research_brief.py` の EDINET 節に追加:

1. `test_assemble_edinet_summary_selects_newest_submissions_not_file_head`
   — 古い行を先頭に13件、最新2件を末尾に置く。上限12でも最新2件が先頭に来て、
   先頭側の古い行が落ちる。`coverage["selection_order"]` と、brief の
   `citations[0]` が最新日を指すことも同時に固定。
2. `test_assemble_edinet_summary_ties_are_stable_across_file_order`
   — 同一 `submitDateTime` 3件を逆順のファイルで与えても docID 昇順で一致（追記順に依存しない）。
3. `test_assemble_edinet_summary_skips_blank_and_broken_lines_and_ranks_undated_last`
   — 空行・壊れ JSON・JSON 配列（非オブジェクト）・`submitDateTime: null` を混在。
   例外を出さず、`row_count=4` / `undated_rows=1`、時刻欠落行は最下位。
4. `test_assemble_edinet_summary_large_filings_follow_the_same_recency_order`
   — 大口提出リストも同じ降順（同じ欠陥クラスの再発防止）。

実行結果（VPS・対象モジュールのみ。全量は TSUKUMO の担当）:

```
.venv/bin/python -m pytest tests/test_research_brief.py -q                  -> RC=0（27 passed／新規4件を含む）
.venv/bin/python -m pytest tests/test_research_brief_prompt_budget.py \
    tests/test_research_ask_prompt_budget.py tests/test_edinet.py -q        -> RC=0（22 passed）
.venv/bin/ruff check src/yowayowa/services/research_brief.py tests/test_research_brief.py   -> All checks passed
.venv/bin/ruff format --check src/yowayowa/services/research_brief.py tests/test_research_brief.py -> 2 files already formatted
.venv/bin/mypy src/yowayowa/services/research_brief.py                     -> Success: no issues found
```

**自分の差分だけを当てた独立確認**: `HEAD` から作った一時 worktree に本変更のみを当て、
そこの `src` を `PYTHONPATH` 先頭にして同じモジュールを実行 → `RC=0`（27 passed）。
他カードの未コミット差分（プロンプト予算）に依存せず単独で通ることを確認した。

## 7. 未実施・限界（そのまま残す）

- **brief は再生成していない**（内蔵LLM禁止のカード制約）。永続化済み brief は読み取りのみで、
  2026-10-23 分は依然 `2026-09-24` を引用したまま。次回の定期生成から新選択順が効く。
- データ自体は最大 1 日遅れ（2026-10-23 時点の最新は 10-22 分）。したがって brief の
  EDINET 節は当面「前日分」を扱う。**提出日は evidence の各行に `submit_date_time` として
  残る**ため、LLM 側は日付を明示できる（今回、表示文言の変更は行っていない）。
- 選ばれる集合が「最新順の12件」に変わるため、以前より**同一日の提出に偏る**。
  これは仕様変更として本ドキュメントに明記した（§4）。
- VPS では対象モジュールのみ実行。`make verify` 全量と実ブラウザ E2E は TSUKUMO／GitHub CI の担当。

## 8. 変更ファイル

```
src/yowayowa/services/research_brief.py   +50 / -1   （_sort_edinet_rows_by_recency, coverage 2キー, docstring）
tests/test_research_brief.py              +新規4テスト・ヘルパ2本
docs/P5_R6_BRIEF_EDINET_RECENCY_20261023.md   （本ドキュメント）
```

実測スクリプト（再現用・リポジトリ外）:
`/root/.hermes/profiles/worker-go/cache/scratch/r6_measure_before_after.py`
（`before` / `after` の2回実行で同一入力のまま前後を比較し、JSON を残す。
`r6_before.json` / `r6_after.json` / `r6_compare.py` が同ディレクトリにある）
