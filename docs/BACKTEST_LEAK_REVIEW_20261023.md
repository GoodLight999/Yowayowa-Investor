# バックテスト未来情報リーク / 過剰適合 静的レビュー（2026-10-23）

対象: `src/yowayowa/services/backtest.py`（849行）、`src/yowayowa/services/strategy_calibration.py`（569行）
参照（入力経路の確認のみ）: `services/strategy_outcomes.py`, `services/backtest_definitions.py`,
`backtest_strategies.json`, `providers/alpaca.py`, `sec_pbr.py`, `run_backtests_*.py`, `run_sensitivity_crypto_trend.py`, `docs/BACKTESTING.md`

制約遵守: コード変更なし / git操作なし / 外部送信なし / 実注文なし / バックテスト本体は未実行。
実施したのは静的解析と、永続OHLCV（`data/stock-ohlcv`, alpaca, 2019-12-30〜2026-10-21, 1711行/銘柄）の読み取り集計のみ。

## 0. 結論

- シグナル→約定の境界は**リークしていない**。全シグナル関数が `day < decision_date` を厳格に適用し、
  約定は当日 `open`。当日終値で当日判断する経路は 0 件（下記 §1 に全経路の行番号）。
- 財務データは PIT 整合。実データで `disclosure_date = period_end + 32〜34日`、`filed == 初出バー日` を確認（§4-2, §7）。
- 一方で**報告値を実質的に無意味化する欠陥が2件**ある。①生価格（分割未調整）での損益計算、②ユニバース選択。
  ①は同一窓の等加重B&Hで**CAGR 9.2pp 過小**（16.68% vs 25.86%）、②は**SPY超過 12.3pp/年**がシグナルではなく銘柄選択（§3 C1/C2 に実測値）。
- 過剰適合の痕跡は「コード内の探索」ではなく**ランナー側の同一期間探索**と、**IS/OOS を検証と読める形で出している点**。

## 1. 時系列順序の追跡（関数別）

| 段階 | 関数 / 行 | 時刻の取り方 | 取り違え候補 |
|---|---|---|---|
| 取得 | `run_backtest` 484-541 | `session = _date(row["as_of"])`、`session > request.end` を除外。`request.start` 以前の行は**残す**（ルックバック用） | なし（将来行は捨てている） |
| 取得 | 543-559 | ユニバース全銘柄に窓内セッションがあること、かつ**全銘柄で同一セッション集合**を要求 | C1（生存者のみ実行可能） |
| 特徴量 | `_returns` 140-141 | 価格系列の対前日比 | なし（非正価格はスキップ、長さガードは各呼出側） |
| 特徴量 | `_garp_momentum_candidates` 159-190 | `history = sorted(day for day in rows if day < decision_date)` | なし（厳格 `<`） |
| 特徴量 | `_garp_point_in_time_pbr` 193-214 | バー日 `< decision_date` かつ `disclosure_date < decision_date` | なし（二重に厳格） |
| 特徴量 | `_risk_on_regime` 144-156 | SPY の `day < decision_date` のみ | なし |
| シグナル | `_target_weights` 217-437 | 全分岐で `day < decision_date`（150, 170, 199, 276, 313, 334, 347, 393, 428） | なし |
| 約定 | 621-698 | 前日終値→当日 `open` のオーバーナイト、当日 `open`→`close` のイントラデイ、コストは `open_weights` 基準 | C9（`open` 欠損時）, C13（OOS初日） |
| 評価 | `_metrics` 50-96, `_bootstrap_cagr` 99-117 | 日次リターン系列のみ。`annualization_days` は request 由来 | C3, C7 |
| 評価 | 812-832 | 70%地点で IS/OOS 分割、`warmup` セッションを purge | C4, C11 |
| 集計 | `calibration_report` 275-569 | `captured_at` 昇順で OOS 末尾 m 件、`exit_at > split_at` を purge | C5, C10, C12 |

