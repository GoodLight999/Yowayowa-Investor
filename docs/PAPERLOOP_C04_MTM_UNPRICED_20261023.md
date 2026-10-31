# PAPERLOOP C-04 修正: mtm の欠測価格を「評価不能」として記録する（2026-10-23）

対象: `src/yowayowa/paperloop.py` の `run_daily_mtm`
出典: `docs/PAPERLOOP_MONEY_REVIEW_20261023.md` C-04【高】
基点: `fd9a1d0`（作業ツリー。C-01〜C-03 の未コミット変更を含むツリーをそのまま起点にした）

## 1. 原因（2 経路・どちらも実測で再現）

旧コード（`run_daily_mtm`）:

```python
prices[symbol] = None          # 確定足が 1 本も無い保有銘柄
...
units_value = sum(units * prices.get(asset, 0.0) for asset, units in state["units"].items())
equity = state["cash"] + units_value
```

- (a) **停止**: 保有銘柄に確定足が無いと `prices[asset] = None` → `float * None` で
  `TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'`。
  `run_daily_mtm` はブック順に書くため、**先に処理したブックの mtm 行だけが残り**、
  run-end 行・anchor も書かれない（日次が半端終了）。
- (b) **0 評価**: `prices.get(asset, 0.0)` の既定値。保有銘柄が現在の `universe` に
  無い（universe 変更後・改名）と、その建玉が **0 円として消える**。`equity` が
  `(1-w)` 倍になり、見かけの DD が `-w` になる（w=25% なら ¥2,500,000 相当の架空損失）。
  AGENTS.md 原則 2「欠測を 0 にしない」への直接違反。

## 2. 修正方針（選択肢と採用理由）

- (A) 欠測時は**前日値を流用**して mtm 行を書く（equity 連鎖は連続）。
- (B) 欠測時は**評価不能として明示**し、mtm 行を書かない（equity 連鎖はその日が穴）。

**(B) を採用**。理由:

1. 「0 を入れない」だけでなく「いつの価格で評価したか」を偽らない。流用は
   `price_bar_date` が古いまま equity だけ現在値の列に載るため、消費側が
   「その日の評価」と誤読する余地が残る。
2. 既存の消費者（`paperloop_panel` は `mtm_date <= day` の最新行、`rebuild_state` は
   最新 mtm）は**行が無い日を前方補完する既存挙動**を持つ。行を作らなければ
   その挙動のまま＝新しい意味を持ち込まない。
3. 復旧後に同じ日を再実行すれば本物の mtm 行が書ける（後述の冪等性）。

併せて (b) は「0 で埋める」のをやめ、**universe 外の保有もストアから価格を引いて評価**する
（正しい equity。ここは流用ではなく実価格）。

### 2-1. 台帳への記録（評価不能の明示）

`event="mtm_unpriced"`（1 ブック 1 日 1 行、冪等）:

```
book, mtm_date, recorded_at, reason="unpriced_positions",
unpriced_assets=["BTC"], price_bar_date, prices, units, cash
```

- `equity` は**書かない**（評価不能なので値を持たせない。0 も入れない）。
- `_KNOWN_JOURNAL_EVENTS` に `mtm_unpriced` を追加。M6（zero-order paper）は
  「注文経路の混入」を見る検査で、この行は注文も金額も持たない。
- 同日再実行: 既に `mtm_unpriced` があれば追記しない。**テープ復旧後の再実行では
  本物の `mtm` 行が書ける**（skip 行は履歴として残る）。
- CLI `mtm` は `mtm_rows` に加えて `mtm_unpriced`（book 一覧）を返す。終了コードは
  0 のまま（`ops/demo-loop/daily.sh` は `set -e` で decide→anchor まで走る必要があり、
  ここで落とすと C-04(a) の半端終了を別の形で再生産する）。

## 3. diff（本体のみ）

```diff
-        units_value = sum(units * prices.get(asset, 0.0) for asset, units in state["units"].items())
+        held = {asset: units for asset, units in state["units"].items() if units}
+        valued: dict[str, float] = {}
+        unpriced: list[str] = []
+        for asset in sorted(held):
+            if asset in prices:
+                price = prices[asset]
+            else:
+                # v1.8 (C-04): a holding the universe no longer lists is still
+                # a position — price it, never default it to 0.
+                bar = _latest_finalized_close(root, asset, mtm_day)
+                price = bar[1] if bar is not None else None
+            if price is None:
+                unpriced.append(asset)
+            else:
+                valued[asset] = price
+        if unpriced:
+            # v1.8 (C-04): unvaluable equity is recorded as such, not stored.
+            if not unpriced_recorded:
+                _append_row(root, { "event": "mtm_unpriced", ... })
+            continue  # no fabricated equity for this book/day
+        units_value = sum(units * valued[asset] for asset, units in held.items())
         equity = state["cash"] + units_value
```

追加した純関数: `_latest_finalized_close(root, symbol, mtm_day)`（universe ループと
保有の評価で共用）、`_unpriced_mtm_books(root, mtm_day)`（CLI 報告用）。

## 4. 回帰テスト（1 本・pass）

`tests/test_paperloop.py::test_C04_unpriced_mtm_is_recorded_and_never_zero_filled`

- 欠測 1 日（BTC の確定足が無い）: A / CRYPTO_1N は `mtm_unpriced`（`unpriced_assets=["BTC"]`）、
  C / EQUITY_EW は通常どおり `mtm`。**equity 0 の mtm 行は 0 件**、例外なし。
