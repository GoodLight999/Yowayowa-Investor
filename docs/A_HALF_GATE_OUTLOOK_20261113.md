# 【A_HALF・確定事実】20営業日ゲートは11-23を待たず不成立確定（3条件中2条件が回復不能）

作成 2026-11-13（worker-flash / カード t_0e528b98）。実弾0・実資金0。
**本書は判定記録を書かない。** 11-23（月）09:50 の週次 run が既存機構で1回だけ FAIL を記録する（§2-e）。本書はその「前座」として、マスターが1行で後続を選べる材料。
数値の再調整・閾値変更・窓の延長・過去データでの再測定は行っていない（§5）。

---

## 1. 事実 — journal からの独立再集計（COO読取値と全行一致）

出所: `data/demo-loop/journal.jsonl`（sha256 `d6e9ec53d2c1c8193a4279834f38264989e3284db2bfd6593924036b08bd3155`・939行・2026-11-13読取）。
book=A_HALF は156行（mtm 20 / decision 40 / fill 38 / mark 36 / halt_check 20 / fill_rejected 2）。
mtm 20行のうち平日（Mon-Fri・`_a_half_20bd_business_days` の素平日定義、paperloop.py L4160-4177）は **15行＝窓 10-26〜11-20 の確定部分**。残り5平日（11-16〜11-20）は未到来。

| # | 日付 | journal行 | equity（費用控除後・mtm.equity） | 1.0未満 |
|---|---------|------|----------|------|
| 1 | 10-26 | L340 | 1.0000 | |
| 2 | 10-27 | L378 | 0.9986 | **下回る** |
| 3 | 10-28 | L418 | 1.0038 | |
| 4 | 10-29 | L450 | 1.0061 | |
| 5 | 10-30 | L478 | 0.9844 | **下回る** |
| 6 | 11-02 | L572 | 0.9881 | **下回る** |
| 7 | 11-03 | L608 | 0.9809 | **下回る** |
| 8 | 11-04 | L648 | 0.9853 | **下回る** |
| 9 | 11-05 | L676 | 1.0111 | |
| 10 | 11-06 | L704 | 0.9786 | **下回る** |
| 11 | 11-09 | L798 | 0.9623 | **下回る** |
| 12 | 11-10 | L834 | 0.9655 | **下回る** |
| 13 | 11-11 | L874 | 0.9554 | **下回る** |
| 14 | 11-12 | L902 | 0.9568 | **下回る** |
| 15 | 11-13 | L930 | 0.9403 | **下回る** |

- **1.0未満は15日中11日**。生値はCOO読取値と4桁丸めで15行すべて一致（例: 11-13 = 0.9403371876462573・L930）。COO報告（2026-11-13 14:00 JST）の再計算一致をここに固定する。
- `data/demo-loop/weekly_summary.jsonl`（sha256 `0977756715932eb5808190ead333130a893b61864a4ae647d6746d5f2891d479`・19行）L19（week_end=2026-11-09）: A_HALF `max_drawdown=-0.0482527`・`dd_vs_benchmark_ratio=0.5007585` → COO引用の **-4.83% / 0.5008 と一致**。
- ゲート自身の測り方（平日のみ・窓先頭10-26リベース・`_max_drawdown_since`）で11-13時点を再計算: **窓内最大DD -6.9955%**（ピーク 1.011067@11-05 → 谷 0.940337@11-13）。ベンチ CRYPTO_1N 同窓DD -13.96% → **DD比 0.5010**（週次行の0.5008と整合）。
- 補足: 週次行では 11-02 週が最大DD -2.42%でまだ合格、**11-09 週の -4.83% で初めて閾値3.0%を割った**。

## 2. 帰結 — 残り5平日（11-16〜11-20）で条件(1)(2)は回復し得ない（条件式引用）

凍結ルール（paperloop.py **L4153-4156**）:

```python
A_HALF_20BD_RULES = {
    "equity_net_min": 1.0,      # prereg: 費用控除後 equity >= 1.0
    "max_drawdown_max": 0.03,   # prereg: 最大DD <= 3.0% (window inner max drawdown)
    "dd_vs_benchmark": 0.6,     # prereg: DD比 <= 0.6
}
```

**a. 条件(1) equity_net_min（回復不能）** — 判定式は L4239-4250:
`equity_failing_days = [day for day in business_days if book_marks[day] < A_HALF_20BD_RULES["equity_net_min"]]` → `"pass": not equity_failing_days`。
failing_days に既に11日が入っている。journal は `prev_hash`/`row_hash` のハッシュチェーン付き追記専用で、確定済みmtm行の書き換えは改変になる（本カードでも行っていない）。残る5平日が1.0以上で終わっても failing_days は消えない。ゲートは「前方20営業日の**この1つの窓**」を測り、窓完結後の再streakは拒否される（L188-191 の case (b) 読み）。**よって判定日時点で min < 1.0 は確定・FAIL確定。**