## 2. 候補一覧（サマリ）

| ID | 場所 | 分類 | 優先度 |
|---|---|---|---|
| C1 | backtest.py:484-507, 543-559, 760 | 生存者・選択バイアス | 高 |
| C2 | backtest.py:495-497, 627-647, 690-698 / alpaca.py:131 | 分割未調整価格 | 高 |
| C3 | backtest.py:63-73, 700-713 | 無リスク金利0（評価） | 中 |
| C4 | backtest.py:738-742, 812-832 | 単一分割・全期間主指標・多重比較 | 高 |
| C5 | strategy_calibration.py:189-211, 393-447 | OOSのコホート化・m=10 | 高 |
| C6 | strategy_calibration.py:123-138, 341-384 | 全期間decile/IC・境界の未来情報 | 中 |
| C7 | backtest.py:99-117 / strategy_calibration.py:214-240 | IIDブートストラップ | 中 |
| C8 | strategy_outcomes.py:169-184（calibration入力） | 超過リターンのβ未調整 | 中 |
| C9 | backtest.py:496, 681-685 | open欠損時の約定＝終値 | 低 |
| C10 | strategy_calibration.py:46, 203 | purgeの時刻粒度 | 低 |
| C11 | backtest.py:815-818 | purged_sessions のoff-by-one | 低 |
| C12 | strategy_calibration.py:105-120, 337 | sample_countがNoneを含む | 低 |
| C13 | backtest.py:128-137, 611 | OOS初日の強制リバランス | 低 |
| C14 | run_sensitivity_crypto_trend.py:24,113 / run_backtests_defensive_rotation.py:50 | 同一期間でのパラメータ探索 | 中 |

## 3. 候補詳細

### C1 ユニバース固定＝生存者・選択バイアス（優先度: 高）

- 場所: `backtest.py:487`（`strategy.universe` から `rows_by_symbol` を構築）、`backtest.py:543-559`
  （全銘柄に窓内セッションを要求。上場廃止・途中上場の銘柄は実行不能で 422/insufficient）、
  `backtest.py:760`（「survivorship bias may remain」と免責のみ）。母集団は `backtest_strategies.json` の10銘柄。
- 再現条件: 2021-01-04〜2026-10-20 の窓で全戦略を実行（`run_backtests_p3.py:31` の既定窓）。
  ユニバースは AAPL/MSFT/NVDA/GOOGL/AMZN/META/TSLA/JPM/XOM/SPY で、**2026年時点の勝者を後から選んだ10銘柄**。
  さらに「同一セッション集合」制約により、途中で消えた銘柄を入れると実行自体が拒否される。
- 歪みの概算（**実測**）: 同一窓・分割補正後・配当なし・月次リバランスなしの等加重B&Hで
  CAGR 25.86%（10銘柄） vs 13.56%（SPY）＝**+12.3pp/年**。累積では +277.3% vs +108.4%。
  前提: 分割のみ補正、配当・コスト・リバランスは未考慮、SPY は同一 store の生価格。
  戦略側は月次リバランスとコストで目減りするが、**報告CAGRの10pp超はシグナルではなく銘柄選択で説明できる**。
- 優先度根拠: 全戦略・全指標に一律に乗る。個別銘柄選択の効果とシグナルの効果が分離できない。

### C2 分割未調整の生価格で損益を計算（優先度: 高）

- 場所: `backtest.py:495-497`（`close`/`open` をそのまま使用）、`627-647`（オーバーナイト）、
  `690-698`（イントラデイ）。価格の出所は `providers/alpaca.py:131` の `"adjustment": "raw"`。
  `backtest.py:758` に「split adjustments are not modeled」とあるが、免責1行のみで補正処理はない。
