# 週次サマリー行のスコープ修正（C-05 / C-06 / C-07）

カード: `t_75cade36`（親: 承認済みキューからの自動供給）
出典: `docs/PAPERLOOP_MONEY_REVIEW_20261023.md` の C-05 / C-06 / C-07（いずれも影響「中」・未修正）
実施: 2026-10-24 JST / worker-goat
対象コード: `src/yowayowa/paperloop.py`（hotspot）、`tests/test_paperloop.py`、
`tests/test_paperloop_cost_dd_units.py`、`docs/DEMO_LOOP_V1_OPS.md`
非対象（触っていない）: `paperloop_panel.py` / `paperloop_verify.py` / `loop_cli.py` /
`services/*` / `docs/INDEX_ja.md` / API・UI・CLI の各面

---

## 1. 何を変えたか

### C-05 週次行の equity 系を `week_end` で閉じる

- 追加: `_equity_as_of(root, book, as_of)` → `(equity, peak, mark_date, reason)`。
  `as_of` 以前の最後の `mtm` 行の equity と、その日までの最大 `peak_equity`
  （列が無い行は走行ピークを保つ = `rebuild_state` と同じ規則）を返す。
  該当 mark が無ければ equity/peak は `None`、reason は
  `no_mark_at_or_before_week_end`。
- `_comparison_line(..., *, as_of: date | None = None)` を keyword-only で追加。
  `as_of` 指定時は両ブックの mark を `as_of` で切り、終点は各自の最後の mark、
  drawdown 窓もそこで切る。base は従来どおり共有基準日。`as_of` 省略時の意味は不変。
- `weekly_summary` の `cum_return` / `drawdown_now` は上記 as-of 値になり、
  `cum_return_as_of` / `cum_return_reason` / `drawdown_as_of` / `drawdown_reason` を
  additive に追加。比較行には `comparison_as_of` / `benchmark_as_of` を追加。
  `week_end` 以前の mark が無ければ **None + 理由**（0 でも 1.0 でも埋めない）。
- `weekly_summary` 内で `_load_journal(root)` を1回だけ読み、各内訳はその list を使う
  （従来は1ブックあたり6回読み直していた。意味は不変、ハッシュ連鎖検証の重複を削減）。

### C-06 `switches` を銘柄別に数える

- 追加: `_week_switches(journal, book, window_start, week_end)` → `(switches, by_asset)`。
- 各 decision 行の `prev_regime_state` と `regime_state` が RISK_ON/RISK_OFF 間で
  異なる場合を1回と数える。`prev_regime_state` を持たない legacy 行だけ、同一
  (book, asset) の直前 decision と比較する（writer と同じ規則）。
- HALT は数えず、その銘柄のカーソルも動かさない（HALT 明けは直前の risk state と比較）。
- 数えるのは `window_start..week_end` かつ `backfill` でない行（= 週次行の `decisions`）。
  窓より前の行は連鎖の材料にだけ使う。
- `switches_by_asset` を追加（週内に judged decision がある銘柄は 0 でも載せる）。
  合計は `switches` と一致。

### C-07 `vs_benchmark_pp_cum` を1定義にする

- トップレベル `vs_benchmark_pp_cum` は `books[A].vs_benchmark_pp_cum` と同値
  （共有基準日の定義。算出不能なら両方 None）。`vs_benchmark_pp_cum_as_of` も追加。
- 旧定義（基準日なしの `(A.equity - CRYPTO_1N.equity) * 100`）は
  `vs_benchmark_pp_cum_legacy_nobaseline` に別名で残す。同じ as-of で計算するので
  `week_end` より後の mark は混ざらない。
- 既存行は append-only で不変。

### スキーマ

- 週次行の `schema` を `demo-loop-summary-v1.7` → `demo-loop-summary-v1.8`。
- モジュール docstring と `weekly_summary` docstring に v1.8 の変更点、
  `docs/DEMO_LOOP_V1_OPS.md` に運用上の意味（C-05/C-06/C-07 の節）を追記。
