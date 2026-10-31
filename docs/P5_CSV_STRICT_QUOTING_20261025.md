# CSV 引用符の厳格解析と fail-closed 契約 (P5 / t_6390a355)

観測日時: 2026-10-25 11:03 JST。実測ホストは VPS（小試験のみ、カード指示どおり）。
本書は本カードの変更・受入の実測記録である。製品コードの変更は 1 引数のみ。

## 1. 欠陥

`extract_csv_tables` は復号済みテキストを `csv.reader(io.StringIO(text))`（既定＝寛容ダイアレクト）に
渡していた。そのため引用符が構造的に壊れた文書は「吸収」され、後続行が同一セルへ飲み込まれ、
閉じ引用符直後の余剰文字が直前のトークンへ連結されたうえで `parsed=True` / `parse_note=None` を返した。
破損文書が正常文書と区別できず、KPI 抽出が捏造されたセルの上で走る（AGENTS invariant 2/9 違反）。

### 旧実装の実出力（本カードで実測。RED 実行時の pytest 失敗メッセージから採取）

| 入力 | 旧 `_source_rows` | 旧 `parsed` / `parse_note` |
| --- | --- | --- |
| `metric,value` / `"revenue,123` / `profit,456` | `[["metric","value"],["revenue,123\nprofit,456\n"]]` | `True` / `None` |
| `metric,value` / `revenue,123` / `"profit,456` / `cost,789` | `[["metric","value"],["revenue","123"],["profit,456\ncost,789\n"]]` | `True` / `None` |
| `metric,value` / `revenue,123` / `profit,456` / `"cost,789` | `[["metric","value"],["revenue","123"],["profit","456"],["cost,789\n"]]` | `True` / `None` |
| `metric,value` / `"revenue"x,123` / `profit,456` | `[["metric","value"],["revenuex","123"],["profit","456"]]` | `True` / `None` |
| `metric,value` / `revenue,123` / `"profit"x,456` / `cost,789` | `[["metric","value"],["revenue","123"],["profitx","456"],["cost","789"]]` | `True` / `None` |
| `metric,value` / `revenue,123` / `profit,456` / `"cost"x,789` | `[["metric","value"],["revenue","123"],["profit","456"],["costx","789"]]` | `True` / `None` |

未閉鎖引用符は後続全体を 1 セルへ吸収し（1〜3 行目）、閉じ引用符直後の余剰文字は直前トークンへ
連結される（`"revenue"x` → `revenuex`、4〜6 行目）。カード本文が記録した CTO 実測（`123` 改行
`profit,456` の吸収と `123junk` への変形）と同じ欠陥クラスであり、本表はその位置違い（先頭/途中/末尾）
の実測である。

同一入力に対する stdlib `csv.reader(strict=True)` は 6 例すべて `csv.Error`。既存 `except csv.Error`
経路は存在するのに、既定ダイアレクトが例外を出さないため到達していなかった。

## 2. 変更

`src/yowayowa/acquisition/documents.py` — `extract_csv_tables` の既存呼び出しに `strict=True` を明示。

```
-        rows = list(csv.reader(io.StringIO(text)))
+        rows = list(csv.reader(io.StringIO(text), strict=True))
```

- 既存の `except csv.Error` 分岐をそのまま再利用する（新分岐・新 validator・新 dialect・sniffer・
  独自引用符規則は作らない）。文書全体が未解析（`format="csv"` / `parsed=False` / `tables=[]` /
  `parse_note="csv parse failed: Error"`）となり、途中まで読めた表は返さない。
- `parse_note` は既存の `f"csv parse failed: {type(exc).__name__}"` のまま。`csv.Error` の型名は
  `Error` なので文言は固定で、例外メッセージ・生文・URL・ファイル名・入力値・秘密 marker を載せない。
- 全文を `csv.reader` で解析してから既存の行/列 cap を適用する順序は不変。cap 外の構造エラーでも
  文書全体が未解析になる。正常な cap 注記は先行 t_3501871f の契約どおり。
- `extract_document` の filename/URL dispatch、IR record/timeline の既存 `parsed`/`parse_note` 伝播、
  未解析時の KPI=[] は既存経路をそのまま使う（サービス本体は未編集）。XLSX/PDF/HTML は不変。

## 3. ハッシュ

| 対象 | SHA256 |
| --- | --- |
| `documents.py` 変更前（＝先行 t_ef599ebf 承認値） | `31f9f073cfe97362e0aa61729931b88fb76f952a9ef84174561a03b89fd26d2e` |
| `documents.py` 変更後 | `d83988a0bd6cf63fe96e4a8e16035b9977ac62ac5050c1213582dc24894575dc` |
| `tests/test_csv_strict_quoting.py`（新規・520 行） | `3bd326ce98f66f39063ec8bbd09701a8d16a85df4cdee97c9850452a39b7ad7a` |

