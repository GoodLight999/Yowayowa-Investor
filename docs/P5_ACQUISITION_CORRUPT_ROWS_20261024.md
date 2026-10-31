# P5: acquisition 系 JSONL の破損行を「無言で飛ばす」から「件数・行番号・理由つきで飛ばす」へ

- カード: t_ebd05744（worker-goat・承認済みキューからの自動供給カード）
- 出典: `docs/SILENT_FAILURE_AUDIT_20261023.md` §3 #11〜#17（影響「低」7 件）
- 実施日: 2026-10-24 (JST)
- 対象: `src/yowayowa/acquisition/discovery.py` / `src/yowayowa/acquisition/snapshots.py` /
  `src/yowayowa/crypto_acquisition.py` / `src/yowayowa/stock_acquisition.py`（＋既存の診断面への配線）
- 触っていないファイル: `services/ir_monitor_service.py` / `services/private_source_service.py` /
  `services/orderbook_service.py`（他カード稼働中。§7 に申し送り）
- 制約の遵守: 外部送信・課金・公開 push・実注文なし。内蔵 LLM なし。新しいデータ源・新しいログ基盤・
  DB 列・画面は作っていない。fixture のみで試験。

## 1. 結論

破損行の扱い自体は変えていない（**1 破損行で全体を落とさない**、という監査 §4 E7 の契約はそのまま）。
変えたのは「飛ばしたことが誰にも見えない」点だけ。

- 破損行は **件数 + 1 起点の物理行番号 + 理由** として既存の戻り値の**外側**（呼び出し側が渡す dict）に載る。
- 既存の**戻り値キー・型・意味は不変**（`known_keys` は `set[str]` のまま、`append` は `int` のまま、
  `history` は `list[SnapshotRecord]` のまま、`recent_entries` は tuple の list のまま）。
  診断を受け取りたい呼び出し側だけが `diagnostics={}` を渡す。
- 破損行を「未取得」として扱う現行の**安全側の意味も不変**。したがって「破損行があると重複追記され得る」
  「latest が 1 つ古い版になる」という劣化は**残る**が、それが**観測できる**ようになった。
- 副次的な堅牢化: これまで **valid JSON だが object でない行**（`[1,2]` / `3` / `"x"`）は
  `AttributeError` / `TypeError` になり read 全体が落ちていた。これを破損行として数えて飛ばすようにした
  （カード指定の境界試験「型不一致」に対応）。

## 2. どこに載せたか（既存の診断面の再利用）

| 監査# | 場所 | 観測面 | 実測できるもの |
|---|---|---|---|
| #11〜#13 | `acquisition/discovery.py` `FingerprintStore.known_keys` / `known_content_urls` / `known_urls` | `diagnostics=` 引数、および `FingerprintStore.read_diagnostics(source_id)` | `corrupt_rows`, `corrupt_row_numbers`, `corrupt_reasons`, `row_count`, `path` |
| #16〜#17 | `acquisition/snapshots.py` `SnapshotStore.history` / `latest_payload` / `recent_entries` | `diagnostics=` 引数 → 既存の `AcquisitionOutcome.notes`（`acquisition/service.py`） | 同上（`latest_payload` が古い版へ落ちた事実も件数で分かる） |
| #14 | `crypto_acquisition.py` `CryptoOhlcvStore._existing_keys` / `append` | `diagnostics=` 引数 → `fetch_crypto_ohlcv(diagnostics=)` → `crypto-fetch` CLI の警告行 | 同上 |
| #15 | `stock_acquisition.py` `StockOhlcvStore._existing_keys` / `append` | `diagnostics=` 引数 → `fetch_stock_ohlcv(diagnostics=)` → `stock-fetch` CLI の警告行 | 同上 |

新しいログ基盤は作らず、`acquisition/snapshots.py` に**共有の読み取りプリミティブ**を 1 組だけ置いた
（このモジュールは既に `_safe_component` を discovery と共有している、JSONL の低レベル置き場）。

```
jsonl_read_diagnostics(path)   -> 空の診断 dict（キーは JSON そのまま出せる形）
parse_jsonl_row(line, row_number=, diagnostics=)   -> dict | None（破損は数えて None）
iter_jsonl_rows(lines, first_row_number=, diagnostics=)
read_jsonl_rows(path, diagnostics=)
mark_corrupt_row(diagnostics, row_number, reason)
corrupt_rows_note(diagnostics) -> str | None（同じ劣化を同じ文面で言うための 1 行）
```

診断 dict の形（そのまま `notes` や CLI に出せる）:

```json
{"path": "…/fingerprints.jsonl", "row_count": 2, "corrupt_rows": 1,
 "corrupt_row_numbers": [2], "corrupt_row_numbers_truncated": false,
 "corrupt_reasons": ["line 2: json_decode_error: Expecting value: line 1 column 47 (char 46)"]}
```

## 3. 対象 7 箇所の前後

### #11 `discovery.py:227` `known_keys`（#12 `known_content_urls` / #13 `known_urls` も同型）

