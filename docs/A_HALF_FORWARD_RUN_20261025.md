# A_HALF 前向き運用: 毎日の記録と週次 DD比の読み方（t_68536057）

- 作成: 2026-10-25 / worker-flash（kanban `t_68536057`）
- 対象: `/root/current-work/yowayowa-investor`（`a05d24890487+dirty`）
- 位置づけ: 影の帳簿 `A_HALF`（グロス50%上限・t_52c4aa20 検収済み）を前向きに毎日走らせ、
  事前登録の判定基準（20営業日連続・12週: 費用控除後 equity>=1.0 / 最大DD<=3.0% / DD比<=0.6）の
  判定材料を日次で蓄積する。**実注文 0 件・実資金 0 円**（帳簿は paper であり、発注経路コードを import しない:
  `grep -rn "submit_order(\|place_order(\|from yowayowa.broker" src/yowayowa/paperloop.py ops/shadow_llm/*.py` → 0 件）。

## 1. 毎日の記録は既存の demo-loop cron で自動（新規 cron の二重登録はしない）

A と A_HALF は同じ 1 コマンド（`ops/demo-loop/daily.sh`、`loop_cli decide`）で両方回る
（`BOOKS_ENABLED` に両帳簿が載っているため。D4: 判断は book:asset:日で高々 1 回・冪等）。

| cron（worker-flash profile） | schedule (JST) | job id | 内容 |
|---|---|---|---|
| demo-loop-decide（既存・2026-10-11から） | 毎日 09:20 | `9b64eaec1cb9` | fetch → mark → **decide（A と A_HALF 両方）** → anchor。flock あり |
| demo-loop-fill（既存） | 毎日 11:05 | `8994e1906300` | 前日までの未約定判断を約定（C6: 新しい判断が古い pending を supersede） |
| demo-loop-report-weekly（既存） | 毎月曜 09:50 | `aa774e8b0d1b` | verify → weekly_summary（`books.A_HALF.dd_vs_benchmark_ratio` に自動記録） |
| shadow-llm-daily-tick（**本カードで新規追加した 1 本**） | 毎日 09:40 | `4a088e2fd45c` | 設計V2 §7 の LLM 影ループ 1 ティック。no_agent・flock あり。無料枠のみ（`:free` 以外は config が起動拒否） |

- 09:40 にした理由: 無料枠のレートリミットが UTC 深夜リセット想定のため、リセット直後の枠が新しい時間帯に呼ぶ
  （実測 2026-10-25 23時台は `HTTP 429: free-models-per-day-high-balance` で未回答。未回答も journal に 1 行として残る）。
  この時間帯でも 429 が続くようなら cronjob_manage で時刻だけずらす。

## 2. 週次 DD比を出す軽量コマンド（既存の検算を呼ぶだけ・読み取り専用）

```bash
cd /root/current-work/yowayowa-investor && PYTHONPATH=src YOWAYOWA_MODE=personal .venv/bin/python -m yowayowa.paperloop eval --book A_HALF --from 2026-10-25 --to $(date -u +%F) | jq '.eval.Q7'
```

- `.eval.Q7.dd_ratio` が CRYPTO_1N（1/N）比の DD比。`equity` / `max_drawdown_book` / `max_drawdown_bench` も同じ出力にある。
- 毎週月曜 09:50 の週次レポートも同じ計算を `data/demo-loop/weekly_summary.jsonl` の `books.A_HALF` に自動記録する:
  `jq '{week_end, ah: .books.A_HALF | {cum_pnl, max_drawdown, benchmark_max_drawdown, dd_vs_benchmark_ratio}}' data/demo-loop/weekly_summary.jsonl | tail -1`

## 3. 初回実測（2026-10-25 手動・本カード）

