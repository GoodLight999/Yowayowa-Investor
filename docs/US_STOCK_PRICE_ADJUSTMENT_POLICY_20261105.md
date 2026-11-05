# 米国株の価格調整方式 — 一本化の決定記録（案 B 採用）

- タスク: `t_4cfb36d0`（親: `t_2fc44a51` の監査結果）
- 決定日: 2026-11-05
- 決定: **案 B — 取得は `adjustment=raw` のまま、分割補正はバックテストエンジンの
  `data/fundamentals/pbr/split-calendar.json` 適用を唯一の正とする**
- 配当: **組み込まない（プライスリターン）。`adjustment=all` は採用しない**
- 稼働経路（Book C・C2・paperloop_verify）への変更: **なし（コード差分 0）**

---

## 1. 決定とその理由（1段落）

保存データが raw で、`source_url` に `adjustment` パラメータが記録されておらず行に
調整基準（vintage）の列も無い以上、案 A（取得を `splits`/`all` に変更）は「append-only の
JSONL に基準の異なる行が混在する」状態を不可避に作り、しかも Alpaca の `splits` は
取得時点で履歴全体を遡って書き換えるため、新規分割のたびに全 14 銘柄（24,108 行・16 MB）
の再取得と、それ以前に生成した全結果 JSON・稼働帳簿の mark の無効化を強いられる一方、
案 B は保存データを一切変えずに同じ補正結果が得られ（`run_backtest` の後方補正は
分割日当日の open/close 比が期待係数の ±15% 以内にあることを要求するため、既に調整済みの
系列を渡しても二重適用は起きない）、稼働中の Book C / C2 が読むのと同じ raw ストアを
そのまま正として使えるので、再取得コスト 0・稼働経路への影響 0 で済む。残る運用負荷は
「カレンダーの網羅性」だけであり、それは SEC accession 付きで自動生成される既存経路
（`scripts/fetch_sec_pbr.py`）と、本決定で追加した監視 `scripts/check_split_coverage.py`
（取りこぼしと二重補正の両方を検出）で担保する。配当を `all` で混ぜると、この
「取得側かエンジン側か」の一本化が再び崩れ、稼働経路の価格基準まで変わるため、
TR は別レイヤ（配当オーバーレイ）として後日扱う。

---

## 2. 実測した前提（2026-11-05 14:40 JST 時点）

| 項目 | 実測値 | 出所 |
|---|---|---|
| 保存ストア | 14 銘柄 × 1,722 セッション = 24,108 行 / 16 MB | `du -sh data/stock-ohlcv` |
| 保存期間 | 2019-12-30 〜 2026-11-04 | 各 `ohlcv.jsonl` 最終行 |
| 取得パラメータ | `adjustment=raw` | `src/yowayowa/providers/alpaca.py:205` |
| 行に調整基準の記録 | なし（`source_url` に `adjustment` の記載 0 件、vintage 列なし） | `grep -c adjustment data/stock-ohlcv/*/ohlcv.jsonl` = 0 |
| カレンダー | 7 イベント / 10 銘柄（SEC accession 付き） | `data/fundamentals/pbr/split-calendar.json` |
| エンジンの補正条件 | 分割日当日の open 比・close 比が期待係数の ±15% 以内 | `src/yowayowa/services/backtest.py:656-657` |
| 稼働経路の株式ユニバース | SPY / IWM / JNJ / PG（Book C = `paperloop.EQUITY_UNIVERSE`、C2 = `C2_UNIVERSE`） | `src/yowayowa/paperloop.py:349`, `scripts/paper_c2/c2_features.py:23` |

カレンダーに登録されている 7 イベントは AAPL 2020-08-31(4:1)、NVDA 2021-07-20(4:1)、
NVDA 2024-06-10(10:1)、GOOGL 2022-07-18(20:1)、AMZN 2022-06-06(20:1)、
TSLA 2020-08-31(5:1)、TSLA 2022-08-25(3:1)。

### 2.1 案 A を採らなかった実測根拠

1. **基準の混在を検出できない。** 保存行のキーは
   `symbol, interval, currency, provider, source_url, license_class, retrieved_at, as_of,
   open, high, low, close, volume, vwap, trade_count, notes` のみで、`source_url` は
   `...&feed=sip` までで `adjustment` を含まない（ストア全体で `adjustment` の出現 0 件）。
   取得を切り替えると raw 行と調整済み行が同一スキーマで同居し、後から区別できない。
2. **遡及書き換え。** `adjustment=splits` は取得時点までの全分割を反映した系列を返すため、
   新規分割のたびに全履歴の再取得が必要。ストアは追記型で、部分更新を許すと同一銘柄の
   系列内で基準が変わり、偽の断絶（＝偽の分割シグナル）を作る。
3. **稼働経路への波及。** `paperloop._daily_map()`（Book C の mark）と
   `c2_runner` は同じ raw ストアを読む。基準を変えると保有建玉の評価額と C2 の
   gap-watch 基準日が遡って変わる（実弾ではないが、ペーパー成績の連続性が壊れる）。

