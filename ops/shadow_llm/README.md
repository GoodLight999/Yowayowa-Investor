# shadow-llm — book D v2 の試作ループ（設計のみ・実注文 0 件）

設計V2（`docs/LLM_DISCRETION_V2_20261025.md`）の §7「試作」を、**1 ティック手で回せる最小のループ**として実装したもの。
LLM が 7 つの判断点（J1・J2・J4・J5・J6・J7・J8）を決め、機械は事故クラス M1〜M7 だけを止める。
**発注はしない・実資金は使わない・既存デモ A（`ops/demo-loop/`）には一切触らない。**

## 回し方

```bash
cd /root/current-work/yowayowa-investor
.venv/bin/python -m ops.shadow_llm.run_once            # 1 ティック（LLM 1 回・journal に 1 行）
.venv/bin/python -m ops.shadow_llm.run_once --offline  # 無料 API を叩かない
.venv/bin/python -m ops.shadow_llm.run_once --dry-run  # journal に書かない
.venv/bin/python -m ops.shadow_llm.selftest_enforcement  # M1〜M7 の実測（合成入力）
.venv/bin/python -m ops.shadow_llm.measure_materials     # 材料のトークン数だけ測る
```

cron には入れていない（タスク制約）。systemd ユニットも無い。常駐プロセスも無い。

## 1 ティックの流れ

1. `materials.build()` — 材料パケットを組み立てる。ローカルの `data/crypto-ohlcv`・`data/demo-loop/journal.jsonl` と、
   キー不要の公開エンドポイント（Binance の `premiumIndex` / `bookTicker` / `globalLongShortAccountRatio`、alternative.me の F&G）だけ。
   取れない材料は `null` にして `unavailable` に理由を書く（捏造しない）。
2. `llm.call()` — **1 回だけ**呼ぶ。リトライは最大 1 回。`finish_reason=length`・429・空本文は「未回答」扱い。
3. `schema.validate()` — §5-2 の型検査（parse / 必須キー / action / targets / 0.25 格子 / cited / skip の再開条件 / hold_plan / degraded）。
4. `enforce.apply()` — 事故クラス M1〜M7。当たったら LLM の値を上書きし、**上書き前・上書き後・理由を必ず記録**する。
5. `data/shadow-llm/journal.jsonl` に 1 行追記（時刻・材料 sha256・生出力・検証合否・強制の当否・上書き内容）。

## モデルの差し替え（1 箇所）

既定は **無料枠**（モデル ID が `:free` で終わる）。差し替えは環境変数か、`ops/shadow_llm/model_config.json` の 1 ファイルだけ。

| 設定 | 既定 | 環境変数 |
|---|---|---|
| モデル | `nvidia/nemotron-3-ultra-550b-a55b:free` | `SHADOW_LLM_MODEL` |
| ベース URL | `https://opengateway.gitlawb.com/v1` | `SHADOW_LLM_BASE_URL` |
| 鍵の環境変数名 | `MINIMAX_M3_API_KEY` | `SHADOW_LLM_KEY_ENV` / `SHADOW_LLM_API_KEY` |
| 材料の公開 API | 叩く | `SHADOW_LLM_OFFLINE=1` で停止 |
| 有料モデルの許可 | 不許可 | `SHADOW_LLM_ALLOW_PAID=1` |

`:free` で終わらないモデル ID は `SHADOW_LLM_ALLOW_PAID=1` が無い限り **起動時に拒否**する（誤って従量課金に乗らないため）。
既存ボットの設定（`~/.hermes/profiles/*/config.yaml`）は 1 文字も変えていない。

## 実測（2026-10-25・すべて実際の出力）

### 1. 1 ティック動き、journal が 1 行増える

`data/shadow-llm/journal.jsonl` は 0 → 1 → 2 → 3 → 4 → 5 行（1 実行 = 1 行）。各行の中身:

| # | 時刻 (UTC) | contract | 棄却理由 | action | 上書き後 targets | fallback | 強制 | finish | prompt/completion | cost | orders |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 07:52:55 | **false** | `not_json: Expecting value at 0` | — | BTC 0 / ETH 0 | true | M4,M6 | `length` | 1556 / 1600 | 0 | 0 |
| 2 | 07:56:16 | true | — | reduce | BTC 0.5 / ETH 0.25 | false | なし | `stop` | 1578 / 3254 | 0 | 0 |
| 3 | 07:57:59 | true | — | hold | BTC 0.5 / ETH 0.5 | false | なし | `stop` | 1578 / 2830 | 0 | 0 |
| 4 | 08:01:33 | true | — | hold | BTC 0.5 / ETH 0.498914 | false | M1b（誤発火→修正） | `stop` | 1577 / 3091 | 0 | 0 |
| 5 | 08:05:48 | true | — | hold | BTC 0.5 / ETH 0.5 | false | なし | `stop` | 1579 / 3112 | 0 | 0 |

