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
  行の `schema` は `demo-loop-summary-v1.7`（v1.6 は C-03 修正前の作業ツリー版で、
  実ファイルには 1 行も書かれていない。実在する旧行は `...-v1.2` と `schema` 無し）。
  実測（実ジャーナル, week_end=2026-10-26）: A は旧 -3.3447% → 新 -0.8421%
  （差 2.5026pp）、CRYPTO_1N は旧 -3.5442% → 新 -0.8651%。
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
- backfill 行（明示フラグ時のみ・D5/F8）は上記判定の分母から除外する（件数だけ数える）。

## 5. 失敗時の復旧

- データ失効は HALT(stale_data)+skipped 行のみ。決定の補完や後追い約定はしない（fail-closed）。
- 欠落日の再実行は decide の backfill フラグで `backfill: true` 行を書き、判定から除外（F8採択）。
- ジャーナルは追記のみ。削除・修復は禁止（ハッシュ連鎖と anchors.jsonl が改竄を検出する）。
- `_load_journal` は壊れた行で ChainError → loop_cli は exit 2（D10）。1h tape cache の
  みスキップを許容。

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