---

## 3. 二重補正が起きていないことの実測

保存値（raw）と、`run_backtest` を通した後の日次騰落（1 銘柄 buy&hold・コスト 0）を
同じ日について並べた。**保存値が −90% 級でもエンジン出力は ±20% 以内**であり、
補正が 1 回だけ効いている。

| 銘柄 | 分割日 | 比率 | 保存 prev_close → close（raw） | raw 1日% | エンジン 1日% | 判定 |
|---|---|---:|---|---:|---:|---|
| AAPL | 2020-08-31 | 4:1 | 499.23 → 129.04 | −74.15 | **+3.39** | ±20% 以内 |
| NVDA | 2021-07-20 | 4:1 | 751.19 → 186.12 | −75.22 | **−0.89** | ±20% 以内 |
| **NVDA** | **2024-06-10** | **10:1** | **1208.88 → 121.79** | **−89.93** | **+0.75** | **±20% 以内** |
| GOOGL | 2022-07-18 | 20:1 | 2235.55 → 109.03 | −95.12 | **−2.46** | ±20% 以内 |
| **AMZN** | **2022-06-06** | **20:1** | **2447.00 → 124.79** | **−94.90** | **+1.99** | **±20% 以内** |
| TSLA | 2020-08-31 | 5:1 | 2213.40 → 498.32 | −77.49 | **+12.57** | ±20% 以内 |
| TSLA | 2022-08-25 | 3:1 | 891.29 → 296.07 | −66.78 | **−0.35** | ±20% 以内 |

- 二重補正なら NVDA 2024-06-10 は `+0.75% − 90% ≈ −90%`（あるいは +900%）になる。
  実測は **+0.75%**、AMZN は **+1.99%**。二重補正なし。
- エンジンの warning にも各イベントにつき **1 回だけ** `Split adjustment applied ... basis=sec_calendar`
  が出る（P3 再生成 6 戦略 × 7 イベント = 42 件、1 イベント 1 件）。
- 再現手順: `.venv/bin/python scripts/check_split_coverage.py`（監視）と、
  `run_backtest` を単一銘柄 buy&hold で回す計測（本節の表）。

### 3.1 補正の効果（同一データ・同一窓での ON/OFF 比較）

窓を旧 JSON と同じ `2021-01-04..2026-09-28` に固定し、カレンダー適用 ON/OFF だけを
切り替えた（`run_backtest(..., split_calendar={})` が OFF）。差は補正だけに帰属する。

| 戦略 | CAGR OFF | CAGR ON | 差(pp) | MaxDD OFF | MaxDD ON | 補正回数 |
|---|---:|---:|---:|---:|---:|---:|
| momentum_12_1 | 13.72% | 20.24% | +6.52 | −34.64% | −31.13% | 7 |
| low_volatility | 13.54% | 16.85% | +3.31 | −35.41% | −23.63% | 7 |
| mean_reversion_20 | 16.84% | 16.96% | +0.12 | −45.89% | −45.89% | 7 |
| equal_weight | 16.27% | 25.37% | +9.10 | −50.21% | −35.50% | 7 |

`t_2fc44a51` の監査値（momentum 13.72→20.24、EW 16.27→25.37）と一致（独立再現）。

---

## 4. 稼働経路（Book C・C2・paperloop_verify）への影響 = 0

1. **コード差分 0。** `src/yowayowa/paperloop.py`、`src/yowayowa/paperloop_verify.py`、
   `scripts/paper_c2/*` は本タスクで一切変更していない。`paperloop` が
   `services.backtest` から取り込むのは `_target_weights`（重み付け関数）だけで、
   分割補正は `run_backtest` の行構築ループ内に閉じている。
2. **データ差分 0。** 稼働 4 銘柄のストアは本タスク開始前の 2026-11-05 08:30:55〜57 JST
   から更新されておらず（セッション開始 14:26）、`sha256` も不変。
3. **そもそも分割イベントが無い。** SPY / IWM / JNJ / PG は各 1,722 セッションで
   |前日比| ≥ 15% の日が **0 件**（カレンダー登録も SPY は空配列、IWM/JNJ/PG は項目なし）。
   したがって案 B を採っても採らなくても、この 4 銘柄の補正は恒等（何もしない）。
4. 参考: `backtest_results_s02.json`（S-02、ユニバース SPY/IWM/JNJ/PG）も同じ理由で
   汚染なし。汚染があるのは分割銘柄を含む P3 / S-03 / S-05。

---

## 5. カレンダー網羅性の担保（本決定で追加した運用ガード）

`scripts/check_split_coverage.py` を追加した。保存ストアとカレンダーを突き合わせ、

- `unexplained_jump`: カレンダーに無い ±35% 以上の日次変動（≥2:1 分割は必ず −50% 以下に
  なるため、ここに出るものは未登録の分割か破損データ）
