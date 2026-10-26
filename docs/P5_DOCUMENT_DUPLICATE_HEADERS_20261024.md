# P5: CSV/XLSX同名ヘッダで列値を失わず保持（HTMLと同一の重複キー規則）

- カード: t_469b2ee4（2026-10-24・worker-flash）
- 先行hotspot: t_c7485e09（同ファイルURL断片fix・検収済み）完了後に着手。
  その親 t_04c17af3（XLSX共有文字列fail-closed）・t_0c4f378f も同一ファイルの先行。

## 1. 背景と確定設計

`documents.py` のCSV/XLSX行投影が `row[key] = value` の無条件上書きで、同名ヘッダの
先行列値を黙って失っていた（AGENTS 不変条件1/2/3/5/9・PRIVATE_OPERATOR_ROADMAP P5）。
先例はHTML側 `acquisition.parsers._TableCollector` + `_dedupe` の逐次規則
（未使用キーはそのまま、使用済みは最小の未使用 `.2/.3/...`）。

修正は **列投影のみ**: 両投影の `row[key] = value` を `row[_dedupe(row, key)] = value`
へ置換し、`parsers._dedupe` を既存helperとして再利用（parsers.py自体は無変更・非public化・
リファクタ無し・importは同ファイル既存の関数内import様式）。基本キー規則
（非空ヘッダはその文字列、空は `_column_letter(column+1)`）、自然なsuffixヘッダの先予約無し
（`a,a,a.2 → a,a.2,a.2.2`）、挿入順＝元列順、値の足合せ/単位/数値/空白/大小文字変換無し、
raw headers と `_source_rows` 不変、short row補完と余剰セルの従来契約、header-only・空文書・
capの意味、extract_document経路、ir.pyの `_source_rows` 優先grid復元とKPI/期間/単位選択は
一切変更していない。ir.py・PDF/HTML製品コード・URL/encoding/座標/relationshipは範囲外。

## 2. 入力とコードhash（実測）

| 項目 | 値 |
|---|---|
| HEAD | f562163（着手時と終端で不変・commit/push無し） |
| documents.py 修正前 | aefb6716ae225aba32c182b3a4a9e114cad5cf1d76ea97184141d40c03226698（= t_c7485e09 検収時hash） |
| documents.py 修正後 | 52de468dfe8db0b91395e8199eb72fb97773fbc2786231417a780157909f7295 |
| documents.py numstat | 141/22 → 154/24（本カード差分 = +13/−2） |
| tests/test_document_duplicate_headers.py（新規） | f20c790a829c70998bb3259bebf6c6cd79be3224fe966bcadd25c035323fa184（21 tests） |
| tests/test_acquisition_documents_units.py | 7da9a31691cee6fff3f9245ac1c43da20bfa7da662686208d23eab33c8b73444（先行カード由来の未追跡ファイル。本カード改変は2箇所のみ＝後述） |
| 試験入力 | 小型独立fixtureのみ: ASCII/日本語CSVバイト列・標準zip XML（inlineStr + sharedStrings t="s"）。ネットワーク遮断・実ファイル/本番DB/registry書込0 |
| TSUKUMO転送検証 | 3変更ファイルのsha256がVPS現物と完全一致（rsync後実測） |

本カードのdocuments.py差分は4hunkのみ:
`extract_csv_tables` 関数内 `from yowayowa.acquisition.parsers import _dedupe`（+2行）、
`extract_xlsx_tables` 同様（+2行）、CSV投影の `row[key]=value → row[_dedupe(row,key)]=value`
+コメント3行、XLSX投影の同置換+コメント4行。先行3カードの全hunk（URL dispatch・
共有文字列fail-closed・部分抽出note等）は現物に保持を確認。

参照unitsへの改変は2箇所のみ（試験本体・他xfail・期待値は無変更）:
1. `test_duplicate_headers_keep_both_columns_in_csv_and_xlsx` の
   `@pytest.mark.xfail(strict=True, ...)` デコレータ削除（解除のみ・本体2assertは1文字も変更せず）。
2. 境界表の該当1行 `DEFECT (xfail)` → `kept (a.2)`。

## 3. RED実測（修正前コード・CTO再現を含む）

