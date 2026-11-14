# S-05 Defensive rotation 実測評価

**価格 basis（2026-11-05 更新）**: 本文書の実測数値は「分割補正済み」basis に統一更新した。raw保存データに `data/fundamentals/pbr/split-calendar.json`（7イベント）で**分割のみ補正**（配当なし・TRではない）して既存 `run_backtests_defensive_rotation.py` を再実行した出力（窓・コスト・seed凍結）。raw（未補正）由来の旧値は git 履歴（コミット `b6f2915`）と `docs/BACKTEST_PRICE_BASIS_20261105.md` で参照。分割補正の影響は保有銘柄（NVDA/AAPL）の full・IS区間に集中し、OOS 数値（分割日を含まない）は全5本で不変。low-volatility は分割銘柄を保有しないので full 数値も不変。TR込み（G1 `results_v6`）の数値は**別basis**であり本表と一致しない。

## 1. 結論

凍結設計の実装と実測評価は完了。採用目安の判定はbasis依存のため、basisを明示して扱う:

- **分割のみ補正の本basis**: 必須条件(b)は full MaxDD −22.01% vs 同一universe momentum −25.13% で**達成に反転**（raw basis では −28.09% vs −27.72% で未達）。主criteriaは(a)(b)(c)(d)すべて合格。
- **配当込みTR basis（G1 `results_v6`）**: full DD −26.30% vs momentum −24.88% で**(b)は再び未達**。さらにG2のW1窓勝率 57.1% < 60% で不合格。
- 採否の結論は **TR basis 由来の「不採用」を維持**。本basisでの反転を単独の採用根拠にしない。正のOOS収益だけで採用・防御効果を主張しない。

monthlyのOOS CAGRは+7.47%で(a)正転合格、Sharpe 0.6078>0.5。一方、同一ユニバースmomentumのOOS CAGR +15.70%より-8.2238pp低い。

## 2. 凍結条件・再現方法

- 開始HEAD: `4ade31e2c67b010f3f3a56e7cd2c2ca7ebea6ccf`、branch `agent/commercial-foundation`。pushなし。最終SHAは同カードの構造化handoffに記録（この文書自身を含むコミットの自己参照を避ける）。
- コード: `src/yowayowa/backtest_models.py`, `src/yowayowa/services/backtest.py`, `run_backtests_defensive_rotation.py`。テスト: `tests/test_defensive_rotation.py`。成果物: `backtest_results_s05.json`, 本文書, `docs/BACKTESTING.md`。追加model fieldなし、catalog/data/frozen contract変更なし。
- SPY/QQQ/IWM/JNJ/PG/XOM/JPM/AAPL/MSFT/NVDA。Alpaca永続化済み日足USDのみ、取得・補完なし。窓=max(common[0],2021-01-04)〜common[-1]。
- full 2021-01-04〜2026-09-28 / 1440 sessions。IS 1007 / purge 253 / OOS 2026-01-09〜2026-09-28 / 180 sessions。
- 分割は既存engineの`int(n * 0.7)`をそのまま使用。1440観測では浮動小数点の切り捨てでIS=1007になる（1008への変更なし）。全5本の報告OOSは253-session purgeで統一。low-vol/EWのnative engine purge61/0とnative OOS日付はJSONに別記し、既存engine値は変更していない。
- 各full/IS/OOSを独立に初期現金から計算。pre-start/purgeバーは過去シグナル入力のみ可、purgeリターンはOOSに入らない。commission10bps+slippage5bps、annualization252、IID bootstrap1000回 seed20260930。engine native IS/OOSのbootstrap-disabled契約は維持し、runnerは独立再実行で区間CIを付加。
- R=(SPYの直近20-session return>0) AND (close(t-1)>SMA100(t-1))。当日/未来入力なし。等号・履歴不足・不正値はrisk-off。成長は253-closeから252-21 momentum上位5（symbol昇順tie）、各20%。不足slotは現金。防御はJNJ/PG/XOM/JPM∩universeの60-return標本σが低い上位3、1/n。σ非有限除外、候補ゼロは全現金。
- 全銘柄キーを常に明示。非選択0.0、空dict禁止。generic weighting/max_positionsを無視、無利息USD cash・レバなし。warmup/purge253は防御側の61-close時点の選択を禁止する一律取引待ちではない。
- monthlyはセッション月初（OOS初日も独立初回リバランス）、dailyは全銘柄の毎日再選択/再配分。dailyは片方向の緊急退出ではなくA/B診断用。

