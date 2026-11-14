# US株デモの評価バー遅延（1営業日）— 原因と修正

タスク: t_bcc12803（親: t_dddae39e の収益検証で観測した実行不足）
対象: `ops/demo-loop/daily.sh` の `stock-fetch`（既存経路の調整のみ。新しいデータ源は追加していない）

## 結論

`providers/alpaca.py` の取得窓が **「今日 − 2日」を日付だけ（`end=2026-10-21`）で送っていた**ため、
毎回の日次実行で1営業日ぶん古いバーしか取れていなかった。

Alpaca は日付だけの `end` を **US/Eastern のその日の終わり**として解釈する。
日次実行は UTC 00:20（= ET 前日 20:20）なので、「昨日」を渡すと ET の翌日 00:00、
つまり **未来の瞬間**になり、無料プランは `403 subscription does not permit querying recent SIP data` で拒否する。
その 403 を避けるために −2日 にしていた結果、前営業日のバーが丸ごと落ちていた。

修正は「窓の終端を **明示的な UTC 時刻**（現在 − 30分）で送る」こと。
日付だけの `end` をやめれば未来解釈が起きないので、00:20 の実行でも前営業日の確定バーが取れる。

## 実測（before）

本番ストア `data/stock-ohlcv/SPY/ohlcv.jsonl`（修正前の実行結果そのもの）:

| 実行（retrieved_at） | 最新 as_of | 状態 |
|---|---|---|
| 2026-10-23T00:21:03Z | 2026-10-21T04:00:00Z | 10/22 セッション（20:00Z 終了）が未取得 = 1営業日遅れ |
| 2026-10-22T00:21:08Z | 2026-10-20T04:00:00Z | 10/21 セッションが未取得 = 1営業日遅れ |

台帳（`data/demo-loop/journal.jsonl` の mtm 行）でも同じ遅れが出ている。
`price_bar_date` と実行時刻から算出した「確定済みセッションに対する遅れ」（0 = 最新）:

```
book      | run(recorded_at)          | price_bar_date | session lag
A         | 2026-10-23T00:21:06+00:00 | 2026-10-22     | 0
CRYPTO_1N | 2026-10-23T00:21:06+00:00 | 2026-10-22     | 0
C         | 2026-10-23T00:21:06+00:00 | 2026-10-21     | 1
EQUITY_EW | 2026-10-23T00:21:06+00:00 | 2026-10-21     | 1
```

同じ実行の中で暗号資産だけ前日バー、株（帳簿C / EQUITY_EW）だけ 1営業日遅れ。
10/20〜10/23 の毎回で C は lag 1 が続いていた（A・CRYPTO_1N は 0）。

## 実測（原因の切り分け: 2026-10-23 01:01Z のライブ実測）

`GET /v2/stocks/bars?symbols=SPY&timeframe=1Day&feed=sip&adjustment=raw&start=2026-10-19`

| end に渡した値 | HTTP | 返ってきた最新バー |
|---|---|---|
| `2026-10-23`（日付のみ） | 403 recent SIP data | — |
| `2026-10-22`（日付のみ） | 403 recent SIP data | — |
| `2026-10-22T23:59:59Z` | 200 | 2026-10-22T04:00:00Z |
| `2026-10-22T00:00:00Z` | 200 | 2026-10-21T04:00:00Z |
| `2026-10-22T03:59:59Z` | 200 | 2026-10-21T04:00:00Z |
| `2026-10-22T04:00:01Z` | 200 | 2026-10-22T04:00:00Z |
| `2026-10-21`（日付のみ） | 200 | 2026-10-21T04:00:00Z |
| `now − 14分57秒` | 200 | 2026-10-22T04:00:00Z |
| `now`（= 01:01:07Z） | 403 recent SIP data | — |

読み取れること:

1. 日付だけの `end` は ET の日末として解釈される（`2026-10-22` は 200 になる `2026-10-22T23:59:59Z` と同値のはずだが 403）。
   つまり ET 翌日 00:00 = まだ未来 → 「最近のデータ」扱いで拒否。
2. 拒否の境界は現在時刻から約15分（`now − 14分57秒` は 200、`now` は 403）。

## 変更内容

- `src/yowayowa/providers/alpaca.py`
  - 窓の終端を `now − 30分` の **明示的な UTC 時刻**で送る（日付だけにしない）。
  - `403 recent SIP data` だけを `AlpacaRecentSipError` として拾い、窓を 2h → 8h → 26h と段階的に古くして再試行する
    （最後の段でも旧挙動より古くはならない）。他の 403（権限不足など）は再試行せず即座に失敗させる。
  - セッションがまだ閉じていないバー（当日の途中経過）は永続化しない。
    確定バーが1本も無ければ `LookupError`（データを作らない）。
  - 時計は `_utc_now()` を経由させ、テストが固定時刻を注入できる。