- 重複 `week_end` 行の書き込み時 dedupe（append-only の再設計）は本カードの範囲外。
  観測だけ第5節に残す。

---

## 2. 入力とコードのハッシュ

`data/demo-loop` は読み取りのみ（scratch へ複製した先でだけ実行）。前後で不変。

| ファイル | sha256（実行前 = 実行後） |
| --- | --- |
| `data/demo-loop/journal.jsonl` | `5b746fdc33bf15785e4eaae612fc5985384483a07b516d31b7033d474e4ef859` |
| `data/demo-loop/weekly_summary.jsonl` | `48d039e453b1ab5b027c3e24005b600d02737178a5ff1fb5b177d4c7c7714ffd` |
| `data/demo-loop/anchors.jsonl` | `3f7e09e6ff984f35ff04184f6479f38008eb73b2045d2d98f3b7ff05ac872ef3` |
| `data/demo-loop/cusum_A.jsonl` | `1185d9f0454b9b0c66c7317f6ab33126572cf49e71b5561b5f43d3d33ef5cb73` |
| `data/demo-loop/prereg.jsonl` | `d18a1dd0be26ec7a86f375d21e396f8c3d0d3183e54e3a24ba6e7b0fc4e57083` |

コード（実装前 = HEAD `b4616f66156f5cf64c4f240dd9ea062797885bdc90314e2e4d039f5578c275d8`。
作業ツリーには本カード以前から mypy 用 `cast` の 4+/2- だけが乗っており、それは保存した）：

| ファイル | 実装後 sha256 |
| --- | --- |
| `src/yowayowa/paperloop.py` | `c03b26f4ffd3775f2fa56432b918351b9c0d5a5e0e036eed35127173744c4e08` |
| `tests/test_paperloop.py` | `c03691302042fcf19308939e2ea021d0c5ad24e462b56d1677ec9560c4ef907a` |
| `tests/test_paperloop_cost_dd_units.py` | `c539b33b43795a920c3687fbbb657510d8d41138b76c1dd9f6f291d5d81fbfed` |

diff 量（`git diff --numstat`）: `src/yowayowa/paperloop.py` +234/-40、
`tests/test_paperloop.py` +321/-4（`tests/test_paperloop_cost_dd_units.py` は未追跡ファイル
なので差分ではなく全文。本カードの変更は 6 テストの追加と 4 箇所の期待値更新）。
commit / push はしていない。

---

## 3. 実測（同じ実ジャーナル複製で、旧実装と新実装を実際に動かした比較）

方法: `data/demo-loop` を scratch に2部複製し、片方を **HEAD の `paperloop.py`
（実装前の実物）**、もう片方を**作業ツリーの v1.8** で `weekly_summary` にかけた
（同一入力・同一週）。実行は pytest 経由（この環境では素の `python script.py` から
パッケージを import できないため。詳細は第6節）。

### week_end = 2026-10-19（実ファイルに2行あり、後の行は 10-19T04:58 生成）

| 項目 | 旧 | 新 |
| --- | --- | --- |
| schema | `demo-loop-summary-v1.7` | `demo-loop-summary-v1.8` |
| トップ `vs_benchmark_pp_cum` | 0.19951597133428534 | 0.17858844554395903 |
| `vs_benchmark_pp_cum_legacy_nobaseline` | （無し） | 0.17858844554395903 |
| A `cum_return` | -0.03344680388173804（10-23 の mark） | -0.02523841228044177（10-19 の mark） |
| A `drawdown_now` | -0.041268668233111594 | -0.03312670331780243 |
| A `vs_benchmark_pp_cum` | 0.19951597133428534 | 0.17858844554395903 |
| CRYPTO_1N `cum_return` | -0.035441963595080894 | -0.02702429673588136 |
| CRYPTO_1N `drawdown_now` | -0.04322386519071719 | -0.03487411073563518 |
| C `cum_return` | +0.006344189779700082 | 0.0 |
| C `drawdown_now` | -0.0034799531459484 | 0.0 |
| **C `switches`** | **2** | **0**（`{"SPY":0,"IWM":0,"JNJ":0,"PG":0}`） |
| EQUITY_EW `cum_return` / `drawdown_now` | 0.0 / 0.0 | **None / None**（`no_mark_at_or_before_week_end`） |
| A `week_return` | -0.03312670331780243 | 同値（C-02 の定義は変更なし） |

