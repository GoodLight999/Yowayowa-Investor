# CIO戦略企画書 — yowayowa-investor バックテスト新戦略・アルファ仮説の策定

- 提案者: CIO-Fortuna（投資担当役員）
- 提案日: 2026-10-02 (JST)
- ステータス: **提案（実装前）**。本書は分析・設計であり、実弾の売買・発注を含まない。分析の完了は売買の承認ではない。
- 引き継ぎ先: cto-hephaestus（バックテスト実装タスク）/ Molt（検収）

---

## 0. エグゼクティブサマリー

安全保護フェーズ1完了後の堅牢な実行・管理基盤と P2/P3 バックテスト基盤（purged walk-forward OOS、bootstrap 95% CI、明示コスト）を実測結果（`backtest_results_p3.json` / `crypto_backtest_results.json`、2026-09-30 実行）から読み、**4つの未開拓ギャップ**を特定した。

1. **ドローダウン制御がどの戦略にも内蔵されていない。** 既存全戦略の MaxDD は -35%〜-50%（株）・-58%〜-60%（暗号）。リスクオフ時に「現金へ逃げる」機構がシグナルに存在しない。
2. **暗号資産戦略はアルファがない。** 既存 crypto 4戦略は warmup 後、BTC/ETH を 50/50 で保有する「遅延イコールウェイト」であり、レポート自身が selective でないと認定している。OOS（2025-08〜）は全戦略 CAGR 約 -30%。**トレンドフィルタを入れて初めて selective になる。**
3. **マクロデータが永続化済みなのに戦略未使用。** `data/macro-observations/` に FRED DGS10（16,172行）・UNRATE・CPI・BLS・財務省金利データが揃っている。
4. **防衛銘柄データが取得済みなのにユニバース外。** IWM / JNJ / PG は OHLCV 取得済み（2019-12-30〜2026-09-28、1,695行）だが現行戦略は未使用。low_volatility の OOS Sharpe 2.09（実測）は防衛寄りレジームで効いた証左。

これに対し **5戦略**を提案する（優先度順: S-01 → S-02 → S-05 → S-03 → S-04）。

| ID | 名称 | 一言 | 優先度 |
|---|---|---|---|
| S-01 | crypto_trend_dual_ma | 暗号資産の絶対トレンドフォロー（SMA200フィルタで下落局を現金化） | ★1（即実装可） |
| S-02 | vol_target_defensive | 実現ボラに応じ株式⇔現金を自動配分するオーバーレイ | ★1（即実装可） |
| S-05 | defensive_rotation | 防衛セクターへのスイッチ型ローテーション | ★2（データ即利用可） |
| S-03 | garp_momentum_hybrid | モメンタム×PBRフィルタ（GARP）二段階ランキング | ★2（fundamental拡充待ち・A/B先行） |
| S-04 | jpx_margin_surge | JPX日次信用残の需給シグナル | ★3（有料データ・マスター承認待ち） |

---

## 1. 実測ベースライン（提案の前提・出所明示）

### 1.1 米国株 4戦略（`backtest_results_p3.json`、2026-09-30 実行）

評価期間 2021-01-04〜2026-09-28（1,440観測）、コスト片道15bps（commission 10 + slippage 5）、bootstrap 1,000回 seed 20260930、Alpaca SIP 日足 USD、next_open 実行。

| 戦略 | CAGR | Sharpe | MaxDD | Calmar | 年間turnover | OOS CAGR | OOS Sharpe | OOS MaxDD | bootstrap 95% CI (CAGR) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 12-1 momentum | 13.72% | 0.616 | -34.64% | 0.396 | 4.45x | **-6.60%** | -0.310 | -14.38% | [-8.23%, +40.73%] |
| low-vol / inverse-vol | 13.54% | 0.773 | -35.41% | 0.382 | 2.79x | +35.37% | 2.087 | -7.25% | [-4.15%, +31.07%] |
| 20-session mean reversion | 16.84% | 0.722 | -45.89% | 0.367 | 12.76x | +16.73% | 0.724 | -28.74% | [-5.32%, +42.88%] |
| equal-weight 1/N | 16.27% | 0.735 | -50.21% | 0.324 | 0.96x | +20.00% | 0.933 | -24.27% | [-5.20%, +41.00%] |

