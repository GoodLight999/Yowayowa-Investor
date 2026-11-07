# S-07 ペーパー日次ランナー（`hl_perp_btc_trend_funding`）

Hyperliquid の BTC パーペチュアルに対する S-07 を**紙で毎日回し、90営業日ゲートを機械判定**する一式。
**実弾0・注文0**。使うのは公開API `POST https://api.hyperliquid.xyz/info` だけで、
署名・APIウォレット・`/exchange` は一切使わない。

設計の正: `docs/CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md` §4.3（戦略）・§6（合格基準）。

## ファイル

| ファイル | 役割 |
|---|---|
| `hl_client.py` | HL公開APIクライアント（candleSnapshot / fundingHistory / l2Book / meta）。読み取り専用。 |
| `fetch_s07_data.py` | 日足とfunding履歴の取得器。日付キーで冪等マージ。fundingは500点上限のためページング。 |
| `s07_strategy.py` | **S-07 rev.1**: SMA50 / EMA10 / EMA20 / ATR14 / 10日ブレイクアウト / funding年率フィルター / ロットサイジング。 |
| `s07_paper_runner.py` | シグナル→ペーパー約定→日次台帳→Markdown成績表＋1行成績表。1日ずつ永続化（冪等）。 |
| `s07_gate.py` | ペーパーゲート判定（90営業日ロール窓・6項目＋強制ロスカット0回）と **20営業日 中間判定（合格/継続/中止）**。`合格/不合格/未達` の3値。 |
| `s07_daily_run.py` | cron用オーケストレータ（取得→ランナー→ゲート）。1行成績表を stdout に出す。失敗2回連続でTelegram通知。 |
| `hl_funding_top20.py` | 【B1】上位20銘柄の年率ファンディング観測（手数料控除後）。読み取りのみ・注文0。s07_daily_run.py から best-effort で呼ばれる（§B1）。 |

データ/状態（gitignore済みの `data/` 配下）:

```
data/s07_paper/
├── candles_1d.json          日足（HL全履歴）
├── funding_1h.json          1時間ごとのfunding（既定220日ぶん維持）
├── state_live.json          本番ペーパー状態（S-07 rev.1・2026-10-30 起点リセット済み）
├── state_hist20.json        過去20営業日の検証用（旧パラメータ・参考）
├── state_hist90.json        過去90営業日の検証用（rev.1 の当てはめ・参考。判定には使わない）
├── health_live.json         失敗連続数・実行履歴
├── funding_top20/           【B1】上位20銘柄ファンディング観測（2026-11-04追記・末尾§B1参照）
│   ├── days/YYYY-MM-DD.json  1日1ファイル（UTC日キー・同日再実行は冪等上書き）
│   └── health.json           この観測専用の稼働記録（S-07本体のhealthは触らない）
├── reset_log.jsonl          起点リセットの実行ログ（旧ファイルのパス・旧成績を1行で保全）
├── archive/                 リセット前の旧状態ファイル（削除しない）
└── reports/
    ├── S07_DAILY_SCORECARD.md     当日の成績表（live・1行成績表つき）
    ├── S07_ONELINE.log            毎日の1行成績表の追記ログ
    ├── S07_SCORECARD_hist*.md     過去営業日の成績表
    └── S07_GATE_live.md           ゲート判定（20営業日の中間判定つき）
```

## 実行

```bash
cd /root/current-work/yowayowa-investor/scripts/s07_paper

# 1) データ取得（差分）
python3 fetch_s07_data.py

# 2) 本番ペーパーを1日進める（初回だけ --init-baseline で基準日を記録）
python3 s07_paper_runner.py --namespace live --measure-book

# 3) ゲート判定
python3 s07_gate.py --namespace live

# 過去データで流す（別名前空間なので live を汚さない）
python3 s07_paper_runner.py --namespace hist20 --reset --start-day 2026-10-10
python3 s07_gate.py --namespace hist20        # hist* は項目2が N/A（過去データのため免除）
```

cron（Hermes・no_agent・毎日 10:00 JST = 01:00 UTC）:

```
script   : /root/.hermes/profiles/worker-go/scripts/s07_paper_daily.sh
schedule : 0 10 * * *
job_id   : e183d8ab2716
deliver  : local（stdoutは実行履歴に残す。Telegramは失敗2回連続のときだけ）
```

