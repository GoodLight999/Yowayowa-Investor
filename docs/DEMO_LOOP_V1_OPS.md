# デモ運用ループ v1.1 運用手順（ゼロ発注・ペーパー）

作成: worker-flash / 2026-10-10、v1.1更新 2026-10-11
対象タスク: t_d9a25dd2（実装 worker-flash）
仕様: SHORT_TERM_STRATEGY_DESIGN.md (t_421158aa) §2-2・§2-1・§3 M1–M9、
実装仕様 DEMO_LOOP_V1_IMPLEMENTATION_SPEC.md (t_fcd37354) D1–D12、
反証レビュー (t_c998951d) の F2/F3/F7/F8 採用。

v1.0 からの変更点（2026-10-11）:
- cron 3本へ集約（decide / fill / weekly）。旧 mark 単独ジョブは削除
  （mark は decide 冒頭で前日分を処理するため）。
- 旧 v1.0 wrapper（decide.sh/fill.sh/mark.sh/report.sh/demo_loop.sh）は
  `ops/demo-loop/legacy_v1/` へ退避。現役は daily.sh / fill_v2.sh / weekly.sh の3本。
- daily.sh の `report_if_monday` 呼び出しを削除（CLIに存在しないサブコマンドだった）。
  週次レポートは weekly.sh（月曜）が担当。

## 1. 構成（cron 3本・VPS で軽い処理のみ）

| phase | cron script | JST時刻 | 対応UTC | 内容 |
|---|---|---|---|---|
| daily | `demo_loop_decide.sh` → `ops/demo-loop/daily.sh` | 09:20 | 00:20 | crypto-fetch → mark(前日分) → decide(+mtm) → anchor |
| fill | `demo_loop_fill.sh` → `ops/demo-loop/fill_v2.sh` | 11:05 | 02:05 | 1h tape 取得 → resolve_fills(guard評価) → mtm |
| weekly | `demo_loop_report.sh` → `ops/demo-loop/weekly.sh` | 月 09:50 | 月 00:50 | paperloop_verify → 週次サマリー1行 |

- すべて no_agent cron（LLMを使わない）。flock による直列化つき
  （`data/demo-loop/.lock`、ロック競合時は exit 0）。
- Hermes cron の schedule はホストローカル（JST）で解釈されるため、登録は
  `20 9 * * *` / `5 11 * * *` / `50 9 * * 1` の JST 表記
  （= 仕様 §9 の UTC 00:20 / 02:05 / 月 00:50 と同じ瞬間）。

## 2. データ source と追記手順

- crypto: `data/crypto-ohlcv/{BTC,ETH}/ohlcv.jsonl`（Binance rows）。daily 冒頭で
  `yowayowa crypto-fetch BTC ETH --days 5` を冪等追記（D12）。確定日足が26時間より
  古いと HALT(stale_data)+skipped 行のみを書いて止まる（fail-closed・後追い取得なし）。
- equity (book C): `data/stock-ohlcv/{SPY,IWM,JNJ,PG}/ohlcv.jsonl`。v1.3 で月曜
  リバランスとして稼働（2026-10-19 投入）。**v1.4（t_dddae39e, 2026-10-23）で
  等金額ベンチマーク `EQUITY_EW` を同じ月曜ゲートで稼働開始**。それ以前は
  帳簿Cの比較対象が存在せず、比較対象差・DD対ベンチが null のままだった。
  `EQUITY_EW` は 1/N を一度買って保有し、以後は取引しない。
- 週次サマリーの各戦略帳簿には、自分の比較行（`benchmark_book` / `cum_pnl` /
  `max_drawdown` / `benchmark_cum_pnl` / `benchmark_max_drawdown` /
  `vs_benchmark_pp_cum` / `dd_vs_benchmark_ratio`）が付く。欠測は null。
  比較は**共有の起点日**（`comparison_start`）から測る。起点は「ベンチマークの
  初回約定日以降で、両方の帳簿が同じ日に mtm を持つ最初の日」であり、その日の
  評価額を両帳簿の基準にする（片方だけが現金の期間の損益を一方的に計上しない）。
  ベンチマークに fill が1件も無い間、および共有の起点がまだ無い間は、比較の
  数値フィールドはすべて null で、`comparison_reason`
  （`benchmark_has_no_fill` / `no_shared_mark_since_benchmark_first_fill` /
  `baseline_unusable`）に理由が入る。0 で埋めない。DD 比だけが算出できない
  ときは `dd_ratio_reason`（`benchmark_drawdown_missing` など）が付く。
  `max_drawdown` / `benchmark_max_drawdown` は、起点日以降の mtm 行の **equity**
  から、**起点日の equity を初期のピークとして**再計算する（t_dddae39e round 3）。
  ジャーナルの `drawdown` 列は inception 起点の高値からの値なので比較には使わない
  （起点日より前の高値が、まだ何も動いていない起点日の DD として混ざるため）。
  `drawdown` 列自体と -20% 警告の判定は従来どおり。
- 週次サマリーは追記専用（`weekly_summary.jsonl`）。書き込んだ行は消さないため、
  比較行の意味を変えたときは本節のように定義を先に固定する。
