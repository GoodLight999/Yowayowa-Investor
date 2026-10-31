# S-03 GARP momentum / PBR hybrid — 実測レポート

## 結論

追記（供給完了）: SEC実PBR供給とXOM新旧CIK併合を完了。供給後の実測は [S-03 PBR supply](BACKTEST_REPORT_S03_PBR_SUPPLY.md) / `backtest_results_s03_supplied.json` を参照。以下は供給なし初回baselineの記録で、`backtest_results_s03.json` は回帰基準として不変。
供給後の(a) OOS Sharpe 0.309216 > −0.310141、(b) IS–OOS CAGR差9.1948% < 22.9203%、(d) full CI下限−9.4416%は達成、(c) full DD −54.9811% vs −34.6371%は未達（OOS DDは改善）のため **採用未承認**。生価格分割損益の制約は継続。

二段階ランキングと A/B 計測器の実装は完了。PBR/disclosure_date の実入力は全10銘柄・全行欠損であり、実データでは GARP は全リバランスで momentum-only にフォールバックする。full/IS/OOS の metrics・equity_curve・trades が必須 baseline と完全一致し、全指標と bootstrap CI の差は厳密に0。これは実装/フォールバックの検証であって、PBR フィルタの効用の検証ではない。合格目安(a)–(d)はすべて「PBR供給なしのため評価不能」。合成PBRによる実行結果は含めていない。

## 実装・再現性・検証

- 開始 HEAD: `b6f291558acf98038d41403e77d7cc906cf99c5a`。branch: `agent/commercial-foundation`。local-only、pushなし。最終コミットハッシュは本レポートを含むコミットとして Kanban 完了ハンドオフに記録（自己参照ハッシュを文書へ捏造しない）。
- 対象7ファイル: `src/yowayowa/backtest_models.py`, `src/yowayowa/services/backtest.py`, `run_backtests_garp_momentum_hybrid.py`, `tests/test_garp_momentum_hybrid.py`, `backtest_results_s03.json`, `docs/BACKTEST_REPORT_S03_GARP_HYBRID.md`, `docs/BACKTESTING.md`。
- 新規モデルフィールド・SPY必須制約・APIカタログ変更なし。旧シグナル分岐、実行数式、warmup、assumptions は不変。PBR有効時は候補8→最低PBR5、各1/5固定、欠員は無利息USD現金。PBR全欠損時だけ max_positions+equal の旧momentumへ委譲。常に全universeキーの明示0.0、候補0なら清算。
- CTO修正契約（コメント217）: 完全一致は momentum の省略キーを0.0へ正規化した選択集合/weight と engine 出力で判定。本文§9の5本は誤記、正しくはD5の3本。
- 専用テスト実測: `59 passed`（合成行は単体テスト内だけ）。候補上位8/PBR下位5、同値symbol順、253境界、厳密disclosure境界、最新適格行/no-imputation、非有限/非正値除外、未来バー、skip21、fallback完全一致、モデル、清算、部分現金/コスト、warnings、engine統合、runner診断を検証。既存テストは変更していない。
- 最終ツリーの `make verify`: exit 0、`1887 passed, 2 skipped, 1 warning in 360.28s (0:06:00)`。ruff check/format、mypy（227 source files）、全既存テスト、OpenAPI export完了。ログはタスクの `make_verify.log`。初回の検証は専用テスト4件追加中だったため中断し、最終ツリーで最初から再実行した結果を採用。
- 追加のrunner検証: `.venv/bin/mypy src/yowayowa run_backtests_garp_momentum_hybrid.py` は228 source filesでエラーなし、runner単独ruff check/formatもpass。診断price predicateの同値な型調整後、専用59テストと実データJSON byte再生成/独立127決定オラクルを再実行して一致を確認。
- 再現コマンド: `.venv/bin/python run_backtests_garp_momentum_hybrid.py`。既存storeのみ使用、ライブfetch・SEC/EDGAR補充・入力書換えなし。JSONは indent 2/ensure_ascii=False/allow_nan=False。JSON全精度が正本、以下は表示丸め。
- S03 JSONの独立再生成はbyte一致。GARP helperを使用しない実データ価格順位オラクルでfull/IS/OOS合計127決定のmomentum top8、選択top5、全10キーweight、fallbackを再計算し一致。再生成SHA-256は `7da48a967068f188339e7564b732343917616c81c5372572da87a935087daa08`。

## 評価窓・コスト

