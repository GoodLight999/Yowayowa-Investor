# P5 検証記録: 文書部分抽出の可視化（PDF表走査中断・XLSX欠落ワークシート部品）

- 日付: 2026-10-24 (JST)
- タスク: t_0c4f378f（yowayowa P5）
- 出典: docs/SILENT_FAILURE_AUDIT_20261023.md §3 #9/#10、PRIVATE_OPERATOR_ROADMAP.md P1C/P5
- 実装: worker-flash（VPS ubuntu / 検証は TSUKUMO-KUN）
- 変更許可範囲の遵守: `src/yowayowa/acquisition/documents.py`（既存差分なし・本カード単独）+ 新規
  `tests/test_document_partial_extraction.py` + 本文書のみ。commit/push なし。他カードの差分は未触及。

## 1. 問題（修正前の実物）

監査 #9: `extract_pdf_tables` の `except Exception: return tables` が、pdfminer
ページ走査の途中例外（初ページ含む）で途中まで積んだ tables を無注記で返し、
`extract_pdf_document` は text 層が成功していれば `parsed=True / parse_note=None`
のまま渡していた → 部分抽出が完全抽出として KPI 抽出へ流れる。

監査 #10: `_read_sheet_rows` の `except KeyError: return []` が、workbook.xml に宣言された
シートの実部品 `xl/worksheets/sheetN.xml` 欠落（チャートシート混在・ trimming 等想定）を
黙って飛ばし、他シートが読めれば `parsed=True / parse_note=None`。全シート欠落時のみ
既存 "no readable sheets" が出る。

修正前の実測（新規テストを先に走査した RED 実行、VPS・rc=1）:
- PDF 走査例外（1ページ成功後）→ `parse_note is None`、tables 1件が無注記で返る
- PDF 初ページ例外 → `parse_note is None`、tables 0件が「表なし」と同じ顔で返る
- XLSX 3宣言中2部品欠落 → `parse_note is None`、`parsed=True`
- XLSX 全部品欠落 → `parse_note == "xlsx contained no readable sheets"`（欠落理由なし）
（RAW ログ: 初回 RED 実行時 scratch `red_run.txt`・10 failed / 7 passed。
本 run で最終試験ファイルの RED を独立再実測（HEAD f562163 のコードのみ + 最終試験
ファイル・`PYTHONPATH=<clone>/src` 付きローカルクローン実行・scratch `red_check.txt`）:
9 failed / 8 passed。落ちる 9 件はすべて新動作（note 付与）テスト、通る 8 件はすべて
保存系テストで、RED/GREEN の境界が設計どおり分離。初回との差 1 件は
`test_xlsx_missing_workbook_*` の改名・整理（初期版ログでは旧名で失敗）による。）

## 2. 実装（確定設計どおり・additive）

対象: `src/yowayowa/acquisition/documents.py`（同一ファイル内の2箇所のみ）

- 固定文note定数（documents.py:35-36）
  - `_PDF_INCOMPLETE_NOTE = "pdf table extraction incomplete"`
  - `_XLSX_MISSING_PARTS_PREFIX = "xlsx worksheet parts missing"`
- 共通ヘルパ `_append_parse_note`（:150）: 既存 note を先頭に残したまま
  "; " 付きで末尾へ追記
  （既存 "text capped at 200000 chars" 注記を消さない。例外の生文字列は決して出さない）
- `extract_pdf_tables(content, *, diagnostics: dict[str, Any] | None = None)`（:158）
  - 戻り値は従来どおり `list`、diagnostics 省略時の呼び出しは完全に従来どおり
  - 走査例外時のみ diagnostics に `table_traversal_incomplete=True` を設定（:253）
  - diagnostics は既存慣習（discovery.py:236 等の keyword-only diagnostics out-param）と同一形
- `extract_pdf_document`（:267）: text 層成功時のみテーブル走査を1回実施し、
  diagnostics フラグが立ったときだけ `parse_note` へ固定文を1回追記（:284）
  - `parsed` の意味は「利用可能な抽出がある」（text 層）のまま変更しない
  - 表なし（正常）・pdfminer 未導入（ImportError）は note 追加なし（無用な警告を増やさない）
- `extract_xlsx_tables`（:364）: 宣言シートのうち部品欠落数を数え（:410）
  - 読めたシートは従来どおり保持、`parsed=True` + `parse_note="xlsx worksheet parts missing: N"`
  - 全シート読めない場合は既存 "xlsx contained no readable sheets" を保持したまま
    "; xlsx worksheet parts missing: N" を足して `parsed=False`
  - 空の正常シート（部品は存在し行なし）は欠落に数えない
  - sheetN.xml 命名・relationship 解析・チャートシート対応は再設計せず現状維持
