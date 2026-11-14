# t_f840d5bc 検証・引継ぎ

## 結論

採否とローカル整理を完了し、独立レビューに提出する。全体の `make verify` は**緑ではない**。ただし失敗9件は変更前snapshotでも同じnodeidで再現し、今回のHL関連試験は本物SDKを含め128件すべて成功した。全体CI/productionの合格や実注文再開を主張しない。

採否正本: `docs/HL_EXEC_DISPOSITION_20261108.md`。

## 実装コミット

- `e82f90d4abb053c139c7b6b6783b8b84904d3489`: orders/journalのformatのみ。
- `ceb90004d1feceb477577eeb832bcc6a685eac3c`: reduce_onlyを採用、TypeError後の再送を撤去、mock署名修正、SDKあり/なしの試験境界。
- `79072c819417bb2c9cfeb96a44eef3e7c7bc605f`: 不採用コード/旧テストの履歴退避、外部cron入口の停止標識、runbook修正、古い証跡の追跡化。
- 本検証ログは後続の証跡コミットで保存する（架空のcommit値を記載しない）。

共有checkoutではこの後にもC2等の別カードのcommitが入っている。検収対象は上記コミットと本証跡であり、他workerの未コミット差分を取り込んでいない。

## 環境と照合

重い検証はすべてTSUKUMO-KUN。専用snapshotは `/home/good-light/vps-work/sessions/t_f840d5bc/repo`、変更前snapshotは同親の `baseline`（d13fde6727f1c414f94bf611b1ae74a2b585c7cd）。実データ/鍵をコピーせずGit tracked treeを展開し、候補のコード/非実行アーカイブをoverlayした。9コード/設定ファイルのSHA256は作業checkoutと専用snapshotで完全一致（`checks/code-local.sha256`）。

基本共有venvにdeclared dev dependencyのopenpyxlが欠けていたため、共有環境には手を加えず、`UV_PROJECT_ENVIRONMENT=<専用verify-venv> uv sync --frozen --extra dev --extra operator-ir --no-install-project` で全検証用の環境を作成した。SDKは別venvへ導入し（hyperliquid-python-sdk 0.24.0）、共有ツールPythonのPYTHONPATHにそのsite-packagesを明示したオフライン試験でのみ使用した。

## 実測

| 検証 | 実測結果 | 証跡 |
|---|---|---|
| 旧TypeErrorフォールバックに新回帰テストを実行 | 想定どおり3 failed。reduce_only=False/Trueで呼出し2回、旧mockでflagを落として成功 | `checks/red.txt` |
| 停止標識の負の対照（入口未設置） | 2 failed / 1 passed。説明付き停止がまだ無いため失敗 | `checks/disposition-red.txt` |
| 修正後HL一式、本物SDKのwire試験込み（frozen-lock検証venv＋SDK） | **128 passed in 3.02s**（共有ツールvenvでも128 passed in 3.22s） | `checks/hl-suite-frozen-sdk.txt` / `checks/hl-suite-final.txt` |
| make lint | 成功、**482 files already formatted** | `checks/make-verify.txt` |
| make typecheck | 成功、**259 source files** | 同上 |
| make verify（専用frozen-lock環境） | **9 failed, 3542 passed, 16 skipped, 1 xfailed, 182 warnings in 3448.81s (57:28)**。make exit2 | 同上 |
| 変更前d13fde6の同5ファイル | **9 failed, 85 passed in 44.68s**。pytest exit1 | `checks/baseline-failures.txt` |
| FAILED nodeid一覧の比較 | 両方9行、diff exit0、完全一致 | `checks/*.nodeids.txt` |
| make openapi openapi-check（pytest失敗で未到達となる項目を別実行） | exit0、**161 operations**のbaseline一致 | `checks/openapi.txt` |
| 実在する旧cron wrapperの --dry-run | exit2、stdout空、DISABLED理由をstderrへ表示 | `checks/legacy-wrapper.stderr.txt` |

本物SDK試験は実際の `order` → `bulk_orders` → wire serializerを通し、署名関数と送信だけをテストdoubleへ置換した。socket接続も拒否し、wireの `r` がFalse/Trueを保つことを検証。新しい鍵や署名は作成していない。