- 手動実行は cron 用ラッパーと同じ手順（fetch → mark → `decide --trigger manual` → anchor。`ops/demo-loop/daily.sh` は 1 バイトも変えていない）。
- `data/demo-loop/journal.jsonl` **307 → 313 行（+6）**: run×2（trigger=manual）・halt_check(A_HALF)・
  **decision A_HALF:BTC:2026-10-25 target 0.25**・**decision A_HALF:ETH:2026-10-25 target 0.25**・mtm(A_HALF) equity 1.0。
  A の判断は +0（同日の 09:20 cron が A:BTC/ETH:2026-10-25 を既に書いたため。D4 冪等が二重記録を防止＝仕様通り）。
- `prereg.jsonl` 6 → 7 行: `ensure_prereg` が A_HALF の事前登録行（spec_hash `155d353fc335401a…`・registered_by `worker-goat:t_52c4aa20`）を
  初回判断の前に追記（t_52c4aa20 検収で「次回 decide で追記」を要注目アイテムとしていたものが実測された）。
- 読み取り専用検証器: `python -m yowayowa.paperloop_verify --data-dir ./data --from 2026-10-10 --to 2026-10-25` →
  **exit 0 / chain_ok / anchors_ok / L1〜L14 全 ok / R1 38/38 / R2 38/38 / failed_checks 0**（追記後の台帳で全通過）。
- 2026-10-25 判断の A_HALF pending 2 本は、翌 10-26 の 09:20 cron が新しい判断を書くため、11:05 fill で
  `fill_rejected(superseded)` になる**設計通りの挙動**（C6）。前方判定の実質開始は 10-26（事前検算ドキュメントの予告どおり）。

## 4. 現在値（既存検算 `loop_cli`…`paperloop eval` の実測・2026-10-25 時点）

| 帳簿 | equity | 最大DD | DD比（CRYPTO_1N比） | 根拠 |
|---|---|---|---|---|
| A | 0.9568718 | -5.0872% | 0.9717 | window 2026-10-10〜10-25・n=32（t_52c4aa20 の実測値と一致） |
| A_HALF | 1.0 | 0.0% | 0.0（n=1日） | **live 1 日目**。判定は未達（0/20 営業日・0/84 日） |
| A_HALF（参考・事前検算） | 0.978641 | -2.534% | 0.484 | A の日次リターンを 0.5 倍した合成チェーン（t_52c4aa20 §4・前向き実測ではない） |

## 5. 不変の証跡（本カード作業前後の sha256）

- `ops/demo-loop/` の 8 ファイル（daily/fill_v2/weekly/legacy_v1×5）: **全て前後一致**（変更 0）。
  daily.sh `ece42ee387becd21…`、weekly.sh `143902bee30c3b4a…`（全値は kanban コメントの before/after 記録を参照）。
- `prereg.jsonl` 1 行目（A の凍結 spec）: `b71b70fc2e9df13c68d9…` 不変。verifier L10（decision spec_hash == prereg）全 ok。
- 追記があったファイル（設計通り）: `journal.jsonl`（+6 行）・`prereg.jsonl`（+1 行）・`data/shadow-llm/journal.jsonl`（+2 ティック行）。
- LLM ティックの cost: 同日成功分（行 2〜6）は `usage.cost_usd = 0.0` を実測。行 7〜8 は 429（トークン消費 0・請求なし・fallback 記録）。

## 6. A_HALF 20営業日ゲート FAIL 確定の判定参照先（2026-10-31 追記・追記のみ）

- **2026-11-23 の週次runでは、A_HALF の20営業日ゲートは FAIL 確定（10-27=0.998612 / 10-30=0.984391）として扱い、判断は `docs/DECISION_A_HALF_20BD_FAIL_OPTIONS.md` の選択肢(A)（この窓を FAIL として公式記録し、本則の12週ゲート＝2027-01 まで待つ）を正とする。**
- 出典は承認済み判断書 `docs/DECISION_A_HALF_20BD_FAIL_OPTIONS.md`（原本 = カード `t_f8c8f92b` 添付・12,130 byte / sha256 `d9c5aca1091007ba5221c698f20c9dc1180b32f8d14d98d8c51a732405a7c49c`・判断本体は監査2回通過済み）。本節は参照先の明示のみを目的とした追記であり、既存記述は無変更。