実ファイルに既に書かれている 10-19 の行（`jq` 実測）は `books.C.switches = 2`、
`books.A.drawdown_now = -0.03312670331780243`。**旧実装の再実行値と一致**する。
つまり C-06 の偽スイッチ（銘柄またぎ）は実データで既に 2 回記録されていた。

### week_end = 2026-10-26（実ファイルに3行あり、いずれも 10-23T01:39〜02:01 生成）

| 項目 | 旧 | 新 |
| --- | --- | --- |
| schema | `demo-loop-summary-v1.7` | `demo-loop-summary-v1.8` |
| トップ `vs_benchmark_pp_cum` | 0.19951597133428534 | 同値（= `books[A]`） |
| `vs_benchmark_pp_cum_legacy_nobaseline` | （無し） | 0.19951597133428534 |
| A / CRYPTO_1N / C / EQUITY_EW の `cum_return`・`drawdown_now`・`switches`・`week_return` | — | すべて旧と同値 |

この週はジャーナルの最後の mark が 10-23（`week_end` 以前）なので、C-05 の変更は
**no-op**（意図どおり: 未来の mark が無ければ値は動かない）。差が出るのは 10-19 の
ように「生成時点より後に mark が増えた週を再生成したとき」で、上の表がその実例。

### 合成ジャーナル（テスト内で固定した前後差）

- C-05（`test_C05_equity_columns_are_closed_at_week_end`）: 10-30 に -22.08% の
  クラッシュを置いた入力で、A の `cum_return` は 10-24 時点の +15.5%（旧実装は
  -10%）、`drawdown_now` は旧 -22.08% ではなく as-of 値。
  `test_C05_comparison_line_is_closed_at_week_end`: 10-30 の +30% を無視して
  10-22 時点 +3.0pp（旧実装 0.0pp）を返し、`comparison_as_of=2026-10-22`。
  同じ入力で `as_of` を省いた `_comparison_line` は従来どおり 0.0pp /
  `comparison_as_of=2026-10-30` を返す（既存呼び出しの意味が変わっていないことの確認）。
- C-06: BTC=RISK_ON / ETH=RISK_OFF が並ぶ入力で `switches=0`
  （旧アルゴリズムを同テスト内で再実装して数えると 3）。1銘柄の反転で 1
  （旧 2）。`prev_regime_state` を持たない legacy 行でも同じ。HALT 行は数えない。
- C-07: A の基準 equity 1.10 / ベンチ 1.00 の入力で、共有基準日の差 +5.0pp、
  旧定義（基準日なし）+15.5pp。トップレベルは前者と同値、後者は
  `vs_benchmark_pp_cum_legacy_nobaseline` にのみ出る（差 10.5pp = 元本1,000万円で
  約105万円の読み違いが、同じ名前のまま二重に出ていたことになる）。

---

## 4. 試験

| 種別 | 結果 |
| --- | --- |
| `pytest tests/test_paperloop.py tests/test_paperloop_cost_dd_units.py` | **87 passed / rc=0**（43 + 44、2026-10-24 09:04 JST に終了） |
| うち新規追加 | `test_C05_equity_columns_are_closed_at_week_end`, `test_C05_comparison_line_is_closed_at_week_end`, `test_C06_two_assets_in_different_regimes_are_not_switches`, `test_C06_one_assets_flip_counts_once_and_the_sum_matches`, `test_C06_legacy_rows_without_prev_regime_state_fall_back_per_asset`, `test_C07_top_level_vs_benchmark_is_the_shared_baseline_definition` |
| `ruff check src/yowayowa/paperloop.py` | rc=1 — **既存**の `SIM113`（1603行、未変更箇所）のみ |
| `ruff check tests/test_paperloop.py tests/test_paperloop_cost_dd_units.py` | rc=1 — **既存**の E501×3（220/625/626行）と I001（608行）のみ。新規コード・新規テストには指摘なし |
| `ruff format --check`（上記3ファイル） | rc=1 — **既存**の差分のみ（`paperloop.py:2269`、`test_paperloop.py:220/443/624`）。いずれも本カードで触っていない行 |
| `mypy src/yowayowa` | **Success: no issues found in 242 source files** |

