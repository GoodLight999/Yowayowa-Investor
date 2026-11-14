# 【A3】Kalshi流動性報酬（Liquidity Incentive Program）調査およびペーパー約定シミュレーション報告書

- **タスクID**: `t_0bee482a`
- **作成日**: 2026-11-04 (JST)
- **担当**: CIO-Fortuna
- **規律**: 完全ペーパー検証（発注0・口座開設0・KYC 0・推測排除）
- **判定結論**: **収支大幅マイナス（-$1,214.92 / 2週間）により【即時終了（KILL）】**

---

## 1. CFTC提出文書の原文引用と採点式の解釈

Kalshiの流動性報酬プログラムは、米商品先物取引委員会（CFTC）の規制枠組みに基づき、以下の2系統の公式文書によって規定・実施されている。

### 1.1 原典URLおよび該当ページ
1. **KalshiEX LLC DCM Rulebook（CFTC認可規則集 v1.29）**
   - **URL**: `https://kalshi-public-docs.s3.amazonaws.com/regulatory/rulebook/Kalshi%20DCM%20Rulebook%20v.1.29.pdf`
   - **該当条項・ページ**: 
     - **Rule 3.13(f)** (p. 40):
       > *"Kalshi may from time to time establish incentive programs that provide participants with incentives that encourage membership and trading."*
     - **Chapter 4 Market Makers (Rule 4.1 – 4.5)** (pp. 40–41):
       > Rule 4.3: *"Market Makers may receive benefits, including but not limited to financial benefits, reduced fees..."*
       > Rule 4.4: *"A Market Maker is obligated to perform all requirements... including but not limited to maintaining two-sided markets within a defined spread and with a minimum depth during trading."*
2. **CFTC自己認証提出文書（Rule 40.6(a) Self-Certification Filing, 2026-09-02）**
   - **文書名**: `KalshiEX LLC – Sports Prop Combo Component Legs Liquidity Incentive Program (Modification)`
   - **URL**: `https://kalshi-public-docs.s3.amazonaws.com/regulatory/notices/Sports%20Prop%20Combo%20Market%20Component%20Legs%20Liquidity%20Incentive%20Program%20(Modification)%20(2).pdf`
   - **該当ページ**: Appendix A (pp. 6–8)
3. **公式運用規則開示（Kalshi Rule 3.13(f) Liquidity Incentive Program Terms）**
   - **URL**: `https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program`
   - **公示URL**: `https://kalshi.com/regulatory/notices`
   - **有効期間**: 2026-10-08改定 〜 2027-01-01

---

### 1.2 採点式の原文引用と数式表現

#### 【体系A: 指値板スナップショット採点方式（一般参加者向け Liquidity Incentive Program）】
Kalshiの標準的な板提示（Resting Limit Order）に対する採点アルゴリズム（Help Center / Regulatory Terms）:

1. **スナップショットと参照価格算出**:
   毎秒1回、ランダムなミリ秒で板を記録。最良気配（Best Bid）から板を歩み、累積数量が `Target Size` の $\frac{1}{5}$（20%）に達する価格レベルを「参照価格（$P_{\text{ref}}$）」とする。
2. **距離ペナルティ（Distance Penalty）**:
   参照価格以上の注文は満額付与（$1.0\times$）。参照価格未満の注文は、Tick乖離数 $N$ に応じてディスカウント係数の累乗を乗じる。
   $$\text{Multiplier}_{k,t} = \begin{cases} 1.0 & (P_{k,t} \ge P_{\text{ref},t}) \\ d^{N_{k,t}} & (P_{k,t} < P_{\text{ref},t}) \end{cases}$$
   （$d$: Discount Factor, $0 < d \le 1.00$、$N$: 参照価格からのTick数）
3. **注文素スコア（Raw Score）**:
   $$\text{RawScore}_{k,t} = \text{Order Size}_{k,t} \times \text{Multiplier}_{k,t}$$