- **`week_return` は「その週の変化率」（v1.6, C-02）**: 各行の `week_return` は
  `week_end-6日..week_end` の7日窓の騰落率で、基準は**窓より前の最後の mark**、
  終点は**窓内の最後の mark**（`week_end` より後の mark は使わない）。したがって
  前回レポートの終値から今回の終値までの変化が載る。同日の `week_return_base`
  （基準 mark の日付。窓より前に mark が無い帳簿は `inception` = 起点1.0）、
  `week_return_as_of`（終点に使った mark の日付）、`week_return_reason`
  （算出できない理由。0 で埋めない）で窓を検証できる。
  **v1.2〜v1.5 の行の `week_return` は inception からの累積**であり、同じ列名で
  意味が違う。修正後の行には累積が `cum_return` として併記される（旧行には無い）。
  行の `schema` は `demo-loop-summary-v1.9`（v1.6〜v1.8 は C-02/C-03/C-05 修正時の作業ツリー
  版で、実ファイルには 1 行も書かれていない。実在する旧行は `...-v1.2` と `schema` 無し）。
  実測（実ジャーナル, week_end=2026-10-26）: A は旧 -3.3447% → 新 -0.8421%
  （差 2.5026pp）、CRYPTO_1N は旧 -3.5442% → 新 -0.8651%。
- **週次行の equity 系は `week_end` で閉じる（v1.8, C-05）**: `cum_return` /
  `drawdown_now` と帳簿別の比較行は「`week_end` 以前の最後の mark」で評価する。
  以前は生成時点の最新 mark（`rebuild_state`）を使っていたため、`week_end` より後に
  書かれた mark が古いラベルの行に載った（実ファイルには `week_end=2026-10-26` で
  値は 10-23 までの行が 3 本ある）。追記専用なので後から直せない。各値には
  `cum_return_as_of` / `drawdown_as_of` / `comparison_as_of` / `benchmark_as_of`
  で使った mark の日付が付き、該当 mark が無ければ None と理由
  （`cum_return_reason` / `drawdown_reason` / `comparison_reason`）になる。0 で埋めない。
  実測（実ジャーナル, week_end=2026-10-19 を今日のジャーナルで再生成）: A の
  `cum_return` は旧 -3.3447%（10-23 の mark）→ 新 -2.5238%（10-19 の mark）、
  `drawdown_now` は旧 -4.1269% → 新 -3.3127%。`EQUITY_EW` は 10-19 に mark が
  無いため 0.0 → None + `no_mark_at_or_before_week_end`。
- **`switches` は銘柄別（v1.8, C-06）**: regime は銘柄ごとの状態なので、各 decision
  行の `prev_regime_state` と比べて RISK_ON<->RISK_OFF の反転を数える
  （`switches_by_asset`。合計は `switches` と一致）。以前は帳簿で1本のカーソルを
  持ち、別銘柄の regime と比較していた。`prev_regime_state` を持たない旧行だけは
  同一（book, asset）の直前 decision と比較する。HALT は数えずカーソルも動かさない。
  実測（実ジャーナル）: `week_end=2026-10-19` の Book C は実ファイルの行が
  `switches=2`（BTC/SPY 系の銘柄またぎ）→ 新 0（全銘柄 0、各銘柄自身は反転なし）。
- **`vs_benchmark_pp_cum` は1定義（v1.8, C-07）**: トップレベルは
  `books[A].vs_benchmark_pp_cum` と同じ「共有基準日」の定義になり、同値（None も同じ）。
  旧来の基準日なしの差 `(A.equity - CRYPTO_1N.equity) * 100` は
  `vs_benchmark_pp_cum_legacy_nobaseline` に別名で残す（同じ as-of で計算）。
  実測（実ジャーナル）: 両週とも A の `comparison_start` が A の起点と一致していた
  ため 0.1786pp / 0.1995pp で一致（定義が割れるのは基準日と起点がずれたとき）。
- **比較の終点が2日になるなら出さない（v1.9, t_94687d03）**: `week_end` 以前の最後の
  mark は帳簿ごとに違う日になり得る。片方が 10-24 で止まり、ベンチマークが 10-21 で
  止まっていると、「A の3日分の騰落」対「ベンチマークの0日分」を引き算して
  `vs_benchmark_pp_cum` に値が出ていた（合成ジャーナルで +10.0pp）。v1.9 は
  `comparison_as_of` と `benchmark_as_of` が違う日なら数値側をすべて null にし、
  `comparison_reason` に `endpoint_mismatch` を付ける。**両方の as-of と
  `comparison_start` は残す**（どの mark があったかは行から読める）。
  代替案（両方を「共有する最後の mark」まで切り詰める）は採らない: 同じ行の
  `cum_return` / `drawdown_now` は `week_end` のままなので、1帳簿について
  2つの期間が同じ行に並ぶことになる。トップレベル `vs_benchmark_pp_cum` も同じく
  null（値が無いとき `vs_benchmark_pp_cum_as_of` も null）。
  `vs_benchmark_pp_cum_legacy_nobaseline` も同じ規則で、日付がずれたら null +
  `vs_benchmark_pp_cum_legacy_nobaseline_reason` に `endpoint_mismatch`
  （mark 自体が無い場合は `no_mark_at_or_before_week_end`）。
  `as_of` を省いた `_comparison_line` の呼び出しは従来どおり（既存呼び出しの意味を
  変えない）。v1.9 より前の行は、この不一致を理由に止めていない。