- 再現条件: 窓内に分割がある銘柄を保有した状態で、分割当日をまたぐ。実データで確認した5件:
  NVDA 2021-07-20（4:1, 当日 -75.2%）、NVDA 2024-06-10（10:1, -89.9%）、GOOGL 2022-07-18（20:1, -95.1%）、
  AMZN 2022-06-06（20:1, -94.9%）、TSLA 2022-08-25（3:1, -66.8%）。
  いずれも `open/前日close` が 0.05〜0.34 で、**分割比率どおりの段差**（例 GOOGL: 2235.55 → 112.64）。
- 歪みの概算（**実測**）: 等加重B&H 10銘柄で 生価格 CAGR 16.68% / 分割補正後 25.86%、
  終端資産 +143.7% / +277.3%。**生価格は終端資産を 54.8% 過小**にする（CAGR -9.2pp）。
  前提: 分割比率は価格比から推定、補正は分割日の以前を比率で除算、配当は未補正。
- 方向: 一様に**悲観**（リターンを破壊する）。ただし副次的に**楽観にも働く**:
  分割直後の銘柄は252セッションのモメンタムが巨大な負値になり、`momentum_12_1` の上位5から
  1年近く排除される。つまり「実際には勝っていた銘柄を、見かけの暴落で除外」する。
  結果として選抜結果そのものが実在しない戦略になる（どちらの方向にも解釈不能）。
- 優先度根拠: 5イベント×保有ウェイト20%×67〜95% = 1イベントあたり最大 -18% の株価が消える。
  実測の -9.2pp/年 は他のどの候補よりも大きい。

### C3 無リスク金利0のSharpe/Sortino・現金0%（優先度: 中）

- 場所: `backtest.py:63-67`（`average / volatility * sqrt(252)`、`average` は生の日次リターン）、
  `68-73`（Sortino も同様）、`700-713`（現金は「無利子USD」で推移）。
- 再現条件: 現金比率が高い戦略（`vol_target_defensive`: 残差 `1-w`、`defensive_rotation` の欠員枠）や、
  高い政策金利期（2022-2024）を含む窓で評価する。`docs/BACKTESTING.md:22` に
  「not a risk-free-rate-adjusted Sharpe」と明記済み＝既知の制限だが、レスポンスの `sharpe_ratio` 自体には注記がない。
- 歪みの概算（**推測**）: ΔSharpe ≈ RF/σ。前提: 窓平均 RF 4.5%/年（未実測）、戦略σ 15% → **-0.30**。
  平均投資比率 50〜70% の戦略なら -0.15〜-0.21。逆に現金0%はCAGRを過小にするので、**Sharpeは楽観／CAGRは悲観**の非対称。
- 優先度根拠: 既知・文書化済み。ただし単独で Sharpe 0.2〜0.3 を動かすため、戦略比較の順位を変え得る。

### C4 単一IS/OOS分割・主指標は全期間・多重比較の補正なし（優先度: 高）

- 場所: `backtest.py:812-832`（`split_index = int(len(window_dates) * 0.7)`、`oos_index = split_index + warmup`）、
  `738-742`（`status` は「全指標が available か」だけ）、`821-828`（同一 `strategy` を再帰呼出＝再推定なし）。
- 再現条件: 出荷済み6戦略を 2021-01-04〜2026-10-20 で実行。実測ジオメトリ（1455セッション、split_index=1018）:
  `momentum_12_1` は OOS が **184セッション（2026-01-28〜）**、`low_volatility` 376、`equal_weight`/`value_*` 437。
  252営業日ウォームアップを要求する戦略では、OOSが約9か月しか残らない。
- 歪みの概算（**構造的、数値は未計測**）: 6戦略×3ホライズン（calibration側は18バケット）から
  「全期間CAGRが最も良いもの」を選び、その `oos_metrics` を検証として読む運用が可能。
  この selection は 6 回の試行に対する補正がなく、期待最大値は ΔSharpe で +0.3〜0.6 程度持ち上がる（**推測**、試行間相関 0.5 前提）。
  加えて `status="complete"` は「検証済み」を意味しないが、消費者が warnings を読まない限り区別できない。
