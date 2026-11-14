# XLSX シートと worksheet 実体の relationship 対応 (P5 / t_275fc714)

観測日時: 2026-10-25 11:39 JST。実測ホストは VPS（カード指示により小試験のみ）。
本書は本カードの変更・受入の実測記録である。新データ源・新依存・新基盤は追加していない。

## 1. 欠陥

`extract_xlsx_tables` は `xl/workbook.xml` から `<sheet>` の**名前だけ**を取り出し（`_sheet_names`）、
列挙順をそのまま `xl/worksheets/sheet<N>.xml` の N に使っていた。workbook の `r:id` と
`xl/_rels/workbook.xml.rels` の対応は無視されていた。

帰結は 2 つ。

1. **年度の取り違え（無言の誤り）**: 実体の並びが表示順と違う workbook では、`Current` に前期の値、
   `Prior` に当期の値が入る。`parsed=True` / `parse_note=None` のままなので、破損としても現れない。
2. **実体があるのに未解析**: パート名が `sheetN.xml` でない workbook は、worksheet が存在しても
   `parsed=False` / `worksheet parts missing` になる。

いずれも AGENTS invariants 2（欠測を 0 にしない・誤解を招く算術は fail closed）と 9（成功は正しさの
証拠ではない）に反する。

### 旧実装の実出力（CTO 原物 fixture を本カードで再実行）

入力は CTO が旧抽出器に対して作製した再現物 `scratch/refill16/reordered.xlsx` と
`nonsequential.xlsx`（バイト不変・読み取りのみ）。

| 入力 | 旧 `parsed` / `parse_note` | 旧の値 | 正しい値 |
| --- | --- | --- | --- |
| `reordered.xlsx`（`Current`→`sheet2.xml`=200, `Prior`→`sheet1.xml`=100） | `True` / `None` | `Current`=100, `Prior`=200（入れ替わり） | `Current`=200, `Prior`=100 |
| `nonsequential.xlsx`（`Current`→`worksheets/financial.xml`=300） | `False` / `xlsx contained no readable sheets; xlsx worksheet parts missing: 1` | 表なし | `Current`=300 |

旧値は CTO の `xlsx_probe.json` と一致し、本カードでも変更前実装（§4 の再構成品）で
`49 failed / 5 passed` として同じ欠陥が再現した。

## 2. 変更（`src/yowayowa/acquisition/documents.py` のみ）

1. `_worksheet_sheets(archive)` を追加。`<sheet>` の表示順・`name`・`r:id` を読み、
   `xl/_rels/workbook.xml.rels` の `Id` に結合して `(name, ZIP part)` を返す。`sheetId` も表示順も
   パート名の推定には使わない。
2. `_relationship_id` / `_has_workbook_relationships_part` / `_workbook_relationships` /
   `_resolve_worksheet_target` を追加（stdlib の `zipfile` / `ElementTree` / `posixpath` のみ）。
3. `extract_xlsx_tables` は上記で得た part を `_read_sheet_rows(..., part=...)` へ渡す。
4. `_read_sheet_rows` は keyword-only `part` を受け取り、未指定なら従来どおり
   `xl/worksheets/sheet<index>.xml` を読む（既存の直呼び互換）。
5. chartsheet は解析・KPI 化せず件数だけ数え、既存 note の**末尾**へ 1 回だけ
   `xlsx non-worksheet sheets skipped: N` を追記。

### 判定規則（fail closed）

- **legacy 経路**: 全 `<sheet>` に `r:id` が無く、rels パートも無い最小入力のみ、従来の
  `sheetN.xml` 位置読みを維持する。
- **relationship モード**: `r:id` か rels のどちらか一方でも存在すれば relationship モード。
  欠落を旧推測で埋めない。次のいずれかで文書全体を
  `parsed=False` / `tables=[]` / `parse_note='xlsx worksheet relationship invalid'` にする。
  未解決 Id、`r:id` 欠落（空文字含む）、重複 Id（未参照の rel も含む）、空 Target、
  `TargetMode=External`、未知の relationship type（worksheet / chartsheet 以外）、不正 Target。