新規試験21件を未修正コードで実行（TZ=UTC・.venv/bin/pytest・junitxml機械集計）:

- **13 failed** — 同名列の全投影系（CSV a,a / XLSX inline / XLSX shared-strings /
  3同名×2 / 自然suffix×2 / fallback衝突×2 / 行別解決 / 値.pass-through /
  short・surplus / 複数sheet）。
- **8 passed** — 不変ガード（uniqueヘッダ全フィールド一致・header-only・同一bytes再読・
  dispatch一致・HTML規則・IR raw grid・期間headers grid・unique KPI観測）。
  注: 同一bytes再読とdispatch一致は「壊れた出力同士の一致」としてREDでも通る性格のガード。

CTO再現の実出力（修正前）:

```
extract_csv_tables(b'a,a\n1,2\n').tables[0]['rows']  ->  [{'a': '2'}]   # 先頭列 '1' 消失
extract_xlsx_tables(inline a,a / 1,2)                ->  [{'a': '2'}]
extract_xlsx_tables(shared-strings a,a / 1,2)        ->  [{'a': '2'}]
k,k,k / 1,2,3                                        ->  [{'k': '3'}]
a,a,a.2 / 1,2,3                                      ->  [{'a': '2', 'a.2': '3'}]
B, / 1,2（実B+空列fallback B）                        ->  [{'B': '2'}]
```

## 4. GREEN実測（修正後コード）

- 新規21件: **21 passed**（rc=0）。
- 参考units（マーカー解除前のまま実行）: 該当1件のみ `[XPASS(strict)]`、他全件pass・
  xfail2件はそのまま → 解除後は green（xfail2件のみ残存）。
- 近傍8ファイル: **263 tests / failures=0 / errors=0 / skipped=2**（junit実測。skipped=2は
  残存strict-xfail2件）。対象: 新規21・documents units・URL断片dispatch・部分抽出・
  acquisition parsers（共有文字列）・IR acquisition・IR monitor service units・IR surfaces。
- ruff autofix（UP012 13件・ASCIIバイトリテラル化）+ format 適用後に新規21+units 91件を
  再実行し **0F/2skip** を再確認（動作同等の書式修正のみ）。

## 5. before/after 実出力（probe差分・全文はscratch保存）

同一probe入力での差分は **重複投影の8行のみ**:

```
CSV/XLSX inline/XLSX shared a,a / 1,2 : [{'a': '2'}]          -> [{'a': '1', 'a.2': '2'}]
k,k,k / 1,2,3（CSV・XLSX）             : [{'k': '3'}]          -> [{'k': '1', 'k.2': '2', 'k.3': '3'}]
a,a,a.2 / 1,2,3                       : [{'a': '2', 'a.2': '3'}] -> [{'a': '1', 'a.2': '2', 'a.2.2': '3'}]
B, / 1,2                              : [{'B': '2'}]          -> [{'B': '1', 'B.2': '2'}]
URL断片/query付きdispatch a,a / 1,2    : [{'a': '2'}]          -> [{'a': '1', 'a.2': '2'}]
```

差分に出なかった行＝完全一致（正常uniqueのCSV/XLSX ExtractedDocument全フィールド・
HTML `a,a,a.2 → a,a.2,a.2.2` 規則・IR `_raw_cell_grid` の2列保持
`[['売上高','売上高'],['912,248','999,999']]`・unique入力のKPI観測
`[{'kpi':'revenue','label':'売上高','value':912248.0,'raw_value':'912,248','unit':None,'source':'table[0]'}]`）。

## 6. 正常文書の不変と列保持の証明

- uniqueヘッダ文書はdataclass全フィールド一致（`ExtractedDocument(format, parsed,
  parse_note, parser_version, tables[headers/rows/_source_rows], text, text_chars)` を
  期待値全体と等値assert・RED/GREEN両方でpass＝修正前後byte相当一致）。
- 全投影列数＝辞書キー数（`len(row) == len(headers)`）・`list(row.values())` が元セル順に一致・
  `list(row.items())` が列順一致。3同名列・fallback衝突・複数sheetでも値を落とさない。
- `headers` は生のまま（重複を保持）、`_source_rows` も生のまま（short rowは短いまま・
  余剰セル保持）。投影への空文字補完は従来どおり行内のみ。
