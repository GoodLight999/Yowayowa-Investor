# P5 R11: `screen` の指標を「名前と値」の表に分離する（実測記録）

- カード: t_d8dcfdcf（worker-goat / 2026-10-23）
- 対象リビジョン: `agent/commercial-foundation` @ `f3aed65`（VPS 作業ツリーには他カードの未コミット差分あり。本カードの変更は `src/yowayowa/cli.py` の screen 描画ブロックと新規テスト1本のみ）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 R11（行き止まりではない運用摩擦）、`docs/PRIVATE_OPERATOR_ROADMAP.md` P5（financial/valuation interpretation・CLI parity）
- 位置づけ: **表示のみの修正**。数値の算出・フィルタ・API・DB・code 表記・欠測判定・投資判断の意味は変えない。

## 1. この変更で固定する見せ方

| 表 | 列 | 意味 |
|---|---|---|
| 要約表 | `Symbol` / `Matched` / `Failures` | 従来どおり。`Matched` は yes/no、`Failures` は不一致だったフィルタの metric 名（API の `failures`） |
| 指標表 | `Symbol` / `Metric` / `Value` | API が返した指標を **1指標1行**。`Metric` は API の既存キーをそのまま、キー順は辞書順（`sorted`）で固定 |

- `Value` は API のスカラーを無変換で表示する。`None` は欠測なので `unavailable`、実値の `0` は `0`／`0.0` として残す（ゼロ補完・空欄化をしない）。
- 単位の推測変換をしない。%・倍・JPY などへの読み替えは行わない（元レスポンスに単位根拠が無いため）。
- 指標の省略をしない。キーと値が端末幅に収まらない場合は **fold（折り返し）** し、`…` による黙殺をしない。
- `metrics` が空の銘柄は、指標表の後ろに `Unavailable metrics: <symbol>` を1行出す（API の `data_unavailable` 行がこれに当たる）。
- `rows` が空のときは要約表のみ（ヘッダのみ）を出し、exit は従来どおり 0。

## 2. 入力の同定（hash 実測）

| 対象 | sha256 |
|---|---|
| `src/yowayowa/cli.py` 変更前（HEAD `f3aed65`） | `89bbf132beab4933632d2aa51001bff63471985d9c3f6d9f9780bd9a3b3eed6c` |
| `src/yowayowa/cli.py` 変更後（VPS 作業ツリー。R3 の未コミット差分を含む） | `fdc63120e7b159cd4bdf63c0a04f598737d6e6a5de4e0db6423f74788130bdaf` |
| `src/yowayowa/cli.py` R11 のみ適用（TSUKUMO 検証に使用） | `b5fe3c540ee49277c982d12e81f6ffc4abf9578aa85f21f2e4dc545ae8b46ae0` |
| `tests/test_screen_metrics_display.py`（新規・最終12件） | `4d232d1297193c3766f84cd4fd45475a2a9d292bcd9f2cee52d62ad24678f5ed` |
| 同ファイル（`make verify` 全量を回した版・11件） | `c06f2693379dba8d7d4d3275a19e4a0be333934900bd0c408908a110b8e96419` |
| 固定入力 `screen_fixture.json`（§4） | `8a79e9bd5cc9f0be2766ba17ee7ab6918dad95ad6e068f6f9475ff141628d681` |
| 実API応答 `live_screen.json`（§5） | `63b99cb80511f1d820c381f1b6189300c7cc4db0e09eb5331bd91479c62ed99f` |

R11 の差分は HEAD 比 **+42/−6 行**（`cli.py` 1ファイル）。うち内訳は (a) `typing.Any` と `rich.text.Text` の import、(b) `_screen_metric_value` / `screen_metrics_display` の追加、(c) `screen()` の表組み替え。
VPS 作業ツリーの `git diff` には R3（`portfolio delete`）の未コミット差分が併存するが、本カードは触っていない。TSUKUMO 検証ツリーは **HEAD + R11 のみ**（`cli.py` = `b5fe3c54…`、R3 を含まない）で実施した。

## 3. 実装

```python
def _screen_metric_value(value: float | int | None) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return repr(value)   # API の JSON と同じ最短往復表現
    return str(value)
```

