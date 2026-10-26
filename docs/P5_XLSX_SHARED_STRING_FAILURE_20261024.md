# P5 検証記録: XLSX共有文字列参照の不正を財務値化せず明示的な未解析にする

- 日付: 2026-10-24 (JST)
- タスク: t_04c17af3（yowayowa P5、親 t_0c4f378f / t_5a078314）
- 出典: AGENTS invariants 2/3/9、`docs/PRIVATE_OPERATOR_ROADMAP.md` P1C/P5 の構造化抽出と failure flows
- 実装: worker-goat（VPS ubuntu / 全量スイートは TSUKUMO-KUN）
- 変更許可範囲の遵守: `src/yowayowa/acquisition/documents.py` の共有文字列解決と局所失敗境界、
  新規 `tests/test_xlsx_shared_string_failclosed.py`、`tests/test_acquisition_documents_units.py` の
  当該 strict-xfail マーカーと境界表1行、本文書のみ。commit/push なし。
  IR サービス/ルータ/CLI・schema/DB/API・config/cron/モデルは未変更。

## 1. 問題（修正前の実物）

`_read_sheet_rows` は `t="s"` セル（共有文字列への**参照**）の解決に失敗すると、
`except (ValueError, IndexError): text = value_node.text` で参照値そのものをセル本文に
差し替えていた。さらに負の index は Python の末尾参照が効くため `-1` が**末尾の共有文字列**
に化けていた。結果、文書は `parsed=True / parse_note=None` のまま KPI 抽出へ流れ、
存在しない財務値が「抽出済み」として扱われる。

修正前の実測（本カード開始時点の `documents.py` sha256 `1333c59c…`・20865B。
RED 実行: `tests/test_xlsx_shared_string_failclosed.py` を**未修正のソース**に対して実行し
16 failed / 4 passed, rc=1。落ちる16件はすべて新動作（fail closed）系、通る4件は
正常値の保存系で、RED/GREEN の境界が設計どおり分離）:

| 入力（`t="s"` の `<v>` と sharedStrings） | 修正前 | 修正後 |
|---|---|---|
| sharedStrings.xml 欠落 + `0` | `parsed=True note=None` データ `['受注残','0']` | `parsed=False note='xlsx shared string reference invalid'` |
| `<sst/>`（空）+ `0` | `parsed=True note=None` データ `['受注残','0']` | 同上 |
| 1件の表 + `5`（範囲外） | `parsed=True note=None` データ `['受注残','5']` | 同上 |
| 1件の表 + `1`（= len） | `parsed=True note=None` データ `['受注残','1']` | 同上 |
| 1件の表 + `-1`（負） | `parsed=True note=None` データ `['受注残','x']`（末尾文字列が混入） | 同上 |
| 1件の表 + `bad` | `parsed=True note=None` データ `['受注残','bad']` | 同上 |
| 1件の表 + `1.0` | `parsed=True note=None` データ `['受注残','1.0']` | 同上 |
| 1件の表 + 空 `<v></v>` | `parsed=True note=None` データ `['受注残']` | 同上 |
| 1件の表 + `<v>` 無し | `parsed=True note=None` データ `['受注残']` | 同上 |
| 先行シート正常・後続シートに `5` | `parsed=True note=None` データ `['受注残','1200']`（先行表が返る） | 同上（表は 1 件も返らない） |
| 正常（index 0 と最終 index） | `parsed=True note=None` | **同一**（下記 §5） |

（実測手順: 現行ファイルから本カードの5ハンクを逆向きに適用して復元した
`pre_fix_documents.py` が**バイト一致**（20865B / sha256 `1333c59c…` = 開始時実測値）で
あることを確認し、同一 fixture を旧/新モジュールへ通して差分を保存。
RAW: scratch `before_after_true.txt` / `reconstruct_pre_fix.py`）

## 2. 確定設計（カード指定どおり）