- 連続欠測: 2 日目も独立に記録される（各日 1 行）。
- 同一日再実行: 追記 0 行・`mtm_unpriced` の重複なし。
- 復旧: 本物の `mtm` 行が書け、`equity == cash + units(BTC)×BTC + units(ETH)×ETH`。
- universe 外の保有: `CRYPTO_UNIVERSE=("BTC",)` に縮めても ETH が 0 ではなく実価格で入る
  （行の `prices` フィールドは従来どおり universe のみ＝既存行の形は不変）。

```
.venv/bin/pytest tests/test_paperloop.py -q -k C04   ->  1 passed (9.4s)
.venv/bin/pytest tests/test_paperloop.py -q          ->  37 passed, exit 0
PYTHONPATH=src .venv/bin/python -m pytest tests/test_paperloop_bench_hold.py \
  tests/test_paperloop_panel_units.py tests/test_paperloop_v12.py \
  tests/test_paperloop_verify_units.py tests/test_cusum_watch.py -q
                                                     ->  128 passed, exit 0
```

## 5. 前後実測

### 5-1. 合成入力（`probe_c04.py before|after`、同一シナリオ）

| 観点 | BEFORE | AFTER |
|---|---|---|
| universe 外の保有 ETH（0.0083208333 × 52.175824 = 0.43414634） | **0.0 として計上** | 0.43414634 として計上 |
| その行の equity | 0.51581743（cash+BTC のみ） | 0.94996376 |
| 欠落額 | 0.43414634（equity の 45.7%） | 0 |
| BTC テープ欠測時の mtm | `TypeError`、C の mtm 1 行だけ書けて停止 | 例外なし（C の mtm＋A の `mtm_unpriced`） |
| 0 評価の保有 | 1 件 | 0 件 |
| equity <= 0 の mtm 行 | 0 件 | 0 件 |

### 5-2. 実データコピー（`data/` の rsync コピー。元データは読み取りのみ）

- **健全データ**（2026-10-24 を mtm、4 ブック）: BEFORE と AFTER が追記した 4 行は
  **row_hash まで完全一致**（`code_rev` は scratch コピーだと `unknown` になるため
  リポジトリの値に正規化して比較）。equity / peak / dd / price_bar_date /
  cum_cost / equity_gross すべて同一 → **既存行は不変**。
- **BTC テープ削除**（2026-10-25 を mtm）:
  - BEFORE: `TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'`、追記 0 行（run 停止）。
  - AFTER: 追記 4 行 = `mtm` C / `mtm` EQUITY_EW ＋ `mtm_unpriced` A / `mtm_unpriced` CRYPTO_1N
    （`unpriced_assets=["BTC"]`）。0 評価の保有 0 件、equity <= 0 の行 0 件。
  - `paperloop_verify`（2026-10-20..26, AFTER コピー）: `chain_ok: true` / `exit_code: 0` / 全チェック failures 空。
  - `paperloop panel`（同コピー）: rc=0（`mtm` 行が無い日があっても落ちない）。

### 5-3. 0 埋めの実測

BEFORE 1 件（ETH 0.43414634 → 0）／ AFTER 0 件。

## 6. この修正の範囲外（記録）

- 評価不能日は `mtm` 行が無いため equity 連鎖に穴が開く。panel / weekly は最新行で
  代替する既存挙動のまま、週次・マイルストーンは `mtm_days` が減る（未評価＝fail closed）。
- 欠測日は `resolve_fills` が使う `rebuild_state` の equity が直前 mtm のまま（古い）。
  C-04 の範囲外。必要なら別カード。
- C-01〜C-03 の未コミット変更には触れていない（着手時と終了時の `git diff` で確認。
  `docs/AGENT_MESSAGE_BOARD.md` の同時編集注意に従い、自分の hunk を逆適用すると
  着手時スナップショットと一致することを実測）。

## 7. 証跡（同時編集の分離確認）

- 着手時の `git diff -- src/yowayowa/paperloop.py`: 311 行 /
  sha256 `0ca295fda502d2d19cd15cb22a520cfd17158796767edff8820a6063f64c78f1`
- 終了時の同 diff: 491 行 /
  sha256 `1e5c6018a3731231e45bc3789ca2e45e28104861d5e88165a3b6de69779c82c6`
- **分離チェック（実測）**: 終了時のファイルに「自分の差分（189 行の unified diff）」を
  逆適用した結果が、着手時スナップショットとバイト一致した。すなわち C-01〜C-03 の
  hunk は 1 行も巻き戻っていない（`paperloop.py` の変更は自分の 189 行のみ）。
- 再現用スクラッチ（レビュー時に再実行可）:
  `/root/.hermes/profiles/worker-goat/cache/scratch/c04/`
  - `probe_c04.py before|after`（合成入力の前後比較）
  - `real_compare.py before|after <data_dir> <day> [code_rev]`（実データコピーの前後比較）
  - `prefix_pkg/`（着手時の `paperloop.py` を含む src のコピー。`before` はこれを使う）
- `tests/test_paperloop.py`（`git diff --numstat` → 297/0）と
  `docs/DEMO_LOOP_V1_OPS.md`（34/0）は **追加のみ・削除 0 行**（他カードの追記を消していない）。
