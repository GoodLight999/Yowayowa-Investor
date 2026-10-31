# P5: IR監視 outcome.notes に fingerprint 破損行の件数を載せる（2026-10-24）

- カード: t_b28a4ed3（親: t_ebd05744）
- 対象: `src/yowayowa/services/ir_monitor_service.py`、`tests/test_ir_monitor_service_units.py`

## 結論

`IrMonitorService.monitor()` の fingerprint 読み取り（`known_keys` / `known_content_urls`）に
keyword-only の `diagnostics` dict を渡し、読み取り後に `corrupt_rows_note()`
（`yowayowa.acquisition.snapshots`）で1行 note を組み立て、破損行があるときだけ既存の
`IrMonitorOutcome.notes` に追加するようにした。件数・1起点行番号・理由・
（破損行URLが `new` 扱いになり再取得・重複アラートが走り得ること）が1行で運用者に届く。
`FingerprintStore` の戻り値・`classify_documents` の意味（破損行＝未取得のまま）は不変。
新しいログ基盤・DB 列・画面は作っていない。

## 背景

t_ebd05744 で acquisition 系 JSONL reader が破損行を「件数・行番号・理由つき」で
観測可能になった（diagnostics out-param 方式）。ただし IR 監視
（`services/ir_monitor_service.py`）への配線は、同ファイルを他カードが稼働中だったため
見送られており、本カードで実施した。IR 監視は `fingerprints.jsonl` を読む側であり、
破損行があるとその URL が `new` 扱いになって再取得・重複アラートが走り得るため、
読み取り側の notes に事実を載せるのがこの診断面の自然な位置。

## 変更（前後）

### 前（ir_monitor_service.py 469〜470 行付近）

```python
known = self._fingerprints.known_keys(source.source_id)
known_content = self._fingerprints.known_content_urls(source.source_id)
```

### 後

```python
fingerprint_diagnostics: dict[str, Any] = {}
known = self._fingerprints.known_keys(
    source.source_id, diagnostics=fingerprint_diagnostics
)
known_content = self._fingerprints.known_content_urls(
    source.source_id, diagnostics=fingerprint_diagnostics
)
corrupt_note = corrupt_rows_note(fingerprint_diagnostics)
if corrupt_note is not None:
    notes.append(
        corrupt_note
        + "; until re-fetched, the affected URLs classify as new and"
        " may trigger re-fetch and duplicate alerts"
    )
```

- 両 reader は同じ dict を clear して上書きするため、note は **`known_content_urls` の後**に
  作る（同一ファイルを読むので件数は同値）。
- 追記は1行のみ。既存 `IrMonitorOutcome.notes` の体裁（`list[str]`・他 note の文言）は不変。

## 実測（fixture のみ・ネットワークなし・LLM なし）

破損1行（2行目 `{not json`）を混ぜた `fingerprints.jsonl` で 1 run、および
クリーンな store で 1 run の `outcome.notes` 生出力
（scratch の `probe_ir_notes.py`・`tests/test_ir_monitor_service_units.py` の fixture と同一形）:

```text
== corrupt row PRESENT (1 broken line at row 2) ==
fetch_state: ok
notes:
  - "1 corrupt line(s) skipped in <tmp>/ir-fingerprints/example-ir/fingerprints.jsonl (rows [2]): those records are treated as unseen; until re-fetched, the affected URLs classify as new and may trigger re-fetch and duplicate alerts"

== corrupt row ABSENT (clean store) ==
fetch_state: ok
notes:
```

- 件数（`1 corrupt line(s)`）・行番号（`rows [2]`）・path・理由と再取得/重複アラートの
  走り得ることが1行に載る。fetch_state は `ok` のまま（破損行＝未取得で run は落とさない）。
- クリーン時（ファイル無し→1 run 目、有効行のみ→2 run 目）は note は増えない。

## 試験

- `tests/test_ir_monitor_service_units.py` に B18b として2件追加:
  - `test_corrupt_fingerprint_line_count_lands_in_notes` — 破損1行で run し、notes に
    件数・行番号・重複アラート文言が1行で出ること／fetch_state が `ok` のままことを検査。
  - `test_clean_fingerprint_reads_add_no_corrupt_note` — ファイル無し→1 run 目と
    有効行のみの 2 run 目で、破損 note が増えないことを検査。
- 実測: 専用テストファイル 32 tests（既存30＋新規2）全緑 **rc=0**。
- ruff check / ruff format --check（変更2ファイル）/ mypy（`ir_monitor_service.py`）いずれも clean。

## 制約の確認

- `FingerprintStore` の戻り値・`classify_documents` の意味は変更なし（破損行は skip されて
  未取得扱いのまま、件数化のみ）。
- 新しいログ基盤・DB 列・画面は無し。既存 `notes` に載せるのみ。
- 外部送信・課金・公開 push・実注文・内蔵 LLM は無し（fixture のみ）。

## 再現コマンド

```bash
cd /root/current-work/yowayowa-investor
.venv/bin/python -m pytest tests/test_ir_monitor_service_units.py -q --tb=short   # 32 passed, rc=0
.venv/bin/ruff check src/yowayowa/services/ir_monitor_service.py tests/test_ir_monitor_service_units.py
.venv/bin/ruff format --check src/yowayowa/services/ir_monitor_service.py tests/test_ir_monitor_service_units.py
.venv/bin/mypy src/yowayowa/services/ir_monitor_service.py
```

## 申し送り

- `FingerprintStore.read_diagnostics()`（t_ebd05744 で追加の読み取り専用メソッド）は本配線では
  未使用。diagnostics out-param 方式で1 run 内に完結するため（reader 呼び出しと同じ辞書を共有）。
- 今後 IR 監視の notes を外部通知に載せる場合は、この行が path を含むことに注意
  （ローカルパスが本文に現れる。本カードでは既存 notes の体裁を変えないためそのまま）。
