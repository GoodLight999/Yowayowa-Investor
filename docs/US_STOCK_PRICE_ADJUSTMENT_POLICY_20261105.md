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
| 既存の設計規約 | 「adjustment=raw を明示」「分割調整値・配当調整値と raw 値を混ぜない」「調整後値を過去時点に遡及適用しない」 | `docs/STOCKS.md:53-56` |
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

---

## 8. 変更点とロールバック

- コミット: `6b3fb15`（ブランチ `fix/us-stock-price-adjustment-policy-t_4cfb36d0`、
  作業ブランチ `agent/commercial-foundation` 上に1コミット。9 ファイル・+868/−2）
- 変更ファイル: 本 doc / `scripts/check_split_coverage.py` /
  `tests/test_split_coverage.py` / `Makefile` / `run_backtests_p3.py` /
  `backtest_results_{p3,s03,s03_supplied,s05}.json`（`_stale` の 9 行追加のみ）
- **稼働経路のファイルは 1 バイトも触っていない**（`src/yowayowa/paperloop.py`,
  `paperloop_verify.py`, `scripts/paper_c2/*`）。コミットにも含まれない。
- ロールバック: `git revert 6b3fb15` で全て戻る（データ側の変更は
  `data/research/split-adjusted-20261105/` の追加のみで、凍結入力は不変）。
- 検証: `pytest tests/test_backtest.py tests/test_stock_ohlcv.py
  tests/test_split_coverage.py` = 86 passed / `mypy src/yowayowa` = 0 error /
  `ruff check`（新規 2 ファイル）= clean / `make split-coverage` = PASS。
  `ruff check src tests` の 30 件は本コミット対象外の既存ワーキングツリー差分
  （`paperloop.py`, `tests/test_c2_*`）に由来し、本タスク以前から存在する。

---

## 9. 残存リスク（本カードで閉じていないもの）

1. ~~**カレンダー欠落時は fail-open。**~~ → **t_abaea291 追補（2026-11-05 午後）で解消。**
   `Uncorroborated price discontinuity` が分割スケール（±35% 以上）のとき
   `data_quality="degraded"` を応答に載せる fail-closed 化と、Alpaca
   corporate-actions ↔ カレンダー照合（欠落＝失敗）を追加した。詳細は §10・§11。
2. **C2 の取得は `adjustment` を明示していない。** `scripts/paper_c2/alpaca_paper_client.py:156`
   `get_daily_bars()` は `adjustment` を params に含めず Alpaca の既定値に依存する。
   `docs/STOCKS.md:53` の「adjustment=raw を明示」規約からは外れており、既定値が変わると
   C2 の価格基準が黙って切り替わる。live path のため本カードでは触らず、別カードに切った
   （t_f2717dff）。
3. **配当・TR は未モデル。** 本決定はプライスリターンであり、S-05 の TR 込み判定
   （`t_2fc44a51` で「TR 込みでは再未達」）は未解決のまま別レイヤ扱い。
4. **カレンダーは 10 銘柄のみ。** 新規上場銘柄・新規分割は `scripts/fetch_sec_pbr.py` の
   再生成とユニバース追加が要る（`unexplained_jump` と §11 の corporate-actions 照合が
   その検出器）。

---

## 10. basis 明示の規約（全レポート・全成果物に適用・t_abaea291）

本ポリシーの下で米国株の価格 basis は 3 種あり得る。**すべてのバックテスト結果
（JSON・レポート・docs・Kanban 報告）は、数値の根拠となった basis を明示しなければ
ならない。basis 未明記の数値は引用してはいけない。**

| basis の表記 | 意味 | 現在の所在 |
|---|---|---|
| `raw+sec_calendar`（**既定**） | 保存は raw。`run_backtest` が `split-calendar.json` で後方補正。約定価格（Alpaca paper/実弾の fill）と同一基準 | エンジンの唯一の正。応答 JSON の `assumptions[0]` に自動記載される |
| `raw`（無補正） | カレンダー適用なし。2026-10-23 以前の旧 JSON・ON/OFF 比較の OFF 側 | 引用時は必ず「無補正」と明記（旧 4 JSON は `_stale` 済） |
| `TR`（total return・配当込み） | G1 の tr2 ミラー等、リポジトリ外の配当オーバーレイ | 配当は未モデルなので、TR 数値は必ず「TR 込み」と明記（S-05 の(b)が反転する） |

