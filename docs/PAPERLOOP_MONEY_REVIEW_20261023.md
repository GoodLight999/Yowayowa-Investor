# PAPERLOOP 金銭計算レビュー（2026-10-23・読み取り専用）

対象: `src/yowayowa/paperloop.py`（3109 行・md5 b100be8a1eb120684cff4cfc8f06813a・HEAD 1b6f28f の作業ツリー）
方法: 静的読解 + 実ジャーナルの読み取り + 合成ジャーナルによる再現（scratch 内のみ）
制約遵守: コード変更なし / git 操作なし / 外部送信なし / 実注文なし / 実データは読取のみ（`data/demo-loop/journal.jsonl` 267 行、`weekly_summary.jsonl` 16 行、いずれも 2026-10-23 11:05 時点）

本モジュールには**通貨も元本も存在しない**（equity は 1.0 起点の比率。`jpy|JPY|currency|capital` は本ファイル・`paperloop_panel.py`・`loop_cli.py` に 0 件／grep 実測）。
したがって以下の「円換算」はすべて **影響率 × 想定元本** の概算であり、想定元本は 1 ブック **¥10,000,000** と置いた（前提であって実測値ではない。実元本は未確認）。

---

## 1. 対象関数と 入力 → 計算 → 出力

| 関数 | 行 | 入力 | 計算 | 出力 |
|---|---|---|---|---|
| `rebuild_state` | 819 | ジャーナル全行 | 最後の fill の units/cash、最後の mtm の equity/peak、halt/stopped の累積 | 状態 dict（状態ファイルなし） |
| `apply_fill` | 882 | units, cash, side, fill_price, delta_notional | buy: `qty=(N-N*0.0015)/P`, `cash-=N` / sell: `traded=min(|N|, units*P)`, `cash+=traded*0.9985` | units_after, cash_after, traded |
| `_daily_map` | 449 | ohlcv.jsonl | provider 一致行のみ、open/close を float 化、重複セッションは ChainError | `{date: {open, close}}` |
| `_first_fill_candidate` | 566 | 1h tape / forming candle / 日足 | `after` より厳密に後の最初のバー open | fill 価格候補 |
| `run_daily_decide` | 1481 | 日足終値（decision_date 未満）, state | SMA200 ゲート → target weight → `delta=target-held_weight`、HALT 判定、freeze | decision 行（+ halt_check 行） |
| `resolve_fills` | 1908 | pending decision, 次バー open, state | guard → `delta_notional=|delta_w|*equity` → `apply_fill` → cost 記録 | fill / fill_rejected 行 |
| `run_daily_mtm` | 1243 | state + 確定日足終値 | `equity=cash+Σunits*close`, `peak=max(peak,equity)`, DD, コスト累計 | mtm 行（1 日 1 ブック 1 行） |
| `_cost_accumulators` | 1318 | ジャーナル | fill の `cost_assumed` を累積し、mtm 列の日次変化に足し戻して gross を再構成 | (cum_cost, cum_turnover, equity_gross) |
| `mark_up` | 2114 | fill 行 + 執行日バー | `shortfall_bps = sgn*(paper/bt-1)*1e4` | mark 行 |
| `_mtm_marks` / `_max_drawdown_since` | 2696 / 2652 | mtm 行 | 日付→equity 辞書、基準日以降の running peak で DD | marks / DD |
| `_comparison_line` | 2709 | 2 ブックの marks + state | 共有基準日 → `cum=equity/base-1`、両側 DD、比 | 比較行（不足時は None + reason） |
| `shortfall_bps_mean_upper95` | 2193 | shortfall リスト | `mean + 1.6449*sd/√n`（n<2 は None） | 26w ゲート入力 |
| `_milestone_judgment` | 2474 | ジャーナル + 窓 | 4w 窓の稼働率・コスト・再計算一致・未知イベント・guard 再現 | 判定ブロック |
| `weekly_summary` | 2795 | ジャーナル全行 | ブック別集計 + 比較行 + eval + milestone | 週次 1 行を追記 |

---

## 2. 候補一覧（優先度順）

### C-01【高】EQUITY_EW が 2 回目の月曜に自己清算する

