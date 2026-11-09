# S-07 X3 独立レビュー（受入判定）

- レビュー者: audit-cld（実装者 worker-terra・テスト作成者とは別系統）。カード `t_f89a4acd`／元タスク `t_29248fff`
- 日時: 2026-11-09 JST
- 対象: worktree `/root/current-work/yowayowa-investor/.worktrees/t_s07x3`、branch `agent/s07-prod-runner`、HEAD `d7ad9f7`（X3 の成果物はすべて未追跡・未コミット。`git diff HEAD --stat` は空）
- 対象ファイルの SHA-256（`sha256sum -c docs/evidence/s07_x3_forward_20261109/protected_paths.sha256` で全件 OK を確認）:
  - `src/yowayowa/hl_exec/s07_forward/runner.py` `34ab6451…97cc`
  - `src/yowayowa/hl_exec/s07_forward/order_gate.py` `ece690ff…b6b8`
  - `tests/test_s07_runner_acceptance.py` `2daad5e4…19e1`、`tests/test_s07_direct_path_evidence.py` `c6198b28…2d52`
- 判定基準: 元タスク本文の完了条件と、カード上の COO 裁定3件（dry-run 専用・実送信器は未接続・sender 未注入なら fail-closed）
- レビュー者はコードを修正していない。書いたのはこのファイルだけ（試験の出力は scratch に置いた）。push はしていない。

## 総合判定: **FAIL**（差し戻し。指摘は1件。修正すれば他の項目は PASS 相当）

不合格の理由は「同日2回で台帳1行・二重発注0」という冪等性の完了条件が、日付の書き方を変えるだけで破れることです（F-1）。ほかの項目は、レビュー者自身が実行して確かめた範囲で合格です。

## 項目ごとの判定