- **マイルストーン判定は3週すべて配線・複数週を1行に（summary-v1.10, t_0be71b95）**:
  `MILESTONE_WEEKS_CONNECTED=(4, 12, 26)`。締切（開始+84日/+182日）を跨いだ最初の
  週次runで凍結ゲート `MILESTONE_RULES[weeks]` を機械判定し、週次行の
  `milestone[book]["weeks"]` に週数をキーとして載せる（同一runで複数週の締切を跨いでも
  両方残る。旧実装の単一スロットは後勝ちで片方を消していた）。ディスク上の週キーは
  JSON文字列（"4"/"12"/"26"）。dedupe読み取りは v1.10 形状と t_d33aef07 の
  単一判定形状の両方を受ける。**12/26週は事実が欠けた締切では判定を書かない**
  （ INSUFFICIENT を永久固定しない）: 事実欠損（mark無し・ベンチDD無し・shortfall
  サンプルn<2・**未凍結の必須ゲート**）は `None` を返して次の週次runで同じ凍結窓を
  再判定する（設計 §4-2 の「保留」）。**判定の記録には凍結ゲート全キーが criteria に
  存在し全て確定していることを要求する**（未評価の必須ゲートを外して残りだけで
  PASS を書かない。監査 AS-M126-01 の是正）。12週DD比はベンチマークDDが10%未満の
  間は判定しない（§4-2a の括弧書き）。
  12週switchesゲートは**資産別**に判定する（設計 §2-3「帳簿Aの BTC なら12週で期待値は
  約2回、警告は5回以上」）: 各資産の反転数をその資産の凍結年率（S-01 バックテスト
  BTC 8.8/年・ETH 6.7/年、`MILESTONE_SWITCH_EXPECTED_ANNUAL`。A_HALFは同一シグナルなので
  同じレート）×窓日数/365×2.0倍と比較し、criterion の `per_asset` に資産別の
  観測/期待/上限/判定を載せて集約する（合算期待値で他資産の余裕を流用しない。
  監査 AS-M126-04 の是正）。
  Book C（S-02）のswitch期待値年率は**凍結・配線済み**（2026-10-28・役員決定 t_be52f5bc
  決定A、採番 t_b63497a9、配線 t_5a2489f5）: SPY=5.6 / IWM=7.7 / JNJ=10.5 / PG=14.6
  （`results.<SYMBOL>.primary.rate_1dp` 逐語。実装定義ミラー・窓2021-01-04..2026-09-28・
  生価格basis — ランタイムのデータbasisがTR調整に変わった場合（AS-TR-01/02 の是正時）は
  再採番）。C の12週判定は他帳簿と同じく実判定として記録される。レートを持たない
  帳簿・資産の必須ゲートは未確定のまま判定ごと保留する規約が存続（観測値だけの記録は
  しない。監査 AS-M126-01 の是正）。出所の全文は `MILESTONE_SWITCH_EXPECTED_ANNUAL` の
  コメントと docs/PAPERLOOP_MILESTONE_12_26_WIRING_20261027.md 末尾節。
  12/26週のDD比は**両側が同じ凍結窓を覆っていること**を要求する（監査 AS-M126-02 の
  是正）: 終点は帳簿・ベンチマークの両方に窓最終日（start+weeks*7-1）の mtm mark が
  必要で、欠けている側を reason（`*_no_mark_at_window_end`）に出して保留する。
  起点は v1.9 の共通ベース契約どおり**窓内で両側が mark を持つ最初の日**
  （`comparison_start` に記録。片側だけの origin だと土俵が混在する。実データでは
  株式ベンチが帳簿Cより4日遅く mark を開始しているため、窓初日を強制すると C の
  DD が永久保留になる — 共通起点なら判定可能で、短縮は記録に見える）。
  mark は `mtm_date`（対象日）で帰属するので
  後日到着した窓内 mark で次の週次runが判定を完了できる（frozen窓を勝手に短縮しない）。
  criterion には `comparison_start` / `comparison_end` / 両側の `*_mark_span` を残す。
  12週の累積損益（`cum_pnl_min`）は**元本1.0起点**（監査 AS-M126-03 の是正）:
  窓内最終 mtm equity − 1.0（週次行 `cum_return` の `equity - 1.0` 契約と同一）。
  初回 mtm を分母にするリベースは初日の損失・コストを消していたため禁止
  （`value_basis="inception_principal_1.0"` を記録）。窓最終日の mark も要求する
  （途中日までの累積で12週ラベルを書かない）。
  26週コストゲートは**署名付き** shortfall_bps 平均の片側95%上界 ≤ 15bps
  （t_2202c100。4週の abs() 版を流用しない）。mark の窓帰属は**決定の実行日**
  （decision行の `decision_date`＝対象時点）で決める（監査 AS-M126-05 の是正）:
  `recorded_at`（記録時点）は証跡でしかなく、締切後に測定された窓内実行の計測も
  n に数える（writer は遡及しないので、正直な遅延計測で凍結窓が完成する）。
  `attribution="decision_execution_date"` と `late_recorded` を記録。
  decision行が無い mark は `shortfall_errors` に `mark_decision_row_missing` として報告。
  26週コストゲートは n>=2 を「上界の最低サンプル数」として扱い、対象集合の完全性とは
  区別する（監査 AS-M126-06 の是正）: criterion は窓内の fill 集合（mark と同じ実行日帰属）
  を照合し、`completeness`（`target_fills` / `measured` / `unmeasured` / `unusable` を
  decision_id 付きで）を記録する。**対象 fill に未計測が1件でも残る間は判定を確定しない**
  （`reason="shortfall_unmeasured_fills"`。部分サンプルの PASS を締切日に記録すると、
  dedupe で正直な遅延計測が永遠に届かなくなるため。記録なし・dedupeされない保留）。
  mark はあるが値が使えない行は v1.7 C-03 契約どおり統計から外して `shortfall_errors`
  に報告する（writer が mark を書き直すことは無いため、使えない行で永久保留にはしない）。
  decision行が無い fill は実行日が確定できないため未計測扱いでブロックする（fail-closed）。
  さらに writer 側（v1.8 C-04）の `mtm_unpriced` で終わった日（過去日と**同日**）は
  `run_daily_mtm` の
  **復旧パス**が補う（監査 AS-M126-02-R2 の是正・同日含むは AS-M126-02-R4 の是正）:
  当日の行を書く前に、未評価のまま残っている日を**失敗した評価自身の時点**
  （AS-M126-02-R3 の是正）で再評価し、全保有の価格が
  揃えば `mtm` 行を書く。評価する保有は **`mtm_unpriced` 行自身のスナップショット**
  （units/cash）で、peak/halt・累積費用・`equity_gross` の鎖は**ジャーナル上その行より前の
  prefix** から再生する。暦日の終わりの状態を使わないのは、00:00の失敗書込の後に同じ日に
  約定した執行（01:00決定→03:00約定）を前日終値で評価すると、評価対象のスナップショットを
  取り替えて FAIL の日を PASS に改変してしまうため（prefix 再生とスナップショットが一致
  しないジャーナルは回収を拒否する — どちらの読みにも賭けない）。`decision_date` は
  意図的に使わない（決定の日付であって評価の時点ではない）。`recorded_at` は復旧runの
  正直な時刻（遡及しない）で、`recovered_from_unpriced: true` を証跡に持つ。B2 の
  (book, mtm_date) 一意は維持。まだ価格が揃わない日は元の `mtm_unpriced` 行が立ったまま
  （複製しない）。loop が一度も走っていない日は作らない（復旧は記録された失敗を完遂する
  だけで、writer が見ていない日の事実は捏造しない）。
  判定側は**凍結窓内に残る既知の未解決 `mtm_unpriced`** を確定前に照合する（監査
  AS-M126-07 の是正・窓全体照合は AS-M126-07-R4 の是正）: 帳簿・ベンチマークの
  **凍結窓の初日から窓最終日まで**（暫定共通起点より前を含む）に
  同一 (book, mtm_date) の `mtm` 行がまだ無い `mtm_unpriced` が1件でも残る間、DD基準は
  `reason="unresolved_mtm_gap_in_window"`（`unresolved_mtm_gaps` に該当日を明記）で保留し、
  12/26週の判定を記録しない。**終点の一致は内部の完全性の証拠にならない**（窓途中の欠測は
  窓の本当の最悪日を隠し、帳簿側の欠測は DD を浅く見せて偽 PASS を、ベンチ側の欠測は
  分母を縮めて偽 FAIL を確定させ得る）。さらに**暫定共通起点より前の既知欠測も保留する**:
  欠測の復旧は最初の共通 mark 自体を前へ動かして比較の取り直しになり得るため、起点を
  下限に使うと偽 PASS が永久確定する（rev4 の「起点より前は照合不要」は撤回）。
  保留するのは**記録された失敗**だけで、未試行日（遅れて開始した帳簿・ベンチ）は
  `mtm_unpriced` 行を持たないので保留しない（初日 mark の一律必須化はしない）。
  窓短縮・欠測日のゼロ騰落扱い・未試行日の捏造は
  しない。復旧または後日の正直な mark で窓が完成した週次runが同じ凍結窓を1回だけ記録する。
  4週ブロックの fail-closed INSUFFICIENT 記録は t_d33aef07 のまま変更しない。

