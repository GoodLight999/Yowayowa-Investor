# C2 lane15（S-02 vol_target_15）証拠欠陥 判定書 — 2026-11-06

対象: `t_88623ba6` / 実弾発注 0・本番接続 0・既存データ削除 0（読み取りのみ。`data/paper_c2/*` は一切書き換えていない）

---

## 0. 結論（先に3行）

1. **原因は (a) 執行の問題。ただし「指値が板に届かず day 失効」ではなく、前カード検証時の `cancel_all_orders()` による明示全取消**。4本すべて `canceled_at = 2026-11-02T11:02:08Z` / `expired_at = null`（失効は起きていない）。
2. **(b) MTM が lane15 を評価していない、はコードで否定**。lane15 は「ブローカー口座の equity をそのまま採点」しており（`c2_runner.py:492-494, 505`）、lane10 は「目標ウェイト×バー・リターンの理論系列」（同 497-504）で、**両者は最初から別の会計系**。同一ルーチンが片方だけ0を返しているのではない。
3. **(c) は会計としては正しい**（建玉0 → 現金100,000 → equity 不変）。ただし `state.json` の `current_weights_15 = 25%×4` は**目標ウェイトを実保有として書いている**ため、「100%投下済み」は事実に反する。ここが証拠を空に見せる表現欠陥。
4. **60営業日ゲートの起算は 2026-11-03 を維持（リセットしない）**。ただし 11-03〜11-05 は「エクスポージャ0の日」として印を付け、証拠としては空であることを記録すべき（下記 §6）。

---

## 1. 実測（すべて読み取り。実行時刻 2026-11-06 23:33〜23:50 JST）

### 1.1 台帳（書き換えなし）

`data/paper_c2/mtm_history.jsonl`（3行）:

| date | equity_10 | equity_15 | daily_return_15 | alpaca_paper_equity |
|---|---:|---:|---:|---:|
| 2026-11-03 | 99,571.84 | 100,000.00 | 0.000000 | 100,000.00 |
| 2026-11-04 | 101,756.76 | 100,000.00 | 0.000000 | 100,000.00 |
| 2026-11-05 | 99,450.78 | 100,000.00 | 0.000000 | 100,000.00 |

`data/paper_c2/state.json`: `current_weights_15 = {IWM,JNJ,PG,SPY: 0.25, USD: 0.0}` / `current_equity_15 = peak_equity_15 = alpaca_paper_equity = 100000.0` / `completed_trading_days = [11-03, 11-04, 11-05]`。

`data/paper_c2/orders.jsonl`: 4行、すべて `as_of_date=2026-11-03`、`timestamp_utc=2026-11-02T10:16:03Z`、`time_in_force="day"`、`alpaca_response.status="pending_new"`（**発注時点のスナップショットのみ。以後のステータスは一度も記録されていない**）。

`data/paper_c2/decisions.jsonl`: 2行（`C2:10:2026-11-03:001` / `C2:15:2026-11-03:002`）。11-04 以降の決定行なし＝週次のみの設計どおり。

### 1.2 Alpaca Paper API（GET のみ・paper-api.alpaca.markets/v2）

```
GET /v2/account   → {"status":"ACTIVE","equity":"100000","cash":"100000","last_equity":"100000",
                     "trading_blocked":false,"account_blocked":false}
GET /v2/positions → []
GET /v2/orders?status=all&limit=10 → 4件（以下）
```

| symbol | qty | limit | status | filled_qty | canceled_at | expired_at | expires_at |
|---|---:|---:|---|---:|---|---|---|
| SPY | 33 | 759.50 | **canceled** | 0 | 2026-11-02T11:02:08.921006Z | **null** | 2026-11-02T21:00:00Z |
| IWM | 89 | 280.73 | **canceled** | 0 | 2026-11-02T11:02:08.910150Z | **null** | 同上 |
| JNJ | 91 | 274.57 | **canceled** | 0 | 2026-11-02T11:02:08.901182Z | **null** | 同上 |
| PG | 154 | 162.38 | **canceled** | 0 | 2026-11-02T11:02:08.913876Z | **null** | 同上 |

- 4本が **20ms 以内に揃って canceled** → `DELETE /v2/orders`（全取消）のシグネチャ。
- 取消時刻 `11:02:08Z` は 11-02 セッション開始（14:30Z）の **3時間28分前**、失効予定（21:00Z）の **9時間58分前**。**注文は市場に一度も到達していない**。
- 既存記録 `docs/C2_FORWARD_POSITION_START_AMENDMENT_20261103.md:18-20` が同一起因（前カード `t_6a99c045` の完了検証による取消）を既に同定済み。本カードのライブ GET はそれを再確認した。