- full: 2021-01-04〜2026-09-28、1440セッション。P3 momentum と同一ユニバース: AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM, XOM, SPY。
- IS: 2021-01-04〜2025-01-03、1007日（既存エンジンの `int(n*0.7)` をそのまま使用）。purge 253日。OOS: 2026-01-09〜2026-09-28、180日。
- 3本すべてmonthly、next_open、commission10bps+slippage5bps、annualization252、bootstrap1000/seed20260930。full/IS/OOSは独立現金初期化。IS/OOS CIは独立再実行で計測し、旧engineのnative IS/OOS-bootstrap-disabled契約は変更していない。
- EW参考のnative purgeは0、native OOS開始2025-01-06のまま。以下のEW OOSは同窓比較のため253 purgeで再実行（旧指標を同窓と偽らない）。

## 3戦略のfull/IS/OOS指標

| 戦略 | 区間 | CAGR | Sharpe | Sortino | MaxDD | Calmar | hit rate | 年turnover | IID CAGR 95% CI |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| s03_garp_momentum_hybrid | full | 13.72% | 0.6161 | 0.8351 | -34.64% | 0.3962 | 53.47% | 4.4525x | [-8.23%, 40.73%] |
| s03_garp_momentum_hybrid | IS | 16.32% | 0.6957 | 0.9162 | -34.64% | 0.4712 | 52.93% | 4.0315x | [-11.43%, 51.37%] |
| s03_garp_momentum_hybrid | OOS | -6.60% | -0.3101 | -0.4208 | -14.38% | -0.4589 | 53.33% | 8.0573x | [-40.05%, 47.12%] |
| s03_baseline_momentum_12_1 | full | 13.72% | 0.6161 | 0.8351 | -34.64% | 0.3962 | 53.47% | 4.4525x | [-8.23%, 40.73%] |
| s03_baseline_momentum_12_1 | IS | 16.32% | 0.6957 | 0.9162 | -34.64% | 0.4712 | 52.93% | 4.0315x | [-11.43%, 51.37%] |
| s03_baseline_momentum_12_1 | OOS | -6.60% | -0.3101 | -0.4208 | -14.38% | -0.4589 | 53.33% | 8.0573x | [-40.05%, 47.12%] |
| s03_reference_ew | full | 16.27% | 0.7351 | 1.0301 | -50.21% | 0.3240 | 53.96% | 0.9573x | [-5.20%, 41.00%] |
| s03_reference_ew | IS | 14.35% | 0.6519 | 0.8931 | -50.21% | 0.2858 | 54.02% | 1.0927x | [-11.87%, 46.41%] |
| s03_reference_ew | OOS | 18.04% | 1.0696 | 1.5715 | -11.24% | 1.6052 | 52.22% | 2.0526x | [-20.72%, 78.14%] |

すべてstatus=complete、各数値metricはavailable。ただしcompleteは価格指標の可用性であり、財務入力充足や戦略の投資適格性を意味しない。

## PBRカバレッジ・除外診断

カウントはprovider=alpacaの既存store全行（評価窓の外のlookback/後続行も含む）。価格入力は2019-12-30開始、共通窓末尾は2026-09-28。AAPL/MSFT/NVDAはstoreに2026-09-29まであり、他は2026-09-28まで。未来の後続バーはengine/診断に使用しない。PBR有効rebalanceはfullの全69決定で全銘柄のPIT入力を確認、PBR除外は一次候補に入った場合のみ集計。

| 銘柄 | 行数 | pbr非null | disclosure非null | PIT PBR有効決定 | 候補PBR欠損除外 | momentum上位8外 |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 1696 | 0 | 0 | 0 | 53 | 16 |
| MSFT | 1696 | 0 | 0 | 0 | 62 | 7 |
| NVDA | 1696 | 0 | 0 | 0 | 47 | 22 |
| GOOGL | 1695 | 0 | 0 | 0 | 54 | 15 |
| AMZN | 1695 | 0 | 0 | 0 | 45 | 24 |
| META | 1695 | 0 | 0 | 0 | 57 | 12 |
| TSLA | 1695 | 0 | 0 | 0 | 50 | 19 |
| JPM | 1695 | 0 | 0 | 0 | 67 | 2 |
| XOM | 1695 | 0 | 0 | 0 | 50 | 19 |
| SPY | 1695 | 0 | 0 | 0 | 67 | 2 |

合計16953行。一次候補のPBR欠損除外合計552件、補完なし。全銘柄の価格warmupは評価開始前に充足。財務カバレッジは0%、PBR有効候補は全決定で0件。

