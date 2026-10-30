# S-07 rev.1 をペーパー日次ランナーへ反映し、起点を 2026-10-30 にリセット（実測記録）

作成: 2026-10-30 / 担当: worker-go / カード: t_79dbb0be
仕様の正: `docs/CIO_S07_REV1_SPEC_20261030.md`（CIO-Fortuna 発行・承認 `t_7e7d7b96`）
**実弾0・注文0**（HL公開API `POST /info` の読み取りのみ。`/exchange` も署名も鍵も使わない）

## 0. 結論

1. **rev.1 のパラメータは仕様書と11項目すべて一致**（`s07_strategy.py` の `DEFAULTS` を実測比較。下の表）。
   rev.0 からの差分は 5定数のみ（SMA 200→50 / EMA 20→10 / EMA 50→20 / BO 20→10 / Stop 2.0→1.8）。
2. **live の起点を 2026-10-30 にリセット**した。旧 `state_live.json` は
   `data/s07_paper/archive/state_live_20261030T081135Z.json` に保全し、`reset_log.jsonl` に1行残した
   （削除なし・過去日の埋め戻しなし）。新 state の**起算日は 2026-10-30**、
   基準日（リセット時点の最終確定足 2026-10-29・約定なし）を1行だけ記録。
3. **冪等性を再確認**。同じ日に5回実行して5回とも `no_new_closed_day`、`days` は 1 のまま増えない。
4. **毎日の1行成績表**（累計損益・往復数・PF・DD・清算回数・当日シグナル）を成績表の冒頭と cron stdout に出し、
   `reports/S07_ONELINE.log` に追記する。
5. **20営業日の中間判定（合格/継続/中止）** を `s07_gate.py` に追加。現時点は `継続（1/20営業日）`。
   90営業日ゲートの判定窓・基準値は**変更していない**。
6. `hist90` の 5往復・PF 1.558・DD −1.65% は仕様書どおり**再現**したが、これは**過去の当てはめ＝参考**であり、
   合格判定には使わない。判定はここから先の日付だけで行う。

## 1. rev.1 パラメータ一致（仕様書 vs 実装・実測）

`python3` で `s07_strategy.DEFAULTS` を読み、仕様書 §2 の値と突き合わせた結果:

| パラメータ | rev.0 | rev.1（現行） | 仕様書§2 | 一致 |
|---|---|---|---|---|
| `sma_window` | 200 | **50** | 50 | ✅ |
| `ema_fast` | 20 | **10** | 10 | ✅ |
| `ema_slow` | 50 | **20** | 20 | ✅ |
| `breakout_window` | 20 | **10** | 10 | ✅ |
| `stop_atr_mult` | 2.0 | **1.8** | 1.8 | ✅ |
| `atr_window` | 14 | 14 | 14（維持） | ✅ |
| `funding_window_h` | 24 | 24 | 24（維持） | ✅ |
| `funding_block_ann_pct` | 30.0 | 30.0 | 30.0（維持） | ✅ |
| `risk_per_trade` | 0.010 | 0.010 | 1.0%（維持） | ✅ |
| `max_leverage` | 3.0 | 3.0 | 3.0倍（維持） | ✅ |
| `min_notional_usdc` | 10.0 | 10.0 | 10 USDC（維持） | ✅ |

シグナル式・ドテン・ストップ判定のロジックは rev.0 から**無変更**（数値の差し替えのみ）。

### 参考: 過去90日の当てはめ（合否には使わない）

`python3 s07_gate.py --namespace hist90` → **合格（ただし参考値）**

| 項目 | 実測 | 仕様書§4の期待値 |
|---|---|---|
| 処理日数 | 90日（2026-08-01〜10-29） | 90日 |
| 往復数 | 5 | 5 |
| 決済済み純損益合計 | +1.8433 USDC | +1.8433 |
| EV | +0.3687 USDC/取引 | +0.3687 |
| PF | 1.558 | 1.558 |
| 最大DD | −1.654% | −1.65% |
| 清算（強制ロスカット） | 0回 | 0回 |

→ 仕様書と数値が一致したので、**実装は仕様どおり**と判断した。ただし往復5回は統計的に小さく、
`docs/CIO_S07_REV1_SPEC_20261030.md` の指示どおり**この数字を合格根拠にしない**。

## 2. 起点リセットの実行ログ

実行（2026-10-30 17:11 JST / 08:11:35 UTC）:

```
$ python3 s07_paper_runner.py --namespace live --reset --init-baseline --measure-book --json
RESET: state_live.json -> /root/current-work/yowayowa-investor/data/s07_paper/archive/state_live_20261030T081135Z.json
      （旧 1日 / 0往復 / 累計 0.0 USDC を保全）
```