CIOの読み（推測を含む場合は明記）:

- **momentum の IS +16.32% → OOS -6.60%**。12-1モメンタムの単純版は直近レジームで崩れている（OOS開始 2026-01-09、180観測）。単純モメンタムへの追伸投資はこの実測が反対している。
- low-volatility の OOS Sharpe 2.09 は 2025-04 以降という短い一本の窓の実測であり、将来検証ではない（レポート記載の通り）。
- **全戦略の bootstrap 95% CI がゼロをまたぐ**。単戦略の CAGR 水準は統計的に未確定。採用判断は相対比較（OOS Sharpe・MaxDD・Calmar）で行うべき。
- OOS 開始日が戦略ごとに異なる（purge幅=最大lookbackのため）。戦略間比較ではこの非対称を考慮する。

### 1.2 暗号資産 4戦略（`crypto_backtest_results.json`、2026-09-30 実行）

Binance BTCUSDT/ETHUSDT 日足（2023-01-01〜2026-09-29、1,368行）、annualization 365日、コスト同15bps。**warmup後は BTC/ETH 50/50 固定（ランキングが効かない遅延イコールウェイト）**はレポート公認の仕様。

| 戦略 | CAGR | Sharpe | MaxDD | OOS CAGR | OOS Sharpe | OOS開始 |
|---|---:|---:|---:|---:|---:|---|
| momentum (12-1) | 25.76% | 0.714 | -60.15% | +29.65% | 0.804 | 2026-04-25 (158観測) |
| inverse volatility | 23.40% | 0.675 | -58.47% | **-30.20%** | -0.465 | 2025-10-15 (350観測) |
| mean reversion (20) | 29.34% | 0.755 | -60.15% | **-29.47%** | -0.411 | 2025-09-05 (390観測) |
| 1/N | 40.44% | 0.910 | -60.15% | **-31.92%** | -0.472 | 2025-08-15 (411観測) |

3戦略の OOS が一律約 -30% であることは「selective でない＝下落局で全額暴露」の直接の帰結。**S-01 の前提実測。**

### 1.3 実ポートフォリオの参照事実（`portfolio_snapshot.json` 取得時点 2026-10-02T03:02:15 JST・評価額は取得時点のもの、時価と混同しないこと）

- 純資産合計 ¥6,556,118（証券 ¥5,541,954 / 暗号 ¥468,740 / 現金 ¥619,566 / ポイント ¥17,222 / 負債 -¥91,364）。
- 楽天証券内で「楽天・プラス・S&P500」が ¥2,745,973 ＝ 同口座 ¥5,046,466 の **約54%**。S&P500単一指数への集中が最大のリスク要因（推測を含む: 集中度リスクは S-02 の動機）。
- 暗号側は ETH ¥114,536・BNB ¥102,732・BTC ¥83,840、ステーブル（SUSDS/USDC/SYRUPUSDC/EURC）合計 ¥165,827 ＝ 暗号全体の約35%が既にステーブル。下落局での現金化という方向性は現物でも始まっている（S-01 はこれをルール化する提案）。

---

## 2. 共通実装要件（全提案に適用・CTO引き継ぎ事項）