warningsはsymbol別PBR欠損とportfolio fallbackを既存形式で集約。実測例:

```text
Fundamental data unavailable for AAPL: missing or invalid point-in-time PBR; rebalance dates 2021-09-01 through 2026-09-01 (53 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for AMZN: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (45 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for GOOGL: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (54 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for JPM: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (67 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for META: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-04-01 (57 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for MSFT: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (62 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for NVDA: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (47 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for SPY: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-09-01 (67 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for TSLA: missing or invalid point-in-time PBR; rebalance dates 2021-01-04 through 2026-08-03 (50 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for XOM: missing or invalid point-in-time PBR; rebalance dates 2021-04-01 through 2026-09-01 (50 affected decisions). Missing inputs are not imputed.
Fundamental data unavailable for portfolio: no valid candidate PBR; momentum-only fallback; rebalance dates 2021-01-04 through 2026-09-01 (69 affected decisions). Missing inputs are not imputed.
```

## A/B比較と帰属

| 区間 | metrics完全一致 | equity_curve完全一致 | trades完全一致 | 指標Δ GARP−momentum | CI端点Δ |
|---|---|---|---|---|---|
| full | True | True | True | 7指標すべて0.0 | [0.0, 0.0] |
| IS | True | True | True | 7指標すべて0.0 | [0.0, 0.0] |
| OOS | True | True | True | 7指標すべて0.0 | [0.0, 0.0] |

IS−OOS CAGRギャップはGARP/baselineとも22.92pp。縮小Δ=0。full取引数は両方397件。JSONには9組の決定的field SHA-256を保存し、同一run条件の実データ出力で比較。PBR効果はまだ一度も働いていないため、差ゼロをalpha不在の証拠や改善判定に使わない。

## OOS重点分析

| 区間 | sessions | rebalance | fallback決定 | fallback日 | 選択集合switch | fallback状態switch | 全現金日 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1440 | 69 | 69 | 1440 | 45 | 0 | 0 |
| IS | 1007 | 49 | 49 | 1007 | 28 | 0 | 0 |
| OOS | 180 | 9 | 9 | 180 | 8 | 0 | 0 |

OOSは180日/9決定すべてmomentum-only。switchはリバランス時の選択集合変更（初回entryを除外）であり、価格ドリフトによる売買回数とは異なる。fallback日の判定と構成日数は予定targetを次のrebalanceまで持ち越したもの。switch日のP&Lには旧保有のovernightが含まれる。

| 銘柄 | OOS選択target持越し日 | OOS選択日比率 |
|---|---:|---:|
| AAPL | 83 | 46.11% |
| MSFT | 37 | 20.56% |
| NVDA | 140 | 77.78% |
| GOOGL | 180 | 100.00% |
| AMZN | 40 | 22.22% |
| META | 0 | 0.00% |
| TSLA | 125 | 69.44% |
| JPM | 73 | 40.56% |
| XOM | 146 | 81.11% |
| SPY | 76 | 42.22% |

| OOS決定日 | momentum候補数 | PBR有効候補 | 選択銘柄（各20%） | fallback |
|---|---:|---:|---|---|
| 2026-01-09 | 8 | 0 | GOOGL, JPM, MSFT, NVDA, SPY | True |
| 2026-02-02 | 8 | 0 | GOOGL, JPM, NVDA, SPY, TSLA | True |
| 2026-03-02 | 8 | 0 | GOOGL, MSFT, NVDA, TSLA, XOM | True |
| 2026-04-01 | 8 | 0 | GOOGL, NVDA, SPY, TSLA, XOM | True |
| 2026-05-01 | 8 | 0 | GOOGL, JPM, NVDA, TSLA, XOM | True |
| 2026-06-01 | 8 | 0 | AAPL, AMZN, GOOGL, NVDA, XOM | True |
| 2026-07-01 | 8 | 0 | AAPL, GOOGL, NVDA, TSLA, XOM | True |
| 2026-08-03 | 8 | 0 | AAPL, GOOGL, SPY, TSLA, XOM | True |
| 2026-09-01 | 8 | 0 | AAPL, AMZN, GOOGL, JPM, XOM | True |

各リバランスの候補symbol/score、PBR有効候補/値、selected、全10キーtarget_weightsはJSONに保存。OOSは短く単一split。CAGR/CIは年率化されるが180観測の確度を超える長期有効性は主張しない。

## CIO参照値との対照（受入基準ではない）

ブリーフの指定どおり外部・別ユニバース参照値として扱う。数値がほぼ同じでも参照値そのものを同条件受入baselineには使わない。主baselineは今回の同条件再実行momentum。

