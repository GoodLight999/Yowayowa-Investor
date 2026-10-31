# P5: URL断片付きIR文書を未知形式に落とさず同じ抽出器へ渡す（t_c7485e09）

- 日付: 2026-10-24 / 実装: worker-flash / 検収: audit-glm-heavy（独立再実行）
- 出典: AGENTS invariants 1/3/5/9、PRIVATE_OPERATOR_ROADMAP.md P1C 構造化抽出・P5 failure flows
- 対象: `src/yowayowa/acquisition/documents.py` の `extension_of` URLフォールバック経路のみ

## 1. 欠陥と修正

旧実装はURL末尾成分から `?` だけを剥がし、`#` 以降（fragment）を拡張子に混ぜていた。
その結果、同一バイト列でも URL の書き方で既知形式が未知形式（`no extractor for this
document format`）に落ちる第二の同一性が生じていた。

修正は URL フォールバック経路のみ: `urllib.parse.urlsplit(url).path` から末尾成分を取り、
query/fragment の内容・順序・fragment内の `?` `/` で拡張子を変えない。`urlsplit` の
`ValueError`（不正bracket URL等）は `None` にし生例外を外へ出さない。percent-decodeはしない。
filename 非空時は従来どおり文字列をそのまま優先し、filename内の `#` `?` はURL構文として
修復しない。末尾スラッシュ除去・最終成分・最後のdot・lowercase・dotfile規則は維持。
保存URL/provenance/キャッシュキー/取得先/認証/host policy・`extract_document` の
優先順位と content-type fallback・未知形式の固定note・`ExtractedDocument` 全フィールドと
`parser_version` は不変。他のURL正規化基盤は作らない。

## 2. コードhash（HEAD f562163・commit/push なし）

| ファイル | 状態 | sha256 |
|---|---|---|
| documents.py | 本カード着手時（先行2カード累積差分 125/19） | `6fae69cb3699ead9fe554c98ca80e1ececca3124ca256c5928b48cf008baa9e5` |
| documents.py | 本カード適用後（累積 141/22） | `aefb6716ae225aba32c182b3a4a9e114cad5cf1d76ea97184141d40c03226698` |
| tests/test_document_url_fragment_dispatch.py | 新規 | `452610d71bb7741305050b88bedfb57ea7c8652419235f158a5102d29beb906d` |
| tests/test_acquisition_documents_units.py | t_04c17af3 検収後（着手時） | `434b31c634dd207b71788f0f6ecdd311ca7fe2aa5ee3c5feedf907b7f70203b8` |
| tests/test_acquisition_documents_units.py | 適用後 | `f19f1bf8b67f69dbf8912cc241a1f5c29006030ceab8574ddbc7aedf537dd5dd` |

- documents.py の差分内訳: HEAD f562163 から累積 141/22 = 親 t_0c4f378f 63/11 +
  t_04c17af3 62/8 + 本カード +16/-3（`extension_of` 本体と `from urllib.parse import
  urlsplit` のみ）。先行差分の hunk は全部残置。
- units への変更は2点のみ: (a) `test_url_fragment_does_not_change_document_identity` の
  strict-xfail マーカー解除（テスト本体の assert は一字も変えず）。
  (b) 境界表の当該1行を `DEFECT (xfail)` → `same extension` に更新。
  xfail マーカー数 4 → 3、他の xfail・期待値は削除・緩和していない。

## 3. 同一定入力の before/after（`extension_of(None, url)`）

旧実装を HEAD f562163 から抽出し、新実装と同一入力24件で比較（プローブ:
`scratch/t_c7485e09/before_after_probe.py`、ログ `before_after.log`）。
**changed=10/24、filename経路 0/6 変更。**

| url | before | after |
|---|---|---|
| `.../summary.csv` | `.csv` | `.csv`（不変） |
| `.../summary.csv?token=abc&v=2` | `.csv` | `.csv`（不変） |
| `.../summary.csv#tab=2` | `.csv#tab=2` | `.csv` |
| `.../summary.csv#tab?x=1` | `.csv#tab` | `.csv` |
| `.../summary.csv?token=abc&v=2#tab=2` | `.csv` | `.csv`（不変・queryが先にある旧経路） |
| `.../summary.csv#tab/2` | `None` | `.csv` |
| `.../summary.csv#fake.pdf` | `.pdf` | `.csv`（fragment偽装を無効化） |
| `.../brief.pdf#page=2` | `.pdf#page=2` | `.pdf`（CTO観測の再現修正） |
| `.../book.xlsx#tab=2` / `.../notes.html#section-3` | `.xlsx#tab=2` / `.html#section-3` | `.xlsx` / `.html` |
| `https://example.co.jp`（/ 同） | `.jp` | `None` |
| `https://]example/ir/a.csv` | `.csv` | `None`（urlsplit ValueError → None） |
| `https://[::1/bad` | `None` | `None`（不変・例外漏出なし） |
| `.../report%2Ecsv` | `None` | `None`（不変・decodeしない） |
| `.../BRIEF.PDF` / `archive.tar.csv` / `a.csv/` | `.pdf` / `.csv` / `.csv` | 同（不変） |
| `.../download`, `.../download?x=1`, `.../ir/.csv`, `.../ir/#f`, `""`, `mailto:…` | — | 同（不変） |

filename経路（旧新同一・6件）: `("filing.csv")→.csv`、`("report#2.csv")→.csv`、
`("report?v2.pdf")→.pdf`、`("weird.csv#frag")→.csv#frag`（修復しない現状維持）、
`(None,None)→None`、`("",url)→URL優先`。

## 4. dispatch回数と抽出器一致（extract_document）