| # | 完了条件 | 判定 | 根拠 |
|---|---|---|---|
| 1 | 新 runner が旧 script と別実装 | PASS | 新パッケージ `src/yowayowa/hl_exec/s07_forward/`。旧 `scripts/hl_testnet_s07/s07_daily_forward.py` は import していない（`runner.py:45-48` の import は strategy・X1・order_gate・manifest だけ。`hl_testnet_s07` の出現は docstring の `runner.py:4` のみ）。旧 script は停止標識のまま（`s07_daily_forward.py:2,11-18`、exit 2） |
| 2 | 既定は dry-run | PASS | `run_day(enable_order_path=False)`（`runner.py:301`）。`_run_order_path` を呼ぶのは `runner.py:363` の `enable_order_path and sender_callable is not None` の分岐だけ。CLI のフラグは `store_true` で既定 False（`runner.py:392-396`） |
| 3 | 明示フラグ無しでは発注0 | PASS | フラグ無しでは、sender を渡しても呼ばれない（`test_sender_is_ignored_without_explicit_flag`）。`_run_order_path` の直接観測で `direct_path_calls=0`。フラグ有りでも sender 未注入なら、決定・台帳より前に `NotImplementedError` で止まる（`runner.py:343-348`）。Mock 以外の sender は拒否（`order_gate.py:51`）。CLI から sender を渡す手段は無い（`runner.py:418-424`）。CLI に `--enable-order-path` を付けた実行も、レビュー者の実測で `S07_FORWARD_ABORTED: NotImplementedError`、ledger・orders とも作られなかった |
| 4 | X1 起動時ゲート | PASS | 決定・台帳・発注より前に `_authenticity_check` を呼ぶ（`runner.py:340`）。`offline=True` 以外は拒否（`runner.py:338`）。`exit_code==0` かつ `verdict=="PASS"` 以外と例外はすべて停止（`runner.py:170-180`）。テスト: `test_x1_failure_stops_before_decision_ledger_and_order`、`test_cli_x1_failure_returns_nonzero`、`test_verification_failure_is_fail_closed`、`test_non_offline_authenticity_is_refused` が GREEN |
| 5 | 台帳は追記専用・1日1行・冪等 | **FAIL** | `O_APPEND`（`runner.py:207`）、重複 day の拒否（`runner.py:197-199`）、同日の再実行では追記しない（`runner.py:355-358`）。ここまでは正しい。ただし day キーを正規化していないため、同じ UTC 日が別の行として記録される → **F-1** |
| 6 | 不明注文は再送しない・取消確認後のみ再送 | PASS（条件付き） | `unknown/pending/missing/unverified/in_flight`・未指定・未知の値はすべて拒否（`order_gate.py:35-37,114-162`）。構造化された証拠（order_id・cancel_confirmed_at・remaining_qty・source=reconciliation）だけを verified として扱う（`order_gate.py:67-104`）。送信直前に `O_EXCL` マーカーを作るため、同じ day キーでの再送は不可能（`order_gate.py:198-212`）。条件: 裸の文字列 `"cancel_confirmed"` でも `authorized=True` になる（`order_gate.py:122-136`、`verified=False` と記録される）。テストダブルにしか届かないので今回は許容する。ただし実送信器を接続するカードでは、この分岐を削除するか拒否に変えることを必須条件にする（F-3） |
| 7 | dry-run E2E ログ | PASS | レビュー者が CLI を実行（入力は合成 market と X1 保存済み fixture、出力は scratch）。1回目 `rc=0 status=appended signal=HOLD`、2回目 `rc=0 status=already_recorded`、`ledger.jsonl` 1行、ledger とレポートを `cmp` してバイト一致、`orders/` は作られなかった。実装者の TSUKUMO 実測（保存済み testnet 市場データ、signal=SHORT）は `docs/s07_x3_evidence.md:9-18` |
| 8 | RED→GREEN | PASS（限界あり） | レビュー者が再実行: `S07_NEGATIVE_CONTROL=1 … test_no_flag_direct_path_not_called` で exit 1（`AssertionError: calls=1`）、元の実装では 26 passed。限界: RED は実装前の履歴ではなく、メモリ内で変異させた負の対照（`tests/test_s07_direct_path_evidence.py:43-49`）。COO 裁定のもとで許容する。なお `test_s07_runner_acceptance.py` と `test_s07_forward_dryrun.py` が監視している `order_path` は、`run_day` からは呼ばれない死蔵関数（`runner.py:56-65`）。この2件は単独では非呼出の証明にならない（F-2） |
| 9 | 実注文0・署名0・鍵非接触 | PASS | `s07_forward/` に `eth_account/private_key/signing/Exchange/requests/httpx/urllib/socket/secret/mnemonic` の出現は0件（grep exit 1）。X1 は `offline=True` を強制し、`HyperliquidInfoClient` を生成するのは `offline=False` の分岐だけ（`s07_authenticity/runner.py:109,193-215`）。`eth_account` を import するのは `HyperliquidExchangeClient.__init__` の中だけ（`client.py:254,284`）。その生成を例外にしても実 X1 経路は完走する（`test_run_never_constructs_exchange_client` GREEN）。全テストでソケット接続を禁止したうえで実行。限界: 推移的依存まで含めた動的監査ではない |
| 10 | 停止標識・既存台帳・cron は変更なし | PASS | 本体 repo の SHA: 旧 script `88f5b3d6…2338`、既存台帳 `c7bd598c…6870`。どちらも基準値と一致。tracked の diff は空。crontab 全体の SHA `7205eb17…cef` は実装者の記録と一致し、`s07` の出現は0件。Hermes cron `12f8248e71a5 s07-testnet-forward-daily` は `enabled=false / paused`（paused_at 2026-11-09 08:03 JST、X3 着手前）。script `s07_testnet_daily.sh` は旧 script を指したままで、mtime は 2026-11-03。`jobs.json` 内の `s07_forward` は0件 |

## 指摘事項

### F-1（ブロッカー）day キーを正規化していないため、同じ UTC 日に台帳2行・mock 送信2回が起きる

- 場所: `runner.py:311`（`dt.date.fromisoformat(day)` で検証するだけで、正規化した値を使っていない）。生の `day` 文字列を `runner.py:355`（既存行の照合）、`_decide`（`runner.py:154` の `"day": day`）、`order_gate.claim_submission`（`order_gate.py:201` の `f"{day}.submitted"`）、レポート名（`runner.py:216`）に使っている。
- Python 3.11 以降の `date.fromisoformat` は `20261108`（基本形式）なども受け付ける。そのため `--day 2026-11-08` と `--day 20261108` は同じ日なのに、別のキーとして扱われる。
- 再現1（CLI・dry-run・同じ出力先）: 1回目 `--day 2026-11-08` → `appended`、2回目 `--day 20261108` → `appended`（rc=0）。`ledger.jsonl` が2行になり、day は `"2026-11-08"` と `"20261108"`。`reports/` にも `2026-11-08.json` と `20261108.json` の2件。
- 再現2（Python API・フラグ有り・Mock sender・`order_status="cancel_confirmed"`）: レビュー者の probe（scratch の `test_x3_day_format_probe.py`。合成データ・ネットワーク禁止）で `statuses=['appended','appended'] ledger_days=['2026-11-08','20261108'] sender_calls=2 markers=['2026-11-08.submitted','20261108.submitted']`。**O_EXCL による二重送信防止もすり抜ける**。
- なぜ違反か: 完了条件「1日1行・同日2回で追記1行・二重発注0」は、同じ暦日について守られなければならない。cron のラッパーや手動実行で書式が揺れると成立しない。将来実送信器を接続すると、実際の二重発注に直結する。
- 修正の最小要件: 入口で `day = dt.date.fromisoformat(day).isoformat()` のように正規化する（または `^\d{4}-\d{2}-\d{2}$` だけを受け付けて他を拒否する）。台帳・レポート・orders マーカー・決定のすべてを正規化後のキーで扱う。回帰テストとして「`2026-11-08` → `20261108` の順で実行すると2回目が `already_recorded` になり、ledger 1行・sender 1回」を追加し、修正前のソースで RED になることも示す。