- **Target 解決**: ZIP 内パート名のみ。`worksheets/foo.xml` と `/xl/worksheets/foo.xml` を
  `xl/workbook.xml` 基準で posixpath 正規化し、`xl/worksheets/` 配下の `.xml` だけを許可。
  URI scheme / authority / query / fragment / バックスラッシュ / NUL / 外へ出る `..` は拒否。
  ホスト FS へのアクセスも ZIP 展開もしない。
- **解決先パートの欠落**: 従来の `xlsx worksheet parts missing: N` 件数 note と部分抽出契約を維持。
  誤った `sheetN.xml` で穴埋めしない。
- **XML 不正**: 既存の `xlsx parse failed: <Type>` 契約を維持。

### 順序と不変条件

note の順序は 既存 missing-parts → rows truncation → columns truncation → 新 skip note（末尾 1 回）。
行・列 cap、非空判定、共有文字列 fail-closed、`_dedupe`（同名 header）、`parsed` の意味、
`parser_version`（`doc-v1`）、表の順序は不変。CSV / PDF / HTML / IR の KPI・単位・通貨・期間の
各アルゴリズムには触れていない。

## 3. hash（実測）

| 対象 | sha256 |
| --- | --- |
| 変更前 `documents.py` | `d83988a0bd6cf63fe96e4a8e16035b9977ac62ac5050c1213582dc24894575dc` |
| 変更後 `documents.py` | `5c1873d4795bcb5504a239ef81d19c0ed3ba1b415c823526eaa350a93a9db17d` |
| 新規 test | `tests/test_xlsx_worksheet_relationships.py` `2ee1c2a14dfad3b111e465356bc1dc1615214b052cc27fd40f3732e44fb5ef7f` |
| CTO 原物 `reordered.xlsx`（入力） | `3302bcb3341e603bd54f2a7683eceb310b0d2a7767db84e86ad0f26c0c151075` |
| CTO 原物 `nonsequential.xlsx`（入力） | `a9fe264cf3520b2788f18649265fdc3544915279be47df5f177cace658f8efab` |

入力 2 件は読み取り前後で hash 不変（probe が assert、書込みなし）。

## 4. スコープ証明（機械的・実測）

変更後の `documents.py` から本カードの追加だけを旧形へ戻した再構成品を作り、その sha256 が
変更前と**バイト一致**することを確認した。

- 再構成品 sha256 = `d83988a0…4575dc`（＝変更前 `documents.py` と完全一致、743 行）
- 変更後は 929 行。差は 9 ハンクで、すべて本カードの追加領域（imports / 定数 / extract の
  docstring と sheet 対応 / skip note / helper 群 / `_read_sheet_rows` の 3 箇所）。
- 先行カード（同名 header・共有文字列 fail-closed・部分抽出 note・URL dispatch・
  CSV/XLSX 上限 note・PDF 表数 note・CSV strict quoting）の差分は 1 バイトも変わっていない。

## 5. 試験（実測値）

新しい専用 test は 54 件。全 fixture は test 内で組む小さな ZIP/XML で、socket はモジュール全体で
遮断した（`socket.connect` / `connect_ex` を失敗させる autouse fixture）。

| 実行 | 結果 |
| --- | --- |
| RED（変更前 `d83988a0` に差し替え、復元後 hash 一致を確認） | **49 failed / 5 passed**（rc=1） |
| GREEN（変更後 `5c1873d4`） | **54 passed**（rc=0, junit tests=54 failures=0 errors=0 skipped=0） |
| 近傍 11 ファイル（xlsx/shared strings/部分抽出/同名 header/上限 note/URL dispatch/units/IR/CSV strict/PDF） | junit **tests=444 failures=0 errors=0 skipped=1**（rc=0。skip は既存 xfail） |
| 広域 `-k "acquisition or document or xlsx or ir_ or csv or pdf"` | junit **tests=621 failures=0 errors=0 skipped=1**（rc=0） |
| CTO 原物 fixture の再実行 probe（4 件） | **4 passed**（`Current`=200 / `Prior`=100 / `financial.xml`=300、入力 hash 不変） |
| `ruff check`（対象 2 ファイル） | rc=0 All checks passed |
| `ruff format --check`（対象 2 ファイル） | rc=0 2 files already formatted |
| `mypy`（対象 2 ファイル） | rc=0 no issues |
| `make typecheck`（src 全体） | rc=0 Success: no issues found in 243 source files |
| `make lint`（全体） | rc=2・36 errors。全件が paperloop 系 5 ファイル＋`src/yowayowa/loop_cli.py` で、**本カード変更ファイルの違反は 0**（baseline 分離） |

