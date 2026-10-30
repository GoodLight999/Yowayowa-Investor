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

データ/状態（gitignore済みの `data/` 配下）:

```
data/s07_paper/
├── candles_1d.json          日足（HL全履歴）
├── funding_1h.json          1時間ごとのfunding（既定220日ぶん維持）
├── state_live.json          本番ペーパー状態（S-07 rev.1・2026-10-30 起点リセット済み）
├── state_hist20.json        過去20営業日の検証用（旧パラメータ・参考）
├── state_hist90.json        過去90営業日の検証用（rev.1 の当てはめ・参考。判定には使わない）
├── health_live.json         失敗連続数・実行履歴
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