### F-2（非ブロッカー・整理推奨）死蔵の `order_path` と、それを監視するテスト

- `order_path` と `decide_and_maybe_order`（`runner.py:56-65`）は `run_day` からも CLI からも呼ばれない。`tests/test_s07_runner_acceptance.py:39-44,62-70`、`tests/test_s07_forward_daily.py:32-37`、`tests/test_s07_forward_dryrun.py` が「呼ばれない」と assert しているのは、実経路とは関係のない関数。実経路の保証は `test_s07_direct_path_evidence.py`（`_run_order_path` を直接ラップ）が担っている。誤解を招くため、監視対象を `_run_order_path` に揃えるか、死蔵関数を削除することを推奨する。

### F-3（非ブロッカー・後続カードへの条件）裸文字列 `"cancel_confirmed"` での許可

- `order_gate.py:122-136` は、呼び出し側が渡した文字列だけで `authorized=True` を返す（`verified=False` として記録はされる）。現状は Mock にしか届かないので受入に影響しない。ただし実送信器を接続するカード（独立裁可の後）では、この分岐を拒否に変え、`verified=True` の構造化証拠だけで再送を許すことを受入条件に入れること。

### 参考（判定に影響しない）

- X1 が失敗したときも、出力先に `.s07-forward-isolated` と `lock` は作られる（実装者も `docs/s07_x3_evidence.md:31` で開示済み）。
- フラグ有り・sender 有りで signal が HOLD の場合、`resend_blocked/signal_not_actionable` が記録され、status は `order_blocked` になる。CLI からは到達しない。

## レビュー者が実行したもの（read-only。出力はすべて scratch `/root/.hermes/profiles/audit-cld/cache/scratch/x3rev/`）

```
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_s07_runner_acceptance.py tests/test_s07_forward_runner_guards.py tests/test_s07_direct_path_evidence.py tests/test_s07_forward_daily.py tests/test_s07_forward_dryrun.py -v -s -p no:cacheprovider --basetemp=<scratch>/green
→ 26 passed in 1.23s (exit 0)
S07_NEGATIVE_CONTROL=1 … pytest tests/test_s07_direct_path_evidence.py::test_no_flag_direct_path_not_called
→ AssertionError: calls=1 / 1 failed (exit 1)  ← 負の対照（RED）
ruff check src/yowayowa/hl_exec/s07_forward → All checks passed!
mypy --follow-imports=skip src/yowayowa/hl_exec/s07_forward → Success: no issues found in 3 source files
CLI e2e (2026-11-08 ×2) → rc=0 appended / rc=0 already_recorded, ledger 1行, cmp ledger==report
CLI --enable-order-path → S07_FORWARD_ABORTED: NotImplementedError, ledger/orders なし
CLI 2026-11-08 → 20261108（同一出力先） → appended / appended, ledger 2行  ← F-1
probe（API・Mock sender）→ sender_calls=2, markers 2件  ← F-1
sha256sum -c protected_paths.sha256 → 全件 OK、tests/fixtures の git status 0件
```

（補足: 最初のループ実行では、終了コードを `echo` の後で読んだため `--enable-order-path` の行が `rc=0` と表示された。これは集計方法の誤りである。新しい出力先で単独に再実行し、`$?` を直接読んだところ **flag_rc=2** で、出力先には `.s07-forward-isolated / authenticity / lock` だけがあり ledger・orders は無かった。同じ方法で通常の再実行を読むと rerun_rc=0。）