再実行（2026-11-05 時点。窓を 2026-09-28 で打ち切った保存データ複製 `data_trunc` を `--data-dir` に渡して旧実行と同一窓・コスト・seed を凍結）:

```sh
.venv/bin/python run_backtests_defensive_rotation.py --data-dir <data_truncへのパス> --output <出力先>/backtest_results_s05.json
# 出力 sha256 7349e1e48f2e278bbdce9ae34f9be24750df59298fb25da7dffe7a40ec62a212（旧raw版 0fe82a6de1b27a24c1c5a0e8675aa568b604f5d6bbfec82ff39ea8aa9d4e3088）
.venv/bin/pytest tests/test_defensive_rotation.py -q -o addopts=
make verify
```

## 3. 5本の指標（コスト後）

割合は%表示、turnoverはgross buys+sellsの年率x、CIはCAGRのIID95%区間。独立区間のCIと全status/sample_countはJSON参照。

### full

| strategy_id | CAGR | Sharpe | Sortino | MaxDD | Calmar | hit rate | turnover x | 95% CAGR CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | +13.25% | 0.8023 | 1.1579 | -22.01% | 0.6019 | +54.17% | 9.2286 | [-2.75%, +29.58%] |
| s05_defensive_rotation_daily | +4.00% | 0.3172 | 0.4429 | -30.98% | 0.1292 | +53.47% | 46.0870 | [-10.87%, +19.52%] |
| s05_baseline_momentum_12_1 | +17.67% | 0.8856 | 1.2896 | -25.13% | 0.7032 | +54.72% | 3.2939 | [-0.30%, +38.75%] |
| s05_baseline_low_volatility | +8.64% | 0.6794 | 0.9776 | -23.20% | 0.3725 | +52.36% | 3.7954 | [-3.27%, +20.40%] |
| s05_baseline_ew | +20.48% | 1.2030 | 1.7814 | -24.16% | 0.8477 | +55.21% | 0.6840 | [+4.97%, +36.90%] |

### IS

| strategy_id | CAGR | Sharpe | Sortino | MaxDD | Calmar | hit rate | turnover x | 95% CAGR CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | +15.02% | 0.8566 | 1.2569 | -22.01% | 0.6821 | +53.53% | 9.3746 | [-3.67%, +36.40%] |
| s05_defensive_rotation_daily | +1.13% | 0.1521 | 0.2139 | -30.98% | 0.0365 | +52.14% | 43.7486 | [-15.20%, +20.28%] |
| s05_baseline_momentum_12_1 | +19.18% | 0.9407 | 1.3645 | -25.13% | 0.7632 | +53.92% | 3.4173 | [-2.55%, +46.39%] |
| s05_baseline_low_volatility | +6.73% | 0.5489 | 0.7771 | -23.20% | 0.2900 | +52.23% | 3.5631 | [-6.28%, +20.59%] |
| s05_baseline_ew | +19.88% | 1.1370 | 1.6713 | -24.16% | 0.8228 | +54.72% | 0.7607 | [+0.69%, +40.44%] |

### OOS

| strategy_id | CAGR | Sharpe | Sortino | MaxDD | Calmar | hit rate | turnover x | 95% CAGR CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | +7.47% | 0.6078 | 0.8663 | -9.37% | 0.7976 | +53.33% | 11.9617 | [-22.89%, +46.68%] |
| s05_defensive_rotation_daily | +14.88% | 1.1802 | 1.7664 | -8.47% | 1.7565 | +56.11% | 64.9510 | [-15.83%, +53.13%] |
| s05_baseline_momentum_12_1 | +15.70% | 1.2671 | 1.8904 | -6.45% | 2.4317 | +55.00% | 4.6781 | [-13.11%, +57.66%] |
| s05_baseline_low_volatility | +15.94% | 1.3196 | 1.9706 | -8.96% | 1.7788 | +53.33% | 4.7486 | [-11.55%, +53.07%] |
| s05_baseline_ew | +26.74% | 2.2434 | 3.5582 | -6.99% | 3.8245 | +57.78% | 1.9842 | [-3.52%, +64.07%] |

