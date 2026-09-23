# P1C 実測証跡（Company IR acquisition）

commit: `64cd3dc`（実装）+ `27a0306`（checkpoint doc）
branch: `agent/commercial-foundation`
実装worktree: `/root/current-work/yowayowa-p1c`
検証環境: python3.13 / `/root/current-work/yowayowa-investor/.venv`

## 1. 実データ end-to-end（p1c_e2e_real.py）— 11/11 PASS

本番と同じ `_default_http_transport`（未認証の公開GET）でニトリホールディングス
`https://www.nitorihd.co.jp/ir/library/summary.html` を対象に実行。

```
discovered: 106 文書（fetch budget 6）
fetched: IFRS202703_1Q_tanshin_202600804.pdf
         IFRS_202703_1Q_Consolidated Financial Summary_20260806.pdf
         IFRS202603_4Q_tanshin_20260212.pdf
         IFRS_202603_4Q_Consolidated Financial Summary_20260513.pdf
         IFRS202603_3Q_tanshin_20260212.pdf
         IFRS_202603_3Q_Consolidated Financial Summary_20260212.pdf

PASS  run1 fetch_state ok -- state=ok notes=['skipped 5 off-origin links']
PASS  run1 fetched documents -- fetched=6 of 106 discovered
PASS  FY2026 4Q tanshin revenue == 912,248 million yen
      -- revenue={'kpi':'revenue','label':'売上収益','value':912248000000.0,
                  'raw_value':'912,248','unit':'百万円','source':'table[8].cells'}
PASS  every fetched doc yielded KPIs or failed closed
      -- 5文書はKPI取得、1文書（画像ベースPDF）は
         ('NOT-PARSED','pdf has no extractable text layer (image-only or scanned)')
PASS  timeline provenance complete -- entries=6
PASS  no HTML navigation treated as document -- html_docs=[]
PASS  run2 new == 0 (idempotent) -- new=0 unchanged=106
PASS  run2 no HTML re-detected -- statuses=['seen','unchanged']
PASS  source registry persists across restart -- sources=['nitorihd-ir']
PASS  registry file on disk -- data/ir-sources/sources.json
PASS  kpi history recorded for fetched doc -- entries=1

11/11 checks passed
```

取得された実KPI（すべて百万円単位、円に正規化）:

| 文書 | revenue | operating_profit | その他 |
|---|---|---|---|
| IFRS202603_4Q_tanshin | 912,248,000,000 | 125,526,000,000 | profit 89,274,000,000 |
| IFRS_202603_4Q_summary (EN) | 912,248,000,000 | 125,526,000,000 | profit_before_tax 127,357,000,000 |
| IFRS202603_3Q_tanshin | 688,503,000,000 | 104,494,000,000 | — |
| IFRS202703_1Q_tanshin | 226,259,000,000 | 36,545,000,000 | — |
| IFRS_202703_1Q_summary (EN) | 226,259,000,000 | 36,545,000,000 | profit_before_tax 37,183,000,000 |
| IFRS_202603_3Q_summary | (画像ベースのため fail closed) | — | — |

## 2. previous-version diff（p1c_e2e_diff.py）— 9/9 PASS

同一URLが実際にファイル差し替えされる状況（IRで一般的な revision）を、実PDFバイト
2件（3Q決算短信 736,752B / 4Q決算短信 587,346B）で再現。

```
real PDFs: 3Q=736752B 4Q=587346B
run1 new=2 | 3Q revenue=688503000000 (百万円) | 4Q revenue=912248000000 (百万円)
run2 new=0 unchanged=2/2
run3 new=0 revised=1
   revised revenue=688503000000 unit=百万円
   kpi_diff: [{"kpi":"operating_profit","previous_value":125526000000.0,
               "current_value":104494000000.0,"change":"decrease","delta":-21032000000.0},
              {"kpi":"profit","previous_value":89274000000.0,
               "current_value":null,"change":"removed","delta":null},
              {"kpi":"revenue","previous_value":912248000000.0,
               "current_value":688503000000.0,"change":"decrease","delta":-223745000000.0}]
kpi history entries for the 4Q URL: 2
   2026-09-23T02:31:46 revenue=688503000000
   2026-09-23T02:31:21 revenue=912248000000

PASS  3Q revenue == 688,503 million yen
PASS  4Q revenue == 912,248 million yen
PASS  first observation carries no fabricated diff -- 4Q diff=[]
PASS  run2 idempotent (new=0, all unchanged)
PASS  run3 classifies the replaced file as revised
PASS  revised document reports the new real figure
PASS  revision diff is a signed decrease of 223,745 million yen
PASS  kpi history keeps both versions of the same URL -- entries=2
PASS  timeline records every KPI-bearing observation

9/9 checks passed
```

## 3. 実データで発見・修正した欠陥

