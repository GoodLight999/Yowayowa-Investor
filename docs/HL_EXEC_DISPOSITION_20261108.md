# HL/S-07未コミット差分の採否（t_f840d5bc）

2026-11-08。ローカル限定の整理。push・本番デプロイ・実注文・取消・送金・署名・鍵生成は行わない。`data/**`・既存state・実台帳・既存鍵・外部cron設定は変更しない。

## 決定

| 対象 | 処理 | 根拠 |
|---|---|---|
| `src/yowayowa/hl_exec/client.py` の `reduce_only` | 採用。ただしTypeErrorフォールバックは削除 | SDKが受理後にTypeErrorを出す可能性を区別できず、二重発注とreduce-only解除を招く。例外をそのまま上位へ返し、送信は1回。mockをSDK契約へ修正する |
| `orders.py`、journal/roundtripテストのformat | 採用、意味変更なし | Ruffで整形。roundtrip mock署名へのreduce_only追加は契約修正として別扱い |
| `scripts/hl_exec_phase3/` と旧鍵生成テスト | 実行コードとして不採用 | 既存SDK/eth-accountがあるのに自作Keccakを維持する必要がない。sidecar既存/同一パス/symlinkを安全に扱わず、秘密鍵ファイルを上書き/緩いmodeへ変える経路がある。旧PASS件数では採用できない |
| `scripts/hl_testnet_s07/` と旧forwardテスト | 旧実装は不採用 | 中立時SHORT強制、未確定足/他環境足の混用、dry-runが実台帳に混入、部分約定/通信断/決済失敗の未解決、成功時しか残らないraw events、欠損を0扱い。戦略・統制の安全性を満たさない |
| `s07_daily_forward.py` の元パス | 停止標識だけ採用 | 外部wrapperがこのパスを呼ぶため、無言の消失でなく理由をstderrへ出してexit2。引数なし/`--dry-run`とも鍵・ネットワーク・ロック・台帳に触れず拒否する。代替運用の実装ではない |
| `scripts/gates/g2_walkforward.py` | 不採用、履歴保存 | 特定workspaceのadapter/入力に依存。年率Sharpeを日次PSR/DSR分散式へ渡す実装、重複OOS窓、日付整列不足、短い入力の窓参照などが未検証。既存G2判定を再実行・上書きしない |
| `scripts/verify_mainnet_state.py` | 不採用、履歴保存 | 固定個人口座、欠損を残高0/建玉0へ補完し、条件未達でもexit0。確認できない状態を安全と表示し得る |
| Phase3/S-07の証跡ディレクトリ | 履歴として追跡化 | 原記録は保持。READMEで旧PASSと現行検収を分離。実口座・運用台帳を再照会/再評価していない |
| approveAgent/mainnet runbook | 修正して採用、旧版も履歴保存 | 接続ウォレットと承認対象口座の一致、予定エージェントの照合を要求。「出金不可=資金安全」、USDC安定の断定、損失上限保証、古い送金先のコピペ指示を撤回 |
| `EVIDENCE_T_FEB3AA55_ROUND2.md` / `AUDIT_G1_AND_STRATEGY_REVIEW_20261102.md` | 履歴として追跡化 | 現行の実測でないことを明示。C2/paperloopの実装や基準を本カードで変更しない |

## 保存方法

不採用の元ソース/テスト10ファイルは `evidence/hl_exec_disposition_20261108/rejected/*.txt` にbyte同一で保存した。`SHA256SUMS.original` は旧パスとhashの対応。実行bitを全て落とし、Python import/pytest収集対象から外した。旧runbookは同フォルダの `historical_runbooks/` に保存。

履歴は修正版として配布しない。退避コードを `.py` に戻したり、直接実行して旧自動運用を再開しない。これは不採用判断の監査証跡であり、未完成の運用実装を残したものではない。

## SDK境界の根拠と検証

既存の注文組立はすでに `reduce_only` を扱う。公式SDK `Exchange.order` にも同引数があり、wireの `r` へ変換される。互換用の別送信関数は設けず、テストdoubleを直す。

- 公式SDKソース: https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/exchange.py
- 注文API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
- agentと実口座の区別: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets
- `precedent-check` はこの環境で未インストール（exit127）。代わりにrepoの既存client/orders/testsと公式SDK実装を検索・確認した。鍵生成機能を新規自作しない判断とした。

TSUKUMOの専用snapshot `/home/good-light/vps-work/sessions/t_f840d5bc/repo` で検証する。共有venvの再syncは行わず、SDKは別venvへインストールした（0.24.0）。本物のorder/bulk_orders/wire変換を残し、署名・送信をテストdoubleへ置換、socket接続を拒否して `r=False/True` を確認した。

負の対照では旧フォールバックに対して3件失敗（TypeError後の呼出し2回×False/True、旧mockでreduce_onlyを落として成功）。修正後のHL関連一式は128 passed（2.99s、SDK試験含む）。新停止標識も引数2通りでexit2/明示理由/作業ディレクトリ書込0を確認。不採用10ファイルはhash一致・実行bitなし・元パスからの排除をテストする。

`make verify` の初回は既存のoptional SDK importが型検査で3件失敗したため、`pyproject.toml`で `eth_account` / `hyperliquid.*` だけをmissing-import許容にし、古いinline ignoreを削除した。アプリケーション本体のstrict検査は維持する。その後、共有venvに宣言済みの `openpyxl` が欠けていたため、共有環境は変更せず専用verify-venvをfrozen lockから作成して全検証を行う。

`make verify` の終端結果・ローカルcommit・残課題は本カードの検証報告に記録する。UI変更/本番接続解禁はなく、CI/production確認のためにpushすることはしない。

## 運用上の注意・後続

外部wrapper `/root/.hermes/scripts/s07_testnet_daily.sh` の存在を読み取りで確認した。現profileのcron一覧は0件だったが、他profileの現行登録状態まで証明したものではない。cron設定は本カードでは変更しない。wrapper経由でも停止標識で注文は発生しない。必要なら運用担当が該当cronを特定・停止し、最後の取引所状態を読み取りで確認する。

旧S-07の置換、公式SDKのcloid型への変換、接続先/注入clientの厳密allowlistと注文不明時の照合は、後続カード `t_38d18e3e`（worker-luna）で独立検収してから採用する。停止標識の解除と実注文は禁止のまま。今回の整理がこれらを修復済み、または実注文再開の許可であると解釈しない。

## 共有ファイルの調整

hotspot: `src/yowayowa/hl_exec/orders.py`, `tests/test_hl_exec_journal.py`, `tests/test_hl_exec_roundtrip.py` — t_28d6394aと重複。当カードで3ファイルを担当する旨を相手に通知。並行相手によるformat commit/revert後のHEADを読み直してから当カード差分を作成した。C2やpaperloopの他workerの未コミット変更は取り込まない。
