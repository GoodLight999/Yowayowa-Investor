# P5: CSV/XLSX サイズ上限切り詰めの parse_note 明示 — 証跡 (t_3501871f)

日付: 2026-10-24（初版）/ 2026-10-25（第2版・検収指摘 R1/R2 への限定補正）
実装: worker-flash / 検収: audit-sol（第1回独立検収 = changes_requested。本第2版の再提出先も同。
初版に記載のあった audit-cld は Molt の reviewer 交代指示により置き換え済み）
対象: `src/yowayowa/acquisition/documents.py` の CSV/XLSX 上限診断とローカル note 合成のみ。
第2版で製品コードは不変（§5・§10）。

## 1. 要旨

CSV/XLSX の行・列上限（既定 10000 行 / 256 列）は、入力の行や列を実際に捨てて
いながら `parse_note=None` で返り、切り詰められた表が完全な表と区別できなかった
（units strict-xfail `test_size_truncation_is_reported_in_the_parse_note` が pin する
既知欠陥・CTO が 2026-10-24 原物再現済）。本変更は、実際に切り詰めが起きたときだけ
固定文言を `parse_note` に加える。

- CSV: `csv rows truncated at 10000` / `csv columns truncated at 256`
- XLSX: `xlsx rows truncated at 10000` / `xlsx columns truncated at 256`
- 数値は実行時上限定数から生成。生データ/URL/シート名/例外文は載せない。
- 1文書で各種 note 1回、行→列の固定順。既存 note（missing parts 等）を先に保ち
  `'; '` で追記（既存 `_append_parse_note` を再利用）。
- 上限未満/ちょうど上限は parse_note を含め従来と完全同値。

## 2. 境界定義（CTO 確定設計どおり）

- CSV 行上限: csv.reader の生行数（header/空行を含む）。`len(raw_rows) > row_cap` で
  行切り詰め。列切り詰めは保持する先頭 row_cap 生行のうち幅 > col_cap があるとき。
  末尾空セルも raw 幅に数える。cap 後の空行除去と空表 parsed=False 契約は不変。
- XLSX 行上限: シート毎の読める非空行数（header 含む、従来どおり）。ちょうど上限で
  終わる場合・後続が空 row だけの場合は note 無し。上限後に非空 payload を持つ row
  があるかを XML の生 `v.text`/inline text（空文字以外。0/空白文字を含む＝主ループの
  採用規則と同一の presence 規則）で確認するだけの lookahead で検出。
  lookahead は shared-string 解決/値採用/参照妥当性検査を増やさず、読取対象外の
  不正参照で文書全体を失敗させない。採用範囲内の不正共有文字列は従来どおり
  文書全体 fail-closed（`xlsx shared string reference invalid`）。
- XLSX 列切り詰め: 保持対象行に col_cap 以降の非空値があるとき。空セルだけなら note 無し。
- `_read_sheet_rows`: 既存 diagnostics へ `rows_truncated` / `columns_truncated` を
  additive に載せる。戻り値型と既存2フラグ（missing_part /
  shared_string_reference_invalid）は不変。シート毎フラグを文書レベル OR で集約。
  後続シートにも対応。空 sheet/欠落 part の既存 note 保持。

## 3. before / after 例（実測・after.json より）

| ケース フィクスチャ                                | before parse_note | after parse_note |
|--------------------------------------------------|-------------------|------------------|
| CSV 4行（cap3）                                  | None              | `csv rows truncated at 3` |
| CSV 3列（cap2）                                  | None              | `csv columns truncated at 2` |
| CSV 両方超過                                     | None              | `csv rows truncated at 3; csv columns truncated at 2` |
| CSV 10101行＝header+10100行（既定cap・新規testの実生成数） | None      | `csv rows truncated at 10000` |
| CSV 空行のみ保持（parsed=False）                 | `csv contained no readable rows` | 同 + `; csv rows truncated at 3` |
| XLSX 4非空行+空row 2（cap3）                     | None              | `xlsx rows truncated at 3` |
| XLSX ちょうど3行+空row                           | None              | None（不変） |
| XLSX cap以降に空セルのみ                         | None              | None（不変） |
| XLSX cap以降に不正 shared ref                    | None（未読）      | `xlsx rows truncated at 3` のみ（fail-closed しない） |
| XLSX 採用範囲内に不正 shared ref                 | `xlsx shared string reference invalid` | 同（不変・truncation note は付かない） |
| XLSX 欠落part + 後続シート行超過                 | `xlsx worksheet parts missing: 1` | 同 + `; xlsx rows truncated at 3` |