1. **複合yen chainの誤読**: 決算短信本文の「当期利益892億74百万円」を単純数値正規表現が
   `892` と読み、真値の1万分の1を無言で記録していた。→ `parse_yen_chain()` で
   chain全体を消費し `89,274,000,000.0` を返す。回帰テスト
   `test_parse_yen_chain_compound_and_single` / `test_text_kpi_consumes_compound_yen_chain`。
2. **英文summaryの単位未適用**: `(Millions of yen)` が表グリッド外のキャプション行に
   あるため単位倍掛けが効かず `912248.0` と記録していた。→ `document_unit_hint()`
   で文書単位を推定（同一倍率の時のみ採用、曖昧ならNoneでfail closed）。
3. **画像のみPDFの偽parsed**: 改ページ文字のみのtext層を `parsed=True` と報告し、
   「KPIが見つからない文書」と「機械可読でない文書」を混同していた。→ 空白のみの
   text層は明示的な parse_note 付きで fail closed。
4. **WAFによる無言タイムアウト**: `nitorihd.co.jp` のWAFがUser-Agent内の
   `+https://...` 参照URLを含むリクエストを接続保持したままタイムアウトさせていた。
   → UAを `Yowayowa-Investor/0.1 (personal research)` にし、理由をコード内に明記。

## 4. 回帰テスト

- `tests/test_ir_acquisition.py` — 33件
- `tests/test_ir_surfaces.py` — 6件
- 合計 39 passed（33 + 6）（上記の全欠陥に対する回帰テストを含む）
- リポジトリ全体: **510 passed**（P1B時点の471から非減少）

## 5. verify / CI / 本番

```
ruff check src tests        : All checks passed!
ruff format --check         : 243 files already formatted
mypy src/yowayowa           : Success: no issues found in 150 source files
pytest                      : 510 passed
openapi export              : /v1/ir/* 5 paths / 6 operations
```

CI run `35811046451` (commit 64cd3dc) — verify success (510 passed、pdfminer-six
20260107 導入確認) / browser success (E2E 69 passed) / deploy-production success
CI run `35811357147` (commit 27a0306) — success

本番:
- `/internal/debug/runtime` → `source_revision=64cd3dc2b99bbd566a479a18dc27d5948ed74e8e`
  （commitと完全一致）、`deployment_id=dpl_BLodR564pztojtq96kcgzb1RVG4q`
- `/openapi.json` に `/v1/ir/sources`、`/v1/ir/sources/{source_id}`、
  `/v1/ir/sources/{source_id}/monitor`、`/v1/ir/instruments/{symbol}/timeline`、
  `/v1/ir/documents/kpi-history` が存在
- `GET /v1/ir/sources` → 403 `Private acquisition is disabled`（public modeでの
  fail-closed設計どおり）

## 6. 非交叉制約

- `git diff --name-status cd93a1b..HEAD -- src/yowayowa/acquisition/` →
  新規3ファイル（discovery.py / documents.py / ir.py）の **A** のみ。
  既存P1Aファイル（service/models/registry/parsers/downloads/transport/
  snapshots/auth/cache）は diff 空。
- `broker_models.py` / `operator_bridge/` は diff 空。
- read-only（GETのみ）、秘密情報なし、認証迂回なし。
- `.github/workflows/ci.yml` は未変更（OAuth Appのworkflow scope不足のため、
  CIへのpdfminer導入は `dev` extra 側で実施）。

## 7. 追補（F1/F2修正, 2026-09-23）

F1/F2修正commit: `4c8393115d57c0a3a40d7877502861d406ae42ec`
（`P1C follow-up: count timeline entries by actual timeline writes; correct
evidence doc test counts`）

- **F1**: `IrMonitorService._process_document()` が
  `tuple[IrDocumentRecord, bool]` を返すようにし、monitor() の
  `timeline_entries` は `self._timeline.append(...)` 成功の実測のみを加算。
  fetch budget超過のURL-only分類文書は加算されない。回帰テスト
  `test_timeline_entries_count_only_actual_timeline_writes` 追加
  （修正前RED: `assert 5 == 2` で失敗 → 修正後GREEN）。
- **F2**: 上記「## 4. 回帰テスト」の `test_ir_surfaces.py` を7件→6件、
  合計を 40 passed→39 passed（33 + 6）に訂正（L107の「510 passed」は
  P1C時点の実測として変更なし）。
- 修正後のIRテスト合計: **40 passed**（34 + 6）
- `make verify`（開発中の計測, P1Dマージ ec2af8e 合流前）: 511 passed
- `make verify`（最終commit eee6def, P1Dマージ込み）: **634 passed**
  （CTO独立検証 2026-09-23, CI run 35824917780 success）
- 実データrecon再実測（nitorihd.co.jp, fresh一時data dir, budget=6）:
  discovered=106 / new_count=106 に対し、
  `outcome.timeline_entries = 6` = on-disk timeline entries `6`
  （修正前の実測では outcome が 106 と報告 / on-disk は 6 だった）。
  12/12 checks passed、run2 new=0（冪等）も維持。