- **`shortfall_*` は「その週の mark」の集計（v1.7, C-03）**: `shortfall_bps_mean_signed` /
  `shortfall_bps_mean_abs` / `shortfall_bps_mean_upper95` / `shortfall_n` は
  `week_end-6日..week_end` の mark 行だけを対象にする（`shortfall_scope="week"`、
  窓の起点は `shortfall_window_start`）。以前は**帳簿の全期間**の mark を集計して
  いたため、同じ行の他の列（週次）と意味が違い、26週コストゲートが全期間値を見て
  いた。mark が属する週は mark 自身の `recorded_at` の日付で決める
  （週次行は記録時点の集計。26週マイルストーンゲートだけは意図的に別規約で、
  決定の実行日で帰属する — v1.10項を参照）。`shortfall_bps` が欠落・null・非数値の
  mark 行は集計から外し、
  `shortfall_errors`（`{reason, decision_id, asset, recorded_at}`）と
  `shortfall_n_excluded` に**記録する**（無言で捨てない）。以前は 1 行の欠落で
  `KeyError` になり週次行が書かれず、milestone 判定ごと失われていた。
  使用可能な mark が 0 件の週は平均が null（0 で埋めない）。
  実測（実ジャーナル, A, week_end=2026-10-26）: 旧 n=26 / -10.2291bps →
  新 n=8 / -11.2105bps。
- 1時間tape: `data/demo-loop/tape/1h/{SYM}.jsonl`。fill 実行時に冪等追記。
  約定価格はこの tape の「recorded_at 以降に始まった最初の1時間足の始値」
  （commit-before-fill、F2/D5採択）。recorded_at より前の足での約定は assert で拒否。
- 状態: ジャーナル `data/demo-loop/journal.jsonl`（前値ハッシュ連鎖・追記のみ）から
  毎回 rebuild（state.json 非依存・D4冪等）。book別内訳は `{book}/journal.jsonl`。