1. **エンジン**: 既存 `run_backtest`（P2）を流用。新シグナルは `BacktestSignal` Literal（`src/yowayowa/backtest_models.py`）への追加と `run_backtests_*.py` 側の定義で対応する。**frozen contract（keirin-ai 等 他基盤）には一切触れない。**
2. **シグナル規約**: 既存どおり直前セッション終値までのシグナルで翌セッション open 実行（lagged signal / next_open）。未来参照・補完禁止。
3. **コスト**: commission 10bps + slippage 5bps（片道、売買元本）。既存デフォルトを踏襲。
4. **評価**: 全期間 / IS（先頭70%）/ purge（最大lookback）/ OOS の4区分 + IID bootstrap 1,000回 seed 20260930。既存 `run_backtests_p3.py` の summary 形式を踏襲。
5. **合格目安（提案）**: (a) OOS Sharpe > 0.5、(b) OOS MaxDD が equal-weight baseline の OOS MaxDD（-24.27%）より浅い、(c) bootstrap CI の下限が -15% を下回らない。3条件すべては要求せず、(b) は必須とする（リスク軽減が本企画の主眼）。
6. **データ規律**: Phase59R / Track B / 2025封印データは開かない。2026 forward data は観測専用でバックテスト入力にしない。
7. **報告形式**: `docs/BACKTEST_REPORT_P3.md` と同形式（指標表・IS/OOS・CI・限界・provenance）。結果は JSON でも保存。

---

## 3. 戦略提案（各提案の必須記載項目）

### S-01 `crypto_trend_dual_ma` — 暗号資産・デュアル移動平均トレンドフォロー

| 項目 | 内容 |
|---|---|
| 戦略ID / 名 | `crypto_trend_dual_ma` / Crypto dual-MA trend filter |
| 概要・投資哲学 | BTC/ETH の buy&hold は実測 MaxDD -60%。**収益源はアップサイド追随ではなく、下落局での損失深度の浅さ**。価格が長期移動平均を上回る期間のみ保有する絶対トレンドフォロー。エッジの根拠は「常に上昇する」という主張ではなく、暗号市場のボラクラスタリング（下落はゆっくり始まり急に深くなる）に対し、機械的な退出ルールがトレンド継続期間の複利を残しつつ深いドローダウン区間を現金化できる、という構造主張。既存 crypto 戦略が「selective でない」ことが実測上の最大欠陥であり、本提案は最初の selective 暗号戦略になる。 |
| 対象ユニバース | BTC, ETH（Binance USDT 日足・現行 `crypto-ohlcv` ストアそのまま、データ追加不要） |
| シグナル算出 | 資産ごとに独立判断。`SMA200 = 直近200セッション終値の単純平均`。`close(t-1) > SMA200(t-1)` → 当該資産を保有、`<=` → 当該資産を現金（USDT建て・無利息）。シグナルは実行セッションの直前セッション終値で確定（lagged） |
| パラメータ | lookback 200セッション。感度分析として 100/150/200/300 の4本をレポートに付記（最良値のみ採用する逆検証（overfit検出）用。200が一択で良い理由を示す） |
| リバランス | daily 判定（実質変位は年2〜4回程度を想定・実測で確認） |
| ウェイト | 発火資産の等金額（active 1/N: 2資産発火なら各50%、1資産なら100%、0資産なら全現金） |
| リスク管理 | ポジション上限2、レバなし、個別資産の現金化、ストップロス相当はMA退出ルールに内包。現金シフトの連続日数・発火回数をレポートに記録 |
| 実装メモ | `BacktestSignal` に `crypto_trend_dual_ma` を追加。crypto runner（`run_backtests_crypto.py`）は inline 定義なので、既存4戦略を残しつつ本戦略を5つ目に追加実行。annualization_days=365 を踏襲。現金期間の日次リターン0が観測数に残る既存仕様に注意（OOS観測数の解釈） |
| 期待検証・合格目安 | OOS MaxDD が既存 -58%〜-60% から **-30%台へ半減**するか。OOS Sharpe が既存 -0.41〜-0.47 から **正転**するか。full CAGR は buy&hold(1/N 40.44%) を下回る可能性を正面から受け入れる（リスク調整後で比較する） |
| 重視指標 | **MaxDD・Calmar・OOS Sharpe**（CAGRは二次） |

### S-02 `vol_target_defensive` — ボラティリティターゲティング・ディフェンシブオーバーレイ