- 箇所: `paperloop.py:1683`（`target_map = _benchmark_weights(book) if not prior_fills else {}`）、`paperloop.py:1709`（`delta=0` の免除が `BENCH_CRYPTO_BOOK` のみ）、`paperloop.py:2053`（売りをそのまま執行）
- 内容: ベンチマークは初回に 1/N を買った後 `target_map={}` になる。CRYPTO_1N は 1709 行で `delta` を 0 に強制されるが、**EQUITY_EW には同じ免除がない**。よって 2 回目の月曜は `target=0.0`, `held_weight≈0.265`, `delta≈-0.265` の `sell` 決定が 4 銘柄分 `pending` で書かれ、`resolve_fills` が全売却する。
- 再現（実測・合成ジャーナル, scratch）: 月曜に decide → 1/N を約定 → 翌週月曜に decide。
  `EQUITY_EW monday#1: [('SPY','buy',0.25,0.25), ...]`
  `EQUITY_EW monday#2: [('SPY','sell',-0.2649,'pending',False), ('IWM',...), ('JNJ',...), ('PG',...)]`
  → 各銘柄 `|delta|*equity = 0.264852` の売り。売りは `min(|N|, units*P)` で全量に丸められるため**全額退避**。
- 実データ: EQUITY_EW はまだ decision 0 行・mtm 1 行（2026-10-23 実測）。**初回の実害日は次の月曜 2026-10-26**。
- 円換算の最大影響: 比較行の `benchmark_cum_pnl` が清算時点で凍結し、`dd_vs_benchmark_ratio` が恒久的に「現金の DD=0」を分母にできなくなる（= Book C の凍結仮説が読めなくなる）。金額は「ベンチが稼ぐはずだったリターン全額」。
  式: `|bench_cum_pnl| × 10,000,000`。ベンチ年 +10% なら **¥1,000,000** 相当の比較不能。
  副次: 不要な往復コスト `4 × 15bps = 0.06%` → `0.0006 × 10,000,000 = ¥6,000`。
- 優先度: **高**（唯一、確定的に「意図しない全売却」を起こす）

### C-02【高】`week_return` が「その週のリターン」ではなく累積リターン

- 箇所: `paperloop.py:2875` `"week_return": state["equity"] - 1.0`
- 内容: `state["equity"]` は最新 mtm の**起点（1.0）からの累積** equity。同名の他列（`decisions`, `fills`, `mtm_days`）は `window_start..week_end` で絞られており、この列だけ期間スコープが違う。
- 再現（実測・実データ）: week_end=2026-10-26 の行で `A.week_return = -3.3447%`。同じ A の mark から実際の週（2026-10-19 終値 0.9747616 → 2026-10-23 0.9665532）を計算すると **-0.8421%**。差 **2.5026pp**。
- 円換算の最大影響: `|累積 - 週次| × 10,000,000 = 0.025026 × 10,000,000 = ¥250,260`（1 行あたりの過大表示）。
  累積が大きいほど誤差は拡大する（例: 累積 +20%・当週 +0.3% なら 19.7pp ≒ ¥1,970,000 の過大表示）。
- 優先度: **高**（週次報告の主要な金額列が恒常的に別物）

### C-03【高】週次行の shortfall が全期間集計＋1 行で週次全体が停止

- 箇所: `paperloop.py:2848` `shortfalls = [float(row["shortfall_bps"]) for row in marks]`（`marks` は 2843-2847 で**期間で絞っていない**）
- 内容(a) スコープ: `marks` は当該ブックの全 mark。`shortfall_bps_mean_signed/abs/upper95/shortfall_n` は「その週」の行に載るが中身は**全期間**。同じファイル内の `_milestone_judgment:2543` は期間で絞っており、同名指標が 2 定義。
- 内容(b) 例外: 辞書の直接参照なので `shortfall_bps` を持たない mark 行（旧スキーマ・手書き行）が 1 行でもあると `KeyError` → `weekly_summary` が行を書く前に落ちる（`main()` は exit 1）。週次の要（milestone 判定を含む）が丸ごと止まる。
- 再現（実測・実データ, 2026-10-20..26）: A の mark は全 26 件、期間内 8 件。平均は全期間 **-10.2291bps** / 期間内 **-11.2105bps**（差 0.98bps）。週次行には -10.2291 と `shortfall_n: 26` が載っている。
- 円換算の最大影響: コスト指標の誤差 1bps は `0.0001 × 10,000,000 = ¥1,000`。ただし主害は 26w コストゲート（閾値 15bps）の判断材料が「全期間値」になること。落ちる場合の影響は当該週の判定・記録の全喪失。
- 優先度: **高**（毎週のコスト数字が別物／例外で週次が停止し得る）

### C-04【高】mtm の欠測価格が「クラッシュ」または「0 評価」