- エッジ寿命: `data/demo-loop/cusum_A.jsonl`（`weekly.sh` が `yowayowa.cusum_watch` で
  追記、前値ハッシュ連鎖・追記のみ）。最新行の `action` が次回決定のゲート入力（§3）。
- 凍結入力: `data/demo-loop/inputs/{date}/{book}.json`（終値+as_of+input_sha256、D9）。
- アンカー: `data/demo-loop/anchors.jsonl`（追記のたびに seq/row_hash を固定、改竄検出用）。

## 3. 規律（クリティカル）

- 発注経路ゼロ: broker / MT5 / operator_bridge / 注文エンドポイント文字列を一切持たない
  （test_zero_order_path_is_free が grep テストで守る。M6）。
- commit-before-fill: 約定価格は必ず recorded_at より後に始まった1時間足始値。
  過去の価格での無断約定は禁止（F2/D5採択）。
- 観測コスト = ペーパー約定価格とバックテスト約定価格（実行日の日足始値）との差の
  符号付きbps（buy:+ / sell:−、D8）。1時間足の O-C はボラティリティでありコストではない（F3採択）。
- 想定コストは 15 bps/売買（凍結。評価期間中の変更禁止）。
- mtm 行の `equity_gross` は「市場リターンは同じで、想定コストだけを払わなかった場合の
  equity」である（t_a2d67004、2026-10-23 修正）。日次チェーンで equity の前日比を gross 側にも
  適用し、前回 mtm 行との間に記録された `cost_assumed` をそのステップで足し戻す
  （恒等式: `gross/equity = Π(1 + cost_t / equity_t)`）。したがって Q3 の `cost_drag_pp`
  （`(gross - equity)/gross`、正の値 = 実際に引かれたコストの割合）は相場の上下を含まない。
  修正前の行（〜2026-10-23 の frozen 値）はコスト率の複利で市場損益を含まないため、
  過去行の `equity_gross` / `cost_drag_pp` は読まない。過去行は書き換えない
  （ジャーナル追記のみ・§5）。
- **欠測価格は 0 でも流用でもなく「評価不能」として記録**（v1.8, C-04, 2026-10-23）:
  保有ポジションのどれか 1 つでも確定足が無い日は、そのブックの `mtm` 行を書かず
  `mtm_unpriced` 行（`reason="unpriced_positions"` / `unpriced_assets` / `prices` /
  `units` / `cash`。`equity` は持たない）を 1 日 1 行だけ書く。`0` を equity に入れない。
  universe から外れた保有もストアの価格で評価する（`prices.get(asset, 0.0)` の既定値は廃止）。
  同じ日の再実行は、その日がすでに `mtm_unpriced` 行だと**現在保有を評価して埋めない**
  （AS-M126-02-R4）。当日の再実行で書けるのは**失敗した評価自身のスナップショット**だけ
  （前項の復旧パスが同日を含む。テープが復旧していれば本物の `mtm` 行が書ける。まだ
  揃わなければ `mtm_unpriced` 行が立ったまま・複製しない）。埋めない理由: 00:00 の失敗書込
  の後に同日 03:00 に約定した執行を前日終値で評価すると、評価対象のスナップショットを
  取り替えて FAIL の日を PASS に改変する（欠測の無い日は 00:00 の mark に後の売却は
  載らない — 到着日で評価対象が変わってはならない）。後日の**通常run**も過去日を復旧する
  （summary-v1.10 rev3, AS-M126-02-R2）: `run_daily_mtm` は
  当日の行の前に、過去日に `mtm_unpriced` 記録が残る日をその日時点の状態で再評価し
  （fill は実行日ベース。現在の保有を過去日に当てない）、全保有が評価できれば
  `recovered_from_unpriced: true` 付きの `mtm` 行を書く（`recorded_at` は復旧runの実時刻・
  遡及なし）。12/26週ゲートが要求する窓最終日の mark は、当日しか書かない writer では
  この経路でしか完成しない。
  `mtm` 行が無い日は panel/weekly が最新行で代替する（既存挙動）。詳細:
  `docs/PAPERLOOP_C04_MTM_UNPRICED_20261023.md`。
- anti-scalp guard（paperloop_guard.check_trade）は純粋関数で、ジャーナルから復元した
  last_fill_ts/side/opened_ts/fills_today を受ける（D6）。v1 の通常フローでは発火しない前提のため、
  合成違反注文を使ったユニットテスト（test_guard_rejects_synthetic_violations 系）が
  「拒否してログに残る」ことの証拠になる（F7採択・違反0件を根拠にしない）。
- 5σ HALT は直前21日リターンの σ（当日を含まない、D1）で判定。当日込み窓では理論上発火しない。
- **エッジ寿命の判定は次回決定の入力**（v1.5, t_e2c98d2b）: `cusum_A.jsonl` の
  `action` を `run_daily_decide` が読み、帳簿A で `PAUSE` があれば決定は
  `regime_state=HALT` / `halt_flag=true` / `fill_status=skipped` /
  `halt_reason=cusum_pause` になる（判定が読めない `unreadable` も HALT＝不明を生存と読まない）。
  警報は**ラッチ**する（登録仕様「S is never reset after an alarm」: 後週の S が
  h* を下回ってもゲートは開かない）。読んだ判定は
  `halt_check.edge_lifetime`（`status`/`week`/`S`/`h_star`/`row_hash`、警報時は
  `alarm_week`/`alarm_S`）に残る。モニタがまだ 1 度も書いていない期間は
  ゲート無しで `status="absent"` を記録する
  （モニタ導入前のジャーナルを壊さないため。モニタは `weekly.sh` の `set -e` 内で走る）。