| 項目 | 内容 |
|---|---|
| 戦略ID / 名 | `vol_target_defensive` / Volatility-targeted equity with cash overlay |
| 概要・投資哲学 | ボラティリティは正の自己相関（vol clustering）を持つ。**リスクを一定に保つ配分は timing ではなく複利形状の改善**（高ボラ期の巨大な損失が複利を破壊する variance drag の抑制）を狙う。実ポートフォリオの S&P500ファンド約54%集中（§1.3）に対し、「指数を持続保有しつつリスクだけ制御する」現実的な解。レバは使わず株式⇔現金の配分のみで実装する（個人口座の制約に適合） |
| 対象ユニバース | SPY, IWM, JNJ, PG + 残余現金（すべてOHLCV取得済み・USD。現金は無利息USD想定と明示） |
| シグナル算出 | `σ21 = SPY 直近21セッション日次リターンの標本標準偏差`。`w = min(1.0, σ*/σ21)`、`σ* = 年15%目標 = 0.15/√252 ≈ 0.945%（日次）`。株式側に w を配分し残余 (1-w) は現金。σ21 は直前セッションまでで確定 |
| パラメータ | lookback 21セッション、目標ボラ年15%、w のハードキャップ 1.0（レバ禁止）。感度分析: 目標ボラ 10%/15%/20% |
| リバランス | **weekly**（月次ではボラ急変への追従が遅い。turnover 増がコストでαを食わないかを必ず実測し報告） |
| ウェイト | 株式内部は等金額（4資産 × w/4） |
| リスク管理 | レバなし（w<=1.0 hard cap）、最大ポジション4 + 現金。ボラ急騰時（σ21が目標の2倍超）の w と実測ターゲティング誤差を記録 |
| 実装メモ | **既存エンジンの部分現金（残余ウェイトを現金保有）対応が最優先の実装リスク**。`BacktestSignal` に `vol_target` を追加。等金額の中に「現金」という資産を差し込む形か、weighting 関数の拡張になる。他4戦略と異なりエンジン改修を伴う点を CTO に強調 |
| 期待検証・合格目安 | equal-weight baseline 比: Sharpe 0.735→**0.9以上**、MaxDD -50.21%→**-30%台**。OOS（2025-01以降）の下落局面で w が実際に縮んでいたかを equity curve と併記で示す |
| 重視指標 | Sharpe・MaxDD・年間turnover（weekly化コストの妥当性） |

### S-05 `defensive_rotation` — 防衛セクター・クオリティローテーション

| 項目 | 内容 |
|---|---|
| 戦略ID / 名 | `defensive_rotation` / Risk-on / defensive rotation |
| 概要・投資哲学 | low_volatility の OOS Sharpe 2.09（実測）は「防衛銘柄が強い」レジームで生きた。**市場勢いが生きている時だけモメンタムを追い、失速したら防衛銘柄＋現金へ寄る**スイッチ型。momentum の OOS崩壊（-6.60%）を、レジームスイッチで救えるかが仮説。単一のエッジ（momentum）への依存を二つの既知エッジ（momentum＋low-vol）のスイッチに分散する |
| 対象ユニバース | SPY, QQQ, IWM, JNJ, PG, XOM, JPM, AAPL, MSFT, NVDA（10銘柄。**QQQ/IWM/JNJ/PG はデータ取得済み・追加取得不要**） |
| シグナル算出 | リスクオン指標 `R = (SPY 20セッションリターン > 0) かつ (SPY(t-1) > SMA100(t-1))`。R=1 → 成長側: 既存12-1 momentum（252-21）上位5銘柄等金額。R=0 → 防衛側: JNJ/PG/XOM/JPM の low_vol（60セッションσ）上位3銘柄 等金額。シグナルは毎リバランス日に再判定 |
| パラメータ | momentum 252/21、リスクオン判定 20日リターン + SMA100、防衛側 low-vol 60セッション |
| リバランス | monthly、execution next_open（シグナル失効セッションでの緊急退出は daily 再判定オプションでA/B比較） |
| ウェイト | equal |
| リスク管理 | max_positions 5、防衛側でも 3銘柄＋現金は無し（70%株式+30%現金相当の意図。実装を単純にするため防衛側は3銘柄等金額とする） |
| 実装メモ | 既存 `momentum_12_1` と `low_volatility` の組合せスイッチ。`BacktestSignal` に `risk_on_switch` を追加。スイッチ回数・防衛側滞在日数をレポートに記録 |
| 期待検証・合格目安 | momentum 単独の OOS CAGR -6.60% が **正転またはゼロ近傍まで改善**するか。MaxDD が momentum の -34.64% より浅いこと。2026-01以降 OOS 区間の振る舞いを重点報告 |
| 重視指標 | OOS Sharpe・MaxDD・Calmar |