## 4. スイッチ・防御滞在とmonthly/daily A/B

スイッチは予定リバランスでのR変化。初期cash→sleeveは数えず、同sleeve内の選択変更/price-drift再配分の取引件数とは別。daily Rは月中には仮想シグナル、実際の保有sleeve/targetは次の予定日までcarry。現金化日数は全額現金targetで数える（浮動小数点の微小cash残とは別）。

| variant | segment | R switches | defensive sessions | defensive ratio | full cash | partial cash | daily R-off sessions | daily R changes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | full | 26 | 505 | +35.07% | 0 | 0 | 541 | 116 |
| s05_defensive_rotation | IS | 19 | 359 | +35.65% | 0 | 0 | 389 | 75 |
| s05_defensive_rotation | OOS | 4 | 65 | +36.11% | 0 | 0 | 71 | 22 |
| s05_defensive_rotation_daily | full | 116 | 541 | +37.57% | 0 | 0 | 541 | 116 |
| s05_defensive_rotation_daily | IS | 75 | 389 | +38.63% | 0 | 0 | 389 | 75 |
| s05_defensive_rotation_daily | OOS | 22 | 71 | +39.44% | 0 | 0 | 71 | 22 |

実績では両variantの全区間に全額/部分現金target日なし。R=0は現金逃避ではなく防御株100%への交代だった。JSONに全予定日のR・選択銘柄・全銘柄target weights・sleeve区分と日次実際cash/exposureを保存。

| variant | segment | net CAGR | zero-cost CAGR | CAGR cost drag (pp) | annualized sum-rate drag | cost rate sum |
| --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | full | +13.25% | +14.83% | 1.5803 | +1.38% | +7.91% |
| s05_defensive_rotation | IS | +15.02% | +16.65% | 1.6304 | +1.41% | +5.62% |
| s05_defensive_rotation | OOS | +7.47% | +9.42% | 1.9477 | +1.79% | +1.28% |
| s05_defensive_rotation_daily | full | +4.00% | +11.45% | 7.4514 | +6.91% | +39.50% |
| s05_defensive_rotation_daily | IS | +1.13% | +8.00% | 6.8658 | +6.56% | +26.22% |
| s05_defensive_rotation_daily | OOS | +14.88% | +26.65% | 11.7673 | +9.74% | +6.96% |

cost rate sumは毎日equityに課した率の総和（ドル手数料総額ではない）。年率sum-rateは観測数/252で除した診断、CAGR cost dragは0bpsで同戦略を実際に再実行したCAGR差。compound factorもJSONに別保存。

fullではdailyのゼロコストCAGR（+11.45%）もmonthly（+14.83%）を下回り、費用増加だけでなくシグナル/取引頻度の劣後がある。OOS dailyはmonthlyよりCAGR/Sharpe改善・DD浅いが、同一momentumより依然DDが深く、full CAGR（+4.00%）はmonthly（+13.25%）を大きく下回る。dailyへの事後差替え採用はしない。

## 5. OOS重点分析（2026-01-09〜）

### monthlyの予定日シグナル/選択

| execution | R | sleeve | selected | per selected target |
| --- | --- | --- | --- | --- |
| 2026-01-09 | True | growth | JNJ, JPM, NVDA, QQQ, SPY | +20.00% |
| 2026-02-02 | True | growth | JNJ, JPM, NVDA, QQQ, SPY | +20.00% |
| 2026-03-02 | False | defensive | JNJ, PG, XOM | +33.33% |
| 2026-04-01 | False | defensive | JNJ, JPM, PG | +33.33% |
| 2026-05-01 | True | growth | IWM, JNJ, NVDA, QQQ, XOM | +20.00% |
| 2026-06-01 | True | growth | AAPL, IWM, JNJ, NVDA, XOM | +20.00% |
| 2026-07-01 | False | defensive | JNJ, JPM, PG | +33.33% |
| 2026-08-03 | True | growth | AAPL, IWM, JNJ, QQQ, XOM | +20.00% |
| 2026-09-01 | True | growth | AAPL, IWM, JNJ, QQQ, XOM | +20.00% |

実際の切替日: 2026-03-02, 2026-05-01, 2026-07-01, 2026-08-03。

### 実際の防御退避期間と同一momentumの同日区間