スコープ証明（機械再構成・本カードで実測）: 変更後の `documents.py` から `strict=True` の 1 箇所だけを
旧形へ戻した再構成ファイルの sha256 が `31f9f073…` に完全一致した。したがって本カードの追加は
`strict=True` の 1 引数のみで、先行 6 カード（同名 header / 共有文字列 fail-closed / 部分抽出 note /
URL dispatch / CSV-XLSX size 上限 note / PDF 表数 note）の未コミット差分はすべて保持されている。
再適用後の sha256 は `d83988a0…` に復帰したことを確認した。

## 4. RED → GREEN（実測）

前提: 変更前 `documents.py` = `31f9f073…`（先行承認値）。専用テストは socket を遮断する autouse
fixture を持ち、ネットワーク到達なし。

| 段階 | コマンド | 結果 |
| --- | --- | --- |
| RED | `.venv/bin/python -m pytest tests/test_csv_strict_quoting.py --tb=line -rf` | `19 failed, 31 passed in 0.72s`（rc=1） |
| GREEN | `.venv/bin/python -m pytest tests/test_csv_strict_quoting.py` | `50 passed in 0.76s`（rc=0） |

RED の失敗 19 件はすべて「壊れた引用符が fail-closed しない」ことの検出（未閉鎖 3 位置・
閉じ引用符直後 junk 3 位置・その他構造エラー 5 種・可読前置行・秘密非露出・決定性・cap 越え 3 件・
dispatch・IR 伝播）。失敗メッセージは `assert True is False`（`parsed`）と
`assert None == 'csv parse failed: Error'`（`parse_note`）で、tolerant 経路が実際に正常扱いしていたことの
直接証拠である。GREEN は変更後のみ。

## 5. 受理される文書が旧契約のままであることの証明

専用テスト内 `_legacy_extract_csv` は変更前実装の忠実な複製（既定＝寛容 reader）で、oracle として
全フィールド deep-equal 比較に使う。以下の 19 fixture で `extract_csv_tables(content)` ==
`_legacy_extract_csv(content)`、かつ stdlib strict が受理することを独立に確認している。

- 引用符内 LF / 引用符内 CRLF / 引用符内カンマ / 二重引用符エスケープ / 日本語 / 空セル / 引用符付き空文字
- 非引用フィールド内の裸引用符 `12"3`（`csv.Dialect.strict` はこれを拒否しない。旧版と同値）
- header-only / 末尾改行のみ / 空 / 空白のみ / 途中空行
- 短い行（余剰列は空文字）/ 余剰セル / 同名 header / 空 header + 同名 header
- UTF-8 BOM / cp932 / 復号失敗（`csv decode failed (neither utf-8-sig nor cp932); not parsed`）

比較対象は `format` / `parsed` / `parse_note` / `parser_version` / `text` / `text_chars` と、
`tables[0]` の `headers` / `rows` / `_source_rows` の値・順序。引用符内改行は 1 セルに留まること
（`{"a": "x\ny", "b": "2"}`）、裸引用符が `12"3` のまま受理されることも個別に固定した。
失敗入力は修復して成功させていない（strict が拒否する入力は拒否のまま固定）。

## 6. cap と strict の相互作用

局所 cap fixture（`_XLSX_MAX_ROWS=3` / `_XLSX_MAX_COLUMNS=2`）で実測。

- 行 cap: cap-1(2 行)/cap(3 行) は note なし、cap+1(4 行) は `csv rows truncated at 3`。旧実装と deep-equal。
- 列 cap: cap-1(1 列)/cap(2 列) は note なし、cap+1(3 列) は `csv columns truncated at 2`。旧実装と deep-equal。
- cap より後ろ（5 行目以降）の構造エラー → 文書全体 `parsed=False` / `tables=[]`。
- cap より後ろの構造エラーで、cap 内の前置行が全部正常 → それでも文書全体が未解析。
- cap 境界上の行（cap 内最終行）の構造エラー → 文書全体が未解析。

つまり「cap の内側だけ見て部分的に成功させる」経路は存在しない。解析は全文、cap はその後。

## 7. dispatch と IR 伝播

- `extract_document(filename="x.csv")` と `extract_document(url="https://…/x.csv")` の双方で
  `format="csv"` / `parsed=False` / `parse_note="csv parse failed: Error"` / `tables=[]`。