`data/s07_paper/reset_log.jsonl` に残した1行（実物）:

```json
{"at": "2026-10-30T08:11:35Z", "namespace": "live", "strategy_rev": "S-07 rev.1 (SMA50 / EMA10-20 / BO10 / Stop1.8)",
 "archived_to": ".../data/s07_paper/archive/state_live_20261030T081135Z.json",
 "prev_days": 1, "prev_first_day": "2026-10-29", "prev_last_day": "2026-10-29", "prev_trades": 0, "prev_total_pnl": 0.0,
 "prev_config": {"sma_window": 200, "ema_fast": 20, "ema_slow": 50, "breakout_window": 20, "stop_atr_mult": 2.0},
 "reason": "S-07 rev.1 適用に伴う起点リセット（過去の当てはめを成績に混ぜない・埋め戻しなし）"}
```

| 項目 | 値 |
|---|---|
| 旧ファイル（保全先） | `data/s07_paper/archive/state_live_20261030T081135Z.json`（2,843B・削除しない） |
| 旧成績 | 1日（2026-10-29 基準日のみ）・0往復・累計 ±0.0000 USDC・rev.0 パラメータ |
| 新ファイル | `data/s07_paper/state_live.json`（`strategy_rev` と `reset` を保持） |
| 新ファイルの基準日 | **2026-10-29**（`skips: ["baseline_no_fill"]`・約定しない1行） |
| 新ファイルの起算日 | **2026-10-30**（この日から成績を積む） |
| リセット後の初回実行 | 2026-10-30 17:11 JST（起算日当日） |

* 過去日の埋め戻しは行っていない。`hist90` は別名前空間のまま（live には混ぜない）。
* 基準日（10-29）の終値シグナルは **SHORT** で、その注文は**翌営業日 2026-10-30 の寄り**で執行する
  （＝リセット日当日の約定から成績が始まる）。先読みはしない。

## 3. cron と冪等性の再確認

| 項目 | 実測 |
|---|---|
| job_id | `e183d8ab2716`（S-07 paper daily runner・`no_agent`） |
| schedule | `0 10 * * *`（毎日 10:00 JST） |
| script | `/root/.hermes/profiles/worker-go/scripts/s07_paper_daily.sh`（変更なし） |
| next_run_at | 2026-10-31 10:00 JST |
| 実行経路 | fetch → runner → gate。失敗2回連続のときだけ Telegram 通知 |

同日2回（実際は5回）実行した結果 — **2回目以降はすべて `no_new_closed_day`、`days` は 1 のまま**:

```
$ python3 s07_daily_run.py --namespace live     # 1回目
S-07 rev.1 paper[live] 起算2026-10-30(基準日2026-10-29・約定なし) 累計+0.0000USDC(+0.00%) 往復0件(0勝0敗)
PF=0.00 EV=+0.0000 DD=0.00% 清算0回 当日シグナル:2026-10-29 SHORT（close=77947 建玉=flat 翌日=enter_short）
実弾0・注文0 / no_new_closed_day / 中間判定=継続(1/20営業日（中間判定は未到達）) / ゲート=未達 / 実弾0・注文0

$ python3 s07_daily_run.py --namespace live     # 2回目（同じ日）
（1回目と同一の1行 / no_new_closed_day）

$ bash /root/.hermes/profiles/worker-go/scripts/s07_paper_daily.sh   # cron と同じ入口
（同一の1行 / no_new_closed_day）
```

`state_live.json` の `run_history` 末尾6件: `['no_new_closed_day' ×5, ...]`、`days=1`、`last_processed_day=2026-10-29` で不変。
実装は「`last_processed_day` より後の確定日だけを処理し、1日ごとに原子的に永続化」なので、
cron が同じ日に複数回走っても二重計上しない。

## 4. 成績表の実物（1日分）

`data/s07_paper/reports/S07_DAILY_SCORECARD.md`（2026-10-30 17:13 JST 生成）冒頭の**1行成績表**:

```
S-07 rev.1 paper[live] 起算2026-10-30(基準日2026-10-29・約定なし) 累計+0.0000USDC(+0.00%) 往復0件(0勝0敗)
PF=0.00 EV=+0.0000 DD=0.00% 清算0回 当日シグナル:2026-10-29 SHORT（close=77947 建玉=flat 翌日=enter_short） 実弾0・注文0
```

当日（基準日）の内訳:

| 項目 | 値 |
|---|---|
| 対象日 (UTC) | 2026-10-29（基準日・約定なし） |
| 当日損益 / 累計 / Equity | +0.0000 USDC / +0.0000 USDC（+0.000%） / 200.0000 USDC |
| 最大DD / 当日DD | 0.000% / 0.000% |
| 建玉 | flat |
| 約定数（当日） | 0（`baseline_no_fill`） |
| シグナル | close=77947.0 / SMA50=81912.1 / EMA10=80677.0 / EMA20=81402.0 / ATR14=2130.2 |
| 当日判定 | LONG=× / SHORT=○ / exit_long=○ / exit_short=× |
| funding年率(24h) | +10.95% |
| 翌日への注文 | `enter_short (trend_breakdown_short)` |
| 往復 / PF / 清算 | 0件 / — / 0回 |
| 実行時ブック半スプレッド | 0.0641 bps（L2実測・読み取りのみ） |

`reports/S07_ONELINE.log` に同じ1行が日付つきで追記される（毎日1行・過去分も残る）。

## 5. 20営業日の中間判定（新設）

`s07_gate.py` の `midterm()` が **合格 / 継続 / 中止** の3択で返す（レポート・`--json`・1行サマリすべてに出力）:

| 判定 | 条件 |
|---|---|
| **中止** | 強制ロスカット ≥1回 / 容量例外 >0件 / 最大DD >4.0% / 往復4件以上で EV≤0 または PF<1.00 |
| **合格** | 20営業日到達・往復4件以上・EV>0・PF≥1.30（→ 90営業日判定へ継続運用） |
| **継続** | 上記以外（20営業日未満 / 往復4件未満 / PF 1.00〜1.30） |

現時点: `継続 — 1/20営業日（中間判定は未到達）`。
境界の検証は `scripts/s07_paper/test_s07_gate_cases.py` に5ケース追加し、**13ケースすべて PASS**。

```
PASS 19日・黒字4往復 → 中間判定は継続（20営業日未到達）
PASS 20日・4往復 PF>=1.30 → 中間判定は合格
PASS 20日・3往復のみ → 中間判定は継続（サンプル不足）
PASS 20日・4往復 PF 1.00〜1.30 → 中間判定は継続
PASS 20日・清算1回 → 中間判定は中止
RESULT: ALL PASS
```

90営業日ゲートは `window_days=90` / `min_days=90` / `min_round_trips=4` / PF≥1.30 / DD≤4.0% /
清算0回のまま**変更していない**（中間判定は追加であって置き換えではない）。

## 6. 実弾0・注文0の担保

* 触るのは `hl_client.fetch_candles / fetch_funding / fetch_l2_book / fetch_asset_meta`（すべて `POST /info`）。
* `/exchange`・署名・APIウォレット鍵はコード上に存在しない（`grep` で確認済み）。
* `s07_paper_runner.py` の JSON 出力は常に `real_orders: 0` / `real_equity_used: 0`。
* 状態ファイルは `data/s07_paper/` 配下のみ（`data/` は gitignore）。

## 7. 変更したファイル

| ファイル | 変更 |
|---|---|
| `scripts/s07_paper/s07_strategy.py` | **変更なし**（rev.1 の5定数は仕様書の適用時に反映済み。11項目を実測で一致確認） |
| `scripts/s07_paper/s07_paper_runner.py` | `--reset` を「削除」から「`archive/` へ退避＋`reset_log.jsonl` 追記」へ変更。`strategy_rev`/`start_day`/`reset` を状態に保持。`oneline()` と `S07_ONELINE.log` 追記を追加。`no_new_closed_day` 経路でも成績表と1行を出す。指標ラベルを rev.1（SMA50/EMA10/EMA20）へ修正。 |
| `scripts/s07_paper/s07_gate.py` | `midterm()`（20営業日・合格/継続/中止）を追加し、JSON・Markdown・1行サマリへ出力。90営業日判定は不変。 |
| `scripts/s07_paper/s07_daily_run.py` | cron の1行出力を1行成績表＋中間判定つきへ（`no_new_closed_day` も明示）。 |
| `scripts/s07_paper/test_s07_gate_cases.py` | 中間判定の境界5ケースを追加（13/13 PASS）。 |
| `scripts/s07_paper/README.md` | rev.1 のパラメータ表・起点リセット手順・1行成績表・中間判定の条件を追記。 |
| `data/s07_paper/`（gitignore） | `state_live.json` を rev.1 で再作成、`archive/state_live_20261030T081135Z.json`、`reset_log.jsonl`、`reports/S07_ONELINE.log` を追加。 |

## 8. 明日以降の動き（前方のみ）

* 2026-10-31 10:00 JST の cron で **2026-10-30 の確定足**を処理し、`enter_short` を 10-30 の寄りで約定させる
  （＝起算日当日のトレード。ここから往復が積み上がる）。
* 20営業日に到達するのは **2026-11-17**（基準日 10-29 を1日目として数えて20日目）。その時点で中間判定が「合格/継続/中止」に確定する。
* 90営業日ゲートは **2027-01-26** ごろ。**過去の当てはめは判定に使わない**。
