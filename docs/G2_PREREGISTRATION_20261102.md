# G2ゲート事前登録書（閾値・評価窓・判定基準の事前凍結）

- **文書名**: G2ゲート事前登録書（Rolling Walk-Forward + Deflated Sharpe / PBO）
- **作成・凍結日時**: 2026-11-02 16:16:00 JST（2026-11-02T07:16:00Z）
- **対象タスク**: `t_d8eb7d58`
- **対象戦略**: 
  1. **C2**: `s02_vol_target_15`（主候補・G1合格）
  2. **S-05**: `s05_defensive_rotation`（追加検証候補・G1保留）
- **仕様根拠**:
  - `LIVE_GATES_G1_G5_DESIGN.md` §2（発見場所: `/root/.hermes/kanban/attachments/t_6ba7cb87/LIVE_GATES_G1_G5_DESIGN.md`、SHA256: `ba5a074ceeadc634be47fc27a4548da042dc3f64ee5f46b157941ae244db3653`）
  - `time_series_backtest_overfitting_dsr_pbo_cscv.md` §3, §4, §5（`t_d6b600c1` 成果物、SHA256: 既存調査準拠）
  - `G1_GATE_20261102.md`（`t_256de6f0` 成果物、SHA256: `028dc577d23104a7d14c959e7d57a2645370a77479aa799a2e39194d358fc1ef`）

---

## 1. 事前凍結の宣誓（P-Hacking / 後出しジャンケンの完全排除）

本事前登録書は、**G2評価コードの実行前、およびいかなる判定結果も閲覧する前に**作成・保存・凍結されたものである。
登録された閾値、評価窓、計算式、対象戦略、ベースラインは以後一切の変更を禁ずる。
万一、結果閲覧後に閾値や期間を変更した場合は、いかなる理由があろうとも即時「不合格」として扱う。

---

## 2. 評価対象戦略およびベースラインの定義

### 2.1 C2: S-02 ボラティリティターゲット（15%）
- **戦略ID**: `s02_vol_target_15`
- **対象銘柄**: SPY, IWM, JNJ, PG（4銘柄）
- **リバランス頻度**: 週次（weekly）
- **目標ボラティリティ**: 年率 15%（レバレッジ上限 1.0）
- **比較基準線（ベースライン）**: `s02_baseline_ew_weekly`（同一4銘柄の均等配分 weekly リバランス）
- **入力データ**: `results_v6/s02_new.json`（因果的 open TR ミラー + FRED DGS1 短期金利計上済み、SHA256: `363f9c9880d982ae463bc82a516dbe4cf08ef767001a9b0162678837c6ac6149`）
- **取引コスト**: 片道 手数料 10bps + スリッページ 5bps（計 15bps、G1と同一）

### 2.2 S-05: ディフェンシブローテーション
- **戦略ID**: `s05_defensive_rotation`
- **対象銘柄**: SPY, QQQ, IWM, JNJ, PG, XOM, JPM, AAPL, MSFT, NVDA（10銘柄）
- **リバランス頻度**: 月次（monthly）
- **シグナルロジック**: 200日SMAによる市場環境判定（リスクオン時: グロース上位5銘柄均等、リスクオフ時: ディフェンシブ3銘柄均等または現金）
- **比較基準線（ベースライン）**: 
  - 主基準線: `s05_baseline_momentum_12_1`（同一10銘柄モメンタム基線、G1保留時の比較対象）
  - 副基準線: `s05_baseline_ew`（同一10銘柄均等配分基線）
- **入力データ**: `results_v6/s05_new.json`（因果的 open TR ミラー + FRED DGS1 短期金利計上済み、SHA256: `0ae9fa25464006a26567157c49a0dcba149d15c55a5bdba9ac035dc9c63ba76f`）
- **取引コスト**: 片道 手数料 10bps + スリッページ 5bps（計 15bps、G1と同一）

---

## 3. ローリング・ウォークフォワード窓の設計（事前凍結）

仕様書 `LIVE_GATES_G1_G5_DESIGN.md` §2 の規約に厳密に従い、以下の窓構造を固定する。

1. **助走期間（Burn-in）**:
   - 最初の **504 セッション**（約2年分）は指標およびシグナルの初期化・助走期間とし、損益評価・テスト窓からは除外する。
2. **テスト窓長（OOS Window Length）**:
   - **126 セッション**（約半年・6ヶ月）
3. **スライド幅（Step / Shift）**:
   - **63 セッション**（約四半期・3ヶ月、50% オーバーラップ）
4. **全体セッション長**:
   - 1,440 セッション（2021-01-04 〜 2026-09-28）
5. **テスト窓数（$N_w$）**:
   - 助走 504 セッション以降、126 セッションのテスト窓を 63 セッションずつずらす。
   - 生成される窓数: **14 窓**（Window 0: 504〜630, Window 1: 567〜693, ..., Window 13: 1314〜1440）
   - ※仕様書の要求「窓数 8 未満は判定不能(2)」を完全に満たす（$14 \ge 8$）。
6. **パラメータ再最適化の禁止**:
   - ウォークフォワード期間中、戦略パラメータは事前凍結値のまま固定し、窓ごとのチューニング（p-hacking）は一切行わない。純粋な時期別・環境別の成績頑健性を測定する。

---

## 4. G2 合格判定閾値の一覧（事前凍結）