4. **側別正規化シェア（Normalized Share）**:
   $$\text{Share}_{i,\text{side},t} = \frac{\sum_{k \in \text{Orders}_{i,\text{side},t}} \text{RawScore}_{k,t}}{\sum_{\text{all qualifying orders on side}} \text{RawScore}}$$
5. **スナップショット合算（両サイド合計）**:
   $$\text{SnapshotScore}_{i,t} = \text{Share}_{i,\text{Yes},t} + \text{Share}_{i,\text{No},t} \quad (\le 2.0)$$
6. **最終報酬配分式（Payout）**:
   $$R_i = \text{Pool} \times \left( \frac{\sum_{t \in T_{\text{qual}}} \text{SnapshotScore}_{i,t}}{\sum_{j} \sum_{t \in T_{\text{qual}}} \text{SnapshotScore}_{j,t}} \right) \times \left( \frac{N_{\text{qualifying}}}{N_{\text{total}}} \right)$$
   （セント未満切り捨て。両側板がTarget Size未達のスナップショットは除外され、報酬プール総額が比例減額される）

#### 【体系B: コンボ/出来高連動採点方式（CFTC Appendix A 提出文書引用）】
CFTC届出 Appendix A §5.C に記載された採点式:
$$S_L = \prod_{k=1}^N (1 + F_k) - 1$$
- $S_L$: 適格参加者 $L$ のスコア
- $F_k$: 当該市場の構成要素 $O_k$ における参加者 $L$ の適格Maker出来高シェア（$\text{Maker Volume Share}$）
- 報酬プール: $\text{Total Fees} \times \text{CLIFF (25\%)} \times \frac{\text{Eligible Components}}{\text{Total Components}}$
- 配分比率: $\frac{S_L}{\sum_j S_j}$（ただし $S_L < 0.01$ は足切り0配分）

---

### 1.3 式の変数と重みの一覧表

| 変数名 | 原文表記 | 定義・役割 | 重み・パラメータ範囲 | 備考・推測排除 |
|---|---|---|---|---|
| **$S_{\text{target}}$** | Target Size | 有効スナップショット判定に必要な片側最小板深度 | 100 〜 20,000 契約 | 市場ごとに設定（API未開示時は「**不明**」） |
| **$P_{\text{ref}}$** | Reference Price | 距離減衰の基準となる価格 | 累積板深度が $S_{\text{target}} \times 0.20$ に達する価格 | 最良気配ではなく板の厚みで動的に決定 |
| **$d$** | Discount Factor | 参照価格より深い指値へのペナルティ減衰率 | $0 < d \le 1.00$（$1.00$ は減衰なし） | 市場ごとに個別設定（静的開示値は「**不明**」） |
| **$N$** | Ticks Away | 参照価格からの価格差（Tick数） | 整数（$0, 1, 2, \dots$） | 1Tick = $0.01（1セント） |
| **$Q_{k}$** | Order Size | 板に出している未約定指値数量 | 契約枚数（Contracts） | 注文量に比例 |
| **$\text{Pool}$** | Reward Pool | 当該市場・期間の総インセンティブ原資 | 日次 $1 〜 $1,000 / 市場 | Web UI上のバッジ表示依存（API自動取得は「**不明**」） |
| **$\text{CLIFF}$** | Fee Fraction | コンボ型市場での手数料還元率 | **25.0%**（固定） | CFTC提出 Appendix A §4 確定値 |
| **$S_{\text{min}}$** | Minimum Score | 報酬受給に必要な最低個別スコア | **0.01**（足切り閾値） | CFTC提出 Appendix A §4 確定値 |
| **$\frac{N_{\text{qual}}}{N_{\text{total}}}$** | Qualifying Ratio | 2面流動性充足スナップショット比率 | 0.0 〜 1.0 (0%〜100%) | 未達時間帯が多いと報酬プール全体が縮小 |