- `adjustment=splits` / `all` の取得系 basis は存在しない（§7 で却下）。もし将来導入
  される場合は、この表にbasisを追加し、保存行の区別方法（vintage 列等）を実装した上で
  本 doc を更新すること。混在系列は禁止のまま。
- 応答 JSON は `assumptions[0]`（raw+カレンダー補正の明示）と、t_abaea291 追補で
  追加した `data_quality`（`ok` / `degraded`）を必ず一緒に読む。
  **`data_quality="degraded"` の結果を、その旨の記載なしに単体で引用してはいけない**
  （未登録分割または破損データが系列に残っている＝数値が信用できない状態）。

---

## 11. fail-closed 化と corporate-actions 照合（追補・t_abaea291・2026-11-05）

親監査 `t_2fc44a51` §6 と本 doc §9-1 を受け、**決定は案 B（raw 維持）のまま**、
「カレンダー外の無防備」を2つのガードで閉じた。データの書換は発生していない
（案 B のためロールバック対象データなし。コードは本追補のコミットを revert で戻る）。

### 11.1 エンジンの fail-closed 化（`data_quality` フィールド）

- `BacktestRunResponse` に `data_quality: Literal["ok", "degraded"] = "ok"` を追加した
  （`src/yowayowa/backtest_models.py`）。
- `run_backtest` は `Uncorroborated price discontinuity` を検出したとき、その overnight
  open ギャップが **±35% 以上（分割スケール）なら `data_quality="degraded"`** を応答に
  載せる（`src/yowayowa/services/backtest.py` の `_UNCORROBORATED_SPLIT_SCALE`）。
  `status` は従来どおり `complete` / `insufficient` のまま（計算は可能だがデータが
  信用できない、という区別）。warning の文面・15% 超えの発火条件は不変。
- **なぜ実行失敗（raise）ではなく degraded か。** 15〜35% の未裏付けギャップは実データ
  に実在する決算ギャップ（監視 `check_split_coverage` の watch 27 件と同じ帯域）であり、
  raise すると実データの全 runs が合法的な決算日で死ぬ。一方 ±35% 以上は「2:1 以上の
  分割なら必ず −50% 以下」という事実から、未登録分割か破損データ以外に説明がなく、
  数値が実際に信用できない状態である。監視スクリプトの
  `unexplained_jump`(35%・FAIL) / `watch_jump`(15%・watch) の二線と同じ値を採った。
  カレンダー全体が欠落した場合は全分割がこの経路で ±50% 級のギャップとして検出され、
  やはり degraded になる（フェイルセーフの連鎖）。

### 11.2 Alpaca corporate-actions ↔ カレンダー照合（欠落＝失敗）

- `scripts/check_split_coverage.py` に corporate-actions 照合を追加した。
  - `--corporate-actions <snapshot.json>`: 記録済みスナップショット（オフライン・
    認証情報不要）を照合。`make split-coverage-ca` がこの形式。
  - `--corporate-actions-live`: `data.alpaca.markets/v1/corporate-actions` から
    ストア＋カレンダー全銘柄分を取得して照合（認証情報は
    `YOWAYOWA_ALPACA_KEY_ID/SECRET_KEY` 環境変数 → リポジトリ `.env` →
    `/root/.secrets/alpaca_paper.env` → `/root/.hermes/.env` の順で解決）。
  - 失敗種別: `corporate_action_missing_from_calendar`（カレンダーに無い分割＝
    監査基準の「欠落＝失敗」）と `corporate_action_ratio_mismatch`（比率不一致＝
    エンジンが補正を skip する状態）。いずれも exit 1。
    逆方向（カレンダーだけにあるイベント）は SEC が Alpaca の上位集合であり得るため
    `[info]` 通知のみで失敗にしない。
- スナップショット `tests/fixtures/corporate_actions_snapshot_20261105.json`
  （出所・取得窓を `_provenance` に記録。監査 t_2fc44a51 の `alpaca_splits.json` と同一）。
