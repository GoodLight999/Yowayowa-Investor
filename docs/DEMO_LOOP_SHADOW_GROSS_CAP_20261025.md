# 改善案1（グロス上限50%）の検証: 影の帳簿 A_HALF の実装・事前登録・事前検算

- 作成: 2026-10-25 / worker-goat（kanban `t_52c4aa20`、親 `t_76501096`）
- 対象: `/root/current-work/yowayowa-investor`（`a05d24890487+dirty`）
- 参照: `docs/DEMO_LOOP_LOSS_ATTRIBUTION_20261025.md` §2（金額分解）・§3（主因）・§6（改善案1）・§7（投入条件）
- 位置づけ: **追加のみ**。既存帳簿 A・ベンチマーク・凍結パラメータ・判断ロジックは変更していない。実資金 0 円、実注文 0 件、API 実注文 0 件。

## 0. 結論（3行）

1. **影の帳簿 `A_HALF` を作った。** A と同一のシグナル・同一の凍結パラメータ（SMA200・15bps・判断経路）で、目標ウェイトだけを `1/len(active)` → `0.5/len(active)` にした。A の `spec_hash`・パラメータ・判断ロジックは 1 バイトも変えていない（UT が凍結値 `b71b70fc…` の一致で固定）。
2. **同じ16日間の事前検算（A の日次リターンを 0.5 倍した合成チェーン）: equity 0.978641 / 最大DD -2.534% / DD比 0.484。** レポート §6 の見積り（0.97840 / -2.54% / 0.486）とほぼ一致し、**DD比は §4 の 0.60 基準を満たす**。改善幅は実測 +2.177pp。
3. **ただしこれは「A_HALF の実運用値」ではない。** 事前検算は A の軌跡の線形変換であり、判定は前方 20 営業日（＋ design §4 の 12 週）で行う。**現時点の達成日数は 0/20 日・0/84 日＝未判定。**

## 1. 実装（追加のみ。既存の挙動は不変）

### 1.1 変更点

| ファイル | 変更 | 内容 |
|---|---|---|
| `src/yowayowa/paperloop.py` | 追加 | `BOOK_A_HALF = "A_HALF"` / `SHADOW_GROSS_CAP = 0.5`、`STRATEGY_BOOKS` と `BOOKS_ENABLED`（末尾に追加）、`BOOK_BENCHMARK[A_HALF] = CRYPTO_1N`、`BOOK_SPEC_HASHES[A_HALF]`、`_engine_target` の 50% 上限適用、prereg の hypothesis/judgment_rule/registered_by |
| `src/yowayowa/paperloop.py` | 整理 | 帳簿の資産クラス判定を `CRYPTO_BOOKS` / `EQUITY_BOOKS` / `STRATEGY_BOOKS` の 1 箇所に集約（従来は `(S01_BOOK, BENCH_CRYPTO_BOOK)` の直書きが 15 箇所。新帳簿が equity 側と誤読される事故を構造的に防ぐ） |
| `src/yowayowa/paperloop_panel.py` | 整理 | パネルの資産クラス判定を `CRYPTO_BOOKS` に |
| `src/yowayowa/paperloop_verify.py` | 整理 | R2 の universe 判定を `CRYPTO_BOOKS` に |
| `src/yowayowa/demo_loop.py` | 追加 | 再エクスポート（`BOOK_A_HALF` / `SHADOW_GROSS_CAP`） |
| `tests/test_paperloop_shadow_gross_cap.py` | 新規 | 本カードの UT 8 本（§3） |
| `tests/test_paperloop.py` | 更新 | 帳簿集合を直書きしていた 8 テストを「有効な帳簿集合から導出」に変更（§3.2） |
| `tests/test_paperloop_v12.py` | 更新 | B1 の約定数 1 箇所を帳簿集合から導出に（§3.2） |

### 1.2 目標ウェイトの作り方（カード指定の `0.5 / len(active)`）

A と同じ `crypto_trend_dual_ma` 分岐（`services/backtest.py:421-433`）の戻り値を、`paperloop._engine_target` の中で 0.5 倍する。

```python
target = _target_weights(strategy, rows_by_symbol, decision_date)
if book == BOOK_A_HALF:
    target = {symbol: weight * SHADOW_GROSS_CAP for symbol, weight in target.items()}
```