### S-03 `garp_momentum_hybrid` — ファンダメンタル×モメンタム・ハイブリッド（GARP）

| 項目 | 内容 |
|---|---|
| 戦略ID / 名 | `garp_momentum_hybrid` / GARP: momentum with PBR filter |
| 概要・投資哲学 | momentum の OOS崩壊の一因として「高バリュエーション銘柄への集中」を仮定（推測）。**割安度フィルタを通したモメンタム（GARP）**は、勢いと割戻しの両方を要求する二重条件で選抜数が減る代わりに個別銘柄の裏取りが増える構造。既存エンジンの point-in-time fundamental 入力枠（`pbr/per` + `disclosure_date` 厳守）をそのまま使う |
| 対象ユニバース | 現行10銘柄（AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM, XOM, SPY）。fundamental は供給入力前提 |
| シグナル算出 | 二段階ランキング。(1) 12-1 momentum スコア（252-21セッションリターン）上位8銘柄を候補に。(2) 候補内で point-in-time **PBR 最小の5銘柄**を選択。PBRは「価格バー日付と disclosure_date がともに実行日より厳密に前」の最新行から取得（既存契約を再利用）。候補全銘柄でPBR欠損の場合は momentum-only フォールバックし warnings に明示 |
| パラメータ | momentum 252/21、一次候補8、採用5 |
| リバランス | monthly、execution next_open |
| ウェイト | equal（5分割） |
| リスク管理 | max_positions 5 |
| 実装メモ | `value_fundamental` / `kiyohara_value` と同じ disclosure 契約を再利用。**A/B必須**: (a) momentum-only、(b) PBRフィル版 の2runでαの帰属分解。**限界の明記**: fundamental データは現在手動供給分のみでカバレッジが薄い。PBRデータの拡充（10銘柄×期間）が本戦略の前提条件であり、データ整備を別タスク化推奨 |
| 期待検証・合格目安 | momentum 単独比で OOS Sharpe 改善（マイナスからの回復）と **IS-OOS CAGRギャップ（+16.3%→-6.6%の22.9pt）の縮小**。fundamentalカバレッジ不足期間の除外状況を warnings で可視化 |
| 重視指標 | OOS Sharpe・IS-OOSギャップ・hit rate |

### S-04 `jpx_margin_surge` — JPX日次信用残需給シグナル（データ蓄積と並行）

| 項目 | 内容 |
|---|---|
| 戦略ID / 名 | `jpx_margin_surge` / JPX daily margin-balance supply/demand signal |
| 概要・投資哲学 | 銘柄別信用買い残の急増＝レバレッジ買いの加熱（需給脆化・押し目の深さ）／売り残急増＝踏み上げ余地。**日次・全銘柄カバーの新サービス（2026-09-28開始）は日本株需給データの解像度を週次から日次へ上げる**。需給は価格モメンタムと独立した情報源であり、ポートフォリオの分散源になる |
| 対象ユニバース | 日本株（初回は TSE 個別10〜20銘柄を想定）。**前提条件: 日本株日足OHLCVの取得基盤が現状ない（Alpaca米国株＋Binance暗号のみ）**。データ取得基盤の拡張が先行必須 |
| シグナル算出 | 信用買い残5日変化率を過去60日分布で標準化: `z = (buy_balance / buy_balance[-5d] - 1) / σ60d`。`z > +2.0` → 過熱（除外）、`z < -2.0` → 短押し買い候補。20日モメンタムと併用（需給のみでの順張りはしない） |
| パラメータ | 5日変化・60日標準化・閾値±2.0 |
| リバランス | weekly |
| ウェイト | equal、max 5 |
| リスク管理 | max 5、z閾値退出 |
| 実装メモ | データ契約仕様は `docs/JPX_DAILY_MARGIN.md` に確定済み（J-Quants Pro / TMI 参照・**有料契約**）。**データ取得にはマスターの承認が必要**なため本戦略は別カード起票を推奨。 |
| 期待検証・合格目安 | 第一歩はバックテストではなく **event study**（シグナル発生日の±20営業日リターン分布）。日次データの蓄積が90日以上（目安2027年明け）になってから walk-forward バックテストを実施 |
| 重視指標 | hit rate・Sortino |