- 優先度根拠: コードのバグではなく**提示の設計**。OOS 184セッションは検出力が低く、
  「OOSでもプラス」を通しやすい。`docs/BACKTESTING.md:63` も「single split, not repeated rolling walk-forward」と認めている。

### C5 calibration のOOS末尾が同時刻コホートになり得る＋m=10（優先度: 高）

- 場所: `strategy_calibration.py:189-198`（`sorted(..., key=(captured_at, snapshot_id))` の末尾 m 件を OOS、
  `split_at = oos[0].captured_at`）、`393-447`、`405`（ICは10件未満で省略）、`403`（`excess_return` 欠損は除外）。
- 再現条件: スクリーナーが**同一バッチで多数銘柄を同時刻に捕捉**する場合。`captured_at` が同値のとき
  `snapshot_id` 昇順で切るため、OOS末尾 m 件が「同一時刻の1コホート」になり得る。
  その場合 `split_at` は最新コホートの時刻になり、ISは「それ以前に窓が閉じた全件」＝時系列分割ではなく
  **直近コホートのクロスセクション切り出し**になる（purge は発動しない）。
- 歪みの概算（**推測、実データ未確認**）: OOS IC は実効サンプルが「1〜2コホート」に落ち、
  報告される `oos_rank_ic` の信頼性は名目 n=10 より大幅に低い。方向は運任せだが、
  **「out-of-sample で確認した」という主張だけが独り歩きする**点が楽観方向。
  加えて、捕捉間隔が週次・ホライズン60日なら窓の重複は約12倍、OOSの実効独立本数は 10/12 ≒ 1。
- 優先度根拠: このモジュールの出力は「検証済み」の根拠として使われる。分割の実装自体は設計どおり
  （テスト T1-T8 で境界を検証済み）だが、**入力の捕捉構造に依存して意味が変わる**ことが明示されていない。

### C6 全期間での decile/rank_ic 算出（境界が未来情報）・18バケット多重比較（優先度: 中）

- 場所: `strategy_calibration.py:123-138`（`_score_deciles` は `available` 全件で十分位を作る）、
  `93-102`（`_decile_groups` は全件ソートの順位で境界を決める）、`358-384`（`rank_ic` は全件、OOS込み）。
- 再現条件: `available` が OOS を含む状態で `score_deciles` / `rank_ic` を読む。
  十分位の境界は「将来のスナップショットのスコア分布」で決まるため、上位十分位の平均超過は
  リアルタイムには再現できない（境界が未来情報）。
- 歪みの概算（**未計測**）: スコアが決定的（`strategy_priority.py` はファンダメンタルと証拠点のみ、成績非依存）で
  分布が安定なら影響は小。分布が時間でドリフトする場合、上位十分位の平均超過は境界ドリフト分だけ上振れ。
  18バケット（6戦略×3ホライズン）から最良の `rank_ic`/`oos_rank_ic` を選ぶ行為に補正はない。
- 優先度根拠: 主指標（`rank_ic`, decile単調性）の読み方を誤らせるが、数値の大きさは入力依存で断定できない。

### C7 IID ブートストラップ（優先度: 中）

- 場所: `backtest.py:99-117`（日次リターンを独立復元抽出）、`strategy_calibration.py:214-240`
  （同じくIID、既定 2000回）。
- 再現条件: 自己相関・ボラティリティクラスタリングのある日次系列、または窓が重複する forward outcome。
- 歪みの概算（**推測**）: 日次ρ1=0.05〜0.15、ボラクラスタを仮定すると実効サンプルは名目の 0.6〜0.8。
  CI幅は **1.1〜1.3倍過小**（楽観）。calibration側は窓の重複ぶんさらに悪化し、週次捕捉×60日ホライズンなら 1.5〜3.5倍過小。
- 優先度根拠: 有意性の主張を強めるが、点推定は歪めない。`bootstrap_status="available"` が
  「IID仮定の下で」であることが出力に現れない。

