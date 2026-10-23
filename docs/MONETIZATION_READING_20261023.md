# yowayowa 収益検証の再開（2026-10-23 / t_dddae39e）

この文書は、収益検証を妨げていた未接続工程を1件だけ閉じ、その前後を実データで
測った記録である。損益はデモ（ペーパー）の仮想元本 1.0 を 100 万円と読み替えた
表示であり、実際の入金・発注・約定は一切ない。

## 1. 選んだ工程 — S-02（帳簿C）の比較対象が存在しなかった

- S-02 は「EW（等金額）に対して DD を抑える」という前提で採用された
  （`prereg.jsonl` の hypothesis: `S-02 ... controls drawdown vs EW`）。
- ところが demo-loop の `BOOKS_ENABLED` は `(A, CRYPTO_1N, C)` のままで、
  等金額ベンチマーク `EQUITY_EW` は **prereg の spec ハッシュ・重み関数・
  equity 分岐まで実装済みなのに、稼働リストに入っていなかった**。
- そのため帳簿Cは「比較対象差」も「DD 対ベンチ」も永久に null で、
  12週/26週ゲート（DD <= ベンチ×0.6）を読む土台が無かった。
  これが収益検証を最も妨げる配線不足だった（t_44edd7a0 §2.2「比較対象差:
  利用不可」の直接原因）。
- 直したのは次の4点だけ。戦略パラメータ・コスト・閾値・凍結値は触っていない。
  1. `BOOKS_ENABLED` に `EQUITY_EW` を追加（1/N を一度買って保有、CRYPTO_1N と同規則）。
  2. `EQUITY_EW` を帳簿Cと同じ週次（月曜）ゲートに載せる（別戦略にならないように）。
  3. `EQUITY_EW` の prereg 行の著者は、実際に登録された行のとおり
     `worker-go:t_dddae39e`（登録済み行は書き換えない。§8-4 に同じ説明を残す）。
  4. `weekly_summary` の各戦略帳簿に自分の比較行
     （`benchmark_book` / `cum_pnl` / `max_drawdown` / `benchmark_cum_pnl` /
     `benchmark_max_drawdown` / `vs_benchmark_pp_cum` / `dd_vs_benchmark_ratio`）を追加。
     欠測は 0 で埋めず null のまま返す。比較は**共有の起点日**（`comparison_start`）
     から測り、ベンチマークが未約定の間は数値を出さず `comparison_reason` に理由を
     入れる（§8-1/§8-2 のレビュー対応で確定した定義）。

## 2. 入力とコードの固定

| 対象 | 値 |
|---|---|
| journal（実行前） | 247 行 / sha256 `904259a2842354ca308988cd947c39dcd722c0547d0a7e8bc3805cfc496ece24` |
| journal（実行後） | 261 行 / sha256 `ed12c32a717d730a6fccc983620cd6c70a4423216499585ad7605b57179c2428` |
| anchors（実行後） | sha256 `3f7e09e6ff984f35ff04184f6479f38008eb73b2045d2d98f3b7ff05ac872ef3` |
| prereg（実行後） | sha256 `d18a1dd0be26ec7a86f375d21e396f8c3d0d3183e54e3a24ba6e7b0fc4e57083` |
| 実行コード | `src/yowayowa/paperloop.py` sha256 `5572700777926e48e155ac2f370ca93c427815ac44361e9a16620ae5f728129f`（round 1。round 2 は `7f5e6589…`、§8） |
| 試験コード | `tests/test_paperloop.py` sha256 `b29cd35aaeed1f8dea055af4995afe7f4e461b55275d791f4d2df39744ee45fe`（round 1。round 2 は `82a36649…`） |
| リポジトリ | `agent/commercial-foundation` 変更前 HEAD `8450e80e32627852eac2dab23b81bdb9fc868745`、round 1 のコミット `d94514d`・`13cbc6c`、round 2 のコミットは本 doc §8 を含む「shared baseline」のコミット |
| 生データ（恒久） | `docs/evidence/20261023/{monetization_reading,paperloop_verify,old_vs_new_weekly_summary,comparison_line_review_r2}.json` |
| 実走時刻 | 2026-10-23 09:20 JST（cron `demo-loop-decide`、`run` 行 seq 261 `ok:true rows_written:4`） |
| 評価バー | 帳簿A/CRYPTO_1N: 2026-10-22、帳簿C/EQUITY_EW: 2026-10-21 |