```python
# before: 破損行は数えられず、URL が「未取得」になる
try:
    item = json.loads(stripped)
except json.JSONDecodeError:
    continue                     # ← 件数も行番号も残らない
url = item.get("url")

# after: 同じく飛ばすが、飛ばした事実が呼び出し側に残る
for _row_number, item in self._rows(source_id, diagnostics):
    url = item.get("url")
```
`known_keys(source_id, diagnostics={})` で件数・行番号・理由が入る。
`diagnostics` を渡さなければ従来と完全に同じ（戻り値も同じ）。

### #14 / #15 `crypto_acquisition.py:64` `_existing_keys` / `stock_acquisition.py:74` `_existing_keys`

```python
# before
try:
    entry = json.loads(line)
except ValueError:
    continue                     # 重複排除キーが黙って欠落

# after
for _row_number, entry in read_jsonl_rows(path, diagnostics=diagnostics):
```
`append(..., diagnostics=d)` は**戻り値の int を変えない**。破損行があると
`d["corrupt_rows"] > 0` になり、「同じ (provider, currency, as_of) を再追記した」理由が分かる。

### #16 / #17 `snapshots.py:80` `history` / `:110` `recent_entries`

```python
# before
try:
    entry = json.loads(stripped)
    records.append(SnapshotRecord.model_validate(entry["record"]))
except (json.JSONDecodeError, KeyError, ValueError):
    continue                     # 履歴から無言で消える / latest は古い版へ落ちる

# after
entry = parse_jsonl_row(lines[index], row_number=row_number, diagnostics=diagnostics)
try:
    records.append(SnapshotRecord.model_validate(entry.get("record")))
except (TypeError, ValueError) as exc:
    mark_corrupt_row(diagnostics, row_number, f"snapshot record unreadable: {type(exc).__name__}")
```
`payload` が object でない行も破損として数える（従来は黙って捨てていた）。
**末尾窓の切り方（`[-limit:]` → 逆順）は変えていない**ので、破損行が末尾にあっても
「窓の外の古い行を繰り上げる」ことはしない（既存意味の保存）。

### 配線: `acquisition/service.py` の `AcquisitionOutcome.notes`（既存フィールド）

```python
prior_diagnostics: dict[str, Any] = {}
prior = self.snapshot_store.recent_entries(connector_id, resource, 1, diagnostics=prior_diagnostics)
if prior_diagnostics.get("corrupt_rows"):
    note = corrupt_rows_note(prior_diagnostics)
    if note is not None:
        notes.append(f"{note}; the diff baseline may be older than the last stored version")
```

### 配線: `crypto-fetch` / `stock-fetch` CLI（既存の警告行と同じ体裁）

```
BTC/binance: persisted 1 rows
BTC: 1 corrupt line(s) skipped in /…/crypto-ohlcv/BTC/ohlcv.jsonl (rows [2]):
     those records are treated as unseen; rows may have been persisted again
```

## 4. 実測（fixture のみ・ネットワークなし）

scratch の probe（`probe_ebd05744.py` / `probe_ebd05744_cli.py`）の生出力。

### A. discovery — 既知 2 URL のうち 1 行を破損させた

```
known_keys        : ['https://ir.example/1q.pdf|']
classify statuses : ['seen', 'new']          ← 破損行の URL が「new」（再取得・重複アラートの原因）
read_diagnostics  : {"path": "…/fingerprints.jsonl", "row_count": 2, "corrupt_rows": 1,
                     "corrupt_row_numbers": [2], "corrupt_row_numbers_truncated": false,
                     "corrupt_reasons": ["line 2: json_decode_error: Expecting value: line 1 column 47 (char 46)"]}
note              : 1 corrupt line(s) skipped in …/fingerprints.jsonl (rows [2]): those records are treated as unseen
```

### B. snapshots — 最新行を破損させた

```
history           : ['s1', 's0']
latest snapshot_id: s1 (fell back one version)     ← #17 の劣化そのもの（残るが観測できる）
diagnostics       : {"row_count": 2, "corrupt_rows": 1, "corrupt_row_numbers": [3],
                     "corrupt_reasons": ["line 3: json_decode_error: Expecting ',' delimiter: line 1 column 32 (char 31)"]}
```

### C. crypto / stock — 破損行があるとキーが欠落し、同一行が再追記される

```
crypto first write : 1
crypto re-append   : 1        ← 意味は不変（重複追記は起きる）
crypto diagnostics : {"row_count": 1, "corrupt_rows": 1, "corrupt_row_numbers": [1], "corrupt_reasons": ["line 1: json_decode_error: …"]}
stock first write  : 1
stock re-append    : 1
stock diagnostics  : {"row_count": 1, "corrupt_rows": 1, "corrupt_row_numbers": [1],
                      "corrupt_reasons": ["line 1: not_a_json_object: list"]}   ← 型不一致も数える
```

### D. fetch summary のキーは不変（診断は横に載る）