- 戻り値は `1/len(active)`（active のみ）と `0.0`（ゲート不成立）なので、0.5 倍すると **`0.5/len(active)` と `0.0`** になる（＝カードの式と同値）。全ゲート不成立の全決済（0.0）も 0.0 のまま。
- **`services/backtest.py` は触っていない。** バックテストや A の経路が同じ上限を拾う事故を作らないため、影の帳簿の定義は paperloop 側 1 箇所に閉じた。
- `weighting` を `inverse_volatility` にする案（レポート §6 案2）と同じく、**この 1 行は新機構ではない**。

### 1.3 識別子（A と混ざらないこと）

| 帳簿 | `spec_hash` の元文字列 |
|---|---|
| A | `primary:s01:crypto_trend_dual_ma:BTCETH:200:daily:0.0015`（**不変**） |
| A_HALF | `shadow:s01h:crypto_trend_dual_ma:BTCETH:200:daily:0.0015:gross0.5` |

`_sha256` 後の値: A = `b71b70fc2e9df13c68d934fdb5cf66020c7b51c52b31ab3bc99cd865b9eadf17`（既存 prereg 行と一致 = 不変の証明）、A_HALF = 別値。UT が両方を固定している。

## 2. 事前登録（prereg 1 行）

### 2.1 追記される行（コード経路が生成した実物）

`ensure_prereg` → `_ensure_regime_prereg` の順で、**次回 decide の冒頭**（判断行を書く前）に 1 行追記される。実データのコピー上で実際に生成した行:

```json
{"book_id":"A_HALF","spec_hash":"155d353fc335401ac4bce9c41118b5bfabe365a289cbbabe171d858f22856a76","hypothesis":"A 50% gross cap on S-01 lowers the drawdown vs 1/N","judgment_rule":"design §4 12週 + 20営業日連続: 費用控除後 equity>=1.0 / 最大DD<=3.0% / DD比<=0.6","registered_at":"<decide 実行時刻 tz-aware UTC>","registered_by":"worker-goat:t_52c4aa20","prev_hash":"<直前行の row_hash>","row_hash":"<chain>"}
```

### 2.2 live の `prereg.jsonl` への追記は「次回 decide で自動」にした（意図的な判断）

- いま手で追記すると、**レビューで `BOOK_SPEC_HASHES[A_HALF]` の文字列が変わった瞬間に `check_prereg` が `ChainError` を投げ、live の decide が全帳簿ごと止まる**（prereg は追記のみで修正できず、コード側を合わせるしかない）。未承認の実装で台帳を先に固定するのは危険。
- 追記のタイミングは `run_daily_decide` の先頭で `ensure_prereg` が走る＝**A_HALF の最初の判断行より必ず前**。順序保証はコードで担保されている（UT `test_A_half_is_preregistered_once_behind_A` が「1回だけ・A の後ろ・チェーン整合・2回目は追記しない」を検証）。
- 本カードでの実データ書き込みは **0 行**（`prereg.jsonl` の sha256 `d18a1dd0…` は作業前後で同一）。

## 3. 受入（UT）

### 3.1 追加した UT（`tests/test_paperloop_shadow_gross_cap.py`、8 本）

| テスト | 何を固定するか |
|---|---|
| `test_A_half_targets_are_half_of_A_when_both_assets_are_active` | 両資産が SMA200 上 → A = 0.5/0.5、A_HALF = **0.25/0.25**、合計 0.5（残りは現金） |
| `test_A_half_is_0_5_over_len_active_with_one_asset_active` | 片方だけ active → A = 1.0、A_HALF = **0.5**（`0.5/len(active)` の確認） |
| `test_A_half_liquidates_with_A_when_every_gate_fails` | 全ゲート不成立 → 両帳簿とも 0.0（0.0 は 0.0 のまま） |
| `test_book_A_spec_and_registration_are_unchanged` | A の `spec_hash` が凍結値 `b71b70fc…` と一致、`BOOKS_ENABLED` の先頭 4 冊が不変、A_HALF は暗号資産クラス・ベンチは CRYPTO_1N |
| `test_A_half_trades_half_of_A_in_the_same_run` | 同一 run で A_HALF の判断 2 本が `target_weight=0.25`、約定 notional が A のちょうど 0.5 倍、`weight_after=0.25`、現金 0.75 |
| `test_A_half_marks_recompute_and_verify_pass` | `mark.recompute_match=true`、mtm は 1 日 1 行、**verifier L1-L14 全通過（R1/R2 含む）** |
| `test_A_half_is_preregistered_once_behind_A` | prereg 行が 1 本だけ・A の後ろ・`prev_hash`→`row_hash` 連鎖・2 回目の decide で重複しない・A の行は不変 |
| `test_real_journal_copy_next_decide_sizes_the_shadow_book_half` | **live journal のコピー**（読み取りのみ、sha256 前後一致）で次回 decide を実行 → A_HALF = A × 0.5、prereg 追記、verifier 全通過 |

