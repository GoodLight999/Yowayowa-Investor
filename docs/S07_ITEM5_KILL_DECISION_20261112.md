# 項目5（清算カスケード受け）キル成立の判定 — KILL・実資金/判定の候補から除外・再挑戦しない

作成: worker-go（カード `t_f2c6f343`）／判定日: 2026-11-12 JST／種別: 文書化＋除外（実弾0・入金0・発注0・口座操作0）
対象: S-07 項目5「清算カスケード受け」の前方再現確認（凍結仕様 深さ2%・遅延30分・往復9bps・1,000 USD/試行）

## 0. 結論

**判定は `KILL`。** 事前登録のキル条件（1局面の損失が -85.32 USD を超えたらその時点で即終了）が成立しており、判定日（独立20局面）も期限（2026-12-02）も待たない。キル成立は後述のとおり再計算でも一致し（`state_source=recomputed` / `recompute_mismatch=false`）、数字を動かして延命する余地はない。

**この系統（深さ2%・遅延30分、および深さ・遅延の他の組み合わせ）は再挑戦しない。実資金・判定の候補から除外する。**（事前登録 `t_0d06b1de` の条件5の適用）

## 1. 事前登録の条件文（原文・カード `t_0d06b1de`・Muse裁定 2026-11-07）

> ■前方の物差し（裁定で先に固定済み・動かさない）
> 1. **新しい独立局面が20件以上たまった時点**で判定する。
> 2. 合格条件: 1件あたりの期待値がプラス、**かつ95%区間の下限も 0 を上回る**。
> 3. **キル条件**: 1局面の損失が測定時の最悪（−42.66）の**2倍（−85.32）を超えたら、その時点で即終了**。判定日を待たない。
> 4. 判定期限は**遅くとも 2026-12-02**（HLテストネット判定と同時）。20局面が先に貯まれば前倒しで判定してよい。
> 5. 通らなければ**この系統は全部終了**（深さ・遅延の他の組み合わせも再挑戦しない）。

この条件文は本カードでも変更していない（仕様の再調整・閾値の再導出をしない）。

## 2. 実測値（判定時スナップショット: 2026-11-12 20:11 JST = 2026-11-12T11:11:33Z）

出所: `/root/current-work/yowayowa-investor/data/s07_paper/funding_top20/fwd_item5/kill_ledger.json`
（sha256 `d8c08bfc66c8992cb977c5c5b85ff6c86ada6477585814581e43f8037c5db58a`）

| 項目 | 実測値 |
|---|---|
| `status` | **KILLED** |
| `killed_at_utc` | **2026-11-11T01:03:17Z**（11-11 10:03 JST の日次フックが記録） |
| `threshold_usd` | **-85.32** |
| `trigger.episode_id` | `ep-2026-11-10T200500Z` |
| `trigger.day_key` | `2026-11-10` |
| `trigger.cluster_pnl_usd` | **-123.4043** |
| `trigger.n_filled` | 1 |
| `trigger.determined_utc` | 2026-11-10T21:37:00Z（確定 → 記録 3.44h） |
| `counts` | **evaluated=22 / triggered=1** |
| `state_source` / `recompute_mismatch` | `recomputed` / `false`（再計算でも閾値割れを再現） |
| `threshold_origin` | 測定時最悪局面 -42.657 USD（`results_5m_primary.json` 深さ2%/遅延30分/30独立局面）の2倍＝`t_d4fdcfc4` で確定（-85.32）。再導出・再調整しない |

`-123.4043 < -85.32` なので、条件3（キル）が成立する。`counts.evaluated=22` はキル判定までに評価した確定局面数であり、独立局面は9件（判定資料 `decision_page.md`：累積の独立局面数 9/20）で、条件1の20件には到達していない。つまり**キルは判定日を待たずに発効した**。

## 3. 凍結判定の固定（sha256）