**b. 条件(2) max_drawdown_max（回復不能）** — 判定式は L4254・L4267: `book_dd = _max_drawdown_since(book_marks, business_days[0])`、`dd_self_pass = book_dd >= -0.03`。`_max_drawdown_since`（L4350-4359）は窓を日順に走査し `peak = max(peak, equity)`・`worst = min(worst, equity/peak - 1.0)` — **worst は日が増えるほど浅くならない（minは下がるだけ）**。既に実現した -6.9955%（-3.0%を大きく超過）より浅い値は future days では作れない。**よって dd_self_pass は窓完結時に False 確定。**

**c. 総合判定** — L4303: `failed = sorted(name for name, check in criteria.items() if check["pass"] is False)`、L4318: `"verdict": "FAIL" if failed else "PASS"`。条件(1)(2)が確定FAILのため、**11-16〜11-20 の5日がどのような値でも verdict=FAIL**（failed_criteria ⊇ {equity_net_min, max_drawdown_max}）。条件(3) DD比は現時点 0.5010 ≤ 0.60 で合格見込みだが、3条件はANDなので verdict には無関係。

**d. （正確性の注記）** 0.5008/0.5010 は「現時点」の値であり、11-23 判定は窓20日全体で再計算される。ただし §2-c のとおり verdict は変わらない。

**e. 記録は既存機構が書く** — L4221-4222: `if week_end < business_days_end: return None  # deadline not reached: never judge early`。週次runは week_end ≥ 2026-11-20 の初回（= **2026-11-23 月 09:50**、worker-flash側 cron `demo-loop-report-weekly`）に、L4129-4132（`slot["20bd"] = gate`）経由で**1回だけ**記録し、以後は L4180-4193 の dedup で二重記録しない。本カードはこの判定行を一切書いていない（§5）。

## 3. 意味 — 数値付き

- **DD比 0.50 ＝ サイズ半減のリスク制御仮説は支持。** prereg（`prereg.jsonl` L7・row_hash `5cd8e914…`）の仮説 "A 50% gross cap on S-01 lowers the drawdown vs 1/N" どおり、半分サイズの A_HALF の窓DD **-7.00%** はベンチ CRYPTO_1N の **-13.96%** のほぼ半分（比 0.5010）。半減機構そのものは設計どおり動いた。
- **負けたのは土台の暗号通貨 S-01 シグナルで、サイズ調整では直らない。** 同一シグナルの実寸法帳簿 A は同窓で累積 **-11.56%**・窓DD **-13.51%**（journal再計算 10-26→11-13）。A_HALF の累積 **-5.97%** はほぼ A の半分 — つまり損失の中身はシグナル側が生んだもので、サイズ係数はそれを半分に縮めただけ。シグナルのDDが13〜14%ある以上、半減しても7%残り、**最大DD 3.0% のゲート(2)はシグナルの質が変わらない限り構造的に通らない**。
- COO引用「この窓 累積 -3.77%」の出所特定: `weekly_summary.jsonl` L19 の A_HALF `cum_pnl=-0.037720`（week_end=2026-11-09・comparison_start=2026-10-25 起点の値）。その後 11-13 までに **-5.97%** へ拡大（journal L340→L930）。S-01実寸法 A は同週次行で -12.31%（同 L19）。

## 4. マスター選択肢（どれか1行を選ぶ）

- **(i) A_HALF退役・暗号S-01を実弾候補から除外**（記録は保持）
- **(ii) 12週窓まで自動観測継続**（追加コスト0）
- **(iii) シグナル自体の再検定は新規事前登録が前提・本カードでは実施しない**

どちらを選んでも 11-23 の機械判定（FAIL記録1回）は既存機構が行う。選択すべきは「その後の処置」である。

## 5. 変更なしの確認（11-23の自動判定は触っていない）

- 本カードの書込は `docs/A_HALF_GATE_OUTLOOK_20261113.md`（本書）とワークスペース内集計スクリプト（`a_half_recount.py`）のみ。journal・weekly_summary・paperloop.py・cron への書込は0。
- `journal.jsonl` / `weekly_summary.jsonl` は読み込みのみ。sha256 は読取前後で同値（上記 §1 の値を最終確認で再照合）。
- `weekly_summary.jsonl` に `"20bd"` 記録は未存在（`grep -n '20bd'` → 0件実測）＝判定は未記録のまま。
- `git status --short` で `src/yowayowa/paperloop.py`・`data/demo-loop/journal.jsonl` は変更なし（未コミット差分は本カード以前の他カード由来のみ）。cron への変更なし。

*証跡: 再集計スクリプト実行出力（2026-11-13）・全数値はこのスクリプトの実測。閾値・窓・銘柄の変更、過去データでの再調整、判定の前倒し記録、マスター裁定の代行はいずれも行っていない。*