- 行 1 は**棄却側の記録**（`contract.ok=false` と理由、`fallback=true` が残っている）。
  原因は 2 つで、どちらも直した: ①`max_tokens=1600` を思考で使い切り `finish_reason=length`、②JSON 強制が無く本文が分析文になった。
  修正は `response_format={"type":"json_object"}`、`max_tokens=4096`、`finish_reason=length` を未回答として扱う、の 3 点。
- 行 1 で M4 が「hold 相当の weights（合計 1.0020）」を 0 に落としていた。**M4 は新しい判断にだけ効く**よう修正し、
  fallback の「前日値保持」は 0 にしない（行 2・3・5 で M4 が誤発火しないことを実測）。
- 行 4 では M1b が誤発火した。ETH の目標 0.5 が保有 0.498914 より 0.11pp 高いだけ（＝mtm の丸めで、追加ではない）なのに
  「stop を不利方向へ動かした」と判定していた。原因は不等号が `>=` だったこと。**厳密に悪化した時だけ**（`stop > prev`）に直し、
  併せて「保有比 +0.2pp 未満は追加とみなさない」許容を入れた（行 5 で再発しないことを実測）。
- 行 3 の生出力（そのまま）:

```json
{"action":"hold","targets":{"BTC":0.5,"ETH":0.5},"hold_plan":{"min_days":1,"max_days":10,"time_stop_to":0.0},
 "stop_basis":"atr15","confidence":0.65,
 "cited":["tech.sma200_distance.BTC","tech.sma200_distance.ETH","tech.dd365.BTC","tech.dd365.ETH",
          "tech.breakout_age_days.BTC","book.drawdown","book.weight.BTC","book.weight.ETH",
          "deriv.funding.BTC","deriv.lsr.BTC"],
 "rationale":"Hold new positions (hold_days=0) in uptrend above SMA200 but deep drawdown from highs; positive funding & high LSR signal crowded longs; book DD -5% limits add.",
 "invalidators":["close below SMA200","funding turns negative","book drawdown exceeds -8%","breakout structure fails"],
 "reentry_condition":"price reclaim above 365d high with funding normalization"}
```

### 2. スキーマ検証の合否（両方の記録）

- 通った例: 行 2・3。`checks` は 9 項目すべて `ok=true`、`reasons=[]`。
- 通らなかった例: 行 1。`checks[0] = {"check":"1.parse","ok":false,"detail":"not_json: Expecting value at 0"}`、`fallback=true`。
- 棄却は再試行しない（§5-2）。plan C により「前日の実行値」を保持し、`fallback` を記録して集計から除外できる形にした。

### 3. 機械強制 M1〜M7 が「その入力を与えると上書きされる」ことの実測

`python3 -m ops.shadow_llm.selftest_enforcement` → **10 ケース全 PASS・failures 0**（合成入力）。

| ケース | 入力 | 上書き |
|---|---|---|
| 対照（きれいな入力） | stop 有効・損失 0・gap 0 | **何も発火しない**（`fired=false`, targets 0.5 のまま） |
| M1 | `stop_basis=guess_the_dip`（解決不能） | BTC 0.5 → **0.0**（`no usable stop (unknown_basis)`） |
| M1b | 前回 stop 77000 に対し今回 78025 | BTC 0.5 → **0.0**（`stop moved adversely`） |
| M2 | `loss_today=-6%` | BTC 0.5 → **0.0** |
| M2b | `loss_week=-12%` | BTC 0.5 → **0.0** |
| M3 | 価格 80000・stop 30000・equity 1.00 | BTC 0.5 → **0.0**（`max_loss=0.3125 > 5%`） |
| M4 | targets 合計 1.50 | 両方 → **0.0**（`sum=1.5000 > 1.0`） |
| M5 | `stale_quotes=9 samples unchanged` | 両方 → **0.0** |
| M6 | `contract_ok=false`（前日 0 を保持） | BTC 0.5 → **0.0**（`hold previous`） |
| M6b | `deadline_exceeded=true`（前日 0.25 を保持） | BTC 0.5 → **0.25** |
| M7 | `reconcile_gap_usd=0.50`（equity 1.00） | 両方 → **0.0** |