### C8 超過リターンが「生リターン−指数リターン」でβ未調整（優先度: 中）

- 場所: `strategy_outcomes.py:169-184`（`excess_return = total_return - benchmark_return`）、
  `50-61`（ベンチマークは同一カレンダー窓の最初/最後の終値）。本モジュールが calibration の唯一の入力。
- 再現条件: 高βのテック中心ユニバース（本件の10銘柄）に対し ^GSPC を引く。
  `excess = α + (β-1)·m` なので、上昇相場ではβの超過分がすべて「超過リターン」に計上される。
- 歪みの概算（**推測**）: β=1.1〜1.2 とすると、120日ホライズンで市場が +5〜9% の局面では
  観測あたり **+0.5〜1.8pp** の見かけの超過。これは50日/120日の中位超過（数pp）と同オーダー。
  窓全体（SPY累積 +108.4%、実測）では累積 +11〜+22pp がβ由来。
- 優先度根拠: `positive_excess_hit_rate` と `rank_ic` の両方に乗る。βを測っていないので断定は不可だが、
  ユニバースの性質上ほぼ確実に正の寄与。

### C9 `open` 欠損時に約定価格が当日終値へ黙って変わる（優先度: 低）

- 場所: `backtest.py:496`（`opening = float(row.get("open", close))`）、`630-632`, `681-685`, `690-694`。
- 再現条件: 行に `open` が無い、または `open <= 0`（後者は行ごと捨てるため §3 C1 のセッション一致検査に波及）。
- 歪み: **リターン合計は代数的に不変**（overnight × intraday = close(t)/close(t-1)）。
  変わるのは (a) 約定が翌営業日寄付ではなく当日終値になる（`execution="next_open"` の宣言と不一致）、
  (b) コスト・ターンオーバーの計上時刻、(c) エクイティカーブの overnight/intraday 分解。%
  で言えば1日ぶんの attribution 誤差で、累積リターンは変わらない。**楽観バイアスではない**。
- 優先度根拠: 宣言と実装の不一致。リターンには効かないため低。

### C10 purge の時刻粒度（`exit_at` が日付ラベル）（優先度: 低）

- 場所: `strategy_calibration.py:46`（`PurgedSplit`）、`203`（`exit_at > split_at` で purge）。
- 再現条件: `exit_at` は価格バーの timestamp（永続storeでは `05:00Z` = 日付ラベル）、
  `split_at` はスナップショットの実時刻。**同種の時刻ではない**ものを比較している。
  split当日 05:00Z に閉じた窓は、その日の 09:00Z の split より「前」と判定され IS に残る。
- 歪み: 最大1セッションぶんの「分割後にしか知り得ない終値」がIS側に残る。ISは記述統計なので実害は小。
- 優先度根拠: 境界の1セッション。`purged_count` の意味も1件ずれ得る。

### C11 `purged_sessions` の off-by-one（優先度: 低）

- 場所: `backtest.py:817`（`purged_sessions = min(warmup, max(len - split_index - 1, 0))`）。
  `is_end` は index `split_index-1`、`oos_start` は index `split_index+warmup`。
  したがって実際に挟まるセッション数は **warmup+1** だが、報告値は warmup。
- 再現条件: 常に。`momentum_12_1` なら「253セッションpurge」と出るが実際は254。
- 歪み: 報告のみ。数値の解釈に実害なし。

### C12 `sample_count` が `None` リターンを含む（優先度: 低）

- 場所: `strategy_calibration.py:111-120`（`sample_count=len(rows)`、統計は `None` を除外）、
  `337`（bucket全体の `statistics`）。
- 再現条件: `available` でも `total_return`/`excess_return` が `None` の行が混じる
  （ベンチマーク欠損・価格欠損）。`StrategyCalibrationMinimumSampleThreshold=30` /
  `DecileMinimumSample=10` の判定に `None` 行が算入される。