---

## 4. 実装順序の推奨（CTOへの引き継ぎ）

1. **S-01 crypto_trend_dual_ma**: 新signal 1個のみ、データ即利用可、crypto runner への追加だけで検証完了まで最短。感度分析100/150/200/300込み。
2. **S-02 vol_target_defensive**: 部分現金ウェイトのエンジン対応確認が要件（唯一のエンジン改修ポイント）。日次→weeklyリバランス対応も同時に確認。
3. **S-05 defensive_rotation**: 既存2 signalのスイッチ。データ追加なし（QQQ/IWM/JNJ/PG取得済み）。
4. **S-03 garp_momentum_hybrid**: PBR供給データの拡充を別タスク化し、momentum-only との A/B から先行。
5. **S-04 jpx_margin_surge**: 有料データ契約・日本株OHLCV基盤が前提。**マスター承認を要するため別カード**（event study から着手）。

各戦略の完了定義: `run_backtest` で status=complete、full/IS/OOS 指標・bootstrap CI・turnover・限界を `BACKTEST_REPORT` 形式で報告し、§2-5 の合格目安との対照表を添える。

## 5. リスク注記・限界（本書の効力）

- 本書は**分析であり、実弾の売買・発注を含まない・承認しない**。バックテスト実装・検証は CTO/作業員レーン、実行判断はマスター。
- 単一レジーム（株2021-2026・暗号2023-2026）の結果は将来を保証しない。既存全戦略の bootstrap 95% CI がゼロ跨ぎという実測から、**採用判断は OOS 相対比較とリスク指標（MaxDD/Calmar）で行う**ことを CIO として推奨する。
- 固定ユニバース由来の survivorship bias、配当・分割未調整、税・market impact・borrow コスト未モデル化はレポート既記載の通り継続する限界。
- 現金想定は無利息。実運用の USD 現金利回りとは乖離する（S-02/S-01 で保守的側に倒れる方向）。
- JPX日次信用残データの取得（S-04）は有料契約を伴い、別途マスター承認を要する。

---

### 付録: 調査ログ（本書の出所）

- `src/yowayowa/backtest_strategies.json`（既存6定義）・`backtest_models.py`（signal Literal・rebalance/weighting 型）・`backtest.py`（P2エンジン・指標定義）・`strategy_signals.py`（日次スクリーニング契約）を実読。
- `backtest_results_p3.json` / `crypto_backtest_results.json`（2026-09-30実行）から指標を抽出（§1の数値はすべてここから）。
- `docs/BACKTEST_REPORT_P3.md` / `docs/BACKTEST_REPORT_CRYPTO.md` / `docs/BACKTESTING.md`（実装制約・限界の規範的記述）。
- `data/stock-ohlcv/`（14銘柄・2019-12-30〜2026-09-29・USD 確認）、`data/crypto-ohlcv/`（BTC/ETH・2023-01-01〜2026-09-29・USDT）、`data/macro-observations/`（FRED/BLS/財務省）の棚卸し。
- `docs/JPX_DAILY_MARGIN.md`（日次信用残の公開仕様・有料契約）。
- `portfolio_snapshot.json`（2026-10-02T03:02:15 JST 取得時点の資産配分）。