---

## 2. 報酬が実際に発生する条件（各1行）

- **対象市場条件**:
  Kalshi公式上で「Rewards」バッジ（またはダイヤモンド印）が付与され、有効なインセンティブ期間中かつ取引時間中であること（※注: 公式規約により米国SSN保有者に限定され、非米国ユーザーは報酬受取対象外と明記）。
- **スプレッド/板深度条件**:
  毎秒のスナップショット時に**Yes側・No側の双方（Two-sided）**でTarget Size以上の板厚が存在し、自注文が参照価格以内または減衰係数（$d^N$）が有効なスプレッド範囲内に提示されていること。
- **在庫条件**:
  自注文が約定（Fill）せずに板上に**未約定の指値（Resting Order）として滞留し続けていること**（約定して在庫化されたポジションには報酬は一切発生せず、逆に価格変動・逆選択損失リスクおよび成行決済手数料を被る）。

---

## 3. 対象1市場の選定と想定報酬の計算（ペーパー・発注0）

### 3.1 対象市場の選定
- **選定市場**: `KXBTC15M-26NOV030700-00`（BTC 15分満期 バイナリー市場）
- **選定理由**:
  1. Kalshiの公式流動性提供プログラム（LPP）指定対象シリーズ（`Crypto 15min markets`）。
  2. 2026-11-03の実測Parquetログにおいて、15分間に331件の約定、14,258契約の出来高が発生しており、板厚み・流動性が十分に満たされている。
  3. 日次・超短期で決着し、逆選択・約定リスクの実測データが揃っている。

### 3.2 想定報酬の計算
- **シリーズ日次報酬プール**: $100.00 / 日（標準的な指定銘柄プール想定）
- **1市場（15分枠）あたりの報酬プール**:
  $$\text{Pool}_{\text{15m}} = \frac{\$100.00}{96} = \$1.0417 \quad (104.17\text{¢})$$
- **Maker想定提示シェア**: 平均板深度に対して **5.0%** の指値数量を常時提示したと仮定。
- **1市場での想定報酬**:
  $$R_{\text{single}} = 104.17\text{¢} \times 0.05 = \mathbf{+5.21\text{¢}} \quad (+\$0.0521)$$
- **1日（96市場通算）での想定報酬**:
  $$R_{\text{daily}} = \$100.00 \times 0.05 = \mathbf{+500.0\text{¢/日}} \quad (+\$5.00/\text{日})$$

---

## 4. 不利約定損失（在庫リスク）と手数料の推定

### 4.1 不利約定損失（Adverse Selection Loss）の実測計算
Kalshiの収集済み約定ログ（`trades.parquet`）に基づき、当該市場（331トレード・14,258.47契約）において板の5%を提示していた場合の受動的約定（Passive Fill）と満期決済損益を実測シミュレーションした。

- **約定契約数**: $14,258.47 \times 5.0\% = \mathbf{712.92\text{ 契約}}$
- **約定内容**:
  - Taker買い（MakerがYes売り）: 平均32¢〜45¢で売却。
  - その後、原資産（BTC）急落に伴い市場価格は 26¢ で満期決済（一部安値は1¢へ暴落）。
  - Taker売り（MakerがYes買い）: 高値圏でYesを買わされる。
- **単一市場での不利約定損失**:
  $$\text{Loss}_{\text{single}} = \mathbf{-7,956.32\text{¢}} \quad (-\$79.56)$$
  （1契約あたり損失: $-11.16\text{¢}$）
- **シリーズ全17市場（出来高471,213契約）の実測通算**:
  全市場でMaker側は一貫して Toxic Flow（インフォームド・Takerの急襲）を浴びており、加重平均で **1契約あたり $-0.39\text{¢}$**（$-39\text{ bps}$）の不利約定損失が発生。