新規 `tests/test_document_url_fragment_dispatch.py`（60件・ネットワーク遮断
`socket.socket.connect` を autouse で拒否・外部取得/LLM不使用）:

- 同一CSV bytes × URL6型（plain/query/#tab=2/query+#/#tab?x=1/#tab/2）→
  `dataclasses.asdict` 完全一致・`format="csv"`/`parsed=True`（6件）。
- 独立最小XLSX zip × 同6型 → 同完全一致・`format="xlsx"`/`parsed=True`（6件）。
- PDF/HTML は既存抽出器への **1回だけ** の dispatch を spy で実測
  （`extract_pdf_document` は bytes を1回、`extract_html_tables` はデコード済み str を1回。
  fragment付きURL `brief.pdf#page=2` / `notes.html#section-3`）。
- 未知形式の固定note・content-type fallback・正常query付きURL出力の不変を直接ピン留め。

## 5. RED→GREEN 実測

| 段階 | 結果 | rc |
|---|---|---|
| RED（未修正ソース×新テスト60件） | **24 failed / 36 passed** | 1 |
| GREEN（修正適用後） | **60 passed** | 0 |

REDの24件はすべて新動作テスト（fragment系拡張子12、fragment偽装1、path無し・root・
bracket `]` 3、CSV/XLSX同一性6、PDF/HTML spy 2）。保全系（plain/query、filename規則、
未知note、content-type fallback）36件は修正前から全PASS。

## 6. テスト実績（host: VPS / rc付き）

| 対象 | 結果 | rc |
|---|---|---|
| 新規専用テスト 60件 | 60 passed | 0 |
| 近傍6ファイル（units・新規・xlsx共有文字列・部分抽出・IR acquisition・IR monitor units） | **242 passed / 3 xfailed** | 0 |
| ruff check（3変更ファイル） | All checks passed | 0 |
| ruff format --check（3変更ファイル） | 3 files already formatted | 0 |
| mypy src/yowayowa | no issues in 242 files | 0 |

先行契約の非回帰: `tests/test_xlsx_shared_string_failclosed.py`（共有文字列fail-closed）と
`tests/test_document_partial_extraction.py`（PDF走査中断・worksheet欠落note）は上記
バッテリー内で全Green。単体テストのマーカー解除は当該1件のみ（§2）。

### TSUKUMO 全量 verify

- クローン: `tsukumo:~/yw-verify-t_c7485e09`（VPSツリーのrsync・3変更ファイルのhashが
  VPS現物と一致を確認済み）。`make -k V=/home/good-light/yw-verify-t0c4f378f-r3/.venv verify`
  （lint/typecheck/test/openapi/openapi-check）＋ test は `python -m pytest` でmake testと
  同一フラグ（--cov --junitxml）により再実行。結果: `verify_logs/verify.log`・
  `verify_logs/full.log`・`verify_logs/full.xml`。
- typecheck: **mypy src/yowayowa = no issues in 242 files, rc=0**。
- openapi: export成功・**baseline一致 (155 operations), rc=0**。
- test（full.xml 実測・junit機械値）: **tests=3003 / failures=14 / errors=0 / skipped=7 /
  XPASS=0, rc=1**。失敗14件の全内訳 = `test_crypto.py` 2件 + `test_defensive_rotation.py`
  8件 + `test_jp_value_quality_backtest_units.py` 4件。3ファイルとも `documents`/
  `acquisition` への参照が無いことを自力確認（import行grep）し、先行カード t_04c17af3
  検収時に同じ14件が「本カード変更面と無関係の既存失敗」として記録された集合と同一。
  **documents/acquisition/IR 由来の失敗は0件**（本カード新規60件を含めて全Green）。
- lint: rc=1（既存債務のみ）。`ruff check src tests` = 36 errors — 発生ファイルは
  `paperloop_panel.py`(11)・`test_paperloop.py`(4)・`test_cusum_watch.py`(4)・
  `test_jp_value_quality_backtest_units.py`(3)・`paperloop.py`(1)・`loop_cli.py`(1) 等で、
  本カード3変更ファイルは含まない。`ruff format --check src tests` = 未整形8ファイル
  （`paperloop.py`・`paperloop_verify.py`・`services/jp_value_quality.py`・
  `services/jp_value_quality_backtest.py`・`test_jp_value_quality_backtest_units.py`・
  `test_paperloop.py`・`test_paperloop_v12.py`・`test_screening_cli_dates.py`）で、
  こちらも本カード3変更ファイルは含まない（426ファイル整形済みに含まれる）。
- 参考: `make test` 由来の素のpytest起動（binary起動・PYTHONPATH=srcのみ）では
  `from tests.test_paperloop import …` 2ファイル（test_cusum_watch / test_paperloop_v12、
  共に他カードのpaperloop系債務ファイル）が collection error になる既存挙動があり、
  `python -m pytest`（リポジトリルートが sys.path に入る起動・VPSでの全実行と同一）で解消される。
  本カードの変更面とは無関係の起動方法起因。

## 7. 未確認範囲・分離事項

- VPS では小試験のみ（カード指示）。全量 `make verify` は上記 TSUKUMO クローンで実施。
- リポジトリ全体の `ruff check src tests` には先行カード由来の既存債務
  （paperloop*・jp_value_quality*・test_screening_cli_dates.py）があり、本カード3ファイル外。
  make lint が全体で失敗する場合はこの既存債務であり本カード変更面ではない。
- 実運用のチャートシート混在XLSX等の実物での確認は未実施（fixture実証のみ・先行カードと同じ限界）。
- `extension_of` 以外のURL取り扱い（discovery/serviceの重複排除キー・保存URL・provenance）は
  本カードでは一切変更していない（IRのB8重複排除はURLレベルで別機能）。
