# S-07 X3 F-1 修正証跡（暫定版・後続修正前の記録）

マスター起票の `t_d6939a16` が厳格な日付形式を担当中。この記録は同カードの修正前の試験に限る。テストから週日付受理ケースは後に除去しており、以下の SHA は当時の版である。

独立レビュー `t_f89a4acd` は同日表記の相違により台帳と mock 送信が重複する F-1 を FAIL とした。`run_day` 入口で `date.fromisoformat(day).isoformat()` に正規化するよう変更。以後の決定・台帳照合・レポート・注文マーカーには同じ値を渡す。旧 script、既存台帳、cron は触らず、実送信器は未接続。

TSUKUMO の隔離コピー `/home/good-light/vps-work/sessions/t_4807e488/repo` で、合成入力、X1 の Mock、Mock sender のみで試験。修正前の元実装に新テストを実行した RED: `3 failed in 0.60s`, 3件とも `result2['status']` が `already_recorded` ではなく `appended`。対象は `2026-11-08→20261108`、逆順、`2026-W45-7→2026-11-08`。

修正後:
```
PYTHONPATH=src ~/vps-work/venvs/yowayowa-17b215b/bin/python -m pytest tests/test_s07_day_normalization.py tests/test_s07_direct_path_evidence.py tests/test_s07_runner_acceptance.py tests/test_s07_forward_runner_guards.py -q -p no:cacheprovider
........................                                                 [100%]
24 passed in 1.04s
```
新回帰テストは2回目 already_recorded、台帳1行（正規日付）、Mock sender 1回、submitted marker 1件を検証。新テスト単体の ruff は All checks passed。runner と新テストの SHA-256 は VPS worktree と TSUKUMO で一致:
```
runner.py e7992a5593dcdf1cc05abc2cf42e0f6587bfe69279bf94522d070caa53b89e72
test_s07_day_normalization.py e40ab471de3117c147e3efcbaf7a39ca5c0226a8eb609ce8480ab7f6502d821b
```
既存 runner を含む現地 ruff は既存の RUF100 1件・TRY004 3件で失敗（今回の1行修正とは別）。

注意: 過去の欠陥版で既に非正規表記の ledger 行や送信マーカーを生成した出力ディレクトリの移行・再利用は検証していない。この修正の試験は新規隔離出力から開始。既存の実台帳を編集しない。再レビューでは旧状態を使い回した場合の安全性も確認し、必要なら使用拒否を要求すること。合成文字列 cancel_confirmed は実取消証拠ではなく、実送信器接続時に拒否する必要がある（レビュー F-3）。X1 の保存済み実データ検証は現在の口座状態を保証しない。