- `t="s"` セルは**共有文字列への参照**であり数値そのものではない。参照値が
  整数として解釈できない／値なし・空／負／`0 <= index < len(shared)` を満たさない
  ときは**修復しない**。`sharedStrings.xml` 不在・空でも参照があるなら不正。
  参照が無い inline/numeric のみの文書は正常。
- 不正参照が読取対象範囲内のどのシートにあっても、文書全体を既存
  `ExtractedDocument(format='xlsx', parsed=False, tables=[], parse_note='xlsx shared string reference invalid')`
  にする。先に読めたシートや `_raw` 値を部分的な正しい財務表として返さない。
- 内部の局所診断は既存境界（keyword-only `diagnostics` out-param）で上記固定 note へ写像し、
  生例外・セル値・URL を露出しない。
- 正常な共有文字列参照（index 0 / 最終 index）、リッチテキスト `si`、inlineStr、通常 numeric、
  空セル、`headers`/`rows`/`_source_rows`/`name`/順序/`parser_version` は従来不変。
  負 index の Python 末尾参照は禁止。
- `_XLSX_MAX_ROWS`/`_XLSX_MAX_COLUMNS`、走査範囲、座標/relationship 解釈、currency/KPI 計算は未変更。
  新 schema/DB/サービス/API は作らない。

## 3. 実装（`documents.py`・本カードの差分 +62/−8）

ベースは親カード t_0c4f378f 適用後の作業ツリー（`documents.py` 63/11）。本カードの変更は次の5点のみ:

- `documents.py:37` 固定文定数 `_XLSX_INVALID_SHARED_STRING_NOTE = "xlsx shared string reference invalid"`
- `documents.py:468` 新ヘルパ `_resolve_shared_string(value_node, shared) -> (text, resolved)`:
  `<v>` 無し／空文字／非整数／負／範囲外を `resolved=False` に落とす。
- `documents.py:541` セル解決: `cell_type == "s"` のときは必ず resolver を通し、
  未解決なら `diagnostics["shared_string_reference_invalid"]=True` を立てて**そのシートの行を
  1行も返さない**（`return []`）。`t="s"` 以外の inlineStr / numeric / 空セルの分岐は従来どおり。
- `documents.py:384` `extract_xlsx_tables`: シート読み取り直後にフラグを確認し、立っていれば
  文書全体を固定 note の `parsed=False / tables=[]` で即返す（先行シートは破棄）。
  既存の `missing_part` 集計・`xlsx worksheet parts missing: N` 契約はそのまま。
- `documents.py:494-509` `_read_sheet_rows` の docstring に diagnostics 契約を明記。

ハッシュ（実測・2026-10-24 06:49 JST）:
- `src/yowayowa/acquisition/documents.py` → 23408B / sha256 `6fae69cb3699ead9fe554c98ca80e1ececca3124ca256c5928b48cf008baa9e5`
  （HEAD `f562163` からの累計 diff 125/19 = 親カード 63/11 + 本カード 62/8）
- `tests/test_xlsx_shared_string_failclosed.py` → 16443B / sha256 `84022039198110286935511377376c2ad4808c74935f332c6490a633042746fd`
- `tests/test_acquisition_documents_units.py` → sha256 `434b31c634dd207b71788f0f6ecdd311ca7fe2aa5ee3c5feedf907b7f70203b8`
- 修正前 `documents.py` → 20865B / sha256 `1333c59c134cfe7e0617f0a60430139c9200ed6aab28300e542ccc306ad82c5e`

## 4. 新規専用試験 `tests/test_xlsx_shared_string_failclosed.py`（20件・独立）

他テストのヘルパを import せず、標準 zip/XML fixture を自前で構築する（参照再現テスト
`test_acquisition_documents_units.py` の strict-xfail とは独立に持つ）。

- 不正参照9態（欠落表／空表／範囲外／=len／負／非整数／`1.0`／空 `<v>`／`<v>` 無し）→
  `format='xlsx' / parsed=False / tables=[] / note=固定文`。