## 3. 実行で確認できたこと（コード変更が実運用に載った証拠）

09:20 JST の日次実行が新コード（`code_rev 8450e80e3262+dirty`）で走り、次を書いた。

- seq 251–256: 帳簿A/CRYPTO_1N の halt_check と decision（従来どおり）。
- seq 257–259: 帳簿A/CRYPTO_1N/C の mtm（従来どおり）。
- **seq 260: 帳簿 `EQUITY_EW` の最初の mtm 行**（equity 1.0 / cash 1.0 / price_bar_date 2026-10-21）。
- **`prereg.jsonl` に `EQUITY_EW` 行**（spec_hash `8fe873e2…4815`、`registered_at 2026-10-23T00:21:06Z`、
  `registered_by worker-go:t_dddae39e`、prev_hash は帳簿Cの行に連結）。
- 実行後の `paperloop_verify`（261 行の状態）: exit 0、chain_ok / anchors_ok true、
  L1–L14 failure 0、R1 32/32、R2除外0、R3改訂0。

## 4. 費用後成績（最終 mtm 2026-10-23 00:21 UTC 時点）

元本 = 仮想 1.0（100 万円換算）。コストは凍結 15bps/片道で、各 fill の
`cost_assumed` として実際に equity から引かれている。

**S-01 帳簿A（暗号トレンド）— 2026-10-10 〜 2026-10-23、13日、decision 28 / fill 26（拒否2）**
- 費用後損益: `-3.345%` / `-33,447 円`（equity 0.966553）
- 想定コスト累計: `1,847 円`
- 最大DD / 現在DD: `-4.127%` / `-4.127%`（高値から下落中）
- 比較（CRYPTO_1N 等金額バイ＆ホールド）: `-3.544%` / `-35,442 円`、最大DD `-4.322%`
- 比較対象差: `+0.200 pp`（`+1,995 円` 相当）、DD比 `0.955`
- 解釈: ベンチより浅いが、両者ともコスト後にマイナス。10/22 の暗号下落で
  前日（10/22 時点 -0.081%）から大きく悪化した。収益の証拠にはならない。

**S-02 帳簿C（米株ボラターゲット）— 2026-10-19 〜 2026-10-23、decision 4 / fill 4（拒否0）**
- 費用後損益: `+0.634%` / `+6,344 円`（equity 1.006344）
- 想定コスト: `1,500 円`（4銘柄×0.25、turnover 1.0）
- 最大DD / 現在DD: `-0.348%` / `-0.348%`
- 比較対象差: **未成立**（§6-1・§8-1。`EQUITY_EW` は 10/23 から稼働、初回判断 2026-10-26・初回約定 2026-10-27。未約定の間は比較行を null で返す）
- 注意: 10/22 時点では評価バーが 10/20 で `+0.986%` と出ていた。10/21 の
  バーが入った今回の再評価では `+0.634%`。バー遅延で数字が動くので、
  確定は週次サマリーで行う。

**EQUITY_EW（比較用）— 10/23 稼働開始、decision 0 / fill 0、equity 1.0**

## 5. 回帰と既存成果の不変（同一ジャーナルのコピーで新旧比較）

- 旧コード（HEAD の `paperloop.py`）と新コードを、同じ `data` コピー・同じ
  `week_end=2026-10-19` で実行して JSON を比較した。
  - 帳簿A・CRYPTO_1N・C の既存フィールドは **完全一致**。差分は `EQUITY_EW` の
    追加と、上記の比較行の追加だけ。
  - `vs_benchmark_pp_cum`（A 対 CRYPTO_1N）は `0.17086960258662076` で新旧同値。
- テスト（VPS・1本ずつ実行。重い suite 一括実行はしない）:
  - `tests/test_paperloop_v12.py` 19本 全pass（最長 `test_eval_Q1_passes_with_enough_observations` 90秒）
  - `tests/test_paperloop.py` 22本 全pass（個別実測: `..._never_falls_back_on_failed_run_days` 123秒、
    `..._never_falls_back_on_crashed_run_days` 83秒、他は 5–8秒。
    一括実行は 400 秒を超えて未完走になるため、個別実行の終了コードで判定した）
  - round 2 で比較行の試験を2本追加し、現在 24本（§8-3）。
  - 追加/更新した試験: 決定件数を `BOOKS_ENABLED` から数える形に変更、
    `EQUITY_EW` が月曜ゲートに乗ること・1/4 ずつ買うことの検証を追加。