- 箇所: `paperloop.py:1283` `units_value = sum(units * prices.get(asset, 0.0) for asset, units in state["units"].items())`（`prices[symbol]=None` は 1278）
- 内容(a): 保有銘柄の確定足が 1 本も無いと `prices[symbol]=None` が入り、`float * None` で **TypeError**（実測: `unsupported operand type(s) for *: 'float' and 'NoneType'`）。`run_daily_mtm` はブック順に書くため、**先行ブックの mtm 行だけ書けた状態**で停止し、anchor も run-end 行も書かれない。
- 内容(b): 保有銘柄が現在の `universe` に無い場合、`prices.get(asset, 0.0)` が **0.0** を返し、その建玉が消える（実測: 既定値 0.0）。「欠測を 0 にしない」というリポジトリ不変条件（AGENTS.md 原則 2）に反する。
- 再現条件: 保有銘柄の `crypto-ohlcv|stock-ohlcv/<SYM>/ohlcv.jsonl` が空/削除、または `provider` 文字列が変わり 464 行のフィルタで全行スキップされる（ベンダー再取込・改名で現実に起こる）。あるいは `EQUITY_UNIVERSE` / `CRYPTO_UNIVERSE` の変更後に旧建玉が残る。
- 円換算の最大影響(b): 保有比率 `w` の銘柄が 0 評価 → `equity` が `(1-w)` 倍 → 見かけの DD が `-w`。`w=0.25` なら `0.25 × 10,000,000 = ¥2,500,000` の架空損失が equity 連鎖・比較行・-20% 警告に混入。
- 優先度: **高**（1 銘柄のデータ欠落で金額が桁で壊れる／日次ジョブが半端終了する）

### C-05【中】週次行が `week_end` に拘束されず、同一 week_end の行が重複する

- 箇所: `paperloop.py:2758`（`cum` は**最新** mtm の equity を使用）、`paperloop.py:2760-2761`（DD は全 mark から）、`paperloop.py:2804`（窓は week_end から算出するが marks には適用しない）、`paperloop.py:2938`（追記のみ・week_end で dedupe しない）
- 内容: 比較行と `drawdown_now` は「生成時点」の値で、`week_end` より後の mark も入る。mtm/halt_check/decision/run はすべて主キーで dedupe しているのに、週次だけ重複を許す。
- 再現（実測・実データ）: 同一 week_end の行数 = `2026-10-10: 5`, `2026-10-12: 6`, `2026-10-19: 2`, `2026-10-26: 3`。さらに `week_end=2026-10-26` の 3 行は 2026-10-23T01:39〜02:01 に生成されており、**未来の週末**をラベルにデータは 10-23 まで。
- 円換算の最大影響: 遡及実行で `ΔDD × 10,000,000`。観測した重複 3 行は数値が同一（差 0）なので**現時点の実害額は ¥0**、遡及実行・再生成時に発生する構造リスク。
- 優先度: **中**（現時点で金額差は出ていないが、どの行が確定値か判定できない）

### C-06【中】`switches` を銘柄横断で数えている

- 箇所: `paperloop.py:2849-2860`（`judged` は「ブックの」決定行を日付・銘柄混在で走査し、`prev_regime` を 1 本だけ持つ）
- 内容: regime は銘柄ごとの状態なのに、直前の**別銘柄**の regime と比較している。BTC=RISK_ON, ETH=RISK_OFF の日は 1 日で最大 3 回の偽スイッチ（ON→OFF→ON→OFF）。
- 再現: 現在の実データは A の BTC/ETH が同一週すべて RISK_ON のため未発火（実測: 当該週の switches=0、銘柄別も 0）。銘柄で regime が割れた週から誤計上。
- 円換算の最大影響: 金額直接ではない。`MILESTONE_RULES[12]["switches_multiple"]=2.0` の分母・分子を最大 21 回/週 狂わせる（12w は現在未配線）。
- 優先度: **中**（凍結ゲートの入力指標が壊れている）

### C-07【中】`vs_benchmark_pp_cum` が同名・別定義で 2 つ出る

- 箇所: `paperloop.py:2915`（トップレベル: `(A.equity - CRYPTO_1N.equity) * 100`、起点基準なし）と `paperloop.py:2768`（ブック別: 共有基準日からの差分）
- 内容: v1.4 で「起点を揃える」修正を入れたが、旧定義のトップレベル列は残置。同じ行に同名キーが 2 つ（トップレベルと `books.A` 配下）、定義が違う。
- 再現（実測）: 現在は `comparison_start` が A の起点 2026-10-10 と一致するため両方 0.19952 で一致。EQUITY_EW 稼働後や Book C では一致しない。
- 円換算の最大影響: `|2定義の差| × 10,000,000`。両ブックの起点がずれた分（例 30 日 × 日次 0.3% ≒ 9pp）で最大 **¥900,000** の読み違い。
- 優先度: **中**（読む側が誤った方を拾う）