| 対象 | sha256 | 内容 |
|---|---|---|
| `decision_frozen.json` | **`c2c54f5c7a014f80f4df22da9c5e99b53e924fd42691d8ad02e4b07860096b9c`** | `frozen_at_utc=2026-11-11T01:03:17Z` / `result.verdict="キル発動"` / `kill_threshold_usd=-85.32` / `worst_episode_usd=-123.4043` / `independent_episodes=9` / `episodes_final=15` / `deadline_utc=2026-12-02`（`deadline_reached=false`）/ `kill_episode_ids=["ep-2026-11-10T200500Z"]` / `input_sha256=cdb928de6283075e6f83af3c61163b0fc7657c6a37dac24ac5abe912100a9917` |
| `decision_frozen.sha256` | 同値（`c2c54f5c…96b9c`） | 上記ファイルの中身の sha256 を記録したサイドカー。再計算しても一致した（`decision_page.py` はこの照合に失敗すると `DecisionInputError` で停止する） |
| `scripts/s07_paper/cascade_sim.py` | **`7370a7e4cdc78a2fd368344bc85a8007652881cd81d364c287eacd87ae4c10df`** | `cumulative.json` の `cascade_sim.sha256` = `expected_sha256` = 実ファイルの sha256（三者一致）・`identical_to_measurement=true`（測定時と同一バイトの関数を使っている） |

補足: 測定量は新しく書いていない。`cascade_sim.py` の `find_events` / `simulate_event` を無改変で呼ぶ既存経路（`fwd_item5_pnl.py` / `decision_page.py`）の出力だけを使った。凍結仕様は `cumulative.json` の `spec` に `depth_pct=2.0` / `delay_min=30` / `rest_min=30` / `hold_min=60` / `round_trip_bps=9.0` / `notional_usd=1000.0` として残っており、本カードで変更していない。

## 4. 再挑戦しない（固定）

**この系統（深さ2%・遅延30分、および深さ・遅延の他の組み合わせ）は再挑戦しない。実資金・判定の候補に戻さない。過去データの拡張・再測定による再挑戦もしない。**

## 5. 実資金・判定の候補からの除外（洗った結果と差分）

### 5.1 洗った範囲と結果

| 資料 | 項目5の残存 | 措置 |
|---|---|---|
| C2ゲート資料（`docs/G2_GATE_20261102.md` / `docs/G2_PAPER_PHASE_DESIGN_20261102.md` / `docs/C2_*.md` / `scripts/paper_c2/c2_gate.py`） | **0件**（項目5への言及なし） | 変更なし |
| S-07本体の判定資料（`fwd_item5/decision_page.md` と生成器 `scripts/s07_paper/decision_page.py`） | キル発動の表示はあるが「除外・再挑戦禁止」が無い | 生成器に1行追加（5.2）。次回日次run の生成物に反映される |
| 実弾判断の候補列挙（`docs/GATE_RETIREMENT_20261111.md`） | 既に §5「対象外（触らない）」に **S-07（前向き測定）** と明記 | 変更なし（既に候補外） |
| 発散候補の実施順序表（`docs/DIVERGENCE_ORDER_20261109.md` §4.2） | **R3-4「清算カスケード受け（項目5）の過去データ拡張」着手日 2026-11-16 が生きていた** | **中止**に変更（5.3） |
| 事前登録カード `t_0d06b1de` の記録 | 「この系統は全部終了」 | 変更なし（原文をそのまま適用） |

備考: `docs/CIO_S07_REV1_SPEC_20261030.md` と `docs/CIO_FX_LEVERAGE_LANE_DESIGN_20261030.md` の「項目5」は **S-07 の判定項目5（反転頻度 ≤ 月6回）** であり、本カードの項目5（清算カスケード受け）とは別物である。混同して消さないため、この2文書は触っていない。

### 5.2 S-07本体の判定資料（生成器 `decision_page.py`）

before（`render_page` の先頭・判定行のみ）:

```
# 前方局面 判定ページ（項目5）

**判定: キル発動** — 確定局面の損益が -85.32 を下回った（期限を待たない）

- 判定日（as_of, UTC）: 2026-11-11／期限: 2026-12-02（期限到達: いいえ）
```