### 1.3 なぜ 11-02 19:16 JST に発注できたのか（再発条件の確認）

`c2_runner.py:154-169` の先行実行ガード（`current_utc_date < as_of_date` なら判定を消費せずスキップ）は **commit `8b733ae`（2026-11-03 22:15 JST）で追加**。当該発注は 11-02 19:16 JST＝ガード追加前の実行。したがって「起算前日の発注」は既に構造的に塞がれている（`git log -1 -S skipped_prior_to_decision_date`）。

---

## 2. 3択の判定（実ファイル該当行つき）

| 選択肢 | 判定 | 根拠（実測行） |
|---|---|---|
| (a) 執行の問題 | **✓ これが原因** | 4/4 が `status=canceled` / `filled_qty=0` / `expired_at=null` / `canceled_at=2026-11-02T11:02:08Z`。取消は寄り3h28m前で、**指値の当否は一度も試行されていない**（板に届く前に死んでいる）。`alpaca_paper_client.py:183-187` の `cancel_all_orders()` が呼ばれた形跡と一致。 |
| (b) MTM が lane15 を評価していない | **✗ 否定** | `c2_runner.py:492-494` `prev_eq_15 = state["current_equity_15"]` / `new_eq_15 = paper_equity` / `ret_day_15 = new_eq_15/prev_eq_15 - 1`、`:505` `state["current_equity_15"] = new_eq_15`。lane15 は毎日 **実際のブローカー equity を代入している**。100,000.00 は「計算されなかった」のではなく「現金100,000・建玉0の口座の実際値」。lane10 が動くのは `:497-504` の理論系列（後述 §3）だから。 |
| (c) 建玉0が正しく反映された結果 | **△ 会計は正しいが表現が誤り** | 建玉0 → equity 100,000 は正しい。一方 `c2_runner.py:377` は約定結果に関係なく `state["current_weights_15"] = final_w_15` を書くため、`weights_15=100%` は**目標**であって**保有**ではない。state に保有数量・エクスポージャの欄が無い。 |

**断定**: 「lane15 が動かない」の原因は **(a) 執行（前カード検証による明示全取消）**であり、会計はそれを正しく反映した（(c)）。MTM のバグ (b) ではない。ただし **(c) の過程で state が「目標=保有」を混同している**ため、外部からは「100%投下済みなのに動かない」という矛盾に見える。この表現欠陥は別途修正が必要（§5.2）。

---

## 3. 数値の整合性（equity_15 は weights_15 と整合するか）

### 3.1 lane10 は「目標ウェイト×リターン＋現金利」の理論系列（逆算で検証）

`w10 = 各0.214424（合計85.77%）+ USD 0.142304`、`ret10 = Σ w_i·r_i + 0.142304×(DGS1/252)`、DGS1 は `c2_features.py:203,224` のフォールバック **4.12%**。

| date | 実測 daily_return_10 | 再計算（4銘柄終値変化＋現金4.12%） | 差 |
|---|---:|---:|---:|
| 11-03 | −0.4282% | −0.4282% | +0.0ppm |
| 11-04 | +2.1943% | +2.1943% | +0.1ppm |
| 11-05 | −2.2662% | −2.2662% | +0.1ppm |

→ lane10 は**モデル系列**（ブローカー非依存）。lane15 は**ブローカー equity**。同じ MTM 関数の中で別の式を通っているので、「片方だけ0」は仕様。

### 3.2 weights_15 が本当に「100%保有」なら equity_15 はこうなっていた（実測バーで計算）

終値ベース（11-02 終値→各日終値、25%×4・現金0を毎日複利）:

| date | 4銘柄等権の日次リターン | 仮想 equity_15 | 実測 equity_15 | 乖離 |
|---|---:|---:|---:|---:|
| 11-03 | −0.5019% | 99,498.09 | 100,000.00 | **−501.91** |
| 11-04 | +2.5557% | 102,040.93 | 100,000.00 | **+2,040.93** |
| 11-05 | −2.6449% | 99,342.08 | 100,000.00 | **−657.92** |