- KPI算法は不変: ir.py 無変更。unique入力の観測（値/単位/出典/raw_value）を全量等値で固定。
  期間headersも `_raw_cell_grid` 経由でbyte一致を固定（layout/期間選択の入力が不変）。
  重複文書では誤値（後列勝ちの黙示上書き）を互換の名目で温存せず、投影が完全行を渡す。

## 7. 先行差分への回帰ゼロ

- documents.py: 本カード差分+13/−2・先行3カードのnumstat累積（141/22）はそのまま残存・
  hunk単位で先行者分を特定（URL dispatch・shared string diagnostics・部分抽出note・PDF周辺は全て現物保持）。
- URL断片t_c7485e09の挙動: 近傍の `test_document_url_fragment_dispatch.py` 全pass
  （同じ入力で回帰0）・`extension_of` 未触碰。
- 共有文字列t_04c17af3: `test_xlsx_unresolvable_shared_string_index_fails_closed` ほか全pass。
- 部分抽出・IR近傍8ファイル263件 0F（上記）。

## 8. 全量検証（TSUKUMO既存環境・機械値）

- 環境: TSUKUMO-KUN / Python 3.14.7（uv sync --extra dev --extra operator-ir
  --no-install-project）/ 作業木 `~/yw-verify-t_469b2ee4`（VPS現物をrsync・
  3変更ファイルhash一致を確認・`.venv/.git/.worktrees/data等`は除外）。
- 全量pytest（`python -m pytest`・CI同等のcwd解決・TZ=UTC・junitxml）:
  **tests=3024 / failures=16 / errors=0 / skipped=9**。
- 失敗16の内訳（junit機械集計）:
  - 既知ベースライン14（先行検収t_c7485e09と同一ファイル・同一件数）:
    `test_crypto.py` 2 / `test_defensive_rotation.py` 8 / `test_jp_value_quality_backtest_units.py` 4。
    4ファイルすべて documents/acquisition/IR への参照0（grep実測）・本カード変更と無関係。
  - 環境欠損2: `test_backtest_p4_regressions.py` の2パラメータ。
    原因はrsync除外したランタイムデータ `data/fundamentals/pbr/split-calendar.json`
    （sha256 ae7668f0…4324bytes）不在による "split calendar unavailable: file not found"。
    当該ファイルのみ転送後に同ファイルを再実行 → **31 tests / failures=0 / errors=0**
    （p4_rerun.xml実測・旧失敗2パラメータを含む）。
    split-calendar参照の残り3ファイル（test_backtest/test_sec_pbr/test_sec_pbr_hardening）
    もデータ補完後に全pass。コード変更は此の間ゼロ。
- 型・lint・API契約（VPS・同一hashツリー）: `mypy src/yowayowa` **242ファイル rc=0** /
  変更3ファイルの `ruff check` + `ruff format --check` **rc=0** /
  `openapi` export rc=0 / `openapi-check` rc=0（155 operations・baseline一致）。
- make lint の全量（src tests 全体）は他カードのdirtyファイル
  （paperloop系・loop_cli.py・test_jp_value_quality_backtest_units.py 等46ファイル・49件）
  で既存失敗 — 本カード3ファイルは含まれない（ファイル別集計を機械実測）。

## 9. 未確認範囲・制限

- 実運用チャートシート混在XLSX等の実物確認は未実施（fixture実測のみ・先行カードと同じ限界）。
- TSUKUMO側Pythonは3.14.7（VPS 3.12）— uv.lockのパッケージバージョンは同一・
  失敗分布は既知ベースラインと一致するがマイナー差分の影響は完全には切り分けていない。
- make lint全量の既存49件（他カードdirtyファイル）は本カードでは触れていない（範囲外）。
- 参考unitsファイルは先行カード由来の未追跡ファイル（本カード改変は2箇所のみ・diffは
  本doc §2に記載）。INDEX は触っていない。commit/push無し。
- TSUKUMO検証木 `~/yw-verify-t_469b2ee4`（~300MB・full.xml/p4_rerun.xml同梱）は
  再帰削除ガードのため残置（他検証物と同じく剪定運用に任せる）。