### 3.2 既存テストの更新（帳簿が 5 冊になったことによる期待値の追随）

`tests/test_paperloop.py` の 8 テストと `tests/test_paperloop_v12.py` の 1 テストで、直書きだった「A + ベンチマーク」を有効な帳簿集合から導出する形に変えた（新帳簿が増えても同じ種類の陳腐化が起きないようにするため）。

- `test_decide_fill_idempotency_and_mtm` / `test_commit_before_fill_discards_early_bars` / `test_C6_superseded_pending` / `test_C04_unpriced_mtm_is_recorded_and_never_zero_filled`: 約定数・判断数を `2 * len(pl.CRYPTO_BOOKS)` に、資産クラス判定を `pl.CRYPTO_BOOKS` に、A_HALF の約定 notional は 0.25 として検証。
- `test_book_c_weekly_cadence_gate`: 月曜の判断数を帳簿集合から導出（コメントの内訳を 6 crypto + 4 stock + 4 EW に修正）。
- `test_dd20_is_warning_not_stop`: ここは**期待値ではなく事実が変わった**。同じ急落で A と 1/N は -20% 警告線を割るが、**A_HALF は半分の深さなので割らない**。よって「full-gross は warn / A_HALF は warn なし」を分けて固定し、A_HALF の DD が full-gross の 0.4〜0.6 倍であることを関係式で固定した（＝グロス半減が DD を半減させることの最初の実測証拠）。

### 3.3 pytest 実行結果

対象を「本カードが追加・更新したテスト」に絞って実行した（他の Bot が同居する VPS で全体スイートを回すと 1 本 20〜60 秒 × 150 本超になるため。§6.3 に全体実行の手順を残す）。

| 実行 | 対象 | 結果 |
|---|---|---|
| BATCH1 | `tests/test_paperloop_shadow_gross_cap.py`（新規 8 本、live journal コピーでの通しを含む） | **8 passed / exit 0** |
| BATCH2 | `tests/test_paperloop.py` の更新 8 本（decide_fill_idempotency, commit_before_fill, mark_signed_shortfall, dd20, C6, book_c_weekly_cadence, milestone_fires_once, C04_unpriced） | 7 passed / 1 failed → **原因は他カードの欠陥 1 行（§6.2）。その 1 行をメモリ上で補うと 8 passed / exit 0** |
| BATCH3 | `tests/test_paperloop_v12.py::test_B1_no_duplicate_fill_or_reject` / `::test_verify_L1_L14_pass` | **2 passed / exit 0** |
| BATCH4 | `tests/test_cusum_watch.py::test_registration_row_chain_and_verifier_accepts` | **1 passed / exit 0** |

コマンド（再現用・そのまま実行できる）:

```bash
cd /root/current-work/yowayowa-investor
PYTHONPATH=.:src .venv/bin/pytest -q tests/test_paperloop_shadow_gross_cap.py
PYTHONPATH=.:src .venv/bin/pytest -q tests/test_paperloop.py \
  -k "decide_fill_idempotency or commit_before_fill or mark_signed_shortfall or dd20_is_warning \
      or C6_superseded or book_c_weekly_cadence or milestone_judgment_fires_once or C04_unpriced"
PYTHONPATH=.:src .venv/bin/pytest -q tests/test_paperloop_v12.py -k "B1_no_duplicate or verify_L1_L14_pass"
PYTHONPATH=.:src .venv/bin/pytest -q tests/test_cusum_watch.py -k registration_row_chain
```


## 4. 過去データでの事前検算（同一期間 2026-10-10〜10-25）

### 4.1 何を計算したか

A の日次 equity 連鎖（`journal.jsonl` の `mtm` 行、同日重複は最後の行）から日次リターン `r_t` を取り、**同じ日次リターンを 0.5 倍した合成チェーン**を作る。グロス半減はポジション・コスト・リバランス損益のすべてを比例縮小するので、一次近似として A_HALF の軌跡になる（相違は複利の二次項のみ）。