### C-08【低】ベンチマークにも guard を適用しているのに `guard_applied: false` と記録

- 箇所: `paperloop.py:1981`（全ブックで `check_trade`）に対し `paperloop.py:2045`, `2092`（`guard_applied` は戦略ブックのみ True）
- 内容: ベンチの fill が `min_hold` 等で棄却されても、行は「guard 未適用」と読める。棄却の事実は `fill_rejected` 行に残るが、`fill` 行だけを見ると矛盾。
- 影響額: 棄却 1 件 = `|delta_weight| × 元本` の未執行（例 0.25 × ¥10,000,000 = ¥2,500,000 の建玉が入らない）。頻度は低い。
- 優先度: 低

### C-09【低】株式フォールバックの執行時刻が 13:30 UTC 固定（DST 無視）

- 箇所: `paperloop.py:582` `datetime(session.year, session.month, session.day, 13, 30, tzinfo=UTC)`
- 内容: 冬時間（EST）の実際の寄付は 14:30 UTC。価格は日足 `open` を使うので**金額は正しい**が、`paper_fill_bar_open_ts` が 1 時間先行し、C2（`fill_open_ts > recorded_at`）の意味と guard の同日判定がずれる。
- 影響額: 直接 0。時刻の証跡が誤る。
- 優先度: 低

### C-10【低】買いで cash が負になり得る（無コストの暗黙レバレッジ）

- 箇所: `paperloop.py:893-898`（`cash -= delta_notional` に残高チェックなし）
- 再現（実測）: 40% を 4 銘柄買うと `cash = -0.6`、資金コストは一切計上されない。
- 円換算: `レバレッジ L × 調達金利 r × 期間 × 元本`。`L=0.6, r=2%, 26週` なら `0.6 × 0.02 × 0.5 × 10,000,000 = ¥60,000` の過小コスト。
- 優先度: 低（Book C の target が 1.0 を超えた場合のみ）

### C-11【低】supersede の同時刻タイブレークが「先着優先」

- 箇所: `paperloop.py:1875` `if key not in newest or stamp > newest[key][0]`
- 内容: `recorded_at` が同一の 2 決定では先に書かれた方が newest になり、後発が superseded にされる。通常は µs 精度で衝突しないが、時計を注入した実行・バックフィルでは順序が逆転する。
- 影響額: 棄却された決定 1 件分の未執行（`|delta_weight| × 元本`）。
- 優先度: 低

### C-12【低】日足キャッシュの重複セッションで金銭経路が全停止

- 箇所: `paperloop.py:474-475`（`raise ChainError(f"duplicate daily session ...")`）
- 内容: 「壊れた行はスキップ」というキャッシュ方針（463 行）と矛盾し、ベンダー再取込で同一 `as_of` が 2 行になると decide/mtm/週次が全停止する。fail-loud 自体は妥当だが、**復旧手段がデータ削除しかない**。
- 影響額: 停止日数 × 日次変動。1 日止まると equity 連鎖に 1 日分の穴（例 日次 1% → ¥100,000/日）。
- 優先度: 低（発生頻度は低いが影響は広い）

### C-13【低】通貨の検証が無い（USDT/USD/JPY の混在を検出しない）

- 箇所: `paperloop.py:449-477`（`currency` フィールドを読まない）
- 内容: 暗号ブックは USDT 建て、株式ブックは USD 建てのバーを、どちらも「1.0 起点の比率」として同じ式で扱う。**本モジュールに円換算は存在しない**ため換算誤差は起きないが、ベンダー側の通貨が JPY 等に変わっても検出できず、そのまま USD として金額計算される。
- 円換算: 通貨取り違え 1 件で `|実際の通貨差|`。JPY 建てを USD として読むと **約 150 倍**の評価誤り（USD/JPY を 150 と仮定。レートは未確認）。保有比率 25% なら `0.25 × 149 × 10,000,000 = ¥372,500,000` 相当の破綻。
- 優先度: 低（発生証拠なし。ただし検出器が無いこと自体が欠陥）

### C-14【低】v1.3 で廃止した `stopped` が週次行と `_halt_action` に残存

- 箇所: `paperloop.py:1798-1799`（`state["stopped"]` なら `action="stop"`）、`paperloop.py:1032`（stopped のとき `drawdown_warning` を付けない）、`paperloop.py:2892`（週次 `stopped`）
- 内容: -20% は警告に降格されたが、凍結行に `stopped: true` が 1 行でもあると以後ずっと `stopped: true` / `action: "stop"` が出続ける（`rebuild_state:836` が OR で累積）。
- 影響額: 直接 0（停止はしない）。監視側が「停止中」と誤読するリスク。
- 優先度: 低

