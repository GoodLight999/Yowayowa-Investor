# 週次比較行の終点不一致を fail-closed にする（v1.9）

カード: `t_94687d03`（親: `t_75cade36` C-05/C-06/C-07 の検収で audit-cld が見つけた残り）
出典: 親カードの検収メモ「比較行の両端が違う日でも値が出る」（実装者の未確認範囲 第3項）
実施: 2026-10-24 JST / worker-goat
対象コード: `src/yowayowa/paperloop.py`（hotspot）、`tests/test_paperloop.py`、
`docs/DEMO_LOOP_V1_OPS.md`、本ファイル
非対象（触っていない）: `paperloop_panel.py` / `paperloop_verify.py` / `loop_cli.py` /
`services/*` / API・UI・CLI の各面 / 実データ `data/demo-loop`（読み取りのみ）

---

## 1. 決めたこと（2案のうち片方）

**fail-closed を採る。両端の日付が違う日なら比較の数値は出さない。**

- `_comparison_line(..., as_of=week_end)` が、book の最後の mark と benchmark の最後の
  mark を別々の日に取っていた。片方が 10-24、もう片方が 10-21 なら、`cum_pnl` の引き算は
  「3日分の騰落」対「0日分」になる（親カードの検証で +10.0pp が実際に出た）。
- 採用: 両端が違う日なら `cum_pnl` / `max_drawdown` / `benchmark_cum_pnl` /
  `benchmark_max_drawdown` / `vs_benchmark_pp_cum` / `dd_vs_benchmark_ratio` を **None**、
  `comparison_reason` に **`endpoint_mismatch`**。
  **`comparison_start` と両方の as-of（`comparison_as_of` / `benchmark_as_of`）は残す**
  （どの mark が存在したかは行から読める。数値だけを止める）。
- 不採用: 両方を「共有する最後の mark」まで切り詰める案。同じ週次行の `cum_return` /
  `drawdown_now` は `week_end` のままなので、1帳簿について**2つの期間が同じ行に並ぶ**
  （比較だけ古い窓、累積は今週）。追記専用の行でそれを読み分けさせるのは、
  「終点が揃っていない」という元の欠陥を別の形に置き換えるだけと判断した。
- `as_of` を省いた既存呼び出しの意味は変えない（終点検査は `as_of` 指定時だけ）。
- 運用上の帰結: ベンチマークが週次など粗い頻度でしか mark を書かない運用に変えると、比較は
  常に `endpoint_mismatch` で止まる。現行は全ブックが日次 mark なので問題ない。

### 波及させた2箇所（同じ規則）

- トップレベル `vs_benchmark_pp_cum` は `books[A]` の値をそのまま写すので自動的に None。
  `vs_benchmark_pp_cum_as_of` は**値が存在するときだけ**日付を入れる（従来は
  他理由の None でも None だったので、その挙動に揃えた）。
- `vs_benchmark_pp_cum_legacy_nobaseline` は各自の as-of equity を引き算していたため、
  日付がずれていても値が出た。同じ規則で None にし、理由を
  `vs_benchmark_pp_cum_legacy_nobaseline_reason` に出す
  （`endpoint_mismatch` / 既存の `no_mark_at_or_before_week_end`）。

### スキーマ

- 週次行の `schema` を `demo-loop-summary-v1.8` → `demo-loop-summary-v1.9`。
  既存の追記専用行は当時の意味のまま（v1.9 以前の行は終点不一致でも値を持ち得る）。

---

## 2. 入力とコードのハッシュ

`data/demo-loop` は読み取りのみ。A/B は scratch へ複製した先でだけ実行し、
前後で5ファイルが不変であることを実測した（`real_data_sha_unchanged: true`）。

| ファイル | sha256（実行前 = 実行後） |
| --- | --- |
| `data/demo-loop/journal.jsonl` | `782b702d346079593168524777f7ed191890ef413112bff2dd34463eb998ae17` |
| `data/demo-loop/weekly_summary.jsonl` | `48d039e453b1ab5b027c3e24005b600d02737178a5ff1fb5b177d4c7c7714ffd` |
| `data/demo-loop/anchors.jsonl` | `2a6c7eeaf4f9419517f3aab6a763175bc5090e3cdabff2916a2fea181934de6f` |
| `data/demo-loop/cusum_A.jsonl` | `1185d9f0454b9b0c66c7317f6ab33126572cf49e71b5561b5f43d3d33ef5cb73` |
| `data/demo-loop/prereg.jsonl` | `d18a1dd0be26ec7a86f375d21e396f8c3d0d3183e54e3a24ba6e7b0fc4e57083` |