## 4. 継続・中止の機械基準（設計 §4。数値固定、評価期間中の変更禁止）

- 4週: ループ成功率 >= 95%（28日の欠落1日まで）、recompute 一致 100%、発注 0、
  guard違反 0（UTで）、観測コスト平均 <= 30 bps。
- 12週: 帳簿DD <= ベンチマークDD × 0.6、累積損益 >= -10%、切替回数 <= 期待値の2倍、
  ループ不具合HALT 0。
- 26週: 12週「続行」×2（「保留」は1回まで）、DD <= ベンチ × 0.6、カルマー >= ベンチ、
  観測コスト <= 22.5 bps（1.5×）、recompute 100% 維持。
- 絶対停止線: 帳簿の高値からのDD <= -20%（mtm 行で毎日判定、HALT と連動して即停止）。
- backfill 行（実装は自動フラグのみ・C4。明示フラグ・遡及行の入口は存在しない）は
  上記判定の分母から除外する（件数だけ数える）。
- 配線状態（2026-10-27, t_0be71b95）: 機械判定は **4週・12週・26週すべて配線済み**
  （`MILESTONE_WEEKS_CONNECTED=(4, 12, 26)`、record-only）。上の列挙のうち機械ゲートは
  `MILESTONE_RULES` の凍結キーだけを評価する: 4週=成功率/コスト/recompute/発注/guard、
  12週=DD比/累積損益/切替回数、26週=DD比/署名付きshortfall上界。列挙にある他の項目
  （12週の「ループ不具合HALT 0」、26週の「12週続行×2」「カルマー」「recompute維持」）は
  `MILESTONE_RULES` に凍結キーが無いため機械判定しない（人間のゲートで読む。数値の
  追加は凍結変更になるので本カードでは行わない）。判定の出力・保留（事実欠損での
  再判定）の規約は §2 の summary-v1.10 項と docs/PAPERLOOP_MILESTONE_12_26_WIRING_20261027.md。

### 4-1. 判定のタイミングと仮想評価の取扱い（2026-10-28 CIO決定 t_29ab71fd）

- milestone verdict の公式記録は、milestone_date 以降の週次 run が
  weekly_summary.jsonl に書く milestone ブロックのみ（`_milestone_judgment`
  は `week_end < milestone_date` で None を返す＝締切前の公式判定は存在しない）。
- milestone_date 前に week_end を強制した dry-run 等の再現評価は、凍結判定式を
  そのまま使った**診断目的の仮想 verdict** である。算術は正確だが pass/fail の
  権限を持たない。仮想 FAIL は未経過日が凍結分母に残っていることの帰結であり、
  運用欠陥や正式不合格を意味しない。
- 仮想 verdict を提示・報告するときは「経過日数/凍結分母」（例: 19/28）と窓が
  未完成であることを必ず併記する。正式判定の確定は締後の週次 run の記録のみで行う。
- 判定式・verdict 語彙・返却 dict は凍結（rev5 不変項）。部分窓マークの追加や
  INSUFFICIENT 化は設計変更であり行わない。

### 4-2. A_HALF の 20営業日ゲートの意味論（t_264e0692・測定前の解釈確定）

事前登録原文（`prereg.jsonl` book_id=`A_HALF`、row_hash `5cd8e914…`、
原文源は `docs/DEMO_LOOP_LOSS_ATTRIBUTION_20261025.md` §7-2 L153）:

> 「design §4 12週 + 20営業日連続: 費用控除後 equity>=1.0 / 最大DD<=3.0% / DD比<=0.6」

機械ゲート（`paperloop.py` `A_HALF_20BD_RULES`、`milestone[book]["20bd"]`）との対応表:

| 原文の語 | 機械ゲート | 定義 |
|---|---|---|
| 20営業日連続 | `business_days_required=20` ＋ equity ゲートの「毎日」適用 | 帳簿の最初の non-backfill decision 日以降の**平日（Mon–Fri・祝日不問）20日**。「連続」＝窓の各営業日すべてで equity 条件を満たすこと |
| 費用控除後 equity>=1.0 | `equity_net_min=1.0`（value `>=`） | mtm 行の `equity`（**費用控除後**。`equity_gross` はゲート値に使わない）が窓内**各営業日** >= 1.0。1日でも 1.0 未満があれば FAIL（`failing_days` に落ちた日を記録） |
| 最大DD<=3.0% | `max_drawdown_max=0.03`（value `>=` -0.03） | 窓内の最大ドローダウン（running peak を窓の最初の営業日の mark にリベース＝12週DDゲートと同じ足場） |
| DD比<=0.6 | `dd_vs_benchmark=0.6`（`<=`） | 同じ20営業日の CRYPTO_1N の窓内最大DD に対する比。ベンチ側の窓内DD が 0（分母にならない）場合は保留 |

選択の根拠と棄却した解釈（測定前に固定。測定後の変更は禁止）:

- **equity は「毎日」**。棄却: 「窓の最終日だけ >=1.0」の解釈。原文が「～連続」と
  付設していること、および本カードの実測ケース (b)（途中1日の 0.995 → FAIL、
  どの日に落ちたかを記録）がこの読みを指示しているため。
  streak 再起動解釈（1.0 未満の日が出た後に連続20日を取り直す）も棄却:
  判定は「前方 20 営業日」の**1つの固定窓**について1回だけ記録される
  （`docs/DEMO_LOOP_SHADOW_GROSS_CAP_20261025.md` §5 の窓語）。