RED の 5 件は legacy 互換・relationship と legacy の deep-equal・workbook 欠落・全パート欠落・
workbook XML 不正で、いずれも変更前後で同じ結果になるべき契約（＝新実装で退行していない確認）。

### 専用 test が固定した内容

- 表示順の逆転（`Current`=200 / `Prior`=100）、`sheetId` と実体の不一致、任意ファイル名
  （`financial.xml`）、相対・package-root・`./`・`../xl/`・サブディレクトリの Target。
- chartsheet 混在（worksheet の名前と値がずれない・skip note は 1 回だけ）、2 枚混在で件数 2、
  全 chartsheet で既存 `xlsx contained no readable sheets` の理由を保持。
- legacy 最小入力の不変、relationship 解決と legacy の**全フィールド deep-equal**、
  rels だけ存在する場合の拒否、`r:id` 混在の拒否。
- 未解決 Id / 重複 Id（未参照含む）/ 空 `r:id` / rels 不在 / External / 未知 type（空 type 含む）/
  不正 Target 14 種で文書全体 fail-closed、固定 note に入力の生値・URL・シート名・例外文が出ない。
- XML 不正は `xlsx parse failed: ParseError`、NUL は XML が運べないため同契約（resolver 単体では
  NUL を拒否することを直接検証）。
- 解決先パート欠落は件数 note＋部分抽出、誤った `sheetN.xml` で穴埋めしないこと。
- `_read_sheet_rows` の位置引数互換と `part` 指定、同一バイト再読の一致、`parser_version`。
- `extract_document` の拡張子 / content-type 両 dispatch、IR の実プロセス内 stub 経路で
  relationship 解決後の**当期値**が KPI に入ること、失敗時は `parsed=False` / 固定 note / KPI 0 件が
  timeline payload まで伝播すること。

## 6. 未確認範囲（偽らない）

- `make verify` 全量、openapi-check、実 IR 文書、実ブラウザ E2E、本番デプロイの確認は未実施
  （カード指示により VPS は小試験のみ、全量は TSUKUMO 既存環境または同等 CI へ委譲）。
- 実際の Excel が出力する全変種（strict 形式の relationship namespace、dialogsheet / macrosheet
  を含む実ファイル、VML・外部リンク入り）は未取得。strict namespace と worksheet/chartsheet 以外の
  type は仕様どおり「未知」として fail closed に倒れる（安全側）が、実ファイルでの確認はしていない。
- chartsheet 以外の非 worksheet シート（dialogsheet 等）を skip ではなく拒否する判断は、
  カード本文の「未知 type は拒否」に従ったもの。実務上必要になれば別カードで扱う。
- 既存の collection error（`tests/test_cusum_watch.py` / `tests/test_paperloop_v12.py` が
  `from tests.test_paperloop import ...` で `No module named 'tests'`）は本カードと無関係な
  ベースライン事象で、当該 2 ファイルを除外して広域試験を実測した。

## 7. 作業状態

- commit / push なし。HEAD は `a05d248` のまま。変更は未コミットのまま既存 dirty ツリーに載る。
- 変更ファイルは `src/yowayowa/acquisition/documents.py`、`tests/test_xlsx_worksheet_relationships.py`、
  本書の 3 点のみ。`docs/INDEX_ja.md` と参照 units は未編集。
- hotspot 継続: `src/yowayowa/acquisition/documents.py` は 8 カード目の未コミット差分を積んだ状態。
  次回このファイルを触る前にコミット／統合の分解を推奨。