（`journal.jsonl` / `anchors.jsonl` は親カードの記録 `5b746fdc…` / `3f7e09e6…` から
変わっている。検収でも指摘のとおり 2026-10-24 09:21 JST の日次 cron が mark を追記した
ためで、本カードの作業による変化ではない。最新 mark は 10-23 → 10-24 に進んでいる。）

コード（作業ツリー、未 commit）:

| ファイル | sha256 | HEAD からの差 |
| --- | --- | --- |
| `src/yowayowa/paperloop.py` | `19bb272c4c2e8444e367eecc37a7f888a946d4b274333ef95bb10f59b5e252b9` | +308 / -40（うち本カードは +86 / -11） |
| `tests/test_paperloop.py` | `d5fd8310253de2467d223e272bd2ec3b25a82c4db03fb58868130aa72393b9a9` | +439 / -4（うち本カードは +118 / 0 と schema 期待値 1 行の更新） |
| `docs/DEMO_LOOP_V1_OPS.md` | `de403c3f9515e562d76476ab2ddd084bb1b58ddead6d76049d8b538b18dd9eae` | 本カードの追記 |

本カード分の差は「本カードの編集だけを1つずつ逆適用して作った v1.8 相当の複製」との
diff で測った（`86 insertions / 11 deletions`、`py_compile` 通過）。

**注意（検収への申告）**: 親カードが記録した `paperloop.py` の sha256
`c03b26f4ffd3775f2fa56432b918351b9c0d5a5e0e036eed35127173744c4e08` は、本カード開始時の
作業ツリーから本カード分を逆適用した複製では再現しない（`e702fe26…`、HEAD 基準 +234/-41。
親カードの記録は +234/-40 で、基準にした commit `b4616f66…` はこのリポジトリに存在しない）。
**振る舞いは一致する**: その複製で実データ `week_end=2026-10-19` を回すと、親カードが記録した
v1.8 の値（A `cum_return` = `-0.02523841228044177`、`vs_benchmark_pp_cum` =
`0.17858844554395903`）と完全一致した。ハッシュ差は本カード開始前に作業ツリーへ入った
別の差分（コメント/整形など）によるものと考えられるが、特定はできていない。

---

## 3. 実測（同じ入力で v1.8 相当と v1.9 を実際に動かした A/B）

方法: `data/demo-loop` を scratch に複製し、v1.8 相当のモジュール（上記の逆適用で作成・
`py_compile` 通過）と作業ツリーの v1.9 を、同じ `weekly_summary` に別々の複製でかけた。
`_eval_blocks` は両方とも stub（Q1-Q7 は本カードの対象外）。
スクリプト: `~/.hermes/profiles/worker-goat/cache/scratch/ab_compare.py`、
生データ: 同ディレクトリ `ab_report.json`。

### 実データ（3週・全ブックの最終 mark が同じ日なので no-op が期待値）

| week_end | A `cum_return`（v1.8 = v1.9） | A `vs_benchmark_pp_cum`（同） | as-of（book / bench） | top legacy（同） |
| --- | --- | --- | --- | --- |
| 2026-10-12 | `0.008158557139212652` | `0.0025095911955430594` | 10-12 / 10-12 | `0.0025095911955430594` |
| 2026-10-19 | `-0.02523841228044177` | `0.17858844554395903` | 10-19 / 10-19 | `0.17858844554395903` |
| 2026-10-26 | `-0.03528367961487788` | `0.18262411264464706` | 10-24 / 10-24 | `0.18262411264464706` |

3週とも v1.8 と v1.9 で**数値が完全一致**し、差は `schema` だけ。つまり実データでは
本カードは no-op（終点が揃っているので止める理由が無い）。10-19 の値は親カードの記録値とも
一致する。

### 合成ジャーナル（検収の再現手順そのまま）

A: 10-21 = 1.00, 10-24 = 1.10 ／ CRYPTO_1N: 10-21 = 1.00（10-24 の mark なし）／
week_end = 2026-10-25。