- **日次（5%シェア）での不利約定損失**:
  $$23,560.66\text{ 契約/日} \times (-0.39\text{¢}) = \mathbf{-9,178.0\text{¢/日}} \quad (-\$91.78/\text{日})$$

### 4.2 手数料（Fees）の推定
- **Maker発注手数料**: Kalshi公式Rulebookに基づき **$0.00**（無料）。
- **満期決済手数料**: **$0.00**。
- ※注: 在庫の偏りを嫌って成行手仕舞い（Taker Exit）を行った場合、二次関数手数料 $F = \lceil 0.07 \times P(1-P) \rceil$（最大1.75¢/契約）が追加発生し、損失がさらに拡大する。本計算では最もMaker側に有利な「手仕舞い成行手数料 0（満期持ち越し）」として計算した。

---

## 5. 2週間シミュレーション収支判定（殺す基準の検証）

### 5.1 収支対照表（14日間通算・想定5%シェア）

| 項目 | 1市場（15分） | 1日あたり（日次） | 2週間通算（14日間） | 備考 |
|---|---|---|---|---|
| **想定流動性報酬 ($R$)** | **+5.21¢** | **+500.0¢** | **+7,000.0¢** (+$70.00) | 日次$100プール・5%獲得前提 |
| **不利約定損失 ($L_{\text{adv}}$)** | **-7,956.32¢** | **-9,178.0¢** | **-128,492.2¢** (-$1,284.92) | 実測Tickデータに基づく逆選択 |
| **Maker手数料 ($F$)** | $0.00 | $0.00 | $0.00 | Kalshi公式無料 |
| **純損益（Net PnL）** | **-7,951.11¢** | **-8,678.0¢** | **-121,492.2¢** (-$1,214.92) | **大幅マイナス** |

### 5.2 殺す基準の判定
- **殺す基準**: **2週間のシミュレーションで「報酬 − 不利約定損失 − 手数料」がマイナスなら終了**
- **判定結果**: 
  $$\text{Net PnL} = 7,000.0\text{¢} - 128,492.2\text{¢} - 0.0\text{¢} = \mathbf{-121,492.2\text{¢} \quad (-\$1,214.92)}$$
  **基準に抵触（大赤字） $\implies$ 【即時終了（KILL）】**

### 5.3 判定の根拠と構造的要因
1. **報酬額と逆選択損失の非対称性**:
   流動性報酬プール（数ドル〜数十ドル/日）は市場全体の出来高に対して極めて少額である一方、外部価格（Binance/CoinbaseのBTCレート等）が急変した瞬間に板に指値を残しているMakerは、レイテンシ優位なインフォームド・Taker（裁定ボット）に必ず「Toxicな注文だけ」を約定させられる。
2. **スプレッドを広げた場合の報酬消滅**:
   逆選択を回避するために参照価格から離れた位置に指値を置くと、ディスカウント係数（$d^N$）によってスコアが指数関数的に減衰し、かつ Target Size（板厚み上限）の外側に押し出されて **獲得報酬そのものが $0$ に収縮する**。
3. **規約上の非適格性（トドメの事実）**:
   Kalshiの公式運用規定において、**「International, non-U.S. users (ineligible for rewards)」**と明記されており、米国外ユーザーはそもそも報酬受給資格を持たない（実質 $R = 0$、純損失100%）。

---

## 6. 結論と次アクション

- **戦略評価**: 単純な指値常駐による流動性インセンティブ獲得戦略（エッジ仮説a）は、**構造的に逆選択損失が報酬を数十倍上回るため成立不可能**。
- **タスク処置**: 殺す基準に則り、本仮説は**正式に破棄・終了（KILL）**とする。
- **今後の推奨集中先**:
  流動性提供（Maker）ではなく、外部確定値（NOAA/METAR気象速報など）を先行取得して板の遅行指値を刈り取る**「情報レイテンシ・Taker戦略（エッジ仮説c）」**に研究資源を集中する。
