# P5: PDF表数上限到達の既存parse_note明示 — 証跡 (t_ef599ebf)

日付: 2026-10-25
実装担当: worker-agy
検収担当予定: audit-cld
対象: `src/yowayowa/acquisition/documents.py`（2分岐のみ変更）
専用テスト: `tests/test_pdf_table_limit_note.py`（新設）

---

## 1. 要旨

PDFの表抽出（`extract_pdf_tables`）において、既存の表数上限 `_MAX_TABLES_PER_PDF = 200` に到達した際、
従来は無注記（`parse_note=None`）で走査を打ち切っていたため、200表で打ち切られた部分抽出が完全な抽出と区別できなかった。
本変更は、既存 `len(tables) >= _MAX_TABLES_PER_PDF` の `break` 分岐において `diagnostics["table_limit_reached"] = True` を additive に記録し、
`extract_pdf_document` において固定文言 `f"pdf table limit reached at {_MAX_TABLES_PER_PDF}"` を `parse_note` へ追記する。

- 文言: `'pdf table limit reached at 200'`（数値は実行時 `_MAX_TABLES_PER_PDF` から動的生成）
- 注記順序: 既存 text capped 注記 → 既存 incomplete 注記 → 今回 table limit 注記
- 上限未満（199表以下）: note も含め従来と完全同値（新注記なし）
- ちょうど上限（200表）および上限超過入力（201表以上）: 200表で停止し注記を付与
- 余分な走査・先読みなし: 上限到達時点で break し、後続ページの `next()` や例外評価は行わない

---

## 2. 境界定義と振る舞い（CTO確定設計準拠）

| 条件 | 表数 | ページ消費数 | diagnostics | parse_note |
|---|---|---|---|---|
| cap - 1 (199表 / cap=200) | 199 | 199 | `{}` | `None` (または text capped のみ) |
| exact cap (200表 / cap=200) | 200 | 200 | `{'table_limit_reached': True}` | `pdf table limit reached at 200` |
| cap + 1 (201表入力 / cap=200) | 200 | 200 | `{'table_limit_reached': True}` | `pdf table limit reached at 200` |
| 局所 cap - 1 (1表 / cap=2) | 1 | 1 | `{}` | `None` |
| 局所 exact (2表 / cap=2) | 2 | 2 | `{'table_limit_reached': True}` | `pdf table limit reached at 2` |
| 局所 cap + 1 (3表入力 / cap=2) | 2 | 2 | `{'table_limit_reached': True}` | `pdf table limit reached at 2` |
| 0表 (空ページ) | 0 | 全走査 | `{}` | `None` |
| 空ページ・header-only混在 | 採用表数 | 表ページのみカウント | 採用表数 >= cap で真 | 採用表数 >= cap で注記 |
| cap 到達直後に raise する generator | cap 表 | cap ページ | `{'table_limit_reached': True}` | limit 注記（例外中断に偽らない） |
| cap 到達前に raise する generator | 取得済表 | 失敗ページまで | `{'table_traversal_incomplete': True}` | `pdf table extraction incomplete` |

---

## 3. コード差分と hash（VPS 実測）

### コード hash
| ファイル | 状態 | sha256 |
|---|---|---|
| `src/yowayowa/acquisition/documents.py` | 変更前（t_3501871f 承認済状態） | `c736ab9118c6addfbfc870e6749f4f09d78d6c37f5fc7db83ff207e9abc81df3` |
| `src/yowayowa/acquisition/documents.py` | 変更後（本タスク実装後） | `31f9f073cfe97362e0aa61729931b88fb76f952a9ef84174561a03b89fd26d2e` |
| `tests/test_pdf_table_limit_note.py` | 新設（独立テスト） | `c938d8ee09ddc03c6866fc3eaba0fa889b9d0f897da9710678fc3246d879f5f4` |

### 実装差分（`src/yowayowa/acquisition/documents.py` の2分岐のみ）
```diff
@@ -260,6 +260,8 @@ def extract_pdf_tables(
             tables.append(...)
             if len(tables) >= _MAX_TABLES_PER_PDF:
+                if diagnostics is not None:
+                    diagnostics["table_limit_reached"] = True
                 break
     except Exception:
@@ -298,4 +300,8 @@ def extract_pdf_document(content: bytes) -> ExtractedDocument:
     if diagnostics.get("table_traversal_incomplete"):
         document.parse_note = _append_parse_note(document.parse_note, _PDF_INCOMPLETE_NOTE)
+    if diagnostics.get("table_limit_reached"):
+        document.parse_note = _append_parse_note(
+            document.parse_note, f"pdf table limit reached at {_MAX_TABLES_PER_PDF}"
+        )
     return document
```

---

## 4. RED / GREEN 実測検証

### 1) RED 実測（旧実装に対する新設テスト実行）
- コマンド: `.venv/bin/pytest tests/test_pdf_table_limit_note.py`
- 結果: **rc=1** (14 failed, 10 passed in 0.98s)
- 内訳:
  - cap exact / cap + 1 / generator raises after cap / diagnostics 記録 / note 合成 / IR伝播 の計14ケースが `AssertionError: assert None == 'pdf table limit reached at ...'` で正しく RED を実証。
  - cap 未満 / 0表 / 欠落 part などの既存契約維持10ケースは pass。