- **営業日 = 平日（祝日不問）**。根拠は事前登録側の計画算数そのもの:
  初回判断日 2026-10-25(日) 以降の最初の平日を起点に数えると
  **20 営業日目 = 2026-11-20（金）** で、`§5` の「約 20 営業日後は 2026-11-20 前後」
  と一致する。ループ稼働日（土日を含む全日）解釈は窓の終点が 11-14 になり
  事前登録の計画と矛盾するため棄却。
- **土日の mtm 行はこのゲートの観測対象外**（crypto の mtm は毎日書かれるが、
  20営業日窓は平日行だけで構成する）。
- 記録作法は週次 milestone と同一: `week_end < business_days_end` は判定なし
  （早すぎる判定なし）、判定は**一生1回**（`"20bd"` スロットで dedupe）。
  窓内平日の mark 欠落・`mtm_unpriced` 残留（AS-M126-07 と同一規約）は
  **判定全体を保留**し、部分的に揃った事実で PASS を書かない。
  なお 20営業日窓の終点が 4週の milestone_date より早く来ることがある
  （例: 機械 fixture では 20 営業日 < 28 暦日）: その場合、`"20bd"` の記録だけが
  4週の判定より先の週次 run に書かれることがある。4週/12週/26週の「早すぎる
  判定なし」は各々の milestone_date 基準であり、`"20bd"` は独立ゲートとして
  自分の窓が完成した最初の週次 run に一度だけ記録される（UT
  `test_milestone_judgment_fires_once_at_deadline` の v1.11 注記どおり）。
- 実装は record-only。実注文・実資金経路は一切なし（`tests/test_paperloop_a_half_20bd.py`）。

記録予定日: 既存 cron `demo-loop-report-weekly` は **月曜 JST 09:50** に
`week_end=当日（月曜）` で `python -m yowayowa.loop_cli report` を走らせるため、
公式記録は **2026-11-23（月）09:50 JST** の週次 run が書く
（`week_end >= 2026-11-20` で初めて判定条件を満たす）。11-20 当日に書きたい場合は
同コマンドの手動実行で出る（§4-1 の「milestone_date 前に week_end を動かす
仮想評価」ではなく、実物の公式記録）。**新規 cron は不要**。

## 5. 失敗時の復旧

- データ失効は HALT(stale_data)+skipped 行のみ。決定の補完や後追い約定はしない（fail-closed）。
- 欠落日の救済は当日中の再実行のみ（手順は §5-1）。翌日以降は当該日を復活させない。
  backfill は日付引数ではなく実行日との比較による自動フラグ（C4: paperloop.py L1737
  `decision_date = now.date()`・L1830 `backfill = decision_date < now.date()  # C4:
  auto (never an argument)`）で、遡って `backfill: true` 行を作る入口は実装に存在しない
  （反証レビュー F8 の「後追い backfill 行」は実装仕様 C4 が自動フラグへ置換し、
  照合 t_69d006ad で収束済み）。欠落は許容欠落として消化し、数値の辻褄合わせ
  （手書き行・journal 改変・窓起点の付け替え）はしない。
- ジャーナルは追記のみ。削除・修復は禁止（ハッシュ連鎖と anchors.jsonl が改竄を検出する）。
- `_load_journal` は壊れた行で ChainError → loop_cli は exit 2（D10）。1h tape cache の
  みスキップを許容。

### 5-1. 欠落日が出たときの当日再実行手順（2026-10-28 COO裁定 t_04678034 / CIO確定 t_0887fd8b）

1. 監視 `demo-loop-gap-watch`（worker-flash・21:40 JST）が当日の ok run 行の不在を
   検知すると、master DM と COO bot chat へ1通ずつ通知する（COO 起床の配線は
   別カードで実装中）。
2. COO は同日中に yowayowa ボードへ再実行カードを起票し、worker-flash に割り当てる
   （不在なら worker-agy）。
3. 救済期限は当日の UTC 日付が終わる時点＝翌日 08:59 JST。daily の通常実行は
   09:20 JST（UTC 00:20）で翌 UTC 日に当たるため、前日は救えない。
4. 実行: `bash /root/.hermes/profiles/worker-flash/scripts/demo_loop_decide.sh`。
5. 実行後の確認: (a) 当日 UTC 日付の run 行に `phase=end`・`job=daily`・`ok=true`
   が1行以上ある、(b) decision 行の `decision_id` 集合が実行前後で変わらない、
   (c) journal は追記のみ。
6. スクリプトが `another run holds the lock` を出した場合、または (a) が満たされない
   場合は、exit 0 でも成功とみなさない。5分間隔で最大6回まで再試行し、それでも
   満たされなければ COO へ報告し、その日は許容欠落として消化する。
7. 2日以上欠けた時点で4週ゲート（28日窓で ok 27以上）は不合格として記録する。
   数値の辻褄合わせはしない。

### 5-2. gap-watch の試験手順（本番配信経路に載せない・2026-10-28 t_049c5cdf）