- `repr(float)` は Python の最短往復表現で、`json.dumps` が API 応答に載せた文字列と同一。丸め（`.4g`/`.6g`）も拡大（%換算）もしないため、精度の欠落も捏造も起きない。
- `screen_metrics_display(rows)` が `Table("Symbol", "Metric", "Value")` を組み、各セルを `Text(..., overflow="fold")` で包む。rich の既定（文字列セルは `…` で切り詰め）を外すのはこのため。
- `screen()` は「POST → 要約表 → 指標表 → 空 metrics の注記」を出すだけ。`--filter` の解釈、`symbols` の大文字化、HTTP 呼び出し、exit 挙動は無変更。

## 4. 固定入力での前後比較（同一レスポンス）

同じ `/v1/screen` 応答（`screen_fixture.json`、36指標×2銘柄、`None`/`0.0`/負値/小数を含む）を、変更前 CLI（HEAD の `cli.py`）と変更後 CLI に通した。HTTP だけスタブし、typer/rich は実物。

| 端末幅 | 変更前 | 変更後 |
|---|---|---|
| `COLUMNS=80` | 107行 / `…` を含む行 6 / sha256 `7ddd79bc…`（JSON 1セルが折り返し） | 82行 / `…` 0 / sha256 `589e65a0…` |
| `COLUMNS=120` | 41行 / `…` 0 / sha256 `b3f3f1ea…`（1セルに JSON 全文） | 82行 / `…` 0 / sha256 `589e65a0…` |

- 変更前は80列で `operating_income_growth_y…` のように**キーが切れて意味が読めない**（6行）。120列では切れないが、JSON の壁で名前と値の対応が読みにくい。
- 変更後は80列・120列で**同一出力**（表の自然幅62列が80列に収まるため rich が引き伸ばさない）。全72指標が1行ずつ並び、`…` は0。
- 変更前後で **POST のリクエスト本文は完全一致**（`{"symbols": ["7203.T", "6758.T"], "filters": [{"metric": "operating_margin", "operator": "gt", "value": 0.1}]}`）、exit 0 も同一。

変更後の実出力（80列・抜粋）:

```
┏━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ Matched ┃ Failures                       ┃
┡━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 7203.T │ no      │ gross_profit, operating_margin │
│ 6758.T │ yes     │                                │
└────────┴─────────┴────────────────────────────────┘
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ Metric                         ┃ Value            ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ 7203.T │ cash                           │ 0.0              │
│ 7203.T │ capex                          │ unavailable      │
│ 7203.T │ operating_cash_flow            │ -1234567890.5    │
│ 7203.T │ operating_cash_flow_growth_yoy │ unavailable      │
│ 7203.T │ operating_income_growth_yoy    │ -0.0315          │
│ 7203.T │ revenue                        │ 45095325000000.0 │
      …（以下、辞書順で全指標。6758.T も同様に全指標）
```

## 5. 実APIの実値での実走（7203.T / 6758.T）

- 起動: `YOWAYOWA_MODE=personal YOWAYOWA_API_TOKEN="" YOWAYOWA_DATABASE_URL=sqlite:///<scratch>/r11_live.db .venv/bin/python -m uvicorn yowayowa.api.app:app --host 127.0.0.1 --port 8127`（**読み取りのみ**。`screening-run`・原本DBへの書込・内蔵LLMは使っていない）
- `POST /v1/screen {"symbols":["7203.T","6758.T"],"filters":[]}` → **HTTP 200、2銘柄とも `matched=true`、各36指標**（応答 sha256 `63b99cb8…`）。行順は `(not matched, symbol)` の既存ソートどおり `6758.T` → `7203.T`。
- CLI 実走（同じ 8127 の実サーバ、`COLUMNS=80` / `120`）:

| 端末幅 | 変更前 | 変更後 |
|---|---|---|
| 80 | 78行 / 最大行幅80 / sha256 `a2ae6e64…`（Metrics セルに JSON） | 82行 / 最大行幅66 / sha256 `7229f22c…` |
| 120 | 40行 / 最大行幅120 / sha256 `b4e5adc7…`（1行1銘柄の JSON 壁） | 82行 / 最大行幅66 / sha256 `7229f22c…` |