株数ベース（11-03 寄付で 25% notional・整数株・端数現金）: SPY 33 @752.73 / IWM 89 @279.84 / JNJ 93 @268.51 / PG 153 @162.69 → 投下 99,608.85（99.61%）・残現金 391.15。

| date | 仮想 equity_15 | 対 100,000 乖離 |
|---|---:|---:|
| 11-03 | 99,735.00 (−0.2650%) | −265.00 |
| 11-04 | 102,271.38 (+2.2714%) | +2,271.38 |
| 11-05 | 99,589.43 (−0.4106%) | −410.57 |

**結論**: 実測 equity_15（100,000.00 固定）は weights_15 と**整合しない**。整合しない理由は数値で説明できる＝**実際の保有が0株で、equity は現金そのもの**だから。もし 25%×4 を保有していたら 3日で −$658〜+$2,041 動いていた（1セントも動くはずがない、という直感は正しく、動いていないこと自体が建玉0の証明）。

### 3.3 「指値が届かなかった」可能性の棄却（数値）

当該注文の基準価格は 2026-10-30 終値（`decisions.jsonl` の `inputs.last_bar_date = 2026-10-30`, `spy_close=755.72`）。11-02 セッションの実測 OHLC と突き合わせる:

| symbol | ref(10-30) | 指値 | 上限bps | 11-02 寄付 | 寄付のref比 | 寄付≤指値 | 11-02 安値 | 安値≤指値 |
|---|---:|---:|---:|---:|---:|---|---:|---|
| SPY | 755.72 | 759.50 | +50.0 | 759.45 | +49.4 | ✓（余裕0.66bps） | 751.60 | ✓ |
| IWM | 279.33 | 280.73 | +50.1 | 281.64 | +82.7 | ✗ | 278.88 | ✓ |
| JNJ | 273.20 | 274.57 | +50.1 | 272.06 | −41.7 | ✓ | 267.44 | ✓ |
| PG | 161.57 | 162.38 | +50.1 | 162.24 | +41.5 | ✓ | 162.24 | ✓ |

→ 4本すべて寄付または日中に指値到達可能。**指値は失効要因ではない**（そもそも取消が寄りの3.5時間前）。原因は取消のみ。

---

## 4. 11-09 22:00（週次）→ 11-10 09:40（実測）の確認手順

前提（実測済み）:
- cron `c2-paper-weekly`（`0 22 * * 1`、`c2_paper_weekly.sh`、次回 **2026-11-09T22:00 JST**）と `c2-paper-daily`（`0 9 * * *`）はどちらも `[active]`。
- 11-09 22:00 JST = 13:00 UTC = **08:00 EST（月曜プレマーケット）**。day 注文は 21:00Z（16:00 EST）失効。11-10 09:40 JST = 11-10 00:40Z（火曜）。

### 4.1 11-09 22:10 JST — 発注が「生きている」ことの確認（取消の再発検知）

```bash
cd /root/current-work/yowayowa-investor
set -a; . /root/.secrets/alpaca_paper.env; set +a
wc -l data/paper_c2/decisions.jsonl data/paper_c2/orders.jsonl
tail -4 data/paper_c2/orders.jsonl | jq -c '{symbol,side,qty,limit_price,tif:.time_in_force,st:.alpaca_response.status}'
curl -s -H "APCA-API-KEY-ID: $ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
  "$ALPACA_BASE_URL/orders?status=all&limit=4" | jq -c '.[] | {symbol,status,filled_qty,expires_at,canceled_at}'
```

期待値:
- `decisions.jsonl` = **4行**（11-09 の C2:10 / C2:15 が2行増える）
- `orders.jsonl` = **8行**（4行増。`as_of_date=2026-11-09`）
- live orders 4件が `status ∈ {new, accepted, pending_new}`、`filled_qty="0"`、`expires_at=2026-11-09T21:00:00Z`、**`canceled_at=null`**
- `canceled_at` が入っていたら **取消の再発**。`§5.5` の原因（誰が DELETE を呼んだか）を先に潰す。再発注はしない（別カードで裁可）。

### 4.2 11-10 09:00 JST の日次バッチ前提チェック（09:40 より前に必ず）

```bash
cd /root/current-work/yowayowa-investor
for s in SPY IWM JNJ PG; do printf "%s " $s; tail -1 data/stock-ohlcv/$s/ohlcv.jsonl | jq -r .as_of; done
git diff --stat scripts/paper_c2/c2_daily_batch.py
```