- 受理される CSV は `extract_document` 経路でも旧実装と deep-equal、`rows == [{"metric": "revenue", "value": "123"}]`。
- `IrMonitorService`（テスト内 stub transport、実 socket なし）で doc record が
  `parsed=False` / `parse_note="csv parse failed: Error"` / `kpis == []` / `notes` に同 note を保持。
  受理される CSV では `parsed=True` / `parse_note=None` のまま。IR サービス本体は未編集。

## 8. 近傍回帰（実測・host/command/rc）

| コマンド | 結果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_ir_acquisition.py tests/test_acquisition_documents_units.py tests/test_csv_strict_quoting.py` | `165 passed, 1 xfailed in 3.88s`（rc=0） |
| `.venv/bin/python -m pytest tests/test_document_duplicate_headers.py tests/test_document_partial_extraction.py tests/test_document_size_cap_notes.py tests/test_document_url_fragment_dispatch.py tests/test_xlsx_shared_string_failclosed.py tests/test_portfolio_csv.py` | `188 passed in 14.35s`（rc=0） |
| `.venv/bin/ruff check --no-cache src/yowayowa/acquisition/documents.py tests/test_csv_strict_quoting.py` | rc=0 `All checks passed!` |
| `.venv/bin/ruff format --check src/yowayowa/acquisition/documents.py tests/test_csv_strict_quoting.py` | rc=0 `2 files already formatted` |
| `.venv/bin/mypy src/yowayowa/acquisition/documents.py tests/test_csv_strict_quoting.py` | rc=0 `Success: no issues found in 2 source files` |
| `make typecheck`（`mypy src/yowayowa` 全体） | rc=0 `Success: no issues found in 243 source files` |

既存 xfail は解除していない。残存する xfail 1 件は
`tests/test_acquisition_documents_units.py::test_pdf_column_gap_tolerance_is_applied`
（PDF の列許容差 `_COLUMN_TOLERANCE` 未使用という別欠陥）で、本カードの対象外・不変。

### baseline / candidate の分離（`make lint` 全体）

`make lint`（`ruff check src tests`）は全体で **rc=2 / 36 errors** で失敗する。違反の分布は
`tests/test_paperloop_v12.py`(16) / `src/yowayowa/paperloop_panel.py`(11) /
`tests/test_paperloop.py`(4) / `tests/test_jp_value_quality_backtest_units.py`(3) /
`src/yowayowa/paperloop.py`(1) / `src/yowayowa/loop_cli.py`(1)。
本カードの変更ファイル（`documents.py` / 新規 test）に違反は **0 件**で、違反ファイル集合と
本カードの diff は交わらない。したがって 36 件は本変更に起因しない既存（他カードの未コミット作業）
baseline であり、candidate で増減していない。`documents.py` は変更前後とも
ruff check / format / mypy の個別実行で rc=0。

## 9. strict が検出する範囲と、検出しない境界

- 検出する（fail-closed）: 未閉鎖引用符（ファイル先頭・途中・末尾のいずれでも）、閉じ引用符直後の
  余剰文字、引用符付きフィールド内の引用符（`"12"3`）、引用符間の余剰文字（`"1"junk"`）。
  いずれも `csv.Dialect.strict` の仕様どおり。
- 検出しない（従来どおり受理）: 非引用フィールド内の裸引用符 `12"3`。これは Python 公式の
  `csv.Dialect.strict` の定義（引用符付きフィールドの終端後に文字がある場合と、引用符付き
  フィールド内の不正文字を対象）に照らして正しい挙動であり、本カードは裸引用符まで禁止しない
  （カード本文の指示どおり）。
- 検出しない: 意味的に誤った値（数値でない金額、単位違い等）。それは本カードの範囲外で、
  既存の KPI 抽出・単位判定の契約が担う。

## 10. 未検証範囲（偽らない）

- `make verify` 全量（`ruff check src tests` 全体の成功 + 全 pytest + `openapi` / `openapi-check`）は
  **本カードで実施していない**。カード指示により VPS は小試験のみ、全量は TSUKUMO 既存環境か同等 CI で
  実施し、既存失敗を baseline/candidate で分離する担当。本カードが実施したのは上表の範囲である。
- 実 IR 文書・本番デプロイ・実ブラウザ E2E は対象外（未実施）。本書の IR 検証はプロセス内 stub transport。
- 本カードは commit / push を行っていない。作業ツリーには先行 6 カードぶんの未コミット差分が残っており、
  `src/yowayowa/acquisition/documents.py` は 7 カード目の未コミット差分を積んだ状態にある
  （hotspot 継続。将来の documents.py 作業前にコミット／統合の分解を推奨）。
- 参考 `units/INDEX` は未編集。既存 xfail は解除していない。