- `src/yowayowa/trading_sessions.py`（新規）
  - NYSE の定期休場日カレンダー（既存 `ohlcv_coverage` から移設し、定義を1箇所に）、
    `session_close_utc` / `is_finalized_bar` / `last_finalized_session` / `session_lag`。
  - `ohlcv_coverage` はこのモジュールから休場日を取るように変更（判定ロジックは不変）。
- `src/yowayowa/stock_acquisition.py`
  - `persisted_freshness()`（最新バーの日付と `session_lag` を返す読み取り専用ヘルパ）。
- `src/yowayowa/stock_cli.py`
  - `stock-fetch` の出力に「最新バー日付」と「遅れセッション数」を追加。1以上なら `[LATE]` と警告行。
    日次 cron のログだけで遅延日を判別できる（スキーマ追加なし。mtm 側の `price_bar_date` もそのまま使える）。
- `ops/demo-loop/daily.sh`
  - `--days 5` → `--days 10`（連休＋祝日でも前営業日が窓に入る。リクエスト数は増えない・再取得は冪等）。

凍結行・戦略パラメータ・コスト率・paperloop.py には触れていない。実発注・送金なし。

## 実測（after: 修正コードでのライブ取得）

修正ツリーのコードを、実 API + スクラッチのストア（本番 `data/` は触らない）に対して実行:

```
2026-10-23T01:23:04Z  tree=.../impl/src/yowayowa/__init__.py
SPY/alpaca: persisted 7 rows, newest bar 2026-10-22, lag 0
IWM/alpaca: persisted 7 rows, newest bar 2026-10-22, lag 0
JNJ/alpaca: persisted 7 rows, newest bar 2026-10-22, lag 0
PG/alpaca:  persisted 7 rows, newest bar 2026-10-22, lag 0
+28 rows
newest: SPY as_of 2026-10-22T04:00:00Z retrieved_at 2026-10-23T01:23:07.154139Z
        IWM as_of 2026-10-22T04:00:00Z retrieved_at 2026-10-23T01:23:07.392279Z
        JNJ as_of 2026-10-22T04:00:00Z retrieved_at 2026-10-23T01:23:07.739243Z
        PG  as_of 2026-10-22T04:00:00Z retrieved_at 2026-10-23T01:23:08.138134Z
```

同じスクリプトの2回目は `persisted 0 rows`（再取得は冪等、重複行なし）。

## 受入の状況

1. **連続2営業日、前営業日バーが取れた実測 — 未達（時刻待ち）。**
   修正コードで「前営業日バー（10/22）が lag 0 で取れる」ことは上で実測済みだが、
   *日次 cron が本番経路で2営業日続けて* 取れた実測は、その日時が来るまで存在しない。
   次回 2026-10-23T00:20Z（この実行は修正前コード）／修正反映後の 2026-10-26T00:20Z・10-27T00:20Z で確認する。
   待つだけで枠を燃やさない方針のため、ここは未達と明記して実装と限定検収を先に進める。
2. **取れなかった日の挙動（遅延の可視化）— 実測済み。**
   上記 mtm 表（C/EQUITY_EW が lag 1、暗号資産が 0）と、`stock-fetch` の `newest bar / lag / [LATE]` 出力。
3. **demo-loop 検証（paperloop_verify L1–L14）— 緑のまま。**
   `python -m yowayowa.paperloop_verify --data-dir ./data --from 2026-10-16 --to 2026-10-23`
   → exit 0、`chain_ok=true`、`anchors_ok=true`、L1–L14 すべて `ok`、R1/R2 20/20、
   `commit_before_fill_violations=0`。
4. **限定検収** — 別系列の役員レビューへ（reviewer 指定）。

## 検証コマンド

```
PYTHONPATH=<worktree>/src pytest tests/test_trading_sessions.py tests/test_stock_ohlcv.py   # 59 passed
PYTHONPATH=<worktree>/src pytest tests/test_ohlcv_coverage.py tests/test_backtest_p4_regressions.py \
    tests/test_strategy_signal_quality.py tests/test_strategy_signal_quote_calendar.py        # passed
ruff check src tests   # 変更ファイルに指摘なし（既存の指摘33件は未変更ファイル: paperloop*, loop_cli, test_paperloop*）
mypy src/yowayowa      # 変更ファイルに指摘なし（既存31件は未変更ファイル）
```

## 残作業（引き継ぎ）

- このブランチを `agent/commercial-foundation` に載せる（稼働中の本番ツリーを直接書き換えない方針のため、マージは裁可後）。
- 週次サマリーの比較に「遅延日」を残す処理（帳簿C/EQUITY_EW）は親カード t_dddae39e 側。
  ここで用意した `session_lag()` と mtm の `price_bar_date` だけで遅延日は算出できる（スキーマ追加不要）。
- 祝日カレンダーは定期休場のみ。緊急休場は「1セッション遅れ」として出るので、その日は目視確認が要る。