after（`--no-write` で同じ入力を描画した実出力。次回日次run が書く `decision_page.md` も同じになる）:

```
# 前方局面 判定ページ（項目5）

**判定: キル発動** — 確定局面の損益が -85.32 を下回った（期限を待たない）

- **扱い（KILL 確定）: この系統（深さ2%・遅延30分および深さ・遅延の他の組み合わせ）は再挑戦しない。実資金・判定の候補から除外**（判定文書: docs/S07_ITEM5_KILL_DECISION_20261112.md）

- 判定日（as_of, UTC）: 2026-11-11／期限: 2026-12-02（期限到達: いいえ）
```

生成器の差分（`scripts/s07_paper/decision_page.py`）:

```diff
@@ -49,6 +49,9 @@
 SCHEMA_VERSION = 1
 CARD = "t_437a9f72"
+# キル成立後の扱い（カード t_f2c6f343）。事前登録 t_0d06b1de の「この系統は全部終了」に対応し、
+# キル発動時は判定資料そのものに「再挑戦しない・実資金/判定の候補から除外」を1行で残す。
+KILL_DECISION_DOC = "docs/S07_ITEM5_KILL_DECISION_20261112.md"
@@ -462,6 +462,10 @@
         "**判定: {verdict}** — {result['reason']}",
         "",
+        # キル確定後は、この判定資料自体に「除外・再挑戦しない」を残す（カード t_f2c6f343）。
+        *([f"- **扱い（KILL 確定）: この系統（深さ2%・遅延30分および深さ・遅延の他の組み合わせ）は"
+           f"再挑戦しない。実資金・判定の候補から除外**（判定文書: {KILL_DECISION_DOC}）",
+           ""] if verdict == "キル発動" else []),
         f"- 判定日（as_of, UTC）: {result['as_of_utc']}／期限: {result['deadline_utc']}"
```

### 5.3 発散候補の実施順序表（`docs/DIVERGENCE_ORDER_20261109.md` §4.2 R3-4）

before（1行）:

```
| **R3-4** | 清算カスケード受け（項目5）の過去データ拡張 | HL / 公開データ取得（発注0・API完備） | 手持ち＋一部追加遡及要（過去清算履歴取得） | 1日 | 2026-11-16 | 2026-11-16 | 得点10点 (経路3/データ2/見積2/非実弾3)。 既存カードなし＝新規（保全 t_c3db60a9 後続）。A番号未対応。唯一の生き残りセル（深さ2%・遅延30分）を過去3ヶ月へ拡張（第1波 11/16実作業） |
```

after（1行・取り消し線で残す）:

```
| ~~**R3-4**~~ | ~~清算カスケード受け（項目5）の過去データ拡張~~ **→ 中止（2026-11-12・項目5キル成立）** | 中止（実資金・判定の候補から除外） | 中止 | 中止 | 中止 | 中止 | 事前登録 `t_0d06b1de` のキル条件成立（`ep-2026-11-10T200500Z` の cluster_pnl_usd **-123.4043 USD** < 閾値 **-85.32 USD**）により「この系統は全部終了（深さ・遅延の他の組み合わせも再挑戦しない）」が発効。過去データ拡張も再挑戦として行わない。判定文書: `docs/S07_ITEM5_KILL_DECISION_20261112.md`（カード t_f2c6f343） |
```

あわせて §4.2 の導入文に1行追記（有効候補22件・他の行の数値と順序は不変）。

`diff`（before を `DIVERGENCE_ORDER_20261109.md.before` として保存し比較）は完了報告に添付した実測ログ `evidence_02_diff.txt` のとおり（該当行 = 1行の置換＋注記1行の追加。他の候補行の変更0行）。

### 5.4 除外後の再走査（生きている候補行 = 0）

除外を入れた後に同じ範囲を洗い直した実測（生ログ `evidence_05_candidate_exclusion.txt`）:

| 対象 | 走査 | 結果 |
|---|---|---|
| C2ゲート資料・ゲート実装 | `grep -rn "清算カスケード\|項目5" docs/G2_GATE_20261102.md docs/G2_PAPER_PHASE_DESIGN_20261102.md scripts/paper_c2/c2_gate.py docs/C2_*.md` | **0行**（言及なし・変更不要） |
| 実弾判断の候補列挙（`docs/GATE_RETIREMENT_20261111.md`） | `grep -n "S-07"` | 1行（§5「S-07（前向き測定）…これらには本裁定を適用していない」＝対象外の明記。本カードの前から候補外） |
| 発散候補の実施順序表（`docs/DIVERGENCE_ORDER_20261109.md`） | `grep -n "項目5" \| grep -v "~~"` | 1行（= §4.2 の**中止を宣言する注記そのもの**。候補行としては **0行**。R3-4 本体は取り消し線で残置） |
| S-07本体の判定資料（`fwd_item5/decision_page.md`・生成物） | `grep -n "扱い（KILL 確定）"` | 1行（「再挑戦しない・実資金/判定の候補から除外」） |

→ 項目5を「生きている候補」として挙げている行は **0行**。

## 6. 記録の扱い = 「記録は継続するが判定に使わない」

**決定**: 日次フックは止めない。キル後の局面も記録し続けるが、**その行は判定・累積評価・実資金判断のいずれにも使わない**（停止ではなく「記録するが判定不使用」を選ぶ）。

- 理由: `kill_ledger.json` / `closed_episodes.jsonl` / `episodes/` はキル判定の証跡そのものであり、止めると証跡が痩せる。記録は単調（KILLED を消さない設計）で、判定側は `decision_frozen.json` で既に凍結済みなので、増えても判定を動かさない。
- 明記先: `data/s07_paper/funding_top20/fwd_item5/README.md` に1行（下表）。
- フックのコードは変更していない（`s07_daily_run.py` → `fwd_item5_pnl.py --daily-hook` の item5 部分は無改変。既存cronの間隔・宛先も不変）。

README 追記（before/after）:

```diff
 判定は `evaluate_kill()` の1箇所だけで、**1局面の `cluster_pnl_usd` < -85.32 で `triggered: true`**。
 再実行時に全履歴から再計算するので、`cumulative.kill` は常に「これまでで最初に閾値を割った局面」を示す。
 
+**キル後のデータは記録するが判定に使わない**（2026-11-12 決定・カード t_f2c6f343: キル成立後も日次フックは局面を記録し続けるが、その行は判定・累積評価・実資金判断のいずれにも使わない。判定文書 `docs/S07_ITEM5_KILL_DECISION_20261112.md`）。
+
 ## 20独立局面トリガ
```

### 6.1 実測（キル後も記録が続くこと）

実測（2026-11-13 10:17 JST = `2026-11-13T01:17:26Z`・読み取りのみ・生ログ `evidence_03_after_daily_cron_20261113.txt`）:

- 次回日次run（`s07-daily-supply-and-run` / cron `074588d200e4` / `20 9 * * *`。当日の書き込みは 10:03:52 JST = `2026-11-13T01:03:52Z`）の後、`episodes/` は **9本 → 10本**。増えたのは `2026-11-12.json`（sha256 `d53d7cfec5dbf7ab8d21c2d20ef7ebe4bd04f80bddd0018fbdaff3aba50aa1bf`・`generated_at_utc=2026-11-13T01:03:52Z`・`day_key=2026-11-12`）。判定時に最後だった `2026-11-11.json` も残っている（同日の書き直しで sha256 は `f269940ca5…` に更新）。
- `closed_episodes.jsonl` は現在 **26行**（`counts.evaluated=26` と一致）。判定時は `counts.evaluated=22` だったので **4局面分**が増えた。末尾は `ep-2026-11-12T131000Z` / `ep-2026-11-12T184500Z`（いずれも `n_filled=0`）。
- 台帳の不変部分は維持: `status=KILLED` / `killed_at_utc=2026-11-11T01:03:17Z` / `trigger.episode_id=ep-2026-11-10T200500Z` / `trigger.day_key=2026-11-10` / `trigger.cluster_pnl_usd=-123.4043` / `trigger.n_filled=1` / `threshold_usd=-85.32` / `state_source=recomputed` / `recompute_mismatch=false`。進んだのは `updated_at_utc`（`2026-11-12T01:03:37Z` → `2026-11-13T01:03:52Z`）だけで、ファイル sha256 は `d8c08bfc…`（判定時）→ `1c329dd6…`（現在）。
- `cumulative.json` の `cascade_sim.sha256` = `expected_sha256` = `7370a7e4…c10df` / `identical_to_measurement=true` は不変（`generated_at_utc=2026-11-13T01:03:52Z`）。
- `fwd_item5/README.md` に「キル後のデータは記録するが判定に使わない」の1行が入っている（sha256 `1c0bbb8a…`）。
- 日次フックのコード（`s07_daily_run.py` / `fwd_item5_pnl.py`）と既存cron は無改変。