上書きはすべて `{"class","asset","before","after","reason"}` として journal の `enforcement.overrides` に残る（黙って直さない）。

### 4. 材料のトークン数（§9 の上限 2,000）

`python3 -m ops.shadow_llm.measure_materials` の実測:

```json
{"model":"nvidia/nemotron-3-ultra-550b-a55b:free","free_lane":true,"materials_bytes":1964,
 "materials_tokens_prompt":965,"local_upper_bound_estimate":982,"ceiling":2000,
 "within_ceiling":true,"cost_usd":0}
```

- 材料は**単体で送って**プロバイダの `usage.prompt_tokens` を読んだ値（＝材料そのもののトークン数）。**965 ≤ 2,000**。
- 判断ティック全体（system + 7 判断点 + 材料 + 契約）でも `prompt_tokens=1578`（行 2・3）。材料はその部分集合なので上限の証明としても十分。

### 5. 実注文 0 件・実資金 0 円・既存デモ A は不変

- `orders_placed: 0` を毎行に記録。発注経路のコードを import していない:
  `grep -rn "submit_order(\|place_order(\|from yowayowa.broker\|import broker" ops/shadow_llm/*.py` → **0 件**。
- `ops/demo-loop/` の 7 ファイルの sha256 は**作業前後で完全一致**（`diff before after` が空）:

```
ece42ee387becd21bf830b12d4b1ce55a7fc520298a4910fa360df5dbc25dbf9  ops/demo-loop/daily.sh
a8a9a97cd75cb4bce9e8618bdfa7514536062c563d00a3b098e8a972c2eb50ed  ops/demo-loop/fill_v2.sh
0d987347794139bdd229627835c4eb72ae0ffeb496008ff879b6271430554fcb  ops/demo-loop/legacy_v1/decide.sh
535e4a3ff61bf318ad6a6e259d99afc58d0a1539f7c61d8634cf5224c7942fed  ops/demo-loop/legacy_v1/demo_loop.sh
b612b4de6630a2aab7719959b71da32ea6dbf2454a5c83081d6a42d97612d0af  ops/demo-loop/legacy_v1/fill.sh
b926d1b83af847eb6259681764e10d10b3aa2f90bddedb77332999845bbd8b00  ops/demo-loop/legacy_v1/mark.sh
e42a21eb9921ab53a2287a708fe1d6b4368956ec68ba789b407142f4f2522cc4  ops/demo-loop/legacy_v1/report.sh
143902bee30c3b4ac52b204e88066aaafa0c13158c3d52fb57f20a2e7d450c3d  ops/demo-loop/weekly.sh
```

- `data/demo-loop/journal.jsonl` は **307 行のまま**、sha256 `81c7b495ad857a13657a462a1f84a201ba05d3925be81a5fd2f420492c9e1020` で不変。
  `ops/demo-loop/daily.sh` は実行していない。
- `data/` への書き込みは `data/shadow-llm/journal.jsonl`（新規）だけ。`data/` は `.gitignore` 対象なので、コミット対象は `ops/shadow_llm/` のみ。

### 6. LLM の実測 cost == 0.0

| 呼び出し | `usage.cost` |
|---|---|
| 事前 probe（`{"ok":true}` を返させる） | `0`（`cost_details.upstream_inference_cost: 0`） |
| 材料のトークン計測 | `0` |
| ティック 1・2・3 | `0` / `0` / `0` |

journal には `cost_usd` と `cost_measured_zero` を毎行記録している。

## 使っていない材料（正直に `unavailable` に出す）

`news.headlines` / `news.topic_persistence`（無料のニュース供給を結線していない）、`rates.10y`、
`cal.minutes_to_event`（無料カレンダー未結線）、`exec.spread_p95_24h`（24 時間の板履歴が無い）。
これらは `null` のまま渡し、`degraded` の判定は §6-3 の必須材料（`tech.*`）だけに掛けている
（degraded の日に `enter`/`add` は型で棄却、`reduce`/`flatten`/`skip` は許可）。

## 設計V2 からの意図的な読み替え（レビューで見てほしい点）

1. **ディレクトリ名**: タスク本文の `ops/shadow-llm/` は Python のモジュール名に使えない（ハイフン不可）ため、
   受け入れコマンド `python3 -m ops.shadow_llm.run_once` が通る `ops/shadow_llm/` に置いた。ファイル構成は 1 本のループのみ。
2. **`data/demo-loop/A/state.json` は使っていない**: 中身が journal の mtm と整合しない（v1 ループの残骸で、
   units/equity が 2026-10-10 時点）。帳簿 A の現在値は journal の mtm 行（cash + units×price）から取る。
   これは M7 の照合（再計算との差）をそのまま実装に使えるので、都合が良い。