### 2) GREEN 実測（新実装に対する新設テスト実行）
- コマンド: `.venv/bin/pytest -v tests/test_pdf_table_limit_note.py`
- 結果: **rc=0** (24 passed in 0.77s)
- 検証項目全 24 件:
  1. `TestLocalCapTwo::test_cap_minus_one_has_no_limit_note` PASSED
  2. `TestLocalCapTwo::test_cap_exact_has_limit_reached_note` PASSED
  3. `TestLocalCapTwo::test_cap_plus_one_stops_at_cap_and_has_limit_reached_note` PASSED
  4. `TestLocalCapTwo::test_zero_tables_has_no_limit_note` PASSED
  5. `TestLocalCapTwo::test_mixed_empty_and_header_only_pages_do_not_count_as_tables` PASSED
  6. `TestLocalCapTwo::test_table_fields_deep_equal_under_and_at_cap` PASSED
  7. `TestLocalCapTwo::test_reexecution_is_deterministic` PASSED
  8. `TestDefaultCapTwoHundred::test_default_cap_is_two_hundred` PASSED
  9. `TestDefaultCapTwoHundred::test_cap_minus_one_199_tables_no_note` PASSED
  10. `TestDefaultCapTwoHundred::test_cap_exact_200_tables_has_note` PASSED
  11. `TestDefaultCapTwoHundred::test_cap_plus_one_201_tables_stops_at_200_with_note` PASSED
  12. `TestPageConsumption::test_generator_raises_after_cap_proves_no_extra_next_or_double_walk` PASSED
  13. `TestPageConsumption::test_default_cap_generator_raises_at_201_consumed_exactly_200` PASSED
  14. `TestDirectExtractPdfTables::test_direct_call_without_diagnostics_omitted` PASSED
  15. `TestDirectExtractPdfTables::test_direct_call_with_diagnostics_dict_records_limit_reached` PASSED
  16. `TestDirectExtractPdfTables::test_direct_call_under_cap_does_not_set_table_limit_reached` PASSED
  17. `TestNoteCoexistence::test_text_cap_and_table_limit_coexistence_and_order` PASSED
  18. `TestNoteCoexistence::test_exception_before_cap_gives_incomplete_note_only` PASSED
  19. `TestNoteCoexistence::test_text_capped_plus_incomplete_plus_table_limit_order` PASSED
  20. `TestContractsAndSafety::test_image_pdf_parsed_false_stays_note_free` PASSED
  21. `TestContractsAndSafety::test_missing_pdfminer_extra_fails_cleanly` PASSED
  22. `TestContractsAndSafety::test_secret_markers_not_exposed` PASSED
  23. `TestServiceDispatchAndIrMonitor::test_extract_document_dispatches_pdf_with_limit_note` PASSED
  24. `TestServiceDispatchAndIrMonitor::test_ir_monitor_preserves_pdf_table_limit_parse_note` PASSED

---

## 5. 近傍テスト・静的検査実測（host: ubuntu）

| 検査種別 | 実行コマンド | 結果 / rc | 備考 |
|---|---|---|---|
| 専用テスト | `.venv/bin/pytest tests/test_pdf_table_limit_note.py` | **24 passed** (rc=0) | 所要 0.72s |
| 近傍回帰スイート (7ファイル) | `.venv/bin/pytest tests/test_pdf_table_limit_note.py tests/test_document_partial_extraction.py tests/test_document_url_fragment_dispatch.py tests/test_document_size_cap_notes.py tests/test_document_duplicate_headers.py tests/test_xlsx_shared_string_failclosed.py tests/test_ir_acquisition.py` | **256 passed** (rc=0) | 所要 3.21s |
| 参考 units | `.venv/bin/pytest tests/test_acquisition_documents_units.py` | **69 passed, 1 xfailed** (rc=0) | 既存xfail（PDF列幅）不変・所要 1.14s |
| コード静的解析 | `.venv/bin/ruff check src/yowayowa/acquisition/documents.py tests/test_pdf_table_limit_note.py` | **All checks passed!** (rc=0) | 0 issues |
| フォーマット検証 | `.venv/bin/ruff format --check src/yowayowa/acquisition/documents.py tests/test_pdf_table_limit_note.py` | **2 files already formatted** (rc=0) | 差分なし |
| 型検査 (対象2ファイル) | `.venv/bin/mypy src/yowayowa/acquisition/documents.py tests/test_pdf_table_limit_note.py` | **Success: no issues found in 2 source files** (rc=0) | 0 errors |
| 型検査 (パッケージ全体) | `.venv/bin/mypy src/yowayowa` | **Success: no issues found in 243 source files** (rc=0) | 既存型検査完全通過 |

---

## 6. 不変性の実証と未検証範囲

### 不変性の実証
- 戻り値の型（`ExtractedDocument` および `extract_pdf_tables` の `list[dict[str, Any]]`）は不変。
- 採用された表の全フィールド（`headers`, `header_xs`, `rows`, `row_xs`, `cells`）は deep-equal。
- 抽出順序、座標、列幅、`parsed`、`parser_version`、`text`、`text_chars` は不変。
- 表のないページ、ヘッダのみのページは表数にカウントされず、上限未満の通常PDFには新規noteは付加されない。
- 上限到達後のページは消費されず、余分な `next()` や例外中断への偽装は発生しない。

### 全量・本番未検証範囲
- VPS 上の規律に従い、本番環境への接続・実運用データの書き換え・外部ネットワーク接続は一切行っていない（テストはすべて socket 遮断環境で実行）。
- `make verify` 全量（カバレッジ・E2E等）は VPS 負荷防止規律のため TSUKUMO 環境 / CI に委ね、VPS 上では近傍 256 件および静的検査（ruff/mypy）のパスを確認している。