→ 記録の扱いは「**継続（判定不使用）**」で確定（実注文0・実弾0）。

補足: `kill_ledger.json` / `cumulative.json` は日次run のたびに書き換わる（追記・単調）。上の sha256 は**判定時スナップショット**のものであり、以降の更新で `status=KILLED` / `killed_at_utc` / `trigger` / `threshold_usd` は変わらない（`fwd_item5_pnl.py:update_kill_ledger` は「読んだ KILLED」と「今回の再計算」の OR を書き、`killed_at_utc` と `trigger` は保存値を維持する）。

## 7. 再発防止（キル成立の1行が流れて終わらないようにする）

新規の監視基盤は作らない。既存の `c2-paper-gap-watch`（cron `e7eba9fb6f21`・`30 9 * * *`・no_agent・宛先 `bot-chat:default`）の1行に、**起票を促す文言**を足した（`scripts/paper_c2/c2_gap_watch.py` の KILLED 分岐のみ。既存スクリプト内の最小変更）。

before（2026-11-12 09:30 の実出力・これが誰にも拾われず流れた1行）:

```
fwd_item5 キル条件成立: status=KILLED（killed_at_utc=2026-11-11T01:03:17Z・閾値 -85.32 USD）
```

after（判定文書が無い状態で実行した実出力）:

```
C2ペーパー監視: fwd_item5 キル条件成立: status=KILLED（killed_at_utc=2026-11-11T01:03:17Z・閾値 -85.32 USD）→ 判定を文書化し、実資金・判定の候補から除外して再挑戦禁止を固定すること（未起票なら /root/.hermes/scripts/kanban-create.sh で起票）
```

判定文書（本ファイル）が存在する間は、同じ KILLED 行を毎日繰り返さない（`facts.fwd_kill_decision_doc` に判定文書のパスを残すだけ）。文書が消えれば元の警報に戻る。単体テスト: `tests/test_c2_gap_watch_fwd_kill.py` の2件。

実測（本番 run・`/root/.hermes/logs/c2_gap_watch.jsonl`）:

| run | `fwd_kill_status` | `problems` | `facts.fwd_kill_decision_doc` |
|---|---|---|---|
| 2026-11-12 09:30 JST（修正前） | KILLED | `["fwd_item5 キル条件成立: status=KILLED（killed_at_utc=2026-11-11T01:03:17Z・閾値 -85.32 USD）"]` | なし（1行だけ流れた） |
| 2026-11-13 09:30 JST（修正後・本番） | KILLED | `[]` | `/root/current-work/yowayowa-investor/docs/S07_ITEM5_KILL_DECISION_20261112.md` |

cron `e7eba9fb6f21` の `expr`（`30 9 * * *`）と宛先（`bot-chat:default`）は変わっていない（§8.1）。

## 8. 実注文0・実弾0・cron 不変の実測

実測（2026-11-13 10:17 JST・出所は `/root/.hermes/cron/jobs.json` / `git status --porcelain` / ソース走査。生ログ `evidence_03_after_daily_cron_20261113.txt`）:

### 8.1 既存cron の `expr` と宛先（判定時と同一）