## 約定モデル

* シグナルは 00:00 UTC 確定足の終値で判定し、**翌日 00:00 UTC の始値**で約定（先読みなし）
* 手数料 **4.5 bps / 片道**（taker）
* スリッページ **15 bps / 片道**（タスク指示の保守仮定。実測ブック半スプレッドは別途記録）
* funding は保有中の1時間ごとの実績値 × 想定元本（場中決済は半日ぶん）
* 損切り **1.8 × ATR14**（S-07 rev.1）、日足の高安で到達判定、寄りギャップは寄り値
* 日次サーキットブレーカー −2.0% で全決済
* ロット: リスク1.0%/取引、実効レバレッジ上限3.0倍、`szDecimals=5` 切捨て、Notional ≥ 10 USDC
* 分離証拠金前提の清算価格（MMR 1.25%）を毎日計算し、到達したら `liquidation` として計上

## 冪等性

`state.last_processed_day` より後の確定日だけを処理し、1日ごとに状態を原子的に書き換える。
同じ日に2回流しても2回目は `新しい確定日なし` で終わる（実測済み）。
cronが数日落ちた場合は、次回実行で未処理日をまとめて追いつく。

## S-07 rev.1（2026-10-30 適用・起点リセット）

仕様の正: `docs/CIO_S07_REV1_SPEC_20261030.md`（CIO承認 `t_7e7d7b96`）。破壊的変更なし・`s07_strategy.py` の `DEFAULTS` のみ。

| パラメータ | rev.0 | rev.1（現行） | 仕様書 |
|---|---|---|---|
| `sma_window` | 200 | **50** | 50 |
| `ema_fast` | 20 | **10** | 10 |
| `ema_slow` | 50 | **20** | 20 |
| `breakout_window` | 20 | **10** | 10 |
| `stop_atr_mult` | 2.0 | **1.8** | 1.8 |

（維持: `atr_window`=14 / `funding_window_h`=24 / `funding_block_ann_pct`=30.0 / リスク1.0% / レバ3.0倍 / 最小Notional 10 USDC）

**過去の当てはめは成績に使わない。** 起点を 2026-10-30 にリセットし、前方の日付だけで積む。

```bash
# 起点リセット（旧状態は archive/ へ退避し reset_log.jsonl に1行残す。削除しない）
python3 s07_paper_runner.py --namespace live --reset --init-baseline --measure-book
```

* `--init-baseline` は**基準日**（リセット時点で確定している最終日足＝2026-10-29）を1行だけ記録し、**約定しない**。
  その日の終値シグナルは翌日（2026-10-30）寄りの注文として引き継ぐ。**起算日は 2026-10-30**。
* リセット後の `state_live.json` は `strategy_rev` と `reset`（旧ファイルのパス・旧成績）を保持する。
* `hist90` の 5往復・PF 1.558・DD −1.65% は**参考値**（過去の当てはめ）。合格判定には使わない。

## 1行成績表と20営業日の中間判定

毎日、成績表の冒頭と stdout に1行で出す（`reports/S07_ONELINE.log` に追記）:

```
S-07 rev.1 paper[live] 起算2026-10-30(基準日2026-10-29・約定なし) 累計+0.0000USDC(+0.00%) 往復0件(0勝0敗) PF=0.00 EV=+0.0000 DD=0.00% 清算0回 当日シグナル:2026-10-29 SHORT（close=77947 建玉=flat 翌日=enter_short） 実弾0・注文0
```

`s07_gate.py` は90営業日判定に加えて **20営業日時点の中間判定（合格/継続/中止）** を出す:

| 判定 | 条件 |
|---|---|
| 中止 | 強制ロスカット≥1回 / 容量例外>0 / 最大DD>4.0% / 往復4件以上で EV≤0・PF<1.00 |
| 合格 | 20営業日到達・往復4件以上・EV>0・PF≥1.30（→ 90営業日判定へ継続運用） |
| 継続 | 上記以外（20営業日未満・往復4件未満・PF 1.00〜1.30） |

## ゲート判定の注意（設計書§6.1・2026-10-30 改訂）