既存失敗の切り分け: HEAD の `paperloop.py` / `test_paperloop.py` を scratch に取り出して
同じ ruff をかけると **同じ5件**（E501×3, SIM113, I001）と**同じ2ファイルの format 差分**が
出る。つまり本カードは lint/format の失敗を増やしていない（`make lint` はこの作業ツリーで
以前から赤）。

### 未実施

- `make verify`（全量 pytest + coverage + 依存 bootstrap）は**未実施**。VPS で重い全量を
  回さない規律による。TSUKUMO 既存環境 / GitHub CI でも未実行。
- ブラウザ E2E / Vercel デプロイ確認は不要（API・UI の応答は変わらない。週次行の
  フィールド追加のみ）。

---

## 5. 重複 `week_end` 行（観測のみ・本カードの範囲外）

`data/demo-loop/weekly_summary.jsonl` は16行で、内訳は
`2026-10-10: 5` / `2026-10-12: 6` / `2026-10-19: 2` / `2026-10-26: 3`。
同一 `week_end` の行が複数あり、どれが確定値かを列から判定できない（C-05 の残り半分）。
本カードでは書き込み時の dedupe を実装していない（append-only の再設計は別カード）。
C-05 の値スコープ修正により、**再生成しても同じ入力なら同じ値**になるため、
重複行の値そのものは一致する（実測: 10-26 の3行は数値同一）。dedupe の設計は
未着手で、次カードの対象。

---

## 6. 未確認範囲

1. `make verify` / GitHub CI / TSUKUMO 実環境は未実行（第4節）。VPS では対象2ファイルのみ。
2. この環境では素の `python script.py` から `yowayowa` を import できない
   （同一プロセスの `os.path.isdir("src/yowayowa")` が False を返す。`ls` では実在。
   sandbox の file-stat 制約と見られる）。実データ実測は **pytest 経由**で行った
   （第3節の方法）。pytest では import でき、`pl.__file__` が作業ツリーを指すことを
   確認済み。
3. ベンチマークの as-of 終点がブックの終点より古い場合（片方だけ mark が遅れている週）、
   比較行は各自の最後の mark で終わる。`benchmark_as_of` に日付は出るが、**不一致を
   理由コードで拒否はしない**（現行データは全ブックが同じ最終 mark なので未観測）。
   金融上の厳密さを上げるなら次段の課題。
4. 実ファイル既存16行は当時の意味のまま（append-only）。本カードは過去行を書き換えない。
   過去行を新定義で読み直す必要がある場合は別途の再生成カードが要る。
5. `paperloop_panel.py` / `paperloop_verify.py` / `loop_cli.py` / `services/*` の
   週次行消費側は未確認（触らない指示）。`_eval_blocks` / milestone 判定の入力は
   今回の変更対象外で、テストでは stub 化している（実パネル経路の milestone 判定は
   既存テスト `test_milestone_*` が全緑）。
6. `switches` を読む凍結ゲート `MILESTONE_RULES[12]["switches_multiple"]=2.0` は
   現在未配線（12w 未接続）。値の意味が変わったことによる下流の再計算は未実施。

---

## 追補（2026-10-24, t_94687d03）

第6節の項目3（比較行の両端が別の日でも値を出す）は **t_94687d03 で fail-closed にした**:
両端が違う日なら比較の数値は None・`comparison_reason="endpoint_mismatch"`、両 as-of は残す。
schema は `demo-loop-summary-v1.9`。詳細と実測は
`docs/PAPERLOOP_COMPARISON_ENDPOINT_20261024.md`。第6節の他の項目は未着手のまま。