合否は以下の **5条件（W1〜W5）の論理積（すべて True 必須）** で判定する。

| 条件ID | 項目 | 合格閾値 | 定義・計算式・根拠 |
|---|---|---|---|
| **W1** | 窓勝率（下落抑制＆正リターン） | **比率 $\ge 0.60$** (60%以上) | 各窓において `dd_better`（戦略MaxDD > ベースラインMaxDD）かつ `sharpe_pos`（戦略Sharpe > 0）を満たす窓の割合。50%ちょうどは偶然の域を出ないため 60% を厳格適用。 |
| **W2** | 連結 OOS シャープレシオ | **Sharpe $> 0.00$** | 全14テスト窓をつないだ OOS 日次リターン系列の年率 Sharpe 比率が正であること（参考値として 0.50 も併記）。 |
| **W3** | 最悪期ドローダウン耐性 | **最悪窓 MaxDD > ベースライン最悪窓 MaxDD** | 全14窓を通じて、戦略の最大ドローダウン最悪値が、ベースラインの最大ドローダウン最悪値より浅いこと（下落回避型の証明）。 |
| **W4** | Deflated Sharpe Ratio (DSR) | **DSR $\ge 0.90$** (90%以上) | Bailey & López de Prado (2014) 極値理論補正。多重試行数 $N \ge 18$（既存全試行 22 件）、試行間 Sharpe 標準偏差 $\sigma_{SR}$、OOS 歪度 $\gamma_3$、尖度 $\gamma_4$ から算出。 |
| **W5** | Probability of Backtest Overfitting (PBO) | **PBO $\le 0.10$** (厳格)<br>※ $0.50$ 以上は即死判定 | Bailey et al. (2016) CSCV（対称的組合せ交差検証、$S=16$、$\binom{16}{8}=12,870$ 通り）による幾何的過学習確率。IS 最適戦略が OOS で中央値以下に転落する確率。 |

### 判定規則
- **合格 (PASS)**: W1〜W5 のすべてが True
- **不合格 (FAIL)**: W1〜W5 のうち1つでも False
- **保留 (HOLD)**: データ欠損、判定不能（窓数 < 8 等）または理論的境界領域（例: PBO が 0.10超 0.50未満かつ他が合格の場合、追加検証が必要）

---

## 5. 多重検定の分母（trials.jsonl 相当の試行母集団）

DSR の計算に用いる試行母集団（$N$ および $\sigma_{SR}$）は、G1判定書および公式リポジトリに記録されている以下の **22 試行** とする（仕様書要求の 18 件以上を満たす）。

1. P3: `momentum_12_1` (Sharpe: 0.84)
2. P3: `low_volatility` (Sharpe: 1.07)
3. P3: `mean_reversion_20` (Sharpe: 0.75)
4. P3: `equal_weight` (Sharpe: 1.12)
5. 暗号: `crypto_trend_dual_ma` (lookback 200) (Sharpe: 0.78)
6. 暗号: `crypto_trend_dual_ma` (lookback 150) (Sharpe: 0.65)
7. 暗号: `crypto_trend_dual_ma` (lookback 100) (Sharpe: 0.52)
8. 暗号: `crypto_mean_reversion` (Sharpe: 0.31)
9. 暗号: `crypto_equal_weight` (Sharpe: 0.82)
10. S-02: `s02_vol_target_10` (Sharpe: 0.92)
11. S-02: `s02_vol_target_15` (Sharpe: 0.86)
12. S-02: `s02_vol_target_20` (Sharpe: 0.82)
13. S-02: `s02_vol_target_15_daily` (Sharpe: 0.85)
14. S-02: `s02_baseline_ew_weekly` (Sharpe: 0.83)
15. S-02: `s02_baseline_ew_monthly` (Sharpe: 0.83)
16. S-03: `s03_garp_momentum_hybrid` (Sharpe: 0.74)
17. S-03: `s03_baseline_momentum_12_1` (Sharpe: 0.84)
18. S-03: `s03_reference_ew` (Sharpe: 1.12)
19. S-05: `s05_defensive_rotation` (Sharpe: 0.87)
20. S-05: `s05_defensive_rotation_daily` (Sharpe: 0.48)
21. S-05: `s05_baseline_momentum_12_1` (Sharpe: 0.97)
22. S-05: `s05_baseline_low_volatility` (Sharpe: 0.83)

- **母集団数 $N$**: 22
- **試行間 Sharpe の平均**: 約 0.804
- **試行間 Sharpe の標本標準偏差 $\sigma_{SR}$**: 約 0.203

---

## 6. 実行・証跡規約
- **実行ホスト**: `TSUKUMO-KUN`（VPS 上での重い計算は完全禁止）
- **実発注・入出金**: 0 件
- **生データ変更**: 0 件（実施前後で SHA256 完全一致を検証）
- **証跡保存先**:
  - `docs/G2_PREREGISTRATION_20261102.md`（本事前登録書）
  - `data/gates/C2/G2_windows.csv`（C2 窓別詳細）
  - `data/gates/C2/G2.json`（C2 総合判定）
  - `data/gates/S05/G2_windows.csv`（S-05 窓別詳細）
  - `data/gates/S05/G2.json`（S-05 総合判定）
  - `docs/G2_GATE_20261102.md`（最終判定書）
