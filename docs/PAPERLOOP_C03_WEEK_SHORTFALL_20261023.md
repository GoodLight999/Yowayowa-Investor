# PAPERLOOP C-03 修正: 週次 `shortfall_*` を単週に限定し、異常行で週次を止めない（2026-10-23）

対象: `src/yowayowa/paperloop.py` の `weekly_summary`（週次サマリー 1 行）
出典: `docs/PAPERLOOP_MONEY_REVIEW_20261023.md` C-03【高】
HEAD: `fd9a1d0`（作業ツリー）／実データは読み取りのみ（書き込みは scratch のコピーのみ）

## 1. 原因（2つ・同一箇所）

旧コード（`weekly_summary` 内、1 行）:

```
shortfalls = [float(row["shortfall_bps"]) for row in marks]
```

`marks` は `_load_journal(root)` から `event=="mark" and book==<その帳簿>` で
絞っただけで、**期間で絞っていない**（`decisions` / `mtms` / `halt_check_days` /
`runs_completed` はすべて `window_start..week_end`）。

1. **集計範囲の誤り**: `shortfall_bps_mean_signed` / `_abs` / `_mean_upper95` /
   `shortfall_n` は週次の行に載るのに中身は**帳簿の全期間**。同じモジュールの
   `_milestone_judgment`（2546-2549 行）は同じ名前の指標を窓で絞っており、
   同名 2 定義だった。26 週コストゲート（`shortfall_bps_mean_upper95_max=15bps`）は
   全期間値を見ていた。
2. **停止条件の単位が「行」ではなく「帳簿」**: `row["shortfall_bps"]` は辞書の直接参照。
   `shortfall_bps` を持たない mark 行（旧スキーマ・手書き行・null・非数値）が
   **1 行でもあると** `KeyError` で `weekly_summary` が落ちる。週次行の追記は
   関数の最後（全帳簿のループの後）なので、**その週の行は書かれず、milestone 判定も
   失われる**（`main()` は exit 1）。

## 2. 変更内容（最小）

- `_week_shortfalls(marks, window_start, week_end)` を追加
  （`_dd_vs_benchmark_ratio` の直後、`weekly_summary` の直前）。
  - 窓 = `week_end - 6日 .. week_end`（他列と同じ）。mark が属する週は
    **mark 自身の `recorded_at` の日付**で決める（`_milestone_judgment` と同じ規則）。
  - `shortfall_bps` が 欠落 / null / 非数値 / 非有限（nan・inf）/ `bool` の行は
    集計から外し、`{reason, decision_id, asset, recorded_at}` として返す。
    reason は `missing_shortfall_bps` / `non_numeric_shortfall_bps` /
    `non_finite_shortfall_bps` / `unparseable_recorded_at`。
- `weekly_summary` の book dict:
  - `shortfall_*` の 4 列を `_week_shortfalls` の結果に差し替え
  - `shortfall_scope: "week"`、`shortfall_window_start`（窓の検証用）を追加
  - `shortfall_n_excluded`、`shortfall_errors` を追加（**無言処理 0**）
  - 行の `schema` を `demo-loop-summary-v1.7` に更新
- ドキュメント: `docs/DEMO_LOOP_V1_OPS.md` §2 に定義と実測を追記、
  `paperloop.py` 冒頭に v1.7 の説明を追記。

diff（本体・`git diff` から抜粋）:

```
-        shortfalls = [float(row["shortfall_bps"]) for row in marks]
+        week_shortfalls, shortfall_errors = _week_shortfalls(marks, window_start, week_end)
...
+            "shortfall_scope": "week",
+            "shortfall_window_start": window_start.isoformat(),
             "shortfall_bps_mean_signed": (
-                sum(shortfalls) / len(shortfalls) if shortfalls else None
+                sum(week_shortfalls) / len(week_shortfalls) if week_shortfalls else None
             ),
             "shortfall_bps_mean_abs": (
-                sum(abs(value) for value in shortfalls) / len(shortfalls) if shortfalls else None
+                sum(abs(value) for value in week_shortfalls) / len(week_shortfalls)
+                if week_shortfalls
+                else None
             ),
-            "shortfall_bps_mean_upper95": shortfall_bps_mean_upper95(shortfalls),
-            "shortfall_n": len(shortfalls),
+            "shortfall_bps_mean_upper95": shortfall_bps_mean_upper95(week_shortfalls),
+            "shortfall_n": len(week_shortfalls),
+            "shortfall_n_excluded": len(shortfall_errors),
+            "shortfall_errors": shortfall_errors,
-        "schema": "demo-loop-summary-v1.6",
+        "schema": "demo-loop-summary-v1.7",
```

