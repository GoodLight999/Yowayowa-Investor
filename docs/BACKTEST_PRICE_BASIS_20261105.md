# バックテスト成果物の価格 basis と分割補正 (2026-11-05, t_d02db084)

## 1. 決定（1行）

**P3 / S-03 / S-05 の tracked 結果JSONとレポートは、raw（未調整）由来の旧値から「分割補正済み同じ窓の再実行値」へ更新した**。raw 由来の旧値は git 履歴に残っており、消していない。

- old / new の sha256 は §4、raw→補正の全数値差分は §3、再現コマンドは §2。

## 2. 数値 basis（本文書と更新後の4レポートすべてに共通の1行）

> 数値basis: raw保存Alpaca日足にSEC法人格splitカレンダー（`data/fundamentals/pbr/split-calendar.json`, sha256 `ae7668f0…d522`, 7イベント）で**分割のみ補正**した価格（配当は含まない・TRではない）。評価窓 2021-01-04〜2026-09-28、コスト commission 10bps + slippage 5bps、bootstrap 1000回 seed `20260930`。

- 配当込みTR basis の G1/G2 数値は `docs/G1_GATE_20261102.md` / `docs/G2_GATE_20261102.md`（TSUKUMO `results_v6`・分割＋配当TR補正済み）。**TR込みの値は本カードの「分割のみ補正」値とは一致しない**（例: S-05 full MaxDD は TR込み −26.30% vs 本basis −22.01%）ので、basis を混ぜて比較しないこと。
- 生の（補正なし）値と補正済み値を混在した結果JSONは無い。syntheticな値は存在しない。raw の旧値は以下のgitコミットに残る: `backtest_results_p3.json` = `83699e3`、`backtest_results_s03.json` = `d1541f6`、`backtest_results_s03_supplied.json` = `0a05a17`、`backtest_results_s05.json` = `b6f2915`。

## 3. raw → 分割補正 の数値差分（full 2021-01-04〜2026-09-28）

| 戦略 | full CAGR raw → 補正 | full MaxDD raw → 補正 | full bootstrap 95% CI raw → 補正 |
|---|---|---|---|
| momentum_12_1 (P3/S-03 baseline) | 13.72% → **20.24%** | −34.64% → −31.13% | [−8.23, 40.73] → [−2.00, 47.49] |
| low_volatility | 13.54% → **16.85%** | −35.41% → −23.63% | [−4.15, 31.07] → [+1.15, 34.19] |
| mean_reversion_20 | 16.84% → 16.96% | −45.89%（不変） | [−5.32, 42.88] → [−5.31, 42.80] |
| equal_weight | 16.27% → **25.37%** | −50.21% → −35.50% | [−5.20, 41.00] → [+3.60, 50.22] |
| s03_garp（供給あり） | 13.44% → **17.73%** | −54.98% → −36.86% | [−9.44, 38.30] → [−2.84, 42.91] |
| s05_defensive_rotation | 4.43% → **13.25%** | −28.09% → −22.01% | [−12.53, 21.50] → [−2.75, 29.58] |
| s05_defensive_rotation_daily | −2.39% → +4.00% | −38.18% → −30.98% | [−17.25, 13.37] → [−10.87, 19.52] |
| s05_baseline_momentum_12_1 | 10.08% → 17.67% | −27.72% → −25.13% | [−7.89, 29.99] → [−0.30, 38.75] |
| s05_baseline_low_volatility | 8.64%（不変） | −23.20%（不変） | [−3.27, 20.40]（不変） |
| s05_baseline_ew | 16.84% → 20.48% | −24.16%（不変） | [+1.12, 33.39] → [+4.97, 36.90] |

備考:

- **OOS は全戦略で不変**（分割日がOOS窓 2026-01-09〜2026-09-28 に無い）。momentum OOS −6.60% / s05 +7.47% は raw・補正で同一。
- 「4戦略すべてのCIがゼロをまたぐ」→ 補正後は **momentum と mean reversion のみがゼロをまたぐ**（low-vol [−4.15,31.07]→[+1.15,34.19]、EW [−5.20,41.00]→[+3.60,50.22] はゼロを下回らなくなる）。full順位も「mean-rev > EW > momentum > low-vol」→「**EW > momentum > mean-rev > low-vol**」に入れ替わる。
- S-05 の必須(b)（full DD < 同一universe momentum）は分割のみの補正では **未達→達成に反転**（−22.01% vs −25.13%）。ただし配当込みTR basis（G1 実測 −26.30% vs −24.88%）では再び未達、G2 のW1窓勝率も未達のため、**S-05 の採否結論（不採用）は不変**。
- S-03 の判定(c)（full MaxDDがmomentumより浅い）は補正後も未達（−36.86% vs −31.13%）で**採用未承認は不変**。
- 信号汚染の実測: momentum の55リバランス中26回で選択銘柄が変わっていた（最大日次差 18.74pp、NVDA 10:1 の日 w=0.20）。詳細は監査 t_2fc44a51 の `AUDIT_SPLIT_CONTAMINATION_20261105.md` §5。
- `backtest_results_s02.json`（SPY/IWM/JNJ/PG・分割0件）は無傷で更新していない。6本のS-02数値は raw と補正で小数第4位まで一致。