以下は独立OOS全体の保有をcarryした実績リターン/DD。期間開始に再初期化/再取引せず、local DDのみ期間開始直前equityを1に正規化。スイッチ当日は前sleeveのovernightと当日のコストを含むため、純粋な防御銘柄単独の因果効果ではない。

| variant | start | end | sessions | rotation return | momentum return | rotation localDD | momentum localDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| s05_defensive_rotation | 2026-03-02 | 2026-04-30 | 43 | -4.17% | +3.45% | -7.19% | -4.20% |
| s05_defensive_rotation | 2026-07-01 | 2026-07-31 | 22 | +1.86% | -0.24% | -3.15% | -2.82% |
| s05_defensive_rotation_daily | 2026-02-05 | 2026-02-06 | 2 | +1.02% | +2.29% | -0.08% | -0.96% |
| s05_defensive_rotation_daily | 2026-02-10 | 2026-02-18 | 6 | +0.79% | -1.01% | -1.79% | -2.34% |
| s05_defensive_rotation_daily | 2026-02-20 | 2026-02-20 | 1 | -1.56% | +0.31% | -1.56% | +0.00% |
| s05_defensive_rotation_daily | 2026-02-24 | 2026-04-09 | 32 | -2.56% | -1.34% | -7.56% | -6.44% |
| s05_defensive_rotation_daily | 2026-06-10 | 2026-06-15 | 4 | +0.27% | +0.24% | -0.75% | -0.50% |
| s05_defensive_rotation_daily | 2026-06-24 | 2026-07-07 | 9 | +4.08% | +2.26% | -1.20% | -1.18% |
| s05_defensive_rotation_daily | 2026-07-16 | 2026-07-16 | 1 | -0.18% | -0.22% | -0.18% | -0.22% |
| s05_defensive_rotation_daily | 2026-07-21 | 2026-07-21 | 1 | +0.89% | +1.25% | +0.00% | +0.00% |
| s05_defensive_rotation_daily | 2026-07-29 | 2026-07-31 | 3 | -0.81% | -2.31% | -1.15% | -2.31% |
| s05_defensive_rotation_daily | 2026-09-02 | 2026-09-03 | 2 | +1.09% | +1.04% | +0.00% | +0.00% |
| s05_defensive_rotation_daily | 2026-09-08 | 2026-09-21 | 10 | -1.26% | +0.55% | -1.67% | -0.81% |

### 日次R要約・月次判定の遅れ

同じOOS180日で日次R-on 109日、off 71日、変化22回。monthly実保有防御65日(36.11%)・4切替に対しdaily防御71日(39.44%)・22切替。

| daily R-off start | end | sessions |
| --- | --- | --- |
| 2026-02-05 | 2026-02-06 | 2 |
| 2026-02-10 | 2026-02-18 | 6 |
| 2026-02-20 | 2026-02-20 | 1 |
| 2026-02-24 | 2026-04-09 | 32 |
| 2026-06-10 | 2026-06-15 | 4 |
| 2026-06-24 | 2026-07-07 | 9 |
| 2026-07-16 | 2026-07-16 | 1 |
| 2026-07-21 | 2026-07-21 | 1 |
| 2026-07-29 | 2026-07-31 | 3 |
| 2026-09-02 | 2026-09-03 | 2 |
| 2026-09-08 | 2026-09-21 | 10 |

月次は2月の複数offを即時反映せず3/2まで成長を保持し、4/9の日次off終了後も4月末まで防御に滞在した。6/24の日次offも7/1まで反映しない。短いoffはmonthlyが無視する一方dailyでは往復と高turnoverを生む。

| OOS comparison | CAGR | Sharpe | MaxDD |
| --- | --- | --- | --- |
| s05_defensive_rotation | +7.47% | 0.6078 | -9.37% |
| s05_defensive_rotation_daily | +14.88% | 1.1802 | -8.47% |
| s05_baseline_momentum_12_1 | +15.70% | 1.2671 | -6.45% |
| s05_baseline_low_volatility | +15.94% | 1.3196 | -8.96% |
| s05_baseline_ew | +26.74% | 2.2434 | -6.99% |

防御に切り替わった事実は確認できたが、今回のOOSでmomentumに対する全体DD軽減は確認できない。