`recompute_R1_rate` の分母（`marks`）は**変更していない**（C-03 の範囲外）。
C-01（ベンチマークの再売却）/ C-02（`week_return`）/ C-04（mtm の欠測価格）の
hunk には触れていない。作業ツリーには C-01・C-02 の未コミット変更が同居しており、
着手前後で `git diff -- src/yowayowa/paperloop.py` を取り、両 hunk
（`benchmark_bought` / `_week_return`）が残っていることを確認した（§6）。

## 3. 実測（実ジャーナルのコピー・A 帳簿）

`data/demo-loop/journal.jsonl` 267 行（`sha256 5b746fdc…ef859`）を scratch に
コピーして `weekly_summary` を実行（実ファイルは読み取りのみ）。
旧値は HEAD の式（`[float(row["shortfall_bps"]) for row in marks]`）を同じ
ジャーナルに適用して算出。

| week_end | 窓 | 旧 n / mean_signed | 新 n / mean_signed | 新 mean_abs | 除外 |
|---|---|---|---|---|---|
| 2026-10-12 | 10-06..10-12 | 26 / -10.2291 | **4 / +32.7008** | 37.9341 | 0 |
| 2026-10-19 | 10-13..10-19 | 26 / -10.2291 | **14 / -21.9341** | 24.1041 | 0 |
| 2026-10-25 | 10-19..10-25 | 26 / -10.2291 | **10 / -8.3417** | 13.4506 | 0 |
| 2026-10-26 | 10-20..10-26 | 26 / -10.2291 | **8 / -11.2105** | 14.4441 | 0 |

レビュー C-03 の独立計算（全期間 -10.2291bps / 期間内 -11.2105bps・8 件）と一致。
旧実装は 4 週すべてに同じ -10.2291 と `shortfall_n: 26` を載せていた。

## 4. 実測（1 行の欠損で週次が止まる／止まらない）

実ジャーナルのコピーに、`shortfall_bps` を持たない mark 行を 1 行だけ
（`_append_row` 経由・ハッシュ連鎖を壊さない形で、`recorded_at=2026-10-24`）追記:

- 旧経路（HEAD の式）: `KeyError('shortfall_bps')`（`decision_id=A:BTC:2026-10-23`）
  → 週次行の追記は関数末尾なので、**その週の行は 1 行も書かれない**。
- 新経路: 週次行が書かれる（`weekly_summary.jsonl` 16 → 17 行）、
  `schema=demo-loop-summary-v1.7`、`shortfall_n=8`、`shortfall_n_excluded=1`、
  `shortfall_errors=[{'reason': 'missing_shortfall_bps', 'decision_id': 'A:BTC:2026-10-23',
  'asset': 'BTC', 'recorded_at': '2026-10-24T00:21:00+00:00'}]`。
  平均は欠損行を除外した -11.2105bps のまま（0 で薄めない）。

使用可能な mark が 1 件も無い週は 4 列とも null（0 で埋めない）。

## 5. 既存ファイル不変

- `data/demo-loop/journal.jsonl`: `sha256 5b746fdc…ef859` / 257,193 bytes —
  計測の前後で不変。
- `data/demo-loop/weekly_summary.jsonl`: `sha256 48d039e4…14ffd` / 108,708 bytes —
  不変（実在する 16 行の schema は `demo-loop-summary-v1.2` 11 行と `schema` 無し 5 行。
  v1.6 の行は 1 行も無い）。

## 6. 回帰テスト

`tests/test_paperloop.py::test_weekly_shortfall_is_scoped_to_the_week_and_survives_bad_rows`

- 窓外の mark（旧週）が平均に入らないこと（旧式の値 22.0bps と明示的に比較）
- 窓内 3 件 → `shortfall_n=3` / `shortfall_bps_mean_signed=20.0`
- 同一週に 欠落キー / null / `"n/a"` の 3 行を混在させても例外にならず、
  `shortfall_n=2` / `shortfall_n_excluded=3` / reason と `decision_id`・`asset` が
  行ごとに残ること、**週次行が実際に追記される**こと
- 使用可能 0 件の週は 4 列とも null（0 で埋めない）

`PYTHONPATH=src .venv/bin/python -m pytest tests/test_paperloop.py -q` で確認。

## 7. C-01 / C-02 との同居

同一作業ツリーの未コミット変更を起点に、追加のみを行った。着手後と終了後の
`git diff -- src/yowayowa/paperloop.py` で、C-01 の hunk（`benchmark_bought` による
D7 免除の一般化、`run_daily_decide`）と C-02 の hunk（`_week_return` の追加、
`week_return` / `week_return_base` / `week_return_as_of` / `week_return_reason` /
`cum_return`）が残っていることを確認済み。`schema` は C-02 の `v1.6` を
`v1.7` に置き換えた（v1.6 は実ファイルに 1 行も書かれていないため、実在する行の
意味の食い違いは生じない）。