- `_read_sheet_rows`（:455）: KeyError 時のみ diagnostics `missing_part=True` を設定。
  部品名の生成はこの1箇所のみ（重複実装なし）

抽出値（text / tables / 数値 / 単位 / 順序）、既存 best-effort 契約、image-only の
`parsed=False`、cap・座標アルゴリズム、R1/R7/R8/R9 相当の差分は一切変更していない。

## 3. コードハッシュ

- ベース HEAD: f5621632b0592f3cd6d25230fb81e4c8d39c3424（agent/commercial-foundation 先頭・未commit差分として適用）
- `src/yowayowa/acquisition/documents.py`（変更後）sha256:
  1333c59c134cfe7e0617f0a60430139c9200ed6aab28300e542ccc306ad82c5e
- `tests/test_document_partial_extraction.py` sha256:
  651cd63540be4b8066ed2f07798aa72e77ecb658acbcd571939b60659ed76720
- diff 規模: documents.py +63 / -11 行、新規テスト 1ファイル

## 4. 修正後の実物（GREEN 実測・VPS rc=0）

固定文の伝搬と値不変（新規 tests/test_document_partial_extraction.py・17件）:

| ケース | 実測 |
| --- | --- |
| PDF 正常（表あり） | `parse_note is None`、tables 1件（heads/rows 不変） |
| PDF 正常（表なし） | `parse_note is None`、tables `[]`（無用な警告なし） |
| PDF 初ページ走査例外 | `parsed=True`・`parse_note == "pdf table extraction incomplete"`・例外文言なし |
| PDF 1ページ成功後例外 | tables 1件保持・固定文1回のみ・`corrupt` 等の生文字列なし |
| 直呼び `extract_pdf_tables` | 従来契約のまま `list`・部分 tables を返す（diagnostics なしで不変） |
| text capped 注記と共存 | `"text capped at 200000 chars; pdf table extraction incomplete"`（各1回） |
| pdfminer 未導入 | 既存 "operator-ir" ヒントのみ・incomplete note なし |
| 同一bytes再読 | note/tables/text_chars 一致・テーブル走査は文書1回（リトライなし） |
| XLSX 全シート正常 | `parse_note is None`・全テーブル保持 |
| XLSX 空シートあり | 空シートは欠落に数えない・`parse_note is None` |
| XLSX 3宣言中2部品欠落 | `parsed=True`・読めたシート保持・`"xlsx worksheet parts missing: 2"` |
| XLSX 全部品欠落 | `parsed=False`・`"xlsx contained no readable sheets; xlsx worksheet parts missing: 2"` |
| 宣言0シート / workbook部品無し | 既存 note の exact 一致を維持（suffix なし） |
| 同一bytes再読 | parsed/note/tables 一致 |
| `extract_document` 経由 | PDF・XLSX とも note がサービス入口まで伝播 |

回帰（既存テストは一切修正していない）:
- `tests/test_acquisition_documents_units.py`: 65 passed / 5 xfailed（既存ピン止め欠陥のまま変動なし）
- 上記は新規17件と合わせて 82 passed / 5 xfailed（VPS・rc=0）

## 5. note の伝搬（既存配線の確認・実 registry/DB への書込なし）

- IR service は `record.parse_note = extracted.parse_note`（ir_monitor_service.py:740-742）と
  timeline payload の `parse_note`（:777-779）で既存配線を使用。本カードの製品差分は
  サービス/ルータ/CLI に追加していない。
- 既存 surface 試験（プロセス内 routes・tmp_path のみ）で配線契約を確認:
  `tests/test_ir_monitor_service_units.py` + `tests/test_ir_acquisition.py` +
  `tests/test_private_acquisition_downloads.py` = 92 passed（VPS・rc=0）。
  うち B14 は unparsable 文書の `parsed=False` + note 伝播、downloads surface は
  parsed=True 文書の "capped" note 伝播をピン止めしており、本カードの新 note 値も
  同一の単一代入経路で流れる。

## 6. 静的検査・全量検証（host / rc 付き）

| 項目 | host | 対象 | rc | 結果 |
| --- | --- | --- | --- | --- |
| ruff check | VPS | documents.py + 新規test | 0 | All checks passed |
| ruff format --check | VPS | 同上 | 0 | formatted |
| mypy (strict) | VPS | documents.py + services/ir_monitor_service.py | 0 | no issues in 2 files |
| pytest 新規+既存documents | VPS | 82 passed / 5 xfailed | 0 | GREEN（本runで再実測） |
| pytest IR surface 3件 | VPS | 92 passed | 0 | 配線契約確認（同上・3件合計174/5xf） |
| make verify 全量相当 | TSUKUMO | yw-verify-t0c4f378f-r3（裸HEAD vs 本差分の2相比較） | §7 | §7 |