## 6. CIO参照値との対照・合否

| reference | universe | OOS CAGR | full MaxDD |
| --- | --- | --- | --- |
| CIO P3 external | 旧P3 10銘柄 | -6.60% | -34.64%（raw basis の外部参照値） |
| S05 momentum | S05同一10銘柄 | +15.70% | -25.13%（raw basis では -27.72%） |
| S05 monthly primary | S05同一10銘柄 | +7.47% | -22.01%（raw basis では -28.09%） |

| criterion | verdict | evidence |
| --- | --- | --- |
| (a) OOS CAGR ≥0 / [-2%,0) | 正転合格 | +7.47%; same-universe Δ -8.2238pp（OOSは補正前後で不変） |
| (b) full DD shallower than same-universe momentum (必須) | **達成（本basis・分割のみ補正）** | -22.01% vs -25.13%（raw basis では -28.09% vs -27.72% で未達。TR込み basis では再未達） |
| (b) CIO -34.64% external | 浅い（主合否ではない） | -22.01%（CIO参照値はraw basis・別basis） |
| (c) OOS switches/retreat/DD report | 記録済み | 4 switches / 65 defensive sessions; DD worse than momentum |
| (c) OOS Sharpe >0.5 | 合格 | 0.6078 |
| (d) full CI lower ≥-15% (参考) | 合格 | -2.75%（raw basis では -12.53%） |
| 総合採用目安 | **不採用（TR basis 優先）** | 本basis（分割のみ補正）では必須(b)達成に反転するが、配当込みTR basis（G1 `results_v6`: −26.30% vs −24.88%）では再未達、G2 W1も未達。TR評価専用の判断として不採用を維持。実装完了とは区別する。 |

### 実ポートフォリオとの関係

CIOの2026-10-02取得snapshotでは楽天証券口座内のS&P500ファンドが約54%に集中する。これは全純資産の54%ではない。本戦略は指数集中を別の3銘柄集中へ置き換える可能性があり、JNJ/PGへの反復集中、JPM/XOMの景気循環リスク、成長側のETFと個別株の重複を持つ。今回のmandatory DD未達は、S&P500実保有へのリスク削減としてそのまま転用する根拠にならない。USDモデルと円建て投信/税/FX/既存保有は一致せず、実口座の売買・設定変更は行っていない。

## 7. 検証・回帰・逸脱/限界

- `make verify`: exit 0、1828 passed, 2 skipped, 1 warning in 360.43s (0:06:00)。ruff src/tests、mypy 227 files、coverage付き全pytest、OpenAPI exportまで実行。新規専用テストは実測52 passed。runner自体もruff check/format確認。
- 新規テストはRのstrict boundary/100-close不足、成長252-21/253-close/skip/tie、防御sampleσ/61-close/subset/候補0..4、明示zero、partial/full cash、full liquidation売却コスト、未来バー変異、補完禁止、model/SPY制約、warmup/purge/OOS、scheduled vs hypothetical診断、区間bootstrap整合、退避実績算出、既存8シグナルをカバー。
- 既存8シグナルは開始HEADをgit archiveで退避し、1100-observation合成fixtureからfull responseを実行前後でSHA256/byte一致（metrics/IS/OOS/curve/trades/warnings/assumptions含む）。HEAD実測hashを専用pytestに固定し8/8合格。

| runner output | preimplementation rerun vs after | SHA256 | tracked match at HEAD |
| --- | --- | --- | --- |
| backtest_results_p3.json | byte一致 | b88c17ea70d1b574fdb04b2a320132e23cd3ea6d29eca1cb64e9fe648cddea1e | staleのため不一致・原本復元 |
| crypto_backtest_results.json | byte一致 | 1cdca730152f697acae252b8bb609ff30f4a1ab13fe393f650849bccf8cc720b | 一致・git diff空 |
| backtest_results_s02.json | byte一致 | 9aa2228bb511580814fb6ab9d39ee8e9c36b77110139b1cd5f6abd3eef28f7dd | 一致・git diff空 |