| 項目 | v1.8 | v1.9 |
| --- | --- | --- |
| `comparison_start` | 2026-10-21 | 2026-10-21 |
| `comparison_as_of` / `benchmark_as_of` | 2026-10-24 / 2026-10-21 | 2026-10-24 / 2026-10-21 |
| `cum_pnl` | `0.10000000000000009` | **None** |
| `benchmark_cum_pnl` | `0.0` | **None** |
| `vs_benchmark_pp_cum` | `10.000000000000009` | **None** |
| `dd_vs_benchmark_ratio` | None（DD 0 同士） | None |
| `comparison_reason` | None | **`endpoint_mismatch`** |
| top `vs_benchmark_pp_cum` | `10.000000000000009` | **None** |
| top `vs_benchmark_pp_cum_as_of` | 2026-10-24 | **None** |
| top `legacy_nobaseline` | `10.000000000000009` | **None** |
| top `legacy_nobaseline_reason` | （キー無し） | **`endpoint_mismatch`** |

検収が再現した +10.0pp が v1.9 で消え、両端の日付は残ることを実物で確認した。
逆向き（benchmark だけが 10-24 に進んでいる）も同じく止まる（テストで両方向を確認）。

---

## 4. 試験

| 種別 | 結果 |
| --- | --- |
| `pytest tests/test_paperloop.py tests/test_paperloop_cost_dd_units.py` | **89 passed / rc=0**（2026-10-24 10:05 JST 終了、89 dot / 失敗 0） |
| うち新規 | `test_C05_comparison_line_is_null_when_the_two_endpoints_differ`（book/benchmark 両方向、None+理由、両 as-of、top 2種、`as_of` 省略時の従来値 +10.0pp）、`test_C05_comparison_line_keeps_its_value_when_both_endpoints_match`（終点が同じなら従来値 +10.0pp、top と legacy も同値） |
| `ruff check src/yowayowa/paperloop.py tests/test_paperloop.py tests/test_paperloop_cost_dd_units.py` | rc=1 — **既存の5件のみ**（`SIM113` paperloop.py:1625、E501×3 test_paperloop.py:220/625/626、I001 test_paperloop.py:608）。新規コード・新規テストへの指摘なし |
| `ruff format --check`（同上3ファイル） | rc=1 — **既存の差分のみ**（paperloop.py:2291、test_paperloop.py:220/443/624）。本カードが触った行に差分なし |
| `mypy src/yowayowa/paperloop.py` | **Success: no issues found in 1 source file** |
| `py_compile`（v1.8 相当の複製） | 通過（逆適用の破損をそのまま基準に使わないため） |

新規テストは「ずれている場合の None + 理由」と「揃っている場合の従来値」の両方を確認する。
既存テストの期待値は `schema` の文字列1箇所のみ更新（弱めていない）。

### 未実施

- `make verify`（全量 pytest + coverage）は未実施。VPS で重い全量を回さない規律による。
  TSUKUMO 既存環境 / GitHub CI も未実行。
- ブラウザ E2E / Vercel デプロイ確認は不要（API・UI の応答は変わらない。週次行の
  フィールド意味の変更のみ）。
- commit / push はしていない（指示どおり）。

---

## 5. 未確認範囲

1. `make verify` / GitHub CI / TSUKUMO 実環境は未実行（第4節）。
2. 親カードが記録した `paperloop.py` のハッシュは再現しない（第2節末尾）。振る舞いは
   実データ 10-19 の値一致で確認したが、開始時点のツリーが検収時のそれと byte 一致で
   あることは示せていない。**検収時に作業ツリーの完全性をどう扱うかは要判断。**
3. 週次行の消費側（`paperloop_panel.py` / `paperloop_verify.py` / `loop_cli.py` /
   `services/*`）は指示により未確認。`endpoint_mismatch` を理由コードとして解釈する
   下流があるかは未調査（現行コードに `comparison_reason` を分岐に使う箇所は無いことを
   grep で確認した範囲では、無い）。
4. 実データは全ブックの最終 mark が同じ日なので、実運用でこの経路が発火した記録は
   まだ無い（合成ジャーナルでのみ再現）。片側のテープだけが止まった日に初めて出る。
5. v1.9 以前に書かれた週次行は当時の値のまま（追記専用）。過去行の再生成は別カード。
6. `docs/INDEX_ja.md` への追記は本カードで行った（親カードは未追記）。