---

## 3. 検査したが欠陥としなかった観点（除外理由）

- **費用の二重計上**: `apply_fill` の買いは notional 内にコストを織り込み（`qty=(N-N*c)/P`）、売りは proceeds から控除（`traded*(1-c)`）。往復を実測すると 100 → 99.85 → 99.70 で `0.2998%`（≒2×15bps）、二重計上なし。`cost_assumed` は記録であり、equity から再控除していない（panel は mtm の `equity_gross` を使う）。`_cost_accumulators` の足し戻しも「前回 mtm 以降の fill のみ」で、二重に足していない。
- **丸め誤差の累積**: Decimal / 円単位丸め / `round()` は金銭経路に存在しない。equity は比率で、float の JSON 往復は可逆。累積する丸めは無い。
- **同時刻重複行（mtm / halt_check / decision / run）**: それぞれ `(book, mtm_date)`（1258-1264）、`(book, check_date)`（1560-1565, 3077-3083）、`decision_id`（1592-1593）、`run_id`（1373-1377, 1407-1411）で dedupe 済み。欠けているのは週次だけ（C-05）。
- **未約定を 0 扱い**: `fill is None → continue` は pending を維持し 0 約定にしていない（1952-1953）。`delta_notional <= 1e-9` を hold の fill として書くのは `1e-9 × 元本 = ¥0.01` 未満で無害。
- **BENCH_DD_STOP**: `enabled=False`, `k=None` の設計上のフック。欠陥ではない（`bench_dd_stop_fired` は k=None で False）。
- **MILESTONE 12/26 が未配線**: `MILESTONE_WEEKS_CONNECTED=(4,)` はコメントで明示された範囲判断（t_d33aef07）。ただし**26w のコストゲートは永久に発火しない**という帰結は記録しておく（設計上「未達」ではなく「未評価」）。
- **並行実行による二重 fill**: `ops/demo-loop/{daily,fill_v2,weekly}.sh` が同一 `.lock` に `flock -n` を取るため運用経路では防がれている。Python モジュール単体には lock が無く、`loop_cli` を直接叩くと残余リスク（欠陥ではなく運用境界）。
- **`paperloop_panel.py` / `paperloop_verify.py` / `paperloop_guard.py` の内部**: 本レビューの対象外（比較行・合計の入力としてのみ参照）。

---

## 4. 未確認範囲

1. **C-01 の実ジャーナルでの発火は未観測**。EQUITY_EW は decision 0 行・mtm 1 行（2026-10-23 実測）で、初回の実害日は次の月曜 2026-10-26。確認は合成ジャーナルによる再現のみ。
2. **v1.1/v1.2 の凍結行が混在した場合の再計算**は未検証。`cost_assumed` を持たない fill 行は `0.0` 扱い（1344）になり `cum_cost` が過少になる。実ジャーナル 267 行の schema 分布は未確認。
3. **week_end=2026-10-26 の行がなぜ未来日付で生成されたか**（`loop_cli.report()` は `_now().date()` を渡す）生成主体を特定できていない。cron 定義は未確認。
4. **円換算の実レートと実元本**は未確認。本レビューの金額はすべて「率 × ¥10,000,000」の概算。
5. **テストスイートは実行していない**（コード変更なしのため）。既存テストは EQUITY_EW の**初回月曜のみ**を検証し（`tests/test_paperloop.py:644-701`）、2 回目以降の月曜は未カバー — C-01 がテストで捕まらない理由。
6. `_daily_map` の provider フィルタ（464）が実データで何行を落としているかは未計測。C-04 の引き金になり得る。

---

## 5. 数値の出所

- `data/demo-loop/journal.jsonl` 267 行（2026-10-23 11:05 時点の読み取り）
- `data/demo-loop/weekly_summary.jsonl` 16 行（最終生成 2026-10-23T02:01:44）
- C-01 の再現: scratch の合成ジャーナル（`tests/test_paperloop.py::_prepare` を再利用、2026-09-21 起点）。実データは書き換えていない。
- C-03/C-02 の実測値: 上記 2 ファイルから直接計算（A の mark 26 件 / 期間内 8 件、A の週次 -0.8421%）。
- C-10 の `cash=-0.6`: `apply_fill` を直接呼んだ実測（40% × 4 銘柄）。
- C-04 の `TypeError`: `float * None` の実測。`prices.get` の既定 0.0 も実測。

---

候補14件/優先度高4件