| 指標 | CIO参照 | 同条件momentum実測 | 実測−参照（pp） |
|---|---:|---:|---:|
| oos_cagr | -6.60% | -6.59851% | 0.00149 |
| full_max_drawdown | -34.64% | -34.63711% | 0.00289 |

## 合格目安(a)–(d)

| 条件 | 判定 | 理由 |
|---|---|---|
| (a) OOS Sharpe改善 | 評価不能 | PBR供給なし、momentum自己比較 |
| (b) IS−OOS CAGRギャップ縮小 | 評価不能 | PBR供給なし、同一22.92ppギャップ |
| (c) MaxDD軽減 | 評価不能 | PBR供給なし、full/OOSともbaseline一致 |
| (d) full CI下限 ≥ −15% | 評価不能 | 観測full下限は−8.23%だがPBR戦略の検証として合格扱いしない（OOS CI下限は−40.05%） |

## 旧4 runner回帰・P3照合

実装前HEADの未変更コードで4本を再生成し、workspaceへ実行出力をbyte退避。実装後にも再生成して実装前出力とbyte完全一致。P3 tracked JSONは実装開始前からstaleであり、ファイル全体のtracked一致ではなくブリーフが許可した実装前再生成退避版との一致を使用した。tracked P3は元のbyteに戻し、コミット対象に含めない。他3本はtrackedとも一致。cryptoレポートの再生成もgit差分なし。

| 生成物 | 実装前/後byte一致 | tracked一致 | 再生成SHA-256 |
|---|---|---|---|
| backtest_results_p3.json | True | False | `b88c17ea70d1b574fdb04b2a320132e23cd3ea6d29eca1cb64e9fe648cddea1e` |
| crypto_backtest_results.json | True | True | `1cdca730152f697acae252b8bb609ff30f4a1ab13fe393f650849bccf8cc720b` |
| backtest_results_s02.json | True | True | `9aa2228bb511580814fb6ab9d39ee8e9c36b77110139b1cd5f6abd3eef28f7dd` |
| backtest_results_s05.json | True | True | `21da050b10c6b72b356fe34005bce96fbe2383ab937265afefe2fcd0b5262795` |

S03 baseline とP3 momentumのfield-by-field照合: 15カテゴリすべてTrue（full7指標、metric status/count、bootstrap status/CI、OOS7指標、P3で公開されるIS CAGR/Sharpe、窓、OOS開始、full/IS/OOSセッション、取引数、purge）。tracked P3内のmomentum指標とも一致し、実装前再生成P3との追加照合でも同一を確認。P3はIS全7指標を保存しないため未保存の値を照合済みと偽らない。S03 JSONにはIS全7指標とCIがある。旧runner/source、data/、APIカタログ、frozen contract（keirin-ai等）は差分なし。

## 逸脱点・限界・実ポートフォリオとの関係

- CTO承認の読み替え以外に設計逸脱なし。§9の本数は3、fallbackキー集合は正規化して比較。engine実行結果は厳密一致。P3のみstaleのため実装前再生成byteオラクルを使用。
- 初回実装時にはPBR供給を別カードへ分離したが、現在は別成果物として完了（上記相互参照）。SPYはETFで構造的PBR欠損を維持し、偽PBRは追加しない。
- METAの近似・ticker履歴問題、raw OHLCVの分割リーク/未調整価格問題は未処理。分割修正、配当、税、FX、market impact、delisting、point-in-time universeはモデル化していない。取得済み価格の不備を戦略alphaと誤認しない。
- 高PBR銘柄を避けるという仮説はまだ測定不能。PBRは金融/IT等の業種構造差の影響を受けるため、供給後もセクター偏りの独立評価が必要。IID bootstrapは系列依存を扱わず将来予測区間ではない。

実ポートフォリオの「S&P500約54%集中」はCIO取得時点の楽天証券口座内比率であり、現時点の全資産比率ではない。今回の固定米株10銘柄GARPはS&P500ファンド全体や実口座を再現せず、指数集中を直接ヘッジするoverlayでもない。PBR供給後にgrowth/value偏重を変える可能性はあるが、現状はmomentumと同一でOOS損失も同一。54%集中の緩和、推奨配分、実売買をこの結果から導かない。

## Provenance

全10銘柄はpersisted Alpaca/USD。source_url、license_class、retrieved_at、保持したprice履歴の始終はJSONの各結果provenanceに保存。未供給PBRに架空の出所を付けていない。