```
crypto summary     : {'BTC': {'binance': {'persisted': 0, 'error': None}}}      ← 従来と同じキー
crypto fetch diag  : {"BTC": {"row_count": 2, "corrupt_rows": 1, "corrupt_row_numbers": [1], …}}
stock summary      : {'AAPL': {'alpaca': {'persisted': 0, 'error': None}}}
stock fetch diag   : {"AAPL": {"row_count": 2, "corrupt_rows": 1, "corrupt_row_numbers": [1], …}}
```

### E. 全行破損 — 空の結果 + 全件数、例外なし

```
known_keys       : set()
read_diagnostics : {"row_count": 3, "corrupt_rows": 3, "corrupt_row_numbers": [1, 3, 4],
                    "corrupt_reasons": ["line 1: json_decode_error: …", "line 3: not_a_json_object: list",
                                        "line 4: not_a_json_object: int"]}
```
（空行は `row_count` にも `corrupt_rows` にも数えない。）

### F. CLI 面（`crypto-fetch` / `stock-fetch` を stub provider で実行）

上の §3 の警告行が実際に出ることを確認（`CliRunner`、fixture ディレクトリのみ）。

## 5. 試験

新規テスト（すべて fixture、ネットワークなし）:

- `tests/test_ir_acquisition.py`（+4）
  `test_fingerprint_clean_read_reports_no_corrupt_rows` /
  `test_fingerprint_corrupt_rows_are_counted_with_numbers_and_reasons`（JSON 壊れ + 型不一致の 2 行、
  3 つの reader すべてで同じ件数、`classify_documents` の status は従来どおり）/
  `test_fingerprint_all_rows_corrupt_is_empty_and_fully_counted` /
  `test_fingerprint_missing_file_reports_reason_without_corruption`
- `tests/test_private_acquisition_snapshots.py`（+6）
  正常時 0 件 / 破損 2 行の件数と行番号（diagnostics 無しの戻り値が同一であることも確認）/
  最新行破損で `latest_payload` が 1 つ古い版へ落ちる + 件数 / payload が object でない行 /
  全行破損 / file_missing
- `tests/test_crypto.py`（+4）: 正常時 0 件 / 破損キー行の再追記 + 件数 / 型不一致行 /
  `fetch_crypto_ohlcv` の summary キー不変 + 診断
- `tests/test_stock_ohlcv.py`（+4）: 上と同じ 4 本（stock 側）
- `tests/test_private_acquisition_service.py`（+1）:
  `test_fetch_notes_corrupt_snapshot_lines` — 破損 snapshot 行がある 2 回目 fetch で
  `outcome.notes` に件数・行番号・「baseline が古い」旨が入り、diff 自体は読める版と比較される

実行結果（VPS では重い全走はしない。対象 + 周辺のみ）:

```
# 対象 5 ファイル（既知の先行失敗 2 件を deselect）
pytest tests/test_ir_acquisition.py tests/test_private_acquisition_snapshots.py \
       tests/test_crypto.py tests/test_stock_ohlcv.py tests/test_private_acquisition_service.py \
       --deselect tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance \
       --deselect tests/test_crypto.py::test_binance_zero_bar_is_skipped
=> 164 passed, 2 deselected, rc=0

# 周辺（private acquisition API/CLI/auth/cache/downloads/parsers/transport、ir monitor units、ir surfaces、documents units）
=> 171 passed, 5 xfailed, rc=0

ruff check / ruff format --check : clean（変更ファイル + 追加テスト）
mypy（変更 4 モジュール）        : Success: no issues found in 4 source files
scripts/check_openapi_baseline.py: 155 operations, baseline 一致（API 契約の変化なし・rc=0）
```

**既知の先行失敗（本カードの変更と無関係・実測で確認）**: `test_crypto.py::test_binance_klines_returns_records_with_daily_provenance`
と `test_crypto.py::test_binance_zero_bar_is_skipped` は、本カードの変更を `git stash` で退避した
クリーン状態でも同じく失敗する（期待 6 本に対し 4 本＝実行時刻依存。JST 00:10 前後の実行で再現）。
Binance provider の未解決事項として本カードの範囲外に置いた。

## 6. 再現

```
cd /root/current-work/yowayowa-investor
.venv/bin/pytest tests/test_ir_acquisition.py tests/test_private_acquisition_snapshots.py \
  tests/test_crypto.py tests/test_stock_ohlcv.py tests/test_private_acquisition_service.py
```

## 7. 未実施・申し送り（意図的）

- **`services/ir_monitor_service.py` への配線**: `known_keys` / `known_content_urls` の呼び出し
  （同ファイル 469〜470 行）に `diagnostics={}` を渡し、`corrupt_rows_note()` を `notes` に積めば
  IR 監視 outcome まで件数が出る。ただし同ファイルは他カードが稼働中のため触っていない。
  現状は `FingerprintStore.read_diagnostics(source_id)` で同じ情報が取れる（API 面は用意済み）。
- 破損行の**修復・書き戻し・退避（mv）はしない**（append-only ストアの現行方針どおり。読むだけ）。
- 重複検知・差分判定の**意味は変えていない**（破損行＝未取得として安全側、のまま）。