| id | name | expr | deliver | script | enabled | 判定時（11-12 20:11）との比較 |
|---|---|---|---|---|---|---|
| `9f57d9154cfb` | c2-paper-daily | `0 9 * * *` | `local` | `c2_paper_daily.sh` | true | 同一 |
| `e7eba9fb6f21` | c2-paper-gap-watch | `30 9 * * *` | `bot-chat:default` | `c2_paper_gap_watch.sh` | true | 同一 |
| `074588d200e4` | s07-daily-supply-and-run | `20 9 * * *` | `local` | `s07_daily.sh` | true | 同一 |

判定時の値は `evidence_00_before.txt` の同名3件（`"expr": "0 9 * * *"` / `"30 9 * * *"` / `"20 9 * * *"`、`deliver` は `local` / `bot-chat:default` / `local`）と一致し、本カードの前後で変わっていない。`jobs.json` 全体の sha256 が変わるのは `last_run_at` / `next_run_at` が進むためで、`expr`・`deliver`・`script`・`enabled` は不変。

### 8.2 実注文0・実弾0

- 取引クライアント呼び出しの走査（`/exchange` / `order_send` / `place_order` / `privateKey` / `signature` / `import hl_client` / `create_order`）: `scripts/s07_paper/fwd_item5_pnl.py` **0行** / `scripts/s07_paper/decision_page.py` **0行** / `scripts/s07_paper/s07_daily_run.py` **1行**（中身はコメント `* 実弾0・注文0: 呼ぶのはHL公開API（読み取り）だけで、署名も /exchange も使わない` のみ。判定時と同じ1行）。
- 本カードで触ったのは docs 2本（本判定文書・`DIVERGENCE_ORDER_20261109.md`）・`scripts/s07_paper/decision_page.py`（表示1行の追加）・`scripts/paper_c2/c2_gap_watch.py`（KILLED 分岐）・テスト1本・`fwd_item5/README.md`（1行）だけ。注文経路（`src/yowayowa/hl_exec/**`）と `.env` は mtime がカード開始（2026-11-12 20:03 JST）より前のままで、本カードの作業では触っていない（`client.py` mtime `2026-11-10 10:14` / `.env` mtime `2026-10-22 23:13`）。
- 日次run が書いたのは `data/s07_paper/funding_top20/fwd_item5/` 配下の記録（`episodes/` `closed_episodes.jsonl` `kill_ledger.json` `cumulative.json` `decision_page.*`）だけ。

## 9. 変更したファイルと sha256

本カードで変更・追加したファイル（before = 判定時 2026-11-12 20:11 JST のスナップショット `evidence_00_before.txt`、after = 2026-11-13 10:17 JST の実測）:

| ファイル | before sha256 | after sha256 | 変更内容 |
|---|---|---|---|
| `docs/S07_ITEM5_KILL_DECISION_20261112.md` | （新規） | 同内容のサイドカー `docs/S07_ITEM5_KILL_DECISION_20261112.sha256` に記載 | 本判定文書（結論 KILL・除外・再挑戦禁止） |
| `docs/S07_ITEM5_KILL_DECISION_20261112.sha256` | （新規） | 本文書の sha256 を1行 | 改竄検知用サイドカー |
| `scripts/s07_paper/decision_page.py` | `a398071aa4b20c2c2517839bb274a5b9a1d3ccd29041722244bf5f37cfd7e828` | `f999088643ee40e4110402e6f7fa24b3a20064d12b820bd4d802dd8a5a77011e` | キル発動時に「再挑戦しない・実資金/判定の候補から除外」の1行を出す（+7行） |
| `scripts/paper_c2/c2_gap_watch.py` | `95bd1b36c6af2b15752677cff09c37c2198fa88da8680db17e2ce52a76888b90` | `c80d893c2e85b646bfb6f3ca3c99d0c0265a4b142568c9e3b3fc85d7ca86885b` | KILLED 分岐に起票を促す文言（本カード分・行 629–657）。**同ファイルは別カード t_8910825b（daily 実行証跡の偽警報修正）も同時期に触っており、after の sha は両方の変更を含む**（本カードの担当分は KILLED 分岐のみ・`git diff` の当該ハンクに閉じる） |
| `tests/test_c2_gap_watch_fwd_kill.py` | （新規） | `16da5e745031d6b5151b4f086df2fbebf2976de9aa78212da2fdfe92212977f7` | KILLED 出力の2件（文書なし＝起票促し／文書あり＝繰り返さない） |
| `data/s07_paper/funding_top20/fwd_item5/README.md` | （1行追加前） | `1c0bbb8a8feb7d6945b3c285f0149f06c597dc089410bce69f80d318178df642` | 「キル後のデータは記録するが判定に使わない」1行 |
| `docs/DIVERGENCE_ORDER_20261109.md` | `2b11ab01ddf4f28e4d9a5292c332c868052b593202e9e4cc7755869e92f2503d`（workspace の `.before` コピー） | `47e5052edd16cb4ebd81c68cb2cd99c6bcecf5baf1daf1818bd59a8130c88299` | §4.2 R3-4 を中止（取り消し線で行は残す）＋注記1行 |