## 4. 更新前/後の sha256

| ファイル | 更新前（raw・git HEAD） | 更新後（分割補正済み・tracked） |
|---|---|---|
| backtest_results_p3.json | `5da1c44951ffdcc4c705d3c4594041bd3c2b15f382200ce05e2a62406b7632eb` | `dde6e7d27844dab36b6808caf9c3e5812d12cfdf5f7b3e022476f1519cb6c5dd` |
| backtest_results_s03.json | `9ffd2e1b9dd15ef56f4cfab04a57eb66f5cf0b656243e5b9a72ee84612bbc51a` | `83e89f1b659f03693e00a03b87b2fa3bbbda8255889c6cd20e50d03e0b44d682` |
| backtest_results_s03_supplied.json | `d9a71c688844633a97c5f2c7827509d77e83a79db2bb50b496a85990cf325682` | `85f5d8aa236047f497ff42bf58d74f7a5f819e479d8c3b227aece12a071b689d` |
| backtest_results_s05.json | `0fe82a6de1b27a24c1c5a0e8675aa568b604f5d6bbfec82ff39ea8aa9d4e3088` | `7349e1e48f2e278bbdce9ae34f9be24750df59298fb25da7dffe7a40ec62a212` |

## 5. 再現コマンド（データ・窓・コスト・seed 凍結）

評価窓を 2026-09-28 で打ち切った保存データの複製（監査 t_2fc44a51 が作成した `data_trunc`）を使い、既存 `run_backtests_*.py` を変更なしで再実行する。コード・データ・コスト・seed は旧実行と同一、差は splitカレンダー適用（既存エンジンの既定経路 `src/yowayowa/services/backtest.py:632`）のみ。

```sh
W=/root/.hermes/kanban/workspaces/t_d02db084/regen          # regen/data/stock-ohlcv -> data_trunc への symlink
R=/root/current-work/yowayowa-investor
# P3 (cwd を $W にして ./data を参照させる)
cd $W && $R/.venv/bin/python $R/run_backtests_p3.py --output out/backtest_results_p3.json
# S-03（供給なし: GARPはPBR欠損でmomentum-only fallback にフォールバック）
$R/.venv/bin/python $R/run_backtests_garp_momentum_hybrid.py --data-dir $W/data \
    --p3-reference $W/out/backtest_results_p3.json --output out/backtest_results_s03.json
# S-03（供給あり）
$R/.venv/bin/python $R/run_backtests_garp_momentum_hybrid.py --data-dir $W/data \
    --pbr-dir $R/data/fundamentals/pbr --p3-reference $W/out/backtest_results_p3.json \
    --output out/backtest_results_s03_supplied.json
# S-05
$R/.venv/bin/python $R/run_backtests_defensive_rotation.py --data-dir $W/data \
    --output out/backtest_results_s05.json
```

実測（2026-11-05・VPS ubuntu）: 3本の再実行は監査 t_2fc44a51 の補正済み再実行出力（`p3_corrected.json` / `s03_supplied_corrected_samewindow.json` / `s05_corrected_samewindow.json`）と **sha256 でbyte一致**、供給なし S-03 は `p3_baseline_equality` 全15カテゴリ True で momentum と厳密一致。raw 側の保存値との再現一致（momentum 13.7242% / 0.6161 / −34.6371% / 397 trades）は監査が実測済み。

## 6. 開放された課題（別カード）

- データストア自体は `adjustment: raw` のまま残る。監査の推奨（raw維持＋不連続警告fail-closed化・corporate-actions照合CI・中期は `splits` 系をバージョン別ディレクターに取得してrawと混在させない）はコード変更を伴うため、本カードでは実施していない。
- `paperloop._daily_map`（Book C）は分割補正処理を持たない（現4銘柄は分割イベントなし・無傷）。
- G1/G2（TR込み）は `results_v6` にありリポジトリ内の正本とは別basis。TR評価の単一参照点をリポジトリ内に置く場合は別途。