```bash
cd /root/current-work/yowayowa-investor/data/demo-loop
jq -s '
 def book($b): [.[]|select(.event=="mtm" and .book==$b)] | group_by(.mtm_date) | map(.[-1]) | sort_by(.mtm_date);
 (book("A")) as $a | (book("CRYPTO_1N")) as $bench
 | (reduce range(1; $a|length) as $i ({eq:1.0, peak:1.0, dd:0.0};
      ($a[$i].equity / $a[$i-1].equity - 1) as $ret
      | .eq = (.eq * (1 + 0.5*$ret))
      | .peak = (if .eq > .peak then .eq else .peak end)
      | .dd = ([.dd, (.eq/.peak - 1)] | min))) as $half
 | (reduce range(1; $bench|length) as $i ({eq:1.0, peak:1.0, dd:0.0};
      .eq = $bench[$i].equity
      | .peak = (if .eq > .peak then .eq else .peak end)
      | .dd = ([.dd, (.eq/.peak - 1)] | min))) as $bd
 | {days: ($a|length), half_equity: $half.eq, half_maxdd: $half.dd,
    bench_maxdd: $bd.dd, dd_ratio: ($half.dd / $bd.dd)}
' journal.jsonl
```

### 4.2 実測値

| 指標 | A（実測） | A_HALF（合成・実測計算） | レポート §6/§7 の見積り | 差 |
|---|---|---|---|---|
| 最終 equity | 0.956872 | **0.978641** | 0.97840 | +0.00024 |
| 累積リターン | -4.3128% | **-2.1359%** | -2.16pp | +0.02pp |
| 最大DD | -5.0872% | **-2.5342%** | -2.54% | +0.006pp |
| ベンチ（1/N）最大DD | -5.2355% | -5.2355% | -5.236% | — |
| DD比（対 1/N） | 0.972 | **0.484** | 0.486（§6）/ 0.49（§7） | -0.002 |
| 改善幅（A 比） | — | **+2.177pp** | +2.16pp | +0.017pp |

- 線形見積り（`1 - 4.3128/2 = 0.978436`）と合成チェーン（0.978641）の差 **+0.0205pp** は、日次リターンを半分にした複利の二次項。見積りより**わずかに良い**方向。
- 上昇局面の取り分: 上昇6日の合計 **+6.977pp → +3.488pp**（半減）。下落9日は -11.206pp → -5.603pp。差し引きで改善。
- コストも半減: 明示コスト 0.196pp → 約 0.098pp（notional が半分になるため）。
- **DD比 0.484 は design §4 の 12週基準（<= 0.60）を満たす**。A（0.972）が構造的に未達だった点が、この案で初めて基準内に入る。

### 4.3 この数字の限界（過大に読まないための明記）

1. **A の軌跡の線形変換であって、A_HALF の実運用値ではない。** A_HALF 自身の約定価格・スリッページ・リバランス経路は、実際に走ってみるまで確定しない（比例縮小なので一次近似の誤差は小さいが、0 ではない）。
2. **期間が 16 日しかない。** 同じ相場（下落8日・上昇6日）に対する結果であり、DD 比の安定性は示していない。
3. **判定は前方で行う**（§5）。この表は「見積りが外れていないこと」の確認までで、採用判断の根拠にはしない。

## 5. 判定（事前登録ルールに対する現在地）

| 基準（prereg の `judgment_rule`） | 必要 | 実測 | 判定 |
|---|---|---|---|
| design §4 12週: DD比 <= 0.60 / 累積 >= -10% / 切替回数 <= 期待の2倍 / ループ不具合HALT 0 | 84日 | **0日**（A_HALF の判断行は次回 decide から） | **未判定** |
| 20営業日連続: 費用控除後 equity >= 1.000 / 最大DD <= 3.00% / DD比 <= 0.60 | 20営業日 | **0日** | **未判定** |
| 事前検算（§4、参考値） | — | equity 0.9786 / DD -2.534% / DD比 0.484 | 参考: 3 指標すべて基準内 |

- 20営業日目の予定: A_HALF の初回判断が 2026-10-26（月）、約 20 営業日後は **2026-11-20 前後**。
- 12週（84日）到達は **2027-01-18 前後**。
- **未判定である以上、実資金は引き続き 0 円**（レポート §7 の条件 1・2 が未達）。

## 6. 実行した検証と結果

### 6.1 本カードの成果物の検証