- **値の一致検証（実データ）**: 変更後CLIの表セルを機械的に取り出し、`live_screen.json` の各スカラーと比較 → `displayed_cells=72 / expected_cells=72 / missing=[] / extra=[] / mismatched=[] → EXACT_MATCH`。期待値の作り方は `repr(value)`（＝API の JSON 表記）そのもの。つまり表示は**一切変換されていない**（`0.7340516980222559` のような16桁の比率もそのまま）。
- **欠測経路の実走**: `7203.T` + `NOSUCH.T` を同じ実APIに投げると、プロバイダが `Quote not found for symbol: NOSUCH.T` を返し、API は `{"symbol":"NOSUCH.T","metrics":{},"matched":false,"failures":["data_unavailable"]}` を返す（HTTP 200）。変更後CLIは要約表に `no` / `data_unavailable`、指標表には 7203.T の36行のみ、その後に `Unavailable metrics: NOSUCH.T` を出す（変更前は `{}` を1セルに表示）。sha256 は変更前 `c2235a55…`（58行）→ 変更後 `f0875ca4…`（47行）。

## 6. 境界試験（`tests/test_screen_metrics_display.py` 12件）

| 試験 | 何を固定するか |
|---|---|
| `test_every_metric_is_one_row_with_keys_in_sorted_order` | 複数銘柄・異なるキー集合 → 1指標1行、辞書順、銘柄ブロック順は API の行順 |
| `test_none_zero_negative_and_decimal_stay_distinct` | `None`→`unavailable`、`0.0`→`0.0`、負値・小数が残る。`unavailable` は欠測数と一致（ゼロ補完なし） |
| `test_integer_scalars_are_echoed_unchanged` | JSON の整数 `0`/`-5` も `0`/`-5` のまま（`0.0` と区別、`unavailable` と区別） |
| `test_no_unit_inference_or_rescaling` | 比率を %・倍・JPY に読み替えない（`0.118` を `11.8` にしない） |
| `test_long_key_folds_instead_of_being_ellipsized` | 80列で60桁キーが fold し、`…` が出ず、折り返しを連結すると元のキーと値に戻る |
| `test_all_metrics_of_the_payload_are_listed` | 36指標×2銘柄=72行。欠落なし・追加なし（独自の指標名を発明しない） |
| `test_matched_and_failures_keep_their_meaning` | 要約表の yes/no と失敗 metric 名が従来どおり。旧 `Metrics` 列と JSON/`null` 表記は消えている |
| `test_empty_metrics_dict_is_announced_as_unavailable` | `metrics={}` の銘柄は `Unavailable metrics: <symbol>` で明示 |
| `test_empty_rows_still_exits_zero` | `rows=[]` でも例外なく exit 0（要約表はヘッダのみ、指標表は出さない） |
| `test_request_payload_and_symbol_normalisation_are_unchanged` | POST 本文とシンボル大文字化が従来と完全一致 |
| `test_invalid_filter_is_rejected_before_any_http_call` | 不正 `--filter` は HTTP 前に `Invalid --filter` で失敗（送信0件） |
| `test_http_failure_reports_nothing_fabricated` | API 503 は例外として表面化し、`unavailable` やゼロ埋めの表を捏造しない |

## 7. 検証結果（host と rc を明記）