- **実測（2026-11-05 15:4x JST・VPS）**: スナップショット照合と live 取得照合の両方で
  **7 イベントすべてカレンダーに存在・比率一致・failures 0（verdict: PASS）**。
  watch_jump 27 件は従来どおり情報表示（決算ギャップ帯）。

### 11.3 テストと検証結果

- 新規テスト 11 件: `tests/test_split_coverage.py` に 8 件
  （normalize 3 形状／スナップショット完全網羅／load_calendar のメタキー skip／
  実カレンダーとの照合／欠落で失敗／比率不一致で失敗／逆方向は info／
  build_report 結線）、`tests/test_backtest.py` に 3 件
  （分割スケールで degraded／決算帯は warning のまま ok／カレンダー補正ありは ok）。
- **既存ピンの追測定（t_3b044506/t_a261a85a の先例に従う）**:
  `tests/test_defensive_rotation.py::test_existing_eight_full_responses_match_preimplementation_head`
  の 8 ピンは応答全文の sha256 を固定しており、`data_quality` フィールド追加で全部失敗した。
  HEAD 直下の pristine worktree で旧ピン 8/8 再現を確認した上で、両ツリーの応答
  `model_dump_json` をフィールド単位で突き合わせ、**`data_quality` 以外の全フィールドが
  完全一致**（本格 8 件中これ以外の差分 0・新値はすべて `ok`）であることを実測し、
  新ハッシュに追測定して分類コメントを追記した。数値の変化は 0。
- `pytest tests/test_backtest.py tests/test_split_coverage.py` = **39 passed**
  （既存の分割関連テストを含む・2026-11-05 VPS 実測）。
  `pytest tests/test_defensive_rotation.py` = **52 passed**（追測定後）。
- **全件走査（`make test` 相当・pytest --cov=yowayowa）実測（2026-11-05 VPS・47:01）**:
  当初 3594 passed / 10 failed / 2 skipped。失敗の内訳は (a) 上記応答ハッシュピン 8 件
  （本追補のフィールド追加が原因→追測定で解消済み）と (b) `tests/test_db_units.py` の
  2 件（positions 一意制約マイグレーション系）。(b) は pristine worktree（本追補適用前の
  HEAD）でも同 2 件が失敗することを実測しており**本追補以前からの既存失敗**で、
  分割・価格調整と無関係のため本カードでは触らない（別カード起票を推奨）。
- `mypy src/yowayowa` = **0 error**（259 files）。`make split-coverage` = PASS、
  `make split-coverage-ca` = PASS。
- `make verify` 相当（lint / typecheck / test / openapi / openapi-check）は本ワーク
  ツリーで実行。ただし並行カードの未コミット差分（`paperloop.py`・`tests/test_c2_*` 等）
  が `ruff check src tests` に既存 30 件を残しているため、`lint` の全体パスは
  ワークツリー都合で判定不能。本追補が触ったファイルに限定した ruff は clean。
  openapi baseline は `--write` で再生成し `openapi-check` は PASS（161 operations）。
  再生成差分には本追補の `data_quality` 加え、**HEAD で既に発生していた JPX public
  ルート（コミット c928ebd・ベースライン最終更新 dd7c85a より前で未反映）の契約差分も
  一括で取り込まれた**。JPX 契約の正が baseline に反映されたことを意味する。

### 11.4 変更ファイルとロールバック

- `src/yowayowa/backtest_models.py`（data_quality フィールド追加） /
  `src/yowayowa/services/backtest.py`（閾値定数＋degraded 判定） /
  `scripts/check_split_coverage.py`（corporate-actions 照合） /
  `tests/test_backtest.py`・`tests/test_split_coverage.py`（テスト追加） /
  `tests/fixtures/corporate_actions_snapshot_20261105.json`（新規） /
  `Makefile`（split-coverage-ca）/ `docs/openapi_baseline.json`（再生成） / 本 doc。
- 稼働経路（`paperloop.py`・`paperloop_verify.py`・`scripts/paper_c2/*`）は
  **1 バイトも変更していない**。ストア・カレンダー等の凍結データも不変。
- ロールバック: 本追補のコミットを `git revert` すれば全て戻る
  （`data_quality` はデフォルト `ok` の追加フィールドなので、応答 JSON の既存消費者への
  破壊的変更はない）。
- 実発注 0・入出金 0。