旧ソース/旧テスト10ファイルは `rejected/SHA256SUMS.original` のhashをそのまま保持。`.txt`、mode644、元パスの排除（停止標識のみ例外）を新テストで確認した。履歴ソースのwhitespaceもbyte一致のため保持しており、通常のgit diff --checkはこの元のtrailing whitespaceを指摘する。実行コードにはこの例外を適用していない。

## 全体で残る9失敗（今回の変更前にも存在）

1. `test_backtest_p4_regressions.py` の2パラメタ: clean snapshotにgitignored split-calendarが無いため、正当なmissing-data warningが発生し `warnings == []` という暗黙前提に違反。
2. `test_c2_kill_switch.py` 1件、`test_c2_ledger.py` 1件、`test_c2_order_timing.py` 3件: `c2_runner.DATA_DIR` が実運用のVPSパスを指したままで、TSUKUMO上ではPermissionError。**この既存テストをVPS rootで実行しないこと。実台帳へ書く危険がある**。今回の専用snapshot実行ではPermissionErrorにより実パスへの書込は成立していない。
3. `test_db_units.py` の2件: 現行migrationに対するRuntimeError/OperationalErrorの期待が満たされない。

失敗を消すために実データをコピーしたり、危険なパスへの権限を開けたり、アプリの安全条件を緩めたりしていない。是正は別カード `t_0412664d`（worker-luna）へ渡した。

## 停止後の取引所状態（読み取りのみ）

2026-11-08T03:46:50Z、テストネットの `/info` に `clearinghouseState` と `openOrders` の2照会を行った。対象は旧S-07設定にあるテストネット口座。取得時点で:

- `assetPositions` は妥当な空配列。
- `openOrders` は妥当な空配列。
- `marginSummary.accountValue` は `99.790224`。

`jq -e` で型と空配列を検査した。`checks/testnet-*.json` と `testnet-readonly-at.txt` に保存。これは継続監視や現在の口座状態の保証ではなく、停止後照会時点の証跡である。mainnetへは送信していない。

## 境界・秘密情報

- 当カードは既存 `data/**`、state、実台帳、鍵、外部cron設定を変更しない。ローカルの重いテストは実行せず、専用snapshotのみで検証した。
- 実注文/取消/入出金/approveAgent/署名/鍵生成/実弾投入は0。当カードの取引所確認は上記testnet公開読取2件。依存取得・公式文書参照・SSH通信は別途実行した。mainnet送信は行っていない。
- pushは行っていない。追跡していたremote refは作業前後でbyte一致。CI/デプロイ確認のためのpushも行わない。
- 新規採用物を静的に確認し、private-key marker/0x+64hex/credential literalのパス限定走査を実施。ヒットは旧keygen内の公開secp256k1群位数定数のみ。鍵の実値は読まない制約のため、既存鍵との完全一致走査は実施していない。公開アドレス、hash、既知ベクタを秘密鍵と混同しない。
- 旧証跡中のPASS件数は履歴であり、現在の安全性として再認定していない。

## 後続とレビュー

- `t_38d18e3e`: 旧S-07経路/cron状態の確認、SDK cloid型、厳密testnet allowlist、不明注文状態の照合、オフラインの置換候補。停止標識解除と実注文は禁止のまま。
- `t_0412664d`: 上記9件のテスト隔離/fixture/DB契約の是正。
- reviewerは別系統の `audit-glm-heavy` を指定する。停止という運用変更、非実行アーカイブの採否、reduce_onlyが落ちないこと、9失敗のbaseline一致を独立に確認してほしい。
- 専用TSUKUMO領域は約812MB時点で確認。独立レビューで再利用するためsnapshot/環境を保持している。検収終了後に担当が不要な検証venv/一時fixtureを整理すること。

hotspot: `src/yowayowa/hl_exec/orders.py`, `tests/test_hl_exec_journal.py`, `tests/test_hl_exec_roundtrip.py` はt_28d6394aと重複したため当カード担当を連絡し、相手のformat commit/revert後にHEADを再読してから処理した。