- **live journal に対する検証器の実測（読み取りのみ・書き込みなし）**: `python -m yowayowa.paperloop_verify --data-dir ./data --from 2026-10-10 --to 2026-10-25` → **exit 0 / L1-L14 全 ok / chain_ok true / anchors_ok true / R1 36/36 / R2 36/36 / failed_checks [] / panel_rows 68**（帳簿を 5 冊にした状態で、既存の 4 冊ぶんの履歴が壊れていないことの実測）。A_HALF の行はまだ 0 行なので、これは「追加が既存を壊していない」ことの証明。
- 追加 UT 8 本: **8 passed**（§3.3 BATCH1）。うち 1 本は **live journal のコピーに対して次回 decide を実際に走らせる**通し検証で、A の `target_weight` と A_HALF の関係（0.5 倍）・prereg 追記・verifier L1-L14 全通過・live journal の sha256 不変を同時に確認している。
- 既存テスト更新 8 本: **8 passed**（他カードの欠陥をメモリ上で補った状態。補わない場合は §6.2 の 1 本だけが落ちる）。
- v12 の B1 / L1-L14、cusum の prereg 連鎖: **3 passed**。
- 合計 **19 本すべて green**（1 本は他カード要因のため条件付き）。

### 6.2 他カード由来の既知の失敗（本カードの変更に起因しない。修正はしていない）

`tests/test_paperloop.py::test_milestone_judgment_fires_once_at_deadline` は **帳簿 C の 4 週判定が INSUFFICIENT ではなく FAIL** になる。原因は並走カード `t_d0fbc804`（worker-flash・レビュー中）が追加した新しい journal event **`fill_wait` が `_KNOWN_JOURNAL_EVENTS` に未登録**なこと。

実測（読み取りのみ）:
- C の `criteria.orders.value = 196`、`failed_criteria = ["orders"]`。
- フィクスチャ journal の C の `fill_wait` 行数（window 内）= **196**（`jq` で一致確認）。C の他の event は decision 16 / halt_check 4 / mtm 28 / fill_rejected 4 ですべて既知。
- この 1 行（`"fill_wait"` を既知集合に追加）を**メモリ上だけで**当てると当該テストは PASS（ファイル・journal は無変更）。→ 原因はこの 1 点。
- 本カードの追加行はすべて `book="A_HALF"` で、判定の book フィルタにより C の集計に入らない（だから 196 は本カードの変更で動かない）。

対応: 修正は持ち主のカードへ返した（`t_d0fbc804` へ実測付きコメント 1895）。本カードでは 1 行も触っていない（重複修正の回避）。

### 6.3 全体スイートを実行していない理由と、実行する場合の手順

- VPS は他の Bot と同居（load average 3 前後）で、paperloop 系テストは 1 本 20〜60 秒。全体（150 本超）は 1〜2 時間かかり、`DEMO_LOOP_V1_OPS.md` の「VPS で重い作業をしない」に反する。よって**本カードの変更が触る範囲**に絞って実行した。
- 全体実行は次のどちらかで:
  - CI（GitHub Actions、`.github/workflows/ci.yml`）に任せる。
  - TSUKUMO-KUN 上で `PYTHONPATH=.:src .venv/bin/pytest -q tests/`。
- なお §6.2 の 1 件は他カードの未確定修正が入った状態の tree で落ちるので、全体実行の結果を読むときは `fill_wait` の登録有無を先に確認すること。


## 7. 制約の遵守（自己申告ではなく実測）

- **実注文 0 件 / API 実注文 0 件**: 本カードは発注経路を持たないコードの追加と UT のみ。`data/demo-loop` への書き込みは 0 行（`journal.jsonl` sha256 `81c7b495…` / `prereg.jsonl` sha256 `d18a1dd0…` が作業前後で一致）。
- **ジャーナルは追記のみ**: 既存行の書き換え・削除 0 件。
- **凍結パラメータ変更 0**: SMA200・15bps・停止線・C の稼働週はいずれも未変更（`git diff` に現れない）。
- **既存 A とベンチマークの挙動は不変**: A の `spec_hash`・判断ロジック・重みは同じ。UT が凍結 `spec_hash` の一致で固定。ベンチマークは `_benchmark_weights` 経路のまま。
- **VPS で重い再計算はしていない**: 過去データの検算は jq の集計のみ（§4）。pytest は paperloop 系に限定して実行（§6）。

## 8. 残るリスク

1. **live の decide が 5 冊になる**ぶん 1 日の処理時間が約 25% 増える（crypto 3 冊 × 2 資産）。現行の所要時間（数十秒）では問題にならないが、次回の run 行で実測する。
2. **A と A_HALF の判断行が同じ `decision_date` で並ぶ**ため、ログを目視するときに混同しやすい。`book` 列で必ず分ける（本ドキュメントの表もそうしている）。
3. 20営業日判定の途中で A_HALF を止めたくなった場合、`BOOKS_ENABLED` から外すと mtm が途切れ、DD 比の連続性が壊れる。**止める場合も「外す」ではなく「判断を止めて保有を維持」**にするのが筋（次に触る人への申し送り）。