- P3 tracked JSONは開始時点でstale。許可された方式に従い「開始HEADのコードから再生成した退避版」対「実装後の再生成」のbyte一致を証明し、tracked原本を戻した。既存結果JSON3本は差分ゼロ。
- S05 final runnerは同一入力で二度実行しbyte一致、raw basis のSHA256 `21da050b10c6b72b356fe34005bce96fbe2383ab937265afefe2fcd0b5262795`、5583079 bytes（旧版・git履歴 `b6f2915`）。trackedの現行 `backtest_results_s05.json` は2026-11-05 分割補正済み再実行版（sha256 `7349e1e48f2e278bbdce9ae34f9be24750df59298fb25da7dffe7a40ec62a212`・5,582,311 bytes）。
- 変更対象は本ブリーフ7ファイルのみ（新規4+既存3）。catalog、data、OpenAPI frozen export、keirin-ai等のfrozen contractには差分なし。既存未追跡CIO/STATE文書、.worktrees/.coverage.*は追加しない。push禁止を遵守。
- 添付ブリーフは存在せずカード本文D1-D7を受入正本とした。実装仕様からの逸脱なし。曖昧なgrowth候補不足は「各20%」を固定し不足slotを現金、全5本OOSは比較可能性のためshared253、native baseline splitは併記する明示解釈を採用。IS=1007は既存実装の実測を維持した結果である。

### 限界

- S05とP3は異なるユニバース。旧P3のGOOGL/AMZN/META/TSLAをQQQ/IWM/JNJ/PGに入れ替えている。CIO -6.60%/-34.64%との差はこの変更とレジームルールの混合で、momentumのOOS崩壊を救済した証拠にはならない。主比較を同一S05 momentumに限定した結果、OOS収益もDDも優位性なし。
- 現在の固定10銘柄はpoint-in-time membershipでなくsurvivorship biasがある。品質fundamentalsを検証する戦略ではなく、名称上のdefensiveは固定サブセットという代理仮説。XOM/JPMは無リスクではない。
- 価格basisは分割補正済み（2026-11-05再実行・`split-calendar.json` 7イベント）。ただし配当/税/impact/為替/上場廃止リターンはモデル化しない（TRではない）。固定bpsコストとfractional weightの理想化、月次急落対応の遅れがある。
- OOSは180観測の一本の時系列holdout。繰り返しrolling walk-forwardではなく、2026後半を見て戦略を設計しているため完全な未見future検証ではない。IID bootstrapは自己相関を保存せずCIは予測区間ではない。
- cashは無利息USDだが今回は実績でcash退避ゼロ。レジーム切替は3防御株へのfull exposureで、市場betaをゼロにする仕組みではない。切替と相対成績の因果を断定しない。

## 8. Provenance

JSONのprovenanceは価格が存在するpre-startを含む、各銘柄のsource URL/license/retrieved_at/as_of範囲を保存。licenseは利用許諾そのものではなくデータ取得時の分類であり、商用再配布の許可を意味しない。

| symbol | rows | first | last | provider | currency | license_class | source_url | retrieved_at |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SPY | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=SPY&timeframe=1Day&feed=sip | 2026-09-30T02:51:06.888044Z |
| QQQ | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=QQQ&timeframe=1Day&feed=sip | 2026-09-30T02:51:07.241272Z |
| IWM | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=IWM&timeframe=1Day&feed=sip | 2026-09-30T02:51:07.614018Z |
| JNJ | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=JNJ&timeframe=1Day&feed=sip | 2026-09-30T02:51:05.379790Z |
| PG | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=PG&timeframe=1Day&feed=sip | 2026-09-30T02:51:06.163139Z |
| XOM | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=XOM&timeframe=1Day&feed=sip | 2026-09-30T02:51:06.525464Z |
| JPM | 1695 | 2019-12-30 | 2026-09-28 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=JPM&timeframe=1Day&feed=sip | 2026-09-30T02:51:05.781865Z |
| AAPL | 1696 | 2019-12-30 | 2026-09-29 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=AAPL&timeframe=1Day&feed=sip | 2026-09-30T02:51:02.115356Z |
| MSFT | 1696 | 2019-12-30 | 2026-09-29 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=MSFT&timeframe=1Day&feed=sip | 2026-09-30T02:51:02.709466Z |
| NVDA | 1696 | 2019-12-30 | 2026-09-29 | alpaca | USD | personal_only | https://data.alpaca.markets/v2/stocks/bars?symbols=NVDA&timeframe=1Day&feed=sip | 2026-09-30T02:51:03.311398Z |
