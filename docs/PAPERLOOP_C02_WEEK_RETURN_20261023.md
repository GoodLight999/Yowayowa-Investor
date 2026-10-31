# PAPERLOOP C-02 修正: `week_return` を単週の変化率にする（2026-10-23）

対象: `src/yowayowa/paperloop.py` の `weekly_summary`（週次サマリー 1 行）
出典: `docs/PAPERLOOP_MONEY_REVIEW_20261023.md` C-02【高】
HEAD: `fd9a1d0`（作業ツリー）／実データは読み取りのみ

## 1. 仕様根拠（変更前の意図の確認）

`week_return` が「その週の変化率」であるべき根拠:

1. **列名**が `week_return`（cumulative / since_inception ではない）。
2. **同じ dict の他の列が週スコープ**: `decisions` / `backfill_days` / `mtm_days` /
   `halt_check_days` / `runs_completed` はすべて `window_start..week_end`
   （`week_end - 6日`〜`week_end`）で絞られている。`week_return` だけが
   inception 起点だった。
3. **関数の位置づけ**: `weekly_summary` の docstring は "Weekly summary row
   (M8/spec §7)"、`docs/DEMO_LOOP_V1_OPS.md` は週次レポート（月曜）と明記。
   週次 1 行に載る「リターン」は当該週の騰落を指す。
4. **導入コミット** `5de9bd7`（demo-loop v1.1）では `week_return` に専用の式も
   コメントも無く、`state["equity"] - 1.0` をそのまま流用していた。仕様文書
   （Notion 正本）に列定義の記述は無く、コード側にも「累積」と読める記述は無い。
   つまり「累積にする」意図の根拠は存在しない。
5. カード（t_5316cde2）のオペレーター追記でも「修正時は『単週の変化率』を返すこと」
   と確定。

**選択肢**（仕様が不明な場合に提示する想定だったもの）:

- (A) 現状の値を「累積」として残し、列名を `cum_return` に改名して週次値を別名で
  追加する → 意味は正しくなるが、同名 `week_return` の履歴が消える。
- (B) `week_return` を単週の変化率に直し、累積は `cum_return` として併記する
  → 名前と意味が一致し、旧行が持っていた情報も失わない。

**(B) を採用**。追記専用ファイルの旧行は書き換えないため、行の `schema` を
`demo-loop-summary-v1.6` に上げ、旧行（`...-v1.2`）の `week_return` は累積であると
`docs/DEMO_LOOP_V1_OPS.md` に明記した。

## 2. 変更内容（最小）

- `_week_return(marks, window_start, week_end)` を追加（`_mtm_marks` の隣）。
  - 終点 = **窓内の最後の mark**（`week_end` より後の mark は使わない）
  - 基準 = **窓より前の最後の mark**（前回レポートの終値）。窓より前に mark が
    無い帳簿のみ `inception`（= 1.0）
  - 窓内に mark が無い / 基準が 0 以下 → `None` + 理由（0 で埋めない）
- `weekly_summary` の book dict:
  - `week_return` を新定義に差し替え
  - `week_return_base` / `week_return_as_of` / `week_return_reason` を追加（窓の検証用）
  - `cum_return`（= `state["equity"] - 1.0`、旧 `week_return` の値）を追加
  - 行の `schema` を `demo-loop-summary-v1.6` に更新
- ドキュメント: `docs/DEMO_LOOP_V1_OPS.md` §2 に定義と実測を追記、
  `paperloop.py` 冒頭に v1.6 の説明を追記。

diff（本体）:

```
-            "week_return": state["equity"] - 1.0,
+            "week_return": week_return,
+            "week_return_base": week_base,
+            "week_return_as_of": week_as_of,
+            "week_return_reason": week_reason,
+            "cum_return": state["equity"] - 1.0,
```

`C-01`（benchmark の再売却）/`C-03`（shortfall の窓）/`C-04`（mtm の欠測価格）には
触れていない。**同じ作業ツリーには C-01 の未コミット変更（t_cad2ff49 由来）が
同居している**が、本修正の差分は上記の hunk のみ。

## 3. 実測（実ジャーナル・読み取りのみ）

`data/demo-loop/journal.jsonl` 267 行（`sha256 5b746fdc…ef859`）、
`week_end=2026-10-26`（窓 2026-10-20..10-26）:

| book | 旧 `week_return`（累積） | 新 `week_return`（単週） | 差 | base / as_of |
|---|---|---|---|---|
| A | -3.3447% | **-0.8421%** | -2.5026pp | 2026-10-19 / 2026-10-23 |
| CRYPTO_1N | -3.5442% | **-0.8651%** | -2.6790pp | 2026-10-19 / 2026-10-23 |
| C | +0.6344% | +0.6344% | 0.0000pp | 2026-10-19 / 2026-10-23 |
| EQUITY_EW | +0.0000% | +0.0000% | 0.0000pp | inception / 2026-10-23 |

A の -0.8421% はレビュー C-02 の独立計算（0.9747616 → 0.9665532）と一致。
`week_end=2026-10-19`（窓 10-13..10-19）では A は旧 -3.3447% → 新 -3.3127%
（その時点では帳簿の全履歴がほぼ窓内なので差が小さい＝累積と単週が近い）。

既存の週次行（`weekly_summary.jsonl` 16 行、`sha256 48d039e4…14ffd`）は不変。
同ファイルの `week_end=2026-10-26` 3 行は今も旧意味の
`week_return=-0.0334468…`（A）を保持している（書き換えていない）。

## 4. 実測（同一の合成ジャーナル・3週間）

`data/` とは別の scratch に 3 週間（+2.0% / +2.94% / -1.9%）の日次 mtm を作り、
同じ入力で旧式（累積）と新式（単週）を並べた:

| week_end | 旧 `week_return` | 新 `week_return` | base |
|---|---|---|---|
| 2026-10-11 | +3.0000% | **+2.0000%** | inception |
| 2026-10-18 | +3.0000% | **+2.9412%** | 2026-10-11 |
| 2026-10-25 | +3.0000% | **-1.9048%** | 2026-10-18 |

旧式は 3 週とも同じ +3.0000%（＝累積）を返し、下げた週も +3.0% と表示していた。
新式は各週の騰落（+2.00% / +2.94% / -1.90%）を返す。

## 5. 回帰テスト

`tests/test_paperloop.py::test_week_return_is_the_weeks_change_not_the_cumulative_return`

- 窓の外の mark を基準に、窓内の最後の mark までの変化率になること
- `week_end` より後の mark を使わないこと（追記専用行に未来の mark を混ぜない）
- 窓より前に mark が無い帳簿は `inception` 基準
- 窓内に mark が無い週は `week_return=None` + `week_return_reason="no_mark_in_window"`
- 旧式の値が `cum_return` に残ること（20% vs 5% の差を明示的に assert）

## 6. 表示経路

`week_return` を読む panel / CLI / web は存在しない（`grep -rn week_return` は
`paperloop.py` と docs のみ）。表示名の変更が必要な箇所は無い。週次行をそのまま
読む利用者向けに、`schema` の版と docs で新旧の意味を区別できるようにした。