## 6. 未成立条件・限界（正直に残す）

1. **帳簿Cの比較対象差はまだ存在しない。** `EQUITY_EW` は 10/23 09:20 JST から
   稼働し、月曜ゲートなので最初の判断は 2026-10-26、約定は 2026-10-27。
   比較は共有の起点日から測る仕様にした（§8-2）。起点は「`EQUITY_EW` の初回
   約定日以降で、C と `EQUITY_EW` が同じ日に mtm を持つ最初の日」で、両帳簿の
   その日の評価額を基準にする。したがって C の 10/21〜10/26 の損益は比較に
   入らない。起点が成立するまでは比較の数値フィールドはすべて null で
   `comparison_reason=benchmark_has_no_fill` になる（実測: 2026-10-23 時点）。
2. **equity の評価バーが1営業日古いことがある。** 10/22 00:21 UTC の実行では
   最新 SPY バーが 2026-10-20 で、帳簿Cの評価が1日遅れた。原因は既存の
   `stock-fetch --days 5` 経路の取得遅延で、本カードでは変更していない。
3. **デモである。** 実口座・本番発注・送金は無い。15bps は想定コストで、
   実際のスリッページ・手数料ではない。したがって「稼いだ」とは言えない。
4. **4週判定はまだ発火しない。** 帳簿Aは 2026-11-07、帳簿Cは 2026-11-16。
   12週/26週の判定は未接続（`MILESTONE_WEEKS_CONNECTED=(4,)`）。本カードでは広げていない。
5. **見つけたが直していない不具合（別カード）**: `_cost_accumulators` の
   `equity_gross` は市場損益を掛けずにコスト率だけを 1.0 から複利するため、
   「コストを払わなかった場合の equity」になっていない。その結果 Q3 の
   `cost_drag_pp` は相場の下落をコストとして表示する（例: 帳簿A）。
   本カードの費用後損益（net equity）は各 fill の実コスト控除で正しいが、
   gross 側の数値は読んではいけない。修正は `t_a2d67004`（worker-flash）へ渡した。
6. **米株の評価バー遅れ**（§6-2）は `t_bcc12803`（worker-agy）へ渡した。

## 7. 次にやること（本カード外）

- 2026-10-26 09:20 JST の日次実行後、`EQUITY_EW` の decision 4件と 10/27 の
  fill 4件を実測確認する。
- equity バーの1営業日遅れ（§6-2）を、既存 `stock-fetch` の取得時刻/期間の
  見直しとして別カードで扱う（データ源追加ではなく既存経路の調整）。
- 12週/26週ゲートの接続は、マイルストーン日が来る前に別カードで扱う。

## 8. レビュー第2ラウンド対応（2026-10-23 / audit-cld 差し戻し）

監査の指摘4点に対し、比較行の意味を先に固定してから最小修正した。戦略パラメータ・
コスト・閾値・凍結値、および既存のジャーナル行は触っていない。

### 8-1 ベンチマーク未約定のときは数値を出さない（必須）

- 修正前: `weekly_summary` は `EQUITY_EW` の mtm 行が1件あれば比較を計算していた。
  2026-10-23 の実データでは C に `vs_benchmark_pp_cum=0.6344pp`、
  `benchmark_cum_pnl=0.0`、`benchmark_max_drawdown=0.0` が入り、**現金のままの
  ベンチマークとの差**を収益として書く状態だった（weekly_summary は追記専用なので
  10/26 の週次でそのまま残る所だった）。
- 修正後: ベンチマークに fill が1件も無い間は比較の数値フィールドをすべて None にし、
  `comparison_reason="benchmark_has_no_fill"` を付ける。実測（同一ジャーナル・
  2026-10-23 の状態）: C の比較行は全 None＋理由。A は CRYPTO_1N が 10/10 に約定済み
  なので従来どおり数値が出る。