## 7. TSUKUMO make verify 実測（本run・裸HEAD baseline との2相比較）

環境: TSUKUMO-KUN 上のクリーンクローン `~/yw-verify-t0c4f378f-r3`（HEAD f562163）。
全stepで `PATH=$HOME/.local/bin:$PATH`・`TZ=UTC`・`PYTHONPATH=<clone>/src` をexport
（src-layout パッケージをこのクローンに確定解決。venv は make の bootstrap
`uv sync --no-install-project` が生成）。ツール: Python 3.14.7 / ruff 0.16.8 /
mypy 1.20.2。

- phase1 = 裸HEAD（本差分なし）の baseline。phase2 = 本カード2ファイルのみ
  オーバーレイ（sha256 を VPS 側と一致確認・`git status` は documents.py 変更 1 +
  新規test 1 のみ）後、同一コマンドを再実行。

| step（同一コマンド） | phase1 裸HEAD | phase2 本差分 |
| --- | --- | --- |
| make lint（make rc） | 2（ruff 33 errors・全て本差分ファイル外） | 2（同一） |
| make typecheck（make rc） | 2（31 errors in 5 files / checked 239） | 2（同一） |
| make test（make rc） | 2（収集2 errors: `from tests.test_paperloop import` の No module named 'tests'） | 2（同一） |
| make openapi | 0 | 0 |
| make openapi-check | 2（ターゲット自体がコミット済みHEADに存在しない・VPS作業ツリーのMakefile差分由来） | 2（同一） |
| 全量pytest（`python -m pytest --cov=yowayowa --cov-report=term-missing`） | 12 failed / 2112 passed / 2 skipped（1541.66s） | 12 failed / 2129 passed / 2 skipped（1542.46s） |
| 新規17件のみ（TSUKUMO） | ― | rc=0 GREEN |
| 既存documents units（TSUKUMO） | ― | rc=4（ファイル不在＝未commit。VPS作業ツリーにのみ存在する他カードファイル） |

全量pytestの失敗12件は名前レベルで一致することを実測: phase2 の12名と、失敗が集中する
3ファイル（tests/test_backtest_p4_regressions.py / tests/test_crypto.py /
tests/test_defensive_rotation.py）を裸HEAD状態へ一時復元して絞り込み再実行した12名の
diff ≡ 空。内訳: test_defensive_rotation.py 8 / test_backtest_p4_regressions.py 2 /
test_crypto.py 2 — すべて documents/IR と無関係な既存債務（本カード対象外）。
phase2 の passed 増分は +17 ちょうど（= 新規テストのみ）で、本差分が新たな失敗や
skip 変動を生んでいない。

備考:
- `make test` の収集2 errors は `bin/pytest` 直接起動では rootdir が sys.path に入らない
  ことが原因で、裸HEADのVPSでも同一再現（`.venv/bin/pytest tests/test_cusum_watch.py
  --co -q` → 同一エラー）。本差分と無関係の既存債務。
- baseline 側の全量ログは phase2 が同一ファイル名へ上書きしたため、baseline の失敗名は
  上記の絞り込み再実行で回収した（12F / 2112P / 2S のカウントは実行時に記録済み）。
- 前回run用の旧クローン（~/yw-verify-t_0c4f378f）には本カード外の未commit修正5ファイル
  （paperloop* / jp_value_quality*）が混入していたため証跡に使用せず、クリーンクローンで
  再実行した（旧クローン自体は未触及のまま残置）。

## 8. 未確認範囲・限界

- 実運用のチャートシート混在 XLSX 実物は取得していない（部品存在有無のみで判定する
  設計のため、欠落検出の正しさは fixture で実証）。 relationship ベースの正式な
  シート→部品解決は本カードの範囲外（カード指示どおり再設計しない）。
- UI・ブラウザ E2E は本差分に画面変更がなく適用外。openapi への影響なし（API差分なし）。
- `test_api_run_and_candidates` の JST 00時過ぎ既知 flake は本差分と無関係（TZ=UTC 実行で回避）。
- TSUKUMO 全量で検出された既存債務（ruff 33 errors / mypy 31 errors / test 収集2 errors /
  全量pytest失敗12件 / Makefileのopenapi-check未commit）は裸HEADで同一に発生し、
  各担当カードの範囲。本カードでは分離記録のみで修正しない。

## 9. 制約遵守の自己申告

- 外部送信・応募・契約・注文・送金・課金・公開push・新データソース・新基盤・内蔵LLM・
  config・cron 変更: なし
- commit / push: なし（VPS・TSUKUMO とも作業ツリーのみ）
- 実装者と検収指定 reviewer（audit-glm-heavy）は同一系統（ともに GLM 系）の可能性が
  あるため、metadata に明示のうえ独立再実行を依頼する