- `calendar_mismatch`: カレンダー登録日があるのに当日の open/close 比が期待係数の ±15%
  以内に入らない（＝**エンジンが黙って補正を skip する状態**。保存系列が既に調整済み＝
  二重補正、または価格異常）
- `calendar_symbol_missing`: カレンダーに分割があるのにストアに系列が無い
- `watch_jump`: ±15% 以上の未登録変動（決算ギャップ等。失敗にしない）

を報告し、失敗が 1 件でもあれば exit 1。2026-11-05 の実データでは
**verdict: PASS（失敗 0 / watch_jump 27 件、いずれも決算ギャップ等）**。

```bash
.venv/bin/python scripts/check_split_coverage.py          # 人間向け
.venv/bin/python scripts/check_split_coverage.py --json-out <path>   # 機械可読
make split-coverage                                       # 上記と同じ
```

新しい銘柄を追加する / 分割が発表された / SEC 側のカレンダー再生成が走った、のいずれか
のあとに実行する。`unexplained_jump` が出たら `scripts/fetch_sec_pbr.py` でカレンダーを
再生成し、`calendar_mismatch` が出たら取得基準が二重になっていないか（過去に
`adjustment=splits` で書いた行が混じっていないか）を先に確認する。

---

## 6. 旧 tracked 結果 JSON の処置

旧 4 ファイルは **生成時にカレンダー補正が効いていない**（`Split adjustment applied` の
出現 0 件、かつ assumption に「Raw OHLCV is used; ... split adjustments ... are not modeled」
と書かれている ＝ 現在のエンジンの実挙動と矛盾）。**数値は履歴として保持し、
ファイルを書き換えずに「stale」を明示し、現行コードの結果は別パスに新規生成**した。

| ファイル | 処置 | sha256（前後で不変） |
|---|---|---|
| `backtest_results_p3.json` | 先頭に `_stale` ブロックを追加。数値は不変 | `7420f8e0…169d61c8` |
| `backtest_results_s03.json` | 同上 | `7da48a96…087daa08` |
| `backtest_results_s03_supplied.json` | 同上 | `6987f592…c2acd8a4` |
| `backtest_results_s05.json` | 同上 | `21da050b…b5262795` |

`_stale` ブロックの中身は 4 ファイル共通の 1 オブジェクト
（`stale: true` / `reason` / `decided_by` / `superseded_by` / `do_not_cite`）。

### 6.1 現行コードでの再生成（別パス・凍結データ無変更）

出力先は `data/research/split-adjusted-20261105/`（`data/` は `.gitignore` 配下。
凍結入力を書き換えず、tracked ツリーも肥大させない）。

| 生成物 | コマンド | 実測時間 |
|---|---|---:|
| `backtest_results_p3.json` | `.venv/bin/python run_backtests_p3.py --output <dir>/backtest_results_p3.json` | 19.5s |
| `backtest_results_s03.json` | `.venv/bin/python run_backtests_garp_momentum_hybrid.py --output <dir>/… --p3-reference <dir>/backtest_results_p3.json` | 19.8s |
| `backtest_results_s03_supplied.json` | 同上 + `--pbr-dir data/fundamentals/pbr` | 22.3s |
| `backtest_results_s05.json` | `.venv/bin/python run_backtests_defensive_rotation.py --output <dir>/backtest_results_s05.json` | 56.2s |

- 再生成は現行データ（〜2026-11-04）を使うため窓が旧 JSON（〜2026-09-28）より 5 週長い。
  **旧 JSON との差分は「補正」と「窓」の合成**なので、両者を直接引き算しないこと。
  補正だけの効果は §3.1（窓を旧 JSON に固定した ON/OFF）を見る。
- 再生成 P3 の 6 戦略のうち `value_fundamental` / `kiyohara_value` は `insufficient`
  （現行レジストリで追加された戦略で、必要な入力が揃っていない）。旧 JSON は 4 戦略のみ
  だったため、この 2 件は新規出現分であり差分比較の対象外。
- `run_backtests_p3.py` に `--output` を追加した（既定値は従来の tracked パスなので
  既存の使い方は不変）。

---

## 7. 却下した選択肢

- **案 A（`adjustment=splits` + エンジン補正の無効化）**: §2.1 の 3 点（基準の混在を
  検出できない / 全履歴の遡及再取得 / 稼働 mark の遡及変更）で却下。再取得そのものは
  14 リクエストで安いが、**その後に生成済みの全成果物と稼働帳簿が無効化される**費用が
  見合わない。
- **`adjustment=all`（配当込み）**: 分割と配当が同じ機構に混ざり、§2.1 の混在問題を
  さらに悪化させる。加えて稼働経路の価格基準が変わる。TR は配当を明示入力にした
  別レイヤで扱う（S-05 の TR 込み判定はその時に再評価する）。
- **旧 JSON の上書き再生成（案 a）**: 監査証跡（補正前の数値）を消すため却下。