期待値: 4銘柄すべて `2026-11-09T05:00:00Z`。かつ `c2_daily_batch.py` の未コミット差分（セッション日を「最新バー日」から決める修正）が残っていること。

> **重要**: HEAD の `c2_daily_batch.py` は `today = datetime.now(UTC).date()` を使う（`git show HEAD:...` で確認）。11-10 00:00Z 時点の `today` は **11-10**、最新バーは **11-09** なので、HEAD のままだと `skipped_stale_or_missing_bar` になり 11-09 の MTM 行が作られない。現在の working tree の修正が効いていることが 11-10 証拠の**必須依存**。差分が消えていたら手動で:
> ```bash
> .venv/bin/python -m scripts.paper_c2.c2_daily_batch --date 2026-11-09
> ```
> （`--date` は working tree 版のみ有効。HEAD 版には引数が無い）

### 4.3 11-10 09:40 JST — 建玉が付いたかの判定

```bash
cd /root/current-work/yowayowa-investor
set -a; . /root/.secrets/alpaca_paper.env; set +a
echo "--- 台帳行数 ---"; wc -l data/paper_c2/{decisions,orders,mtm_history}.jsonl
echo "--- 11-09 MTM ---"; tail -1 data/paper_c2/mtm_history.jsonl | jq -c '{date,equity_10,equity_15,alpaca_paper_equity,daily_return_15}'
echo "--- state ---"; jq -c '{current_equity_15,alpaca_paper_equity,completed_trading_days,current_weights_15}' data/paper_c2/state.json
echo "--- 建玉 ---"; curl -s -H "APCA-API-KEY-ID: $ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" "$ALPACA_BASE_URL/positions" | jq -c '.[] | {symbol,qty,avg_entry_price,market_value}'
echo "--- 注文最終状態 ---"; curl -s -H "APCA-API-KEY-ID: $ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" "$ALPACA_BASE_URL/orders?status=all&limit=4" | jq -c '.[] | {symbol,status,filled_qty,filled_avg_price,canceled_at}'
```

期待値（建玉が付いた場合）:
| 検査 | 期待値 |
|---|---|
| 行数 | decisions **4** / orders **8** / mtm_history **4** |
| mtm 最終行 | `date="2026-11-09"`、`equity_15 ≠ 100000.00`、`alpaca_paper_equity ≠ 100000.00`、`daily_return_15 ≠ 0.0` |
| positions | **4銘柄**、qty は 11-09 価格で `int(25000/price)` 相当（SPY≈33 / IWM≈89 / JNJ≈93 / PG≈153、価格次第で±1） |
| orders | `status="filled"`、`filled_qty = qty`、`filled_avg_price ≠ null`、`canceled_at=null` |
| state | `current_equity_15 == alpaca_paper_equity`、`completed_trading_days` に `2026-11-09`（長さ4） |
| 整合式 | `daily_return_15 ≈ (daily_return_10 − 0.142304×0.0412/252) / 0.857696`、許容 **±50bps**（lane15 は整数株の端数現金ぶん約0.4%だけ lane10 より現金比率が低い） |

**建玉が付かなかった場合の分岐**:
- orders が 4行のまま → 週次が発注まで到達していない。cron 出力（`hermes -p default cron list` の当該ジョブ / `C2_WEEKLY_OK status=...`）で `skipped_prior_to_decision_date` / `already_executed` / `halted_by_kill_switch` を確認。
- 注文はあるが `canceled` → §4.1 と同じ取消の再発。5.5 の監視を先に入れる。
- 約定したが mtm 行が無い → バー欠測 or 日付決定の不整合。`--date 2026-11-09` で再実行（冪等: `last_mtm_date` 一致なら即 return）。

---

## 5. 最小修正の提案（実装は別カード。本カードでは変更していない）

### 5.1 約定結果が台帳に残らない（最重要・観測性）
`orders.jsonl` は発注時の `pending_new` スナップショットのみ。以後の fill/cancel/expire は**どこにも記録されない**ため、今回「3日間だれも気づかない」が成立した。
- 最小修正: 日次サイクルに `GET /orders?status=all` の突き合わせを1ブロック追加し、`order_status_history.jsonl`（追記専用）へ `{client_order_id, status, filled_qty, filled_avg_price, checked_at}` を書く。launch 条件は「orders.jsonl に当日以降の未確定行があるとき」だけ。