| 検証 | host | 結果 |
|---|---|---|
| 対象テスト12件 + `tests/test_cli.py` 10件 | VPS (ubuntu, Python 3.12 venv) | **22 passed rc=0** |
| 対象テスト12件 + `tests/test_cli.py` 10件（最終hash `4d232d12…`） | TSUKUMO-KUN (Python 3.14.7 venv) | **22 passed rc=0** |
| 関連CLI試験 `test_portfolio_delete` / `test_order_inquiry` / `test_preset_snapshot_cli` / `test_broker_read_cli` / `test_screening` | VPS | **56 passed rc=0** |
| `ruff check` + `ruff format --check`（本カード2ファイル） | VPS / TSUKUMO 両方 | **rc=0** |
| `mypy src/yowayowa/cli.py`（strict） | VPS / TSUKUMO 両方 | **rc=0**（`Success: no issues found`） |
| `make verify` 全量 | TSUKUMO-KUN（HEAD+本カード2ファイル、11件版で実走） | **rc=1** — lint ✓ / typecheck ✓（228ファイル）/ `test` で **2 failed, 1999 passed, 2 skipped**（331.54s）。失敗は `tests/test_crypto.py::test_binance_klines_returns_records_with_daily_provenance` と `::test_binance_zero_bar_is_skipped` の**既存2件のみ**。`openapi` は make が test で停止するため未到達 |
| 上記2件のベースライン再現（本カードの2ファイルを**外した** TSUKUMO ツリー） | TSUKUMO-KUN | **同じ2件が失敗**（＝本変更に起因しない。fixture の日付窓と実日付の関係で落ちる既存のbinance系） |
| `make openapi`（単独実行） | TSUKUMO-KUN | **rc=0**（`Wrote openapi.json`） |
| 対象テスト再実行（最終12件・`4d232d12…`、TSUKUMO） | TSUKUMO-KUN | **22 passed rc=0**（全量実走は11件版。差分はテスト追加1件とテスト側のスタブ整理のみで、製品コード `cli.py` は両実行で同一 `b5fe3c54…`） |

`make verify` 全量合格には到達していない（既存の crypto 2件で停止）。**全量合格とは扱わない。** 本変更が失敗を追加していないことは、(a) 失敗2件が本カードの2ファイルを含まない、(b) 本カードの2ファイルを外した同一ツリーで同じ2件が失敗する、(c) 対象12件と関連56件が rc=0、で示した。

## 8. 制約遵守の実測

- 変更したのは `src/yowayowa/cli.py` の screen 描画ブロックと、新規テスト1ファイル、本ドキュメントのみ。`screening_cli.py` / `cli_entry.py` / `domain.py` / `api/routes.py` / `services/research_ask.py` / `services/research_brief.py` は**本カードでは触っていない**（作業ツリー上のこれらは他カードの未コミット差分で、着手前から存在する。例: `screening_cli.py` の +41 行は候補日付表示カード t_8b9aa12f の差分）。
- 数値の算出（`services/screening.py` の `derived_metrics` / `screen`）、フィルタ判定、API スキーマ、DB、code 表記、欠測判定は無変更。API 応答も不変（同じ curl で 200・同じ2銘柄・同じ36指標を実測）。
- 実API実走は**読み取りのみ**。`screening-run`・原本DB `data/yowayowa.db` への書込・内蔵LLM・新データソース・外部送信は無し。実走用DBは scratch の `r11_live.db`。
- R1変換 / R4通貨 / R10リスク / R3削除 / 候補日付表示 / CUSUM / 運用費用の再実装はしていない。
- commit / push はしていない。TSUKUMO 検証ツリーは**着手前へ復元済み**（`cli.py` sha256 `89bbf13…` に復帰、新規テストを削除、`git status` 13件で着手時と一致）。
- ブラウザ/UI 変更は無い（本件は CLI 表示のみ）。

## 9. 未達・限界（正直な記録）

- `make verify` 全量は**未合格**（既存 crypto 2件で rc=1）。GitHub CI・本番Vercel確認は本カードでは実施していない（commit/push 禁止のため）。
- 端末幅は rich の `COLUMNS` で 80 / 120 を実測。それ以外の幅（例: 40列）での fold 挙動は個別実測していない（fold の仕組みは同じ `overflow="fold"`）。
- `Value` の表記は API の JSON 表記（`repr`）をそのまま使う。したがって float の `0` は `0.0` と表示される（整数で来た `0` は `0`）。「ゼロを `unavailable` にしない」ことは満たすが、桁区切りや単位付与は行わない（単位根拠が応答に無いため）。
- 36指標×銘柄数だけ行が増えるため、多数銘柄では縦に長い（カードの要求どおり1指標1行を優先した）。ページングや絞り込みは本カードの範囲外。
- 証跡は scratch `/root/.hermes/profiles/worker-goat/cache/scratch/r11/`（24h で消える前提）: `screen_fixture.json` / `render.py` / `render_before.py` / `render_live.py` / `render_live_unavailable.py` / `verify_live.py` / `before_*.txt` / `after_*.txt` / `live_*.txt` / `live_screen.json` / `live_unavailable.json`。