- 歪み: サンプル数を過大に見せ、`minimum_sample_warning` を消す方向（楽観）。
  実効nが半分なら警告は本来出るべき。%表示は不可（入力依存）。

### C13 OOS セグメント初日に強制リバランス（優先度: 低）

- 場所: `backtest.py:611`（`_rebalance_dates(window_dates, ...)` は先頭を必ず選択）、`128-137`。
- 再現条件: OOS 実行の `request.start = oos_start` は月の途中（`momentum_12_1` なら 2026-01-28）。
  内部実行の `window_dates[0]` が無条件にリバランス日になるため、**戦略が本来取らない日に建玉を作る**。
- 歪み: 初回建玉コスト 15bps × 回転率 ≒ 100% = **-0.15%**（1回限り）。OOSの水準をわずかに押し下げる。
  実害は小さいが、ISとOOSでリバランス暦が非対称になる。

### C14 同一期間でのパラメータ探索（ランナー側）（優先度: 中）

- 場所: `run_sensitivity_crypto_trend.py:24`（`LOOKBACKS = (100, 150, 200, 300)`）、`113-150`、
  `run_backtests_defensive_rotation.py:50`（signal×frequency×weighting×max_positions の変種表）、
  `run_backtests_vol_target.py`（週次 10/15/20%）、`run_backtests_p3.py:31`（評価開始を 2021-01-04 に固定）。
- 再現条件: ランナーを同一窓で回し、結果JSONを見てから出荷既定（lookback 200、max_positions 5、monthly）を決める。
  コード上の `_target_weights` 既定値（`or 200`, `or 21`, `target_volatility or 0.15`）と一致しており、
  **探索窓＝評価窓**であることが読み取れる。`run_sensitivity_crypto_trend.py:183-184` は
  「no post-hoc best selection」と自己申告しているが、機構としては選択可能。
- 歪み: 変種数 k に対する最大値の上振れ。k=4（lookback）で ΔSharpe +0.2〜0.4 程度（**推測**）。
- 優先度根拠: 対象2モジュールの外側だが、「パラメータ探索と評価に同一期間を使用」の該当箇所はここ。

## 4. 検査して問題なしと判断した項目（除外理由）

1. **当日終値で当日判断**: 該当なし。`_target_weights` の全分岐と特徴量関数が `day < decision_date` を厳格適用
   （150, 170, 199, 276, 313, 334, 347, 393, 428）。約定は当日 `open`（630-632, 681-685）。
   `close(t)` を判断に使う経路は0件。**「next_open」宣言と実装は一致**（`open` 欠損時を除く＝C9）。
2. **未来の財務データ参照**: 該当なし。`_garp_point_in_time_pbr:199-207` と `_target_weights:347-358` が
   バー日と `disclosure_date` の**両方**に厳格 `<` を要求。上流 `sec_pbr.build_pbr` は `filed <= bar日` で
   PBRを付与し、実データで `filed = period_end + 32〜34日`、初出バー日 == `filed`（バックデートなし）を確認。
   同日開示を許す `join_pbr:323` の `disclosure_date > day` 判定（非厳格）も、backtest側が翌営業日以降しか
   使わないため実害なし。
3. **リバランス日のずれ**: 月初の最初の共通立会で判断・翌営業日…ではなく当日寄付。前月末終値→月初寄付は整合。
   `weekly` は ISO 週キー。月跨ぎの二重リバランスなし（`_rebalance_dates:128-137`）。
4. **手数料・スリッページの適用漏れ**: なし。`turnover = Σ|target - open_weight|` は売買両建てを含むため
   片道表記に対して**保守的**（回転1回で 30bps）。初回建玉・全解約・ゼロ目標も課金される（650-689）。
   唯一の非課金は「目標が空で建玉保持」の分岐（661）だが、これは取引しないので正しい。
5. **`warmup` と参照範囲**: 窓前バーはシグナルのみに使い、リターンは要求区間内（608-624）。
   `dates` に窓前が混じるのは `prior_session` 設定のためだけで、`daily_returns` には入らない。
