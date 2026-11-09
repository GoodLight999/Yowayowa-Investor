# S-07 X3 本番代替 runner（dry-run 既定）実測記録

- 日時: 2026-11-09 14:44 JST
- worktree: `/root/current-work/yowayowa-investor/.worktrees/t_s07x3`
- ブランチ: `agent/s07-prod-runner`（HEAD `d7ad9f7`）
- 状態: 未コミット・未 push（COO 裁定）。カード `t_38f4414f`。

## 実装

- 入口: `PYTHONPATH=src python -m yowayowa.hl_exec.s07_forward.runner`（契約 `docs/s07_x3_contract.md`。
  repo 慣例どおり `PYTHONPATH=src` を付ける。`--help` は exit 0 を実測）
- 新規: `src/yowayowa/hl_exec/s07_forward/{__init__.py,runner.py,order_gate.py}`
- 契約のテスト用インターフェース: `run_day(..., enable_order_path=False, sender=None, order_status=None)`
- 不変更: 旧 `scripts/hl_testnet_s07/s07_daily_forward.py`（停止標識）、本番台帳
  `docs/evidence/hl_testnet_s07_forward_20261103/daily_ledger.json`、cron。

## 安全構造（すべて fail-closed）

1. **X1 先決**: 決定・台帳・発注の前に `s07_authenticity.runner.run` を
   `out_dir=<out>/authenticity`・`offline=True` で呼ぶ。`exit_code != 0` または
   `verdict != "PASS"` なら `ValueError`（CLI は exit 2）。`offline=True` 以外も拒否。
2. **既定 dry-run**: `enable_order_path` が無ければ `order_path` を呼ばない。
3. **sender 必須**: フラグだけ（sender 無し）は `NotImplementedError` で停止し、台帳にも
   何も書かない。CLI は sender を注入しないため、CLI から実送信経路へは到達できない。
4. **テストダブル限定**: `unittest.mock.Mock` 以外の sender は `OrderGateError`。
5. **不明注文は再送しない**: 裸の `"cancel_confirmed"` は `verified=false` /
   `kind=synthetic_test_only` として記録し、実取消証拠として扱わない。構造化証拠
   （`order_id` / `cancel_confirmed_at` / `remaining_qty` / `source=reconciliation`）だけを
   `verified=true` とする。
6. **二重送信防止**: 送信直前に `orders/<day>.submitted` を `O_EXCL` で作る。既存なら再送しない。
7. **台帳**: `ledger.jsonl` は追記専用・1 日 1 行。同日再実行は追記なし・発注なし
   （レポート欠損のみ復旧）。
8. **出力隔離**: 既存の空ディレクトリのみ、symlink 拒否、入力・repo `data` との重なり拒否、
   `flock`、`O_NOFOLLOW`。

## 実測値

| 対象 | コマンド | 結果 |
| --- | --- | --- |
| 受入テスト | `pytest tests/test_s07_runner_acceptance.py` | 8 passed |
| runner 4 ファイル | `pytest tests/test_s07_runner_acceptance.py tests/test_s07_forward_daily.py tests/test_s07_forward_dryrun.py tests/test_s07_forward_runner_guards.py` | 23 passed |
| HL 一式 | `pytest $(grep -rl hl_exec tests --include='*.py')` | 313 passed |
| lint | `ruff check src/yowayowa/hl_exec/s07_forward tests/test_s07_forward_runner_guards.py` | All checks passed |
| 型 | `mypy --follow-imports=skip src/yowayowa/hl_exec/s07_forward` | 3 files, no issues |
| 送信系クライアント不生成 | `HyperliquidExchangeClient.__init__` を例外化した実 X1 オフライン実行 | 0 回生成で `appended` |
| 禁止 import 走査 | `grep -rniE "eth_account\|private_key\|signing\|hyperliquid\.exchange\|secret\|mnemonic" src/yowayowa/hl_exec/s07_forward/` | 該当なし |
| 保護パス | `sha256sum -c protected_paths.sha256` | 旧 script・既存台帳とも OK |
| tracked diff | `git diff --stat` | 空 |
| cron | `crontab -l \| grep -i s07` | 0 件 |

生ログ: `ev_acceptance.log`, `ev_runner_all.log`, `ev_hl_suite.log`,
`git_cron_state.txt`, `protected_paths.sha256`。

## 既知の限界

- 実送信アダプタは存在しない（契約どおり）。再送は注入された mock への 1 回だけを実行する。
- 合成 `"cancel_confirmed"` 文字列は実取消証拠ではない（`verified=false`）。実運用の再送判断には
  構造化証拠の照合実装が別途必要。
- 未コミット・未 push（COO 裁定）。