* **判定窓**: 直近 **90営業日ロール窓**（`GATE_DESIGN['window_days']=90`）。90日を超えた履歴は直近90日に切って評価する。
  **窓は項目1〜6すべてに適用**（2026-10-30 rev.2）。項目2は窓内の板実測のみ（`at` が窓開始日以降）、
  項目3は窓の開始直前残高を初期ピークに窓内Equityから再計算する（全期間ピークの `dd_pct` は持ち越さない）。
  窓外履歴の混入で合否が変わる再現は `test_s07_gate_cases.py` の回帰ケース4本で固定してある。
* **3値判定**: `合格` / `不合格` / `未達`。`未達` は材料不足（営業日 < 90、または決済済み往復 < 4）で、
  戦略の破綻を意味しない。**清算（ロスカット）が1回でもあれば営業日不足でも `不合格`**（§6 ★）。
* **項目4（PF/EV）**: 決済済み往復が **4未満** のときは「判定保留（Pending）」とし、不合格に倒さない。
  4往復以上で `EV > 0 かつ PF ≥ 1.30` を判定する。
* **項目2（実効スリッページ）**: live は **L2ブック半スプレッド実測 p95 ≤ 2.0 bps** で厳格判定（実測が無ければ不合格）。
  `--namespace hist*` または `--hist` 指定時のみ「**N/A（過去データのため免除）**」として総合判定を落とさない
  （HL API に過去のL2板が無いため）。
* **基準値は設計書§6が唯一の正**（PF≥1.30 / 反転≤6回・月）。旧暫定値（PF≥1.25 / 反転≤4回）の併記判定は削除した。
* **Phase 2-A インフラ適格**: 20営業日時点で項目1/2/3/6/★が合格なら `適格`（戦略適格は90営業日＋4往復）。

## §B1 【B1】HL 上位20銘柄 年率ファンディング観測（2026-11-04 追記・カード t_00e2c1d7）

採用済み HL メインネット・シャドー観測への**追記**。既存の取得/ランナー/ゲート・state・health・通知は変更しない
（2026-11-04 実測: s07_daily_run.py への変更は純追記16行・削除0）。`s07_daily_run.py` がロック取得後に
best-effort で `hl_funding_top20.py` を呼ぶ（失敗しても S-07 本体の成否・Telegram通知に影響しない）。
読み取りのみ・注文0（テストネット含む）。単独実行も可:

```bash
python3 hl_funding_top20.py                 # 即時実行（--json で1行JSONサマリ）
```

* 対象: HL メインネット HyperCore perp の isDelisted 以外（HIP-3 dex は対象外）。流動性フィルタなし（OI・出来高は記録）。
* 選出: 年率換算ファンディング絶対値の上位20銘柄（毎日1回・UTC日キーで冪等記録）。
* **年率換算式**: `ann_gross_pct = funding_rate_hourly × 24 × 365 × 100`（HL は 1時間ごとに精算）
* **手数料控除後**: `ann_net_pct = |ann_gross_pct| − 1.095%pt/年`
  （往復 taker 9bps = 4.5bps×2 を 30日保有の組み替え 年365/30回 で償却。S-07 約定モデルと同一の手数料想定）。
  保守参考: 7日保有想定 = −4.6929%pt/年（`ann_net_pct_7d`）。
* **殺す基準（カード t_00e2c1d7）**: 30日実測で手数料控除後の年率が 3% 未満なら候補落ち。
  判定値（KILL_GATE）= 上位20銘柄それぞれの過去30日 HL 実測 fundingHistory（1時間足）の30日平均から
  算出した控除後年率の **20銘柄中央値**。レポートに毎日自動計算して記録する。
* 30日未満の間は「暫定（経過N日/30日）」を明記。欠測銘柄・欠測日は「未取得」と明記。
* 出力: `funding_top20/days/YYYY-MM-DD.json`（1日1ファイル・冪等）/ `funding_top20/health.json`（観測専用）/
  `reports/FUNDING_TOP20.md`（人間用レポート・毎回再生成）。
* 注意: 構成銘柄は観測時点の上位20なので生存者バイアスあり。控除は手数料のみ（スリッページ・スポットヘッジ費用は不含）。

## §B2 【B2】清算検知用データ追加（2026-11-06 追記・カード t_7a53946d）

CIOレビュー（t_b6e4bec8）の実測に基づく**純追記**。既存 S-07 本体ロジック・B1 の定義は変更しない
（hl_funding_top20.py への変更は挿入のみ・削除0。`hl_liq_obs.py` は新規の追記モジュール）。
実弾0・注文0（呼ぶのは HL 公開APIの読み取りのみ）。