- ヘッダセル側・データセル側の不正 → 文書全体 fail closed。
- 後続シートの不正 → 先行シートの表（`受注残`/`1200`）も返らない（`repr` にも現れない）。
- 固定 note 以外を漏らさない: セル値マーカー・URL・ファイル名・`Error`/`Traceback` を含まず、
  `text` は None。
- 正常全境界: 共有文字列 index 0 と最終 index、リッチテキスト `si` の連結、inlineStr、
  通常 numeric、空セル、シート順、`name`、`headers`/`rows`/`_source_rows`、`parser_version='doc-v1'`。
  sharedStrings 不在でも**参照が無い**文書（inline/numeric のみ）と空 `<sst/>` は
  `parsed=True / note=None`。
- 同一 bytes 再読で dataclass 完全一致（`first == second`）。
- `extract_document` 経由（拡張子 `.xlsx` / content-type spreadsheetml）でも同じ固定 note。
- `_read_sheet_rows` 直呼び: 不正参照時に `[]` を返し、参照値を list に混ぜない
  （diagnostics 省略時も同様）。正常時は従来の行を返す。
- IR プロセス内実経路: `IrMonitorService.monitor()` をソケット遮断下の stub transport で実行し、
  文書レコードが `parsed=False / parse_note=固定文 / kpis=[] / notes に固定文`、
  タイムライン payload も `parsed=False / parse_note=固定文 / kpis=[]` を保持。

## 5. 正常値の不変・先行部分抽出契約の非回帰

- 正常文書（index 0・最終 index・リッチテキスト・inlineStr・numeric・空セル）は
  修正前モジュールと**全フィールド一致**（`dataclasses.asdict` 比較で True）。
  実測 `valid-tables: [{'name': 'KPI', 'headers': ['受注残','1200'], 'rows': [{'受注残':'受注残','1200':'1200'}], '_source_rows': [['受注残','1200'],['受注残','1200']]}]`（修正前後同一）。
- 親カード t_0c4f378f の部分抽出契約は保持: `tests/test_document_partial_extraction.py`
  17件 GREEN（`xlsx worksheet parts missing: N`、PDF 走査中断 note、既存 note の非消去、
  同一 bytes 再読一致）。本カードは `_append_parse_note` と missing 集計に触れていない。
  併せて `tests/test_acquisition_documents_units.py` は 66 passed / 4 xfail（マーカー解除で 5→4）。
- `tests/test_ir_acquisition.py`（共有文字列の正常読み取り `test_xlsx_extraction_reads_shared_strings`
  を含む）・`tests/test_ir_surfaces.py`・`tests/test_ir_monitor_service_units.py` も rc=0。

## 6. IR への伝搬（追加変更なし）

`IrMonitorService._process_document` は既存の `record.parsed = extracted.parsed` /
`record.parse_note = extracted.parse_note` 配線、`_extract_kpis` の `if not extracted.parsed:
return [], [extracted.parse_note or "document not parsed"]` 分岐でそのまま扱う。
IR サービス/ルータ/CLI の製品変更は行っていない。実測（§4 のプロセス内テスト）で
KPI 0件・同一理由の保持を確認。

## 7. 実測（host / コマンド / rc）

VPS (ubuntu, 4 core):

| 対象 | コマンド | 結果 |
|---|---|---|
| RED | `pytest tests/test_xlsx_shared_string_failclosed.py`（未修正ソース） | 16 failed / 4 passed, rc=1 |
| 新規専用 | 同上（修正後） | 20 passed, rc=0 |
| 近傍6ファイル | `pytest tests/test_xlsx_shared_string_failclosed.py tests/test_acquisition_documents_units.py tests/test_document_partial_extraction.py tests/test_ir_acquisition.py tests/test_ir_surfaces.py tests/test_ir_monitor_service_units.py` | 194 tests / failures 0 / errors 0 / skipped 4（既存 strict xfail）, rc=0（junit 実測） |
| 近傍その他 | `tests/test_private_acquisition_service.py` + `test_private_acquisition_snapshots.py` + `test_private_source_service.py` | rc=0 |
| ruff check（当該3ファイル） | `ruff check src/yowayowa/acquisition/documents.py tests/test_xlsx_shared_string_failclosed.py tests/test_acquisition_documents_units.py` | All checks passed, rc=0 |
| ruff format（当該3ファイル） | `ruff format --check` 同上 | 3 files already formatted, rc=0 |
| mypy | `mypy src/yowayowa` | Success: no issues found in 242 source files, rc=0 |
| openapi | `python scripts/check_openapi_baseline.py` | baseline 一致（155 operations）, rc=0 |