`demo-loop-gap-watch`（worker-flash・no_agent・deliver=bot-chat:default）の
試験で、**本番 job id を `hermes cron run` しない**。手動 run は起動側プロセス
で実行され、その環境変数（`DEMO_LOOP_GAP_DRYRUN` 等）がスクリプトへそのまま
渡る。2026-10-28 13:05 に本番 job へ `DEMO_LOOP_GAP_DRYRUN=1`＋sandbox
journal で試験したところ、stdout 本文が deliver 経路で COO に届き、偽の
「欠落日」アラートと救済カード（t_65491add）を生んだ。本番 journal は無傷
だったが、試験と本番は構造的に切り離す（同日改修済み: DRYRUN は stdout に
出さず sandbox ファイルへ書く・非DRYRUN 本文は2行目に判定対象 journal の
絶対パスと sha256 を添える。さらに同日 t_1c20cf5d で非 DRYRUN 実行は
`DEMO_LOOP_GAP_JOURNAL` を無視して判定を本番 journal に固定した — 手順3
はこの影響で書き換えてある）。

手順（script を直接叩く）:

1. sandbox journal を用意する（本番 `journal.jsonl` を読み取り専用でコピーし、
   当日の `phase=end`・`job=daily`・`ok=true` 行を除く。本番は書き換えない）。
2. 無言確認（deliver 0 件を期待）:
   `DEMO_LOOP_GAP_DRYRUN=1 DEMO_LOOP_GAP_JOURNAL=<sandbox> python3
   /root/.hermes/profiles/worker-flash/scripts/demo_loop_gap_watch.py`
   → stdout 空・exit 0。本文は sandbox 側の `gap_watch_dryrun_notice.txt`
   （`DEMO_LOOP_GAP_DRYRUN_OUT` で変更可）へ書かれる。DRYRUN は起動経路に
   かかわらず stdout を出さない設計なので、うっかり本番 job を起動しても
   配信は発生しない。検出本文と本文ラベル（2行目の判定対象 journal パスと
   sha256）の確認はこのファイルで足りる（実測 2026-10-28: 当日 ok end 行を
   除いた sandbox で kind1 本文＋sandbox パス/sha256 を確認・本改修後も有効）。
3. deliver 経路（DM 実送信と deliver=bot-chat:default の配信）と本文ラベル
   （2行目の journal パスと sha256 が本番 journal を指すこと）の実測は
   sandbox では行えない。t_1c20cf5d 改修により非 DRYRUN は判定対象を本番
   journal に固定して `DEMO_LOOP_GAP_JOURNAL` を無視し、DRYRUN は stdout/
   deliver に出さないため、sandbox 判定を deliver 経路に載せる組合せは本
   スクリプトに存在しない（実測 2026-10-28: 旧手順どおり非 DRYRUN で
   `DEMO_LOOP_GAP_JOURNAL=<sandbox>`＋`DEMO_LOOP_GAP_SKIP_DM=1` を付けて
   script を直接実行しても stdout 0 バイト・exit 0 で、本番が ok の日は
   配信 0 件の空振りになる）。したがってこの実測は、本番 journal に実際に
   欠落がある日（当日の ok end 行なし）に本番 job `demo-loop-gap-watch`
   （env 設定なし・21:40 JST 自然発火）がアラートを配信したときに限り、
   その配信実物（executions.db の queued 1 件・DM 本文2行目が本番 journal
   のパスと sha256 を指すこと）で行う。ok の日に擬似的に試す手段は用意
   しない。`DEMO_LOOP_GAP_SKIP_DM=1` は試験専用で master DM だけを止める
   フラグ（**本番 env には設定しない**）だが、本改修後は sandbox 差し替えと
   組めないため本手順での用途は無い。
4. 実行前後で次を数え、期待差分を確認する: (a) worker-flash の
   `cron/executions.db` の `delivery_outcome='queued'` 件数（DRYRUN 試験は
   +0。+1 が見られるのは本番欠落日の本番 job 配信時のみ — 手順3 参照）、
   (b) default profile の `cron/deliveries.db` の行数（DRYRUN 試験は +0）、
   (c) kanban のカード数（+0）、(d) 本番 `journal.jsonl` の sha256（不変）。

## 6. 変更管理

- パラメータ（SMA200、σ21窓、HALT閾値、コスト15 bps、帳簿Cの稼働週）は評価前に凍結、
  評価期間中の改変は禁止。新しい仮説は影の帳簿 + strategy-trial-memo.md への事前登録1行で始める。

## 7. cron 登録（cronjob_manage、no_agent=True、deliver local）

- demo-loop-decide（9b64eaec1cb9）: `20 9 * * *` → scripts/demo_loop_decide.sh
- demo-loop-fill（8994e1906300）: `5 11 * * *` → scripts/demo_loop_fill.sh
- demo-loop-report-weekly（aa774e8b0d1b）: `50 9 * * 1` → scripts/demo_loop_report.sh
- wrapper は flock を取ってから `exec bash <repo>/ops/demo-loop/{daily,fill_v2,weekly}.sh`。
- deliver: 'local'（週次サマリーは Molt の実況で読み上げ）。

## 8. 検収コマンド

- 全体: `cd /root/current-work/yowayowa-investor && TZ=UTC make V=/root/current-work/yowayowa-investor/.venv verify`
- 単体: `.venv/bin/pytest tests/test_paperloop.py -q --tb=no`（G/D/C2/M6 系 8本以上）
- 連鎖検証: `.venv/bin/python -m yowayowa.paperloop_verify --data-dir ./data --json data/demo-loop/verify/latest.json`
  （chain_ok / anchors_ok / R1 決定性 / R2 run_backtest 突合 / exit 0）
- 生ログ: `data/demo-loop/journal.jsonl`（decision/mtm/fill 行 + ハッシュ連鎖）、
  `data/demo-loop/anchors.jsonl`、`data/demo-loop/weekly_summary.jsonl`