## 4. 不変範囲（実測）

invariance probe（72エントリ = 36 fixture × smallcap/defaultcap、同一スクリプトを
変更前後で実行し before.json / after.json を機械比較）:

- note 以外の全フィールド（format/parsed/parser_version/tables/text/text_chars で
  tables は headers/rows/_source_rows/name を含む全フィールド）: 72/72 完全一致。
- parse_note: 49件 unchanged（上限未満/ちょうど/fail-closed 系は完全同値）、
  23件 appended（追加部分は4種の固定文言のみ、既存文言は prefix 保持）。
  broken=0。rc=0。
- 実文書（tests/fixtures/jpx/*.csv 2件）は読み取りのみ。前後で sha256 不変:
  - 6b1bf5c2df40a563973f940d379a9f04839678fabf80d1925ff030ca215828cd
  - 38f9bd690a12627d3b9b612e0028fe3d0b64be45b7884f1b12d1e9b273388693
- 同一 bytes 再読: 新規 test `test_truncated_extraction_is_deterministic` で pin。

## 5. コード hash（VPS 実物）

| ファイル | 状態 | sha256 |
|---|---|---|
| src/yowayowa/acquisition/documents.py | 変更前（先行4カード承認済み状態） | 52de468dfe8db0b91395e8199eb72fb97773fbc2786231417a780157909f7295 |
| src/yowayowa/acquisition/documents.py | 変更後（第2版でも不変・2026-10-25 再実測） | c736ab9118c6addfbfc870e6749f4f09d78d6c37f5fc7db83ff207e9abc81df3 |
| tests/test_acquisition_documents_units.py | 変更前 | 7da9a31691cee6fff3f9245ac1c43da20bfa7da662686208d23eab33c8b73444 |
| tests/test_acquisition_documents_units.py | 変更後（第2版でも不変・再実測） | d68e5433fd9ed97f08d873ef589813610b52d521d3bfb8cae1cbc408d10949d2 |
| tests/test_document_size_cap_notes.py | 新設（第1版） | 7af3c444c9c8cdfe0d9a54dfab9def48f2b7c0a8a3a3a8593f9022c806446db2 |
| tests/test_document_size_cap_notes.py | 第2版（境界35ケース追加。documents.py/units/fixture は不変） | 5daa075726edb56a23a1f37ddfe88e0848002e8d1909a3f95d61d8f041c86db0 |

先行承認済み差分（t_c7485e09 URL dispatch / t_04c17af3 共有文字列 fail-closed /
t_0c4f378f 部分抽出 note / t_469b2ee4 同名header `_dedupe`）は現物 diff に保持を確認。

## 6. 試験実績（host/command/rc）

VPS (ubuntu) — TZ=UTC, `.venv/bin/python` / ruff 0.16.8 / mypy（いずれも .venv 版）。

ラウンド1（2026-10-24）のうち現在有効な記録は変更前コードでの RED 実測のみ:

| 対象 | 結果 |
|---|---|
| RED: 新規 test のみ実行（変更前コード・当時33項目） | 22 failed（欠陥再現）/ 11 passed（無印・fail-closed pin）rc=1 |

※ 初版の表に記載していた「GREEN 96項目 / 近傍 155項目」は保存実ログの機械集計
（それぞれ 103項目 / 299項目）と一致していなかったため廃棄し、下の第2版実測に差し替えた。
また初版・第2版に保存した ruff format ログ（ruff_format_r2.log）はいずれも format 修正前の
失敗ログのまま保存されており、第2版の表がこれを rc=0 根拠として参照したのは実ログとの
不一致だった（第2回独立検収の R2 残存指摘）。最終の根拠は第3版で最終hashの3ファイルに
対し再取得した ruff_format_final.log（rc=0・§11）である。旧失敗ログは履歴として
bundle 内に保持する。

第2版（2026-10-25・最終コード。証跡は VPS
`/root/.hermes/profiles/worker-flash/cache/scratch/t_3501871f_r2/` と
`tsukumo:~/vps-work/tmp/t3501871f/r2/`。コマンドはいずれも
`TZ=UTC .venv/bin/python -m pytest <対象> --tb=short --junitxml=<同名>.xml` / `.venv/bin/ruff check|format --check --no-cache <3ファイル>`）:

| 対象 | 結果 |
|---|---|
| 新規 test のみ（tests/test_document_size_cap_notes.py・境界35ケース追加後） | 68項目 / 68 passed rc=0（junit 機械値 tests=68 failures=0 errors=0 skipped=0・new_tests_r2.xml） |
| 近傍8ファイル（新規68 + units70 + 同名header21 + 部分抽出17 + URL断片60 + 共有文字列20 + IR acquisition46 + IR monitor units32） | 334項目 / 333 passed + 1 xfail（PDF gap・従来どおり）rc=0（junit 機械値 tests=334 failures=0 errors=0 skipped=1・neighborhood_r2.xml） |
| ruff check（3ファイル） | rc=0（ruff_check_r2.log） |
| ruff format --check（3ファイル） | rc=0（ruff_format_final.log・第3版で再取得。入力3ファイルのhash c736ab91/d68e5433/5daa0757 と ruff 0.16.8 をログ先頭に記録、出力「3 files already formatted」。ruff_format_r2.log は修正前失敗ログとして履歴保持） |
| mypy src/yowayowa | rc=0（243 files, no issues・mypy_r2.log） |

TSUKUMO-KUN 別機（第2版 focused 実行。コマンド
`TZ=UTC PYTHONPATH=src .venv/bin/pytest tests/test_document_size_cap_notes.py tests/test_acquisition_documents_units.py --tb=short --junitxml=$HOME/vps-work/tmp/t3501871f/r2/focused_r2.xml`）:

| 対象 | 結果 |
|---|---|
| 新規 test + units（本カード同期ファイルのみ・hostname=TSUKUMO-KUN 実測） | 138項目 / 137 passed + 1 xfail rc=0（junit 機械値 tests=138 failures=0 errors=0 skipped=1・focused_r2.xml sha256 85cabde5…） |
| ruff check / format --check（3ファイル・ruff 0.16.8） | 各 rc=0（ruff_check_ts.log / ruff_format_ts.log） |

units への変更は2箇所のみ: 該当 strict-xfail マーカー解除（試験本体 assert は
1字も変えず docstring 1行のみ追加）＋境界表 1行（`truncated` → `capped + note`、
DEFECT 行の削除）。他 xfail（PDF gap）と本文 assert は未触。

TSUKUMO-KUN 別機 — full verify A/B は §7。新規 test は socket 遮断
（`socket.socket`/`create_connection` を monkeypatch して例外化）下で抽出を
実行する test を含み、全 fixture は純 bytes のためネットワーク到達なし。

## 7. TSUKUMO 全量 verify（baseline / candidate 分離）

検証木: TSUKUMO-KUN `/home/good-light/vps-work/current-work/yowayowa-investor`
（HEAD 0a05a171、別カードの research WIP ファイルを含む既存環境のまま・共用のため
不改変）。同一木で baseline（現状のまま）→ candidate（本カード3ファイルのみ同期、
sha256 が VPS 実物と完全一致: c736ab91 / d68e5433 / 7af3c444）→ 完全復元
（documents.py を 63f442ce に戻し2 test を削除、`git status` で痕跡ゼロを実証）。
コマンド: `PYTHONPATH=src .venv/bin/pytest --cov=yowayowa -q --junitxml=...`（TZ は
ホスト既定 JST）。証跡一式: `tsukumo:/home/good-light/vps-work/tmp/t3501871f/`
（baseline/candidate の .log/.xml/failures 一覧/documents_backup.py）。

| run | tests | failures | errors | skipped | time |
|---|---|---|---|---|---|
| baseline | 1992 | 2 | 0 | 2 | 341.9s |
| candidate | 2095 | 2 | 0 | 3 | 342.6s |

- 失敗の diff: **NO NEW FAILURES**。両 run とも
  `tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance`
  と `test_binance_zero_bar_is_skipped` のみ（本カード範囲外の既知ベースライン失敗
  で、baseline/candidate で同一）。
- candidate の増分 103 項目 = units 70 + 本カード新規 33、全て pass。skipped +1 は
  `test_pdf_column_gap_tolerance_is_applied`（残存 PDF gap strict-xfail 試験）の
  pdfminer 無環境での skip で、失敗ではない。
- TSUKUMO 側 ruff check / ruff format --check（本カード3ファイル）: rc=0。
  TSUKUMO 側 mypy documents.py: rc=0（Success）。
- 全体の make lint/typecheck は別カード WIP ファイル由来の結果を混入するため
  A/B 対象外とし、本カードファイルへの限定実行で rc を記録した。

第2版（2026-10-25）: documents.py 不変（c736ab91…）のため監査指示どおり全量 A/B は
再施工せず、上表の baseline/candidate 実測（1992→2095・既知2失敗のみ）を証拠として
保持した。追加した境界 test の focused 実行のみ別機で実施（§6 のとおり 138項目 rc=0）。
実行後は検証木を完全復元して痕跡ゼロを実証: documents.py を元の 63f442ce に戻し
（`git status` で documents.py は clean に戻ることを実測）、同期した 2 test ファイルと
実行時生成の `.coverage.TSUKUMO-KUN.pid*` / 対象 `__pycache__` を削除。共用樹には
他カードの research WIP（12エントリ）が既存のまま残る（本カード由来ではない・
計測時点間での外部変動 13→12 は本カードファイル以外の差分）。

## 8. IR 保持経路への到達（実測）

`IrMonitorService._process_document` の既存保持行
`record.parse_note = extracted.parse_note`
（src/yowayowa/services/ir_monitor_service.py:742）経由で、切り詰め note が
`IrDocumentRecord.parse_note` に到達することを新規 test
`test_ir_document_record_preserves_truncation_note` が in-memory transport で実測。
タイムライン payload への同値コピー（同:779）は同一変数の受け渡し。

## 9. 不変で守った範囲 / 未検証範囲

- 変更なし: PDF 列幅/座標/relationship/重複header算法/URL dispatch/通貨/数値/KPI、
  downloads.py 独自 note（`rows capped at ...`）、R1/R7/R8/R9/legacy broker。
- commit/push 無し。外部送信・応募・契約・注文・送金・課金無し。
- 未検証: 実運用チャートシート混在 XLSX 等の実物での上限到達（fixture 実証のみ・
  先行カードと同じ限界）。TSUKUMO full suite の baseline 失敗は §7 に分離記録。

## 10. 第2版修正記録（2026-10-25・検収指摘 R1/R2 への限定対応）

第1回独立検収（audit-sol・changes_requested・コメント1749）の必須修正 R1/R2 のみを
実施。**製品コード（documents.py）は 1 字も変更していない**
（sha256 c736ab91… が第1版から不変であることを 2026-10-25 再実測。units も不変）。

- R1（恒久回帰試験の補完）: tests/test_document_size_cap_notes.py へ 12 関数・35 ケースを
  追加（全 parametrize・製品コード無変更）:
  - CSV/XLSX × 行/列 × 局所cap(3行/2列) と既定cap(10000行/256列) の cap-1/exact/cap+1
    一式（既定は 9999/10000/10001 行・255/256/257 列の実数値で固定）。
  - XLSX 上限直後 row / 保持行の上限外列に置く '0' / 半角空白 / tab / 空文字 の
    presence 差（'0' と空白文字は payload・空文字は非該当＝採用ループと同一規則）。
  - CSV 生空行が cap に数える（cap を跨ぐ空行で note が付く）ことと、XLSX が
    非空行だけを数えることのカウント差を対比 1 test で pin。
  - 判定は `(parse_note == 固定文言) is noted` と `(parse_note is None) is (not noted)`
    の両面で note 付与/無付与を固定。
- R2（証跡と実ログの一致）: §3 の fixture 例行を実生成数（header+10100 = 10101行）へ訂正、
  §5 の新規 test hash を第2版へ更新、§6 の GREEN/近傍/ruff 行を実ログ（junitxml の機械値
  と最終 rc=0 ログ）で差し替え。初版の誤記録（GREEN 96 / 近傍 155・format 修正前の ruff
  失敗ログ）は廃棄し、歴史的記録としてのみ §6 に明記。
- 検収経路: 第1回 audit-sol（changes_requested・2026-10-25）。カード本文の旧記載
  （audit-cld）は Molt の reviewer 交代指示（audit-cld→audit-gmn→audit-sol）で置き換え
  済み。本第2版の再提出先も audit-sol。実装者 worker-flash と監査役は別プロファイルで、
  検収側は実装ファイルを一切編集していない。

  ※ ただし本第2版では §6 の ruff format 参照先（ruff_format_r2.log）を実際の失敗ログの
  まま rc=0 として記載してしまい、第2回検収で R2 残存1点として差し戻された（§11 で訂正）。

## 11. 第3版修正記録（2026-10-25・第2回検収 R2 残存1点の限定対応）

第2回独立検収（audit-sol・changes_requested）の必須修正のうち未解消だった1点のみ
（doc が rc=0 根拠とする ruff_format_r2.log が修正前失敗ログのまま保存されていた）
を実施。**documents.py / units / 新規 test は 1 字も変更していない**
（sha256 c736ab91… / d68e5433… / 5daa0757… を 2026-10-25 再実測で不変確認。
検収側の明示どおり全量 A/B・focused の再実行は不要で、製品hash不変のため
第1回検収の同値証拠は継続有効）。

- ruff format --check（本カード3ファイル）を VPS (ubuntu) の最終hash実物に対し再実行し、
  host/command/入力hash/ruff 0.16.8/rc=0・「3 files already formatted」を
  ruff_format_final.log（sha256 f3f2650a48e03ddf301c4797649c609af8face37ff36d5f8292dde178155d756）
  として保存。§6 の参照先を本ログへ訂正した。ruff_format_r2.log は修正前失敗ログとして
  履歴保持し、§6 註にその旨を明記した。
- 証跡 bundle を再作成: evidence_r3.tar.gz（sha256
  ef2979398b8dc2f2b70ccf56713c18a28c6824f75f848a7da90c9930d092b3e4）。内容は
  旧 evidence_r2.tar.gz（b6370c92…・sha256 不変で履歴保持）と同一ファイル＋
  ruff_format_final.log。bundle 内の final log を `tar -xOzf` で取出し、
  sha256 f3f2650a… が保存実物と一致することを実測済み。
- TSUKUMO ミラー同期: tsukumo:~/vps-work/tmp/t3501871f/r2/ へ final log と
  evidence_r3.tar.gz を配置し、リモート実測 sha256 が VPS 実物と一致
  （f3f2650a… / ef297939…）。
- 検収経路: 再提出先は audit-sol（第2回検収コメントの明示どおり）。実装者と検収役は
  別プロファイル。commit/push 無し・INDEX 非接触・他 hotspot 非接触。