6. **IS/OOS のデータ分離**: `is_request` は `end=is_end`、`oos_request` は `start=oos_start` で再帰呼出し、
   内部でも `session > request.end` を落とすため、IS が OOS 期間の値を見ることはない（812-828）。
   OOS が purge 期間をシグナルのルックバックに使うのは設計どおり（リターンには入らない）。
7. **欠測の扱い**: 前方補完・ゼロ埋めなし。`_returns` は非正価格を捨て、呼出側が長さを検証（287, 320, 431）。
   欠測時は「建玉保持」で、これは `backtest.py:750` と `docs/BACKTESTING.md` に明示された仕様。
   ただし**データ欠測時にだけ売らない非対称性**は結果を歪め得る（方向は銘柄次第）。
8. **品質ガード**: 重複セッション検出（499-500）、通貨混在拒否（560-571）、provenance必須（511-517）、
   provenance混在拒否（525-528）。いずれもリークではなく防御として機能。
9. **スコアの非依存性**: `strategy_priority.py` のスコアはファンダメンタルと証拠点のみで成績に依存しない。
   したがって calibration の IS/OOS は「モデル再学習の検証」ではなく記述統計である（`oos_notes` もそう書いている）。
   **この点は過剰適合ではない**（ただし C5/C6 の読み方問題は残る）。
10. **`value_fundamental` / `kiyohara_value` / `garp` の実データ**: API経路の OHLCV store には
    `pbr`/`disclosure_date` が**1行も入っていない**（1711行×10銘柄で0件、実測）。したがって現状のAPI実行では
    これらは常に現金待機となり、値の妥当性は評価不能。サイドカー `data/fundamentals/pbr/*.jsonl` は
    別経路（ランナーの `join_pbr`）でのみ結合される。

## 5. 未確認範囲

- **戦略レベルの実額は未計測**。C1/C2 の実測は等加重B&Hの代理指標であり、`run_backtest` は実行していない
  （重い実行は禁止）。したがって「momentum_12_1 のCAGRが何pp動くか」は未測定。
- `filed` の原データ粒度（SEC submissions の acceptance datetime か date か）は未確認。
  サイドカー上の `disclosure_date` は日付で、period_end+32〜34日という整合は確認したが、
  当該日の**何時**に公開されたかは分からない（同日終値をPBRに使う設計のため、寄付前公開の前提が要る）。
- `data/fundamentals/pbr/split-calendar.json` の中身と、それが C2 の補正に使えるかは未確認（存在のみ確認）。
- 配当・増配・株式併合以外のコーポレートアクション（スピンオフ等）は未確認。
- calibration の実入力（`StrategyResearchSnapshot` / forward outcome の実テーブル）は未確認。
  したがって C5 の「同一時刻コホート」が実際に発生しているか、C6 の分布ドリフトの大きさは未検証。
- 対象外: `crypto_trend_dual_ma`（binance経路）・`jp_value_quality_backtest.py`・`defensive_rotation` /
  `vol_target_defensive` / `garp_momentum_hybrid` は **APIカタログ未収載**（`backtest_strategies.json` は6戦略のみ）。
  本番の `/v1/backtest/run` では到達しないが、テストとランナーからは呼べる。
- 約定の実現可能性（寄付きスプレッド、出来高制約、市場インパクト）は未検証。`slippage_bps=5` は固定値。

## 6. 再現に必要な入力