### 5.2 `weights_15` が目標と保有を混同（表現欠陥）
`c2_runner.py:377` が約定結果を見ずに `current_weights_15 = 目標` を書く。
- 最小修正: `target_weights_15`（現行値）と `actual_positions_15`（`GET /positions` の symbol→qty/market_value）を別フィールドに分離し、`exposure_15 = Σ market_value / equity` を追加。既存キーは互換のため残す。

### 5.3 MTM 行にエクスポージャが無い（証拠の質）
`mtm_record`（`c2_runner.py:515-526`）には建玉有無が無く、後から「その日は建玉があったか」を台帳だけで判定できない。
- 最小修正: `positions_count` と `gross_exposure_15` を1行に追加（既存行は変更しない＝追記のみ）。

### 5.4 ペーパー約定モデル: 「指値+50bps」か「成行」か（数値比較）

| 方式 | コスト上限 | 実測コスト | 非約定リスク | 証拠への影響 |
|---|---:|---:|---|---|
| 指値+50bps（現行） | 1銘柄50bps＝全体で最大 $500 | 11-02 寄付換算で **+33.0bps（$328）**（ref=10-30終値） | **実測で発生**。SPY は上限+50.0bps に対し寄付+49.4bps＝**余裕0.66bps**で、翌寄付が+51bps なら未約定 | 非約定＝その週の証拠が丸ごと空（60日の1/12を失う） |
| 成行（寄付） | 青天井（当日ギャップ） | 同じく **+33.0bps**（寄付約定なら指値と同じ値段。11-02 の寄付ギャップは −1.2%〜+0.8%） | 0 | 必ず建玉が付く |

- 指値の弱点は「上限50bps」そのものではなく、**基準が1営業日前の終値**であること。SPY は上限50.0bps に対し寄付が+49.4bps で、実質的に余裕が無い（0.66bps）。
- 推奨（最小）: (i) 指値の基準を**発注直前の直近クォート**に取り直す（当日 08:00 EST の気配/最終約定）、(ii) 寄付後 30分（10:00 EST）に未約定なら**成行へ置換**、(iii) 置換も取消も台帳に残す。
- 数値根拠: 上限50bps を維持しつつ、基準を当日に取り直せば余裕が 0.66bps → 約50bps に戻る。コスト上限は現行と同じ $500 のまま、非約定リスクだけを消せる。

### 5.5 監視の穴
`c2_gap_watch.py` は**バーの鮮度だけ**を見ており、`orders` / `positions` / `fill` に触れる行が0件（grep 0ヒット）。「建玉が付いていない」ことを検知する監視が存在しない。
- 最小修正: gap-watch に「`target_weights_15` の Σ|w|>0 かつ `positions=[]` が2営業日連続」で警告する1条件を追加。

### 5.6 未コミット依存（11-10 の証拠が消えるリスク）
`git status` の C2 差分（`alpaca_paper_client.py` / `c2_daily_batch.py` / `c2_attribution.py` / `c2_features.py`）が**未コミット**。特に `c2_daily_batch.py` のセッション日決定は HEAD と挙動が違う（§4.2）。別カードで検収・コミットするまで、11-10 の証拠は working tree に依存する。

---

## 6. 60営業日ゲートの起算（結論1行）

**起算日 2026-11-03・判定日 2027-02-01 は維持（リセットしない）。** 11-03〜11-05 は「エクスポージャ0の日」として記録し、11-04 裁定どおり1営業日として数える。ただし `c2_gate.py:204-211` は標準偏差0のとき Sharpe を `0.0` に落とす実装なので、**建玉0の日が続けば Sharpe は機械的に 0.00 → 第1基準 FAIL** になる（「3.3%希釈で 0.77」が成り立つのは、実エクスポージャ日が大半を占める場合だけ）。したがって起算は動かさず、判定時に「建玉成立日以降のサブセット」の数値も併記できるよう §5.3 のフラグを先に入れる。

---

## 7. このカードで実施していないこと

- 実弾発注: **0件**（Alpaca は `paper-api` のみ、GET のみ）
- 本番接続: **0件**（live API `api.alpaca.markets` への接続なし。`alpaca_paper_client.py:66-77` のガードは維持）
- データ削除・書き換え: **0件**（`data/paper_c2/*` は読み取りのみ。検証スクリプトは `/root/.hermes/profiles/worker-go/cache/scratch/t_88623ba6/`）
- 新規注文・新規 cron: **0件**
- C2 設計（design_doc SHA256 `be036bbf...`）・11-04 裁定の変更: **なし**