xFAIL の増減: `test_acquisition_documents_units.py` の strict xfail は 5 → 4
（`test_xlsx_unresolvable_shared_string_index_fails_closed` のマーカーのみ解除。テスト本体は
弱めず、コメント1行を追加）。境界表の当該1行を `DEFECT (xfail)` → `fail closed` に更新。
他の xfail・期待値・テスト数は削除・緩和していない。

## 8. 全量スイート（TSUKUMO-KUN）と既存失敗

TSUKUMO の `~/yw-verify-t04c17af3`（VPS 作業ツリーを rsync・共有 venv `yw-verify-t0c4f378f-r3/.venv` を使用）で実測:

- 新規専用20件: 20 passed（20 dots, rc=0）
- 全量: **tests=2926 / failures=14 / errors=0 / skipped=8（うち strict xfail 4） / 1565.5s**（junit 実測:
  `verify_logs/full.xml`、ログ `full.log`）
- 失敗14件の内訳（いずれも本カードの変更対象外のファイル。`documents.py` /
  `acquisition.*` / IR を参照しない）:
  - `tests/test_crypto.py` 2件（Binance klines の record 数）
  - `tests/test_defensive_rotation.py` 8件（`test_existing_eight_full_responses_match_preimplementation_head`）
  - `tests/test_jp_value_quality_backtest_units.py` 4件（`KeyError: '1000.T'` / `turnover` 期待値。
    同テストは `yowayowa.services.jp_value_quality*` のみ import し `documents` 参照 0。
    `src/yowayowa/services/jp_value_quality*.py` と当該テストは別カードの未コミット変更で、
    ruff 債務も同じファイル群。本カードは未触及）
- 参考: 親カード t_0c4f378f 時点の baseline は 12件（`test_backtest_p4_regressions` 2 +
  `test_crypto` 2 + `test_defensive_rotation` 8）。今回 `test_backtest_p4_regressions` は通過、
  `jp_value_quality_backtest_units` 4件が新たに失敗しており、**差分は他カードの同時進行変更に由来**する。
  本カードの変更は XLSX 読み取り（`t="s"` セル）に閉じており、これらの失敗経路に接続しない。
  よって「全量 pass」ではなく「既存/並行作業由来の14件失敗のみ・本カード関連0件」と記録する。

## 9. 未確認範囲・制約（正直な記載）

- 走査範囲外（`_XLSX_MAX_ROWS`=10000 行を超えた行、`_XLSX_MAX_COLUMNS`=256 列を超えた列、
  宣言外のシート、`sheetN.xml` の relationship 解決外）に不正参照があっても検出しない。
  これは仕様どおり（走査範囲は変更していない）。
- `sharedStrings.xml` 自体が壊れた XML の場合は従来どおり既存の
  `xlsx parse failed: ParseError` 経路（本カードの固定 note ではない）。カードの許可範囲外。
- 実運用チャートシート混在 XLSX の実物での確認は未実施（fixture 実証のみ）。
- `t="s"` と `<is>` が同時に存在する（仕様外）セルは参照として検証する（fail closed 側）。
- 既存 lint 債務は本カード外: リポジトリ全体の `ruff check src tests` は rc=1（36 errors は
  `paperloop*`/`jp_value_quality*`/`test_screening_cli_dates.py`）、`ruff format --check src tests` は
  8ファイル未整形（いずれも当該3ファイル外）。全量 pass と偽らない。