日次run が書き換えた記録（追記・単調。上の台帳 sha は判定時スナップショットのもの）:

| ファイル | 判定時 sha256 | 2026-11-13 10:17 の sha256 |
|---|---|---|
| `kill_ledger.json` | `d8c08bfc66c8992cb977c5c5b85ff6c86ada6477585814581e43f8037c5db58a` | `1c329dd660c613df66ee936dc74296f6bb7517a1476d140e1e992ab56842a3c9` |
| `cumulative.json` | `753572abe20df829cd5dfd70e4e7de612ee5762e780b09c5e45b0660f18353e6` | `9a31fdb7dc4ffb9f93b940f9bed8be8090bc320470a91a798ac302ec5d5da46e` |
| `closed_episodes.jsonl` | `e67bd0097a8ad7b507bba8f8eccca4c1ed44a0348f6ff95ea441b6c5f3f125b7` | `0f765f6926da5a497ba13f2972d6bfdf48e6bed332c44e7021b9d940b661e8a8` |
| `decision_page.md` | `f8dd6846dce03861b93550d21909243023fed795d32bac267c54c4bc0c50a8c9` | `a21223bd2a262a4d3e95262719d94056852ce7deefeb5af376b6ae5f0c3423b9`（除外行が入った） |
| `episodes/` | 9本 | 10本（`2026-11-12.json` 追加） |
| `decision_frozen.json` | `c2c54f5c7a014f80f4df22da9c5e99b53e924fd42691d8ad02e4b07860096b9c` | 同値（不変） |

## 10. 検証コマンド（読み取りのみ・再実行可能）

```bash
cd /root/current-work/yowayowa-investor

# 1) 台帳と判定の一致（status/killed_at/trigger/threshold/counts）
.venv/bin/python scripts/s07_paper/decision_page.py --data-dir data/s07_paper --no-write | head -20

# 2) 凍結判定の sha256（decision_frozen.json と サイドカーが一致すること）
sha256sum data/s07_paper/funding_top20/fwd_item5/decision_frozen.json
cat data/s07_paper/funding_top20/fwd_item5/decision_frozen.sha256

# 3) 測定量のバイト同一（7370a7e4… と identical_to_measurement=true）
sha256sum scripts/s07_paper/cascade_sim.py
jq '.cascade_sim' data/s07_paper/funding_top20/fwd_item5/cumulative.json

# 4) キル成立の1行（判定文書ありなら KILLED 行を出さない／無ければ起票を促す）
PYTHONPATH="$PWD:$PWD/src" .venv/bin/python -m scripts.paper_c2.c2_gap_watch --log /dev/null

# 5) 単体テスト（除外・再挑戦禁止の行／gap watch の KILLED 出力）
.venv/bin/python -m pytest tests/test_c2_gap_watch_fwd_kill.py scripts/s07_paper/test_decision_page.py -q
```

## 11. 検収

`kanban_request_review` で別系統（reviewer: `audit-glm-heavy`）へ渡す。自己完了はしない。