```bash
python3 hl_liq_obs.py --status        # B2の状態（蜡烛/OI/閾値/収集ループ/負荷実測）
python3 hl_funding_top20.py           # 日次ランナー（B1+B2を通しで実行・dayファイルへB2セクション追記）
python3 hl_liq_obs.py --backfill 14   # 単独実行: 直近14日分の1m蜡烛を取得（キャリブレーション用）
python3 hl_liq_obs.py --calibrate     # 単独実行: 「長い下ヒレ」閾値を凍結（1回だけ・以後上書きしない）
```

* **名称の約束: 本観測が数えるのは「代理検知件数」であり清算の実値ではない。**
  公式APIに市場全体の清算履歴は無い（type=liquidations deserialization 失敗・t_b6e4bec8 実測）。
  第三者データ（Hydromancer 等・キー必要）の採用は裁可待ちのため取らない。
* **1m蜡烛**: `candleSnapshot interval=1m`・上位20銘柄・完全UTC日で1440点/銘柄を日次追記
  （`funding_top20/candles_1m/YYYY-MM-DD.json`。同日再実行は冪等マージ。1呼び実測5000点上限・前進ページング）。
  日次ランナーは cron 実行時点（01:00 UTC）で「前日の完全1440点＋当日の部分」を記録する。
* **OI高頻度スナップショット**: `type=metaAndAssetCtxs`（上位20銘柄）を15分間隔（10〜30分枠内）で前向き蓄積
  （`oi_snapshots/YYYY-MM-DD.jsonl`）。公的 OI 履歴 API は実測不在のため自前前向き蓄積が唯一の OI 粒度。
  収集ループは日次ジョブ内から flock 二重起動なしで起動し、毎UTC日00:52に自己終了（cron 重複回避）。
* **代理検知件数（清算実値ではない）**: 完全UTC日の1m蜡烛に対して計算（当日の部分日は翌日cronで確定）。
  構成と4計数:
  - typeB相当(1m) = 前分終値比 −3.0% 以下 かつ 出来高が同日・同銘柄の1m出来高中央値の2.0倍以上
    （親レビュー実測95件/月水準と同規則の1m継続計数）
  - typeA相当(1m) = 「長い下ヒレ」かつ前分比 −1.0% 以下 かつ 出来高3.0倍以上
    （1h粒度の60%脚は30日20銘柄0件・t_b6e4bec8実測 → 1mで置換）
  - 「長い下ヒレ」閾値 W = clamp(p99.9[全銘柄×完全UTC日の1m lower_wick_ratio プール], 0.60, 0.98)
    を0.5%刻みで丸める — **12〜14日分の実データでキャリブレーションしてから固定**（t_b6e4bec8指摘2）。
    `liquidation_thresholds.json` に1回だけ書いて以後不変。OI低下 −1.0% は暫定（蓄積後に別カードで固定可）。
  - **実測（2026-11-06プローブ）: HL 1m蜡烛履歴は約3.6日分しか存在しない**（ startTime=14日前でも
    最早蜡烛≈3.6日前・endTime単独は422）。そのため閾値固定は **前向き蓄積で12〜14日分を確保してから**:
    `--calibrate` は complete_days < 12 の間は拒否し、それまでは provisional（W=0.60）で走り、
    day ファイル・レポートに `wick_status: provisional` を明記する。凍結時の実行例:
    `python3 hl_liq_obs.py --calibrate`（主体: 後続カード。12/02判定前に必ず凍結）。
  - OI低下あり/なしを両方daily記録（OI低下 = 発生時刻を挟む前後スナップのOI変化率 ≤ −1.0%）。
    OIスナップ欠測日は「計上不能（欠測）」と明記し、なし扱いにしない。
* **出来事台帳**: `funding_top20/liquid_events.jsonl`（event_id冪等追記）。
  各イベントに「**保有中funding収支**」欄を **予約**（`holding_funding_pnl: null`）として含める（t_b6e4bec8指摘3）。
* **VPS負荷**: 日次追記データ量は day ファイル `b2_liquidation_observation.vps_load`（実測拡張）と
  レポートに毎日記録。1m蜡烛≤40呼び（当日∪前日の上位20）＋OIスナップ96/日で軽量（28日で約0.2GB未満）。