- evidence JSON も同じ扱いに直した:
  `docs/evidence/20261023/monetization_reading.json` の C ブロックと、
  `old_vs_new_weekly_summary.json` の `new_C_block_*`。round 1 の値は
  `comparison_correction.removed_round1_values` / `_round1_new_C_block_new_fields` に
  残した（消していない）。

### 8-2 比較の起点を共有日にする（必須）

- 起点 = 「ベンチマークの初回約定日以降で、戦略帳簿とベンチマークが同じ `mtm_date` を
  持つ最初の日」。その日の評価額を両帳簿の基準にし、`comparison_start` に記録する。
  `cum_pnl` / `benchmark_cum_pnl` / `vs_benchmark_pp_cum` はこの起点からのリターン、
  `max_drawdown` / `benchmark_max_drawdown` も起点以降の mtm 行だけで測る。
- 理由: C は 10/21 に建玉済み、`EQUITY_EW` の約定は 10/27 以降。inception equity の
  差を取ると C の 10/21〜10/26 の損益だけが一方的に乗り、12週/26週ゲートまで残る。
- 実測: A は起点 `2026-10-10`（両帳簿 equity 1.0）なので round 1 と全フィールド同値
  （`vs_benchmark_pp_cum=0.19951597133428534` / DD比 `0.9547658001204044`）。C は共有
  起点が未成立なので全 None。
- docs と実装を一致させた: `docs/DEMO_LOOP_V1_OPS.md` §2、本 doc §1-4 / §6-1。
- DD 比だけが算出できないときは `dd_ratio_reason`（`book_drawdown_missing` /
  `benchmark_drawdown_missing` / `benchmark_drawdown_not_negative`）を付ける。

### 8-3 比較フィールドの試験を追加（必須）

- `tests/test_paperloop.py::test_comparison_line_is_null_while_benchmark_has_no_fill`
  — ベンチマーク未約定なら比較の6フィールドがすべて None＋理由。CRYPTO_1N 側も
  未約定なら同じ。
- `tests/test_paperloop.py::test_comparison_line_starts_at_the_shared_baseline_after_benchmark_fill`
  — 10/23 に C が +10%（ベンチ未約定）、10/26 にベンチ約定・両帳簿 mtm、10/27 に
  C -5% / ベンチ -1% の合成ジャーナルで、起点 `2026-10-26`・`cum_pnl=-0.05`・
  `benchmark_cum_pnl=-0.01`・`vs_benchmark_pp_cum=-4.0pp`・DD比 `5.0` を確認した。
  inception 差なら +4.5% になるので、起点が効いていることを判別できる。
- 実行結果: `test_paperloop.py` 24本すべて rc=0（1本ずつ実行）、
  `test_paperloop_v12.py` 19本 rc=0（`grep -c '^def test_'` = 24 と一致）。

### 8-4 prereg 著者の記述を実データに合わせた（軽微）

- 実際に登録された `EQUITY_EW` の行は `registered_by = worker-go:t_dddae39e`
  （`registered_at 2026-10-23T00:21:06Z`、`row_hash 167df9ed…`）。コードも同じ。
  round 1 の docs と commit message が「t_feb3aa55 に修正」と書いていたのを、実データ
  どおりに直した。登録済み行は書き換えていない（prereg sha256 は `d18a1dd0…` のまま）。
  `docs/PRIVATE_OPERATOR_ROADMAP.md` の同じ記述も直した。

### 8-5 参考（差し戻し理由ではない）: rebuild_state は week_end で区切らない

- `rebuild_state` はジャーナル全体を畳むため、過去の `week_end` で再計算しても最新の
  状態が返る（既存の A の比較も同じ性質）。新しい比較行もこの性質を引き継ぐ。
  仕様変更はせず、ここに注記するに留めた（必要になるのは「過去週の断面」を要求された
  時点）。
- 未成立（実データ）: 10/26 の decision と 10/27 の fill が入るまで、C の比較行は実
  データでは出ない。起点と値は合成ジャーナルの試験でのみ検証した（§8-3）。
- 証拠: `docs/evidence/20261023/comparison_line_review_r2.json`
  （journal sha256 不変、旧コード `5572700…`／新コード `7f5e6589…`、A の比較行一致、
  C の round 1 値と修正後の null）。