3. **M1 は「新規に建てる銘柄」にだけ効く**: 既存の建玉を守る話ではないので、`target <= held` の銘柄は検査しない。
   M4 も同じ理由で fallback（前日値保持）には掛けない（掛けると必ず 0 になる）。
4. **`stop_basis` は LLM、stop 価格は機械**: §5-1 のとおり LLM に価格を書かせない。価格は `enforce.resolve_stop()` が
   `wick_low` / `sma20` / `atr15` / `fib` から計算し、解決できなければ M1（fail-closed）。
5. **`finish_reason=length` を未回答扱い**にした（§5-3 の「length は未回答」に合わせた）。行 1 がその実例。

## まだ無いもの（この試作の範囲外）

- 約定（`apply_fill`）・mtm・weekly への結線（§7-1 の「既存機構をそのまま使う」部分）。今は決定と記録だけ。
- 20 営業日の判定（§7-5）と対照（同じ平均グロスでの機械的縮小）。判定は §7-4 の事前登録どおり別カードで行う。
- 帳簿を 1 本増やす改修（`paperloop.py` の 11 分岐）と「帳簿 A の決定 32 本がバイト一致」の golden テスト（§7-3）。

## 障害と復帰の実測（2026-11-10〜11-13）

### 何が起きたか

11-10 / 11-11 / 11-12 の日次 tick が 3 日連続で `finish_reason=length`（§5-3 どおり未回答扱い・規則は変更しない）。
contract 不合格 → 前日 hold フォールバック・発注 0。起動元 cron は journal が増えるだけを見て ok を返し続け、監視に映らなかった。

### 原因の実測（journal 26 行 + 生 probe 1 回）

- 入力は膨らんでいない: MATERIAL PACKET は bytes 1941-1963 / token_estimate 約 975 / prompt_tokens 1556-1579 で全期間ほぼ一定。
- **真因はモデルの隠れ推論（reasoning）**: contract ok 日の `raw_output`（純 JSON）は 486-917 文字なのに、
  `usage.completion_tokens` は 1485-3374。gateway が content から除去する推論が completion に計上される。
  11-13 の生 probe では gateway が `completion_tokens_details.reasoning_tokens=5645` を直接返し、これを確定させた
  （content は 695 文字の純 JSON、completion 合計 5905）。
- 推論量は日により変動（2796 / 1849 / 3085 …）し、11-10〜11-12 は推論+JSON が 4096 を超えて毎日 length で打ち切られた。
  max_tokens 4096 はこのモデルには小さすぎた、が結論。(b) 入力膨張説は prompt_tokens 一定により棄却。

### 修正（3 ファイル + 起動スクリプト、モデルは不変）

1. `config.py`: `max_tokens` 4096 → **8192**。モデルは `nvidia/nemotron-3-ultra-550b-a55b:free` のまま、`:free` ガード不変。
2. `llm.py`: 失敗 attempt も `usage` / `finish_reason` / 本文先頭 200 文字（`fail_content_head`）を返す
   （旧実装は破棄し、journal の length 日は `usage=null` で証跡が残らなかった）。
3. `run_once.py`: journal の `llm` に `fail_content_head` を記録。
4. 起動スクリプト（profile 側 `shadow_llm_tick.sh`）: journal が増えても run_once の rc を握り潰さず `exit "$RC"`
   （rc=3 = contract 拒否 → cron が失敗を記録する）。

### 復帰の実測（2026-11-13 09:11 JST）

| 項目 | 値 |
|---|---|
| probe（max_tokens 8192） | HTTP 200・`finish_reason=stop`・completion 5905（reasoning 5645・content 695 文字の純 JSON） |
| 手動 run_once 1 回 | `tick_id=20261113T001134Z`・attempts 1・HTTP 200・**`finish_reason=stop`・`contract.ok=true`**（9 チェック全通） |
| usage | prompt 1546 / completion 4822 / total 6368 |
| その他 | fallback=false・orders_placed=0・journal 27→28 行・rc=0 |

### 盲点修正の実測（2026-11-12）

config の base_url を一時的に死んだ URL へ差し替えて `hermes cron run` → journal 1 行追記（fallback 行）・rc=3 →
**cron `last_status=error`・"Script exited with code 3"・failure_streak=1**（旧実装は journal が増えると無条件に ok を返した）。
注入は復元済み。以後、contract 不合格の tick は cron の監視に失敗として映る。