| 目的 | 入力 | 現物 |
|---|---|---|
| C1/C2 の再現 | `data/stock-ohlcv/{SYM}/ohlcv.jsonl`（alpaca, 2019-12-30〜2026-10-21, 1711行/銘柄） | あり（実測済み） |
| C2 の補正 | 分割カレンダー（`data/fundamentals/pbr/split-calendar.json` または外部の分割一覧） | 存在のみ確認 |
| C2 の検算 | 分割日の `open`/`close`/前日 `close`（比率が分割比と一致すること） | あり（5件を実測） |
| C3 | 無リスク金利系列（FF金利 or 13週T-bill） | 未取得 |
| C4/C14 | `run_backtests_p3.py` 等の出力JSON（`backtest_results_*.json`） | 未確認 |
| C5/C6/C7(calibration) | snapshot 実テーブル（`captured_at` の粒度・同時刻件数）と `excess_return` 実値 | 未確認 |
| C8 | 各銘柄のβ（同一窓、^GSPC 対比） | 未計測（β推定だけで算出可能） |

再現コマンド（C1/C2、読み取りのみ）:

```
cd /root/current-work/yowayowa-investor
S=/root/.hermes/profiles/worker-goat/cache/scratch
.venv/bin/python $S/probe_splits.py    # 分割段差と被覆の確認（読み取りのみ）
.venv/bin/python $S/probe_splits2.py   # 生価格 vs 分割補正のCAGR比較（読み取りのみ）
```

（scratch は24時間で自動pruneされる。長期保存するなら `tests/` ではなく `scripts/` 配下へ移すこと。）

`probe_splits2.py` の出力（2026-10-23 実行）:

```
sessions=1455 years=5.77
EW10 raw : total=+1.437 cagr=+0.1668
EW10 fix : total=+2.773 cagr=+0.2586
delta cagr = +0.0917  (raw understates by 54.8% of terminal wealth)
SPY      : total=+1.084 cagr=+0.1356
  NVDA 2021-07-20 factor=4: simulated one-day loss = -0.752
  NVDA 2024-06-10 factor=10: simulated one-day loss = -0.899
  GOOGL 2022-07-18 factor=20: simulated one-day loss = -0.951
  AMZN 2022-06-06 factor=20: simulated one-day loss = -0.949
  TSLA 2022-08-25 factor=3: simulated one-day loss = -0.668
```

## 7. 実測ログ（生データで確認した事実）

- 被覆: 10銘柄とも `n=1711`, `2019-12-30..2026-10-20`（SPYのみ 1712/2026-10-21）、provider は全て `alpaca`、通貨は全て `USD`。
  共通セッション 1711。評価窓 2021-01-04〜2026-10-20 の共通セッションは 1455。
- 価格は**分割未調整**。AAPL 2019-12-30 close=291.52（4:1分割前の水準）。
  NVDA 2024-06-10 open/前日close=0.0996、GOOGL 2022-07-18=0.0504、AMZN 2022-06-06=0.0512、TSLA 2022-08-25=0.3392。
- 分割以外の最大下落は META 2022-02-03 の -26.4%（open/前日close=0.757）で、これは実イベント。誤検出ではない。
- IS/OOS ジオメトリ（1455セッション、`split_index=int(1455*0.7)=1018`）:
  `momentum_12_1` warmup=253 → OOS 184セッション、`low_volatility` 61 → 376、
  `mean_reversion_20` 21 → 416、`equal_weight`/`value_*` 0 → 437。
- 財務: `data/stock-ohlcv` に `pbr`/`disclosure_date` は 0 件。`data/fundamentals/pbr/AAPL.jsonl` は
  1696行・28開示日・`filed - period_end = 32〜34日`・初出バー日 == `filed`。

## 8. 付記（レビュー範囲外の観察）

- `backtest.py:325` の `assert sum(target.values()) <= 1 + 1e-12` は `-O` で無効化される。
  レバレッジ禁止を実行時に担保するなら assert ではなく明示的なクランプ/例外が望ましい（現状は設計上 ≤1 なので実害なし）。
- `backtest.py:496` の `opening` は有限性検査にしか使われず、以後は再取得している（重複・軽微）。
- `status="complete"` の意味は「指標が揃った」だけであり、`warnings` を読まない消費者には
  「検証成功」と誤読され得る（C4 の一部）。

