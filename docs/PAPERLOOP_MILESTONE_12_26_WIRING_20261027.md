# 12週・26週マイルストーンの機械判定配線（t_0be71b95・2026-10-27）

実装: worker-flash / 2026-10-27。ブランチ `card/t_0be71b95-milestone-12-26`（ローカルコミットのみ・pushしない）。

## 1. 着手前の状態と問題

- `MILESTONE_WEEKS_CONNECTED = (4,)` — `MILESTONE_RULES` には 4/12/26 の凍結ゲートが
  全部存在するのに、機械判定は4週だけが動いていた（t_d33aef07 の範囲で意図的に未接続）。
  12週締切 = 開始+84日、26週 = +182日 を迎えても**黙って何も判定されない**。
- `_collect_milestone_judgments` が `out[book] = judgment`（単一スロット）。同じrunで
  複数週の締切を跨ぐと後勝ちで片方が消える。
- `_milestone_already_judged` は一度きり記録。締切時に事実が欠けると
  INSUFFICIENT が**永久に固定**される（12/26 は短期欠測で容易に起こる）。
- 26週コストゲート (`shortfall_bps_mean_upper95_max=15.0`) は**署名付き**平均の
  片側95%上界。既存4週の `observed_cost_bps_mean` は `abs()` を使うので流用禁止。

## 2. 実装（`src/yowayowa/paperloop.py`）

- `MILESTONE_WEEKS_CONNECTED = (4, 12, 26)`。
- `_milestone_judgment` は共通の起点/締切/一回きり判定を担い、4週は
  `_milestone_4w_judgment`（t_d33aef07 の本体を**一字も変えず**移動のみ）、
  12/26 は `_milestone_long_judgment` へ振り分け。
- 12週の基準（凍結キーどおり）:
  - `dd_vs_benchmark`: 窓 `[start, start+84日)` 内の帳簿最大DDとベンチマーク最大DDを
    `_max_drawdown_since` で再計算し、比 ≤ 0.6（`MILESTONE_RULES[12]` 凍結値）。
    設計 §4-2a の括弧書き「ベンチマークのDDが 10% 以上の場合」を機械化:
    ベンチDDが `MILESTONE_DD_BENCH_MIN=0.10` 未満（または定義不能）なら `pass=None` → 判定保留。
  - `cum_pnl_min`: 同窓の最初と最後の mtm equity から累積損益 ≥ -0.10。
  - `switches_multiple`: `_week_switches`（週次行と同一の銘柄別ルール）で窓内の反転数を
    **資産別**に数え、各資産の反転数を**その資産自身の**凍結年率 × 窓日数/365 × 2.0倍と
    比較し、criterion の `per_asset`（observed/expected/max/pass）を載せてから集約する。
    （本節初版の「`MILESTONE_SWITCH_EXPECTED_ANNUAL[book]` の年率合計 × 窓日数/365」の
    合算形式は、設計 §2-3 が資産別であることとの不一致を監査 AS-M126-04 に指摘され、
    rev2 で置き換え済み — 詳細は下記 AS-M126-04 節。）
    期待値レートの出所は SHORT_TERM_STRATEGY_DESIGN.md §2-3（S-01 バックテスト
    BTC 8.8/年・ETH 6.7/年・計58回、2023-01-01..2026-09-29。§2-3自身の計算:
    BTCの12週期待 ≈ 8.8×84/365 ≈ 2回、警告5回以上）。A_HALF は A と同一シグナル
    エンジンなので同じレートを適用。
    **Book C（S-02）のswitch期待値年率は凍結・配線済み**（2026-10-28・役員決定
    t_be52f5bc 決定A、採番 t_b63497a9、配線 t_5a2489f5）: SPY=5.6 / IWM=7.7 /
    JNJ=10.5 / PG=14.6（実装定義ミラー・窓2021-01-04..2026-09-28・生価格basis・
    rate=switches/span_days×365・1dp ROUND_HALF_UP。`results.<SYMBOL>.primary.rate_1dp`
    逐語。出所の全文は `MILESTONE_SWITCH_EXPECTED_ANNUAL` のコメントと本doc末尾節）。
    C の12週判定は他帳簿と同じく実判定として記録される（初版の「C の switches ゲートは
    criterion に入れず観測値をファクト記録」と
    `switches_gate="not_evaluated_no_frozen_expected_rate"` は廃止 — `pass=None` の
    保留規約はレートを持たない帳簿・資産向けの契約として存続）。
- 26週の基準: `dd_vs_benchmark`（窓 `[start, start+182日)`、保留括弧は §4-3 に無いので
  非負DDのみ fail-closed）と `shortfall_bps_mean_upper95_max` — 窓内 mark の**署名付き**
  `shortfall_bps` に対し既存 `shortfall_bps_mean_upper95()`（Z=1.6449, n≥2）の上界 ≤ 15bps。
  検査・除外は週次行と同じ `_week_shortfalls` 契約（欠落行は `shortfall_errors` に記録）。
- 基準は週ごとに `MILESTONE_RULES[weeks]` の凍結キーそのものだけを criterion キーにする
  （4週の5項目を 12/26 に流用しない。_HALT 0 / カルマー / recompute維持 / 12週続行×2 は
  `MILESTONE_RULES` に凍結キーが無いので機械判定しない — 数値追加は凍結変更になる）。
- **INSUFFICIENT 保留規約（12/26 のみ）**: criterion の `pass=None` がある判定は
  **記録せず None を返す**。次の週次runが同じ凍結窓（`start..start+weeks*7` は不変）を
  再判定し、事実が揃った最初のrunが1回だけ記録する（`judged_at_week_end` がそのrunを
  示す）。これは設計 §4-2「保留」と同じ規約（判断材料なしは失敗ではなく未判定）。
  **4週は t_d33aef07 の既存活動（INSUFFICIENT を記録する fail-closed）を verbatim で維持**
  — 本番ですでに生きているセマンティクスを遡って変えない。
- `MILESTONE_SWITCH_EXPECTED_ANNUAL` / `MILESTONE_SWITCH_YEAR_DAYS=365.0` /
  `MILESTONE_DD_BENCH_MIN=0.10` の3定数を追加（出所は上記と設計 §2-3/§4-2a）。
  `MILESTONE_RULES` の数値は一切変更していない（凍結遵守）。
  （2026-10-28 追記: `MILESTONE_SWITCH_EXPECTED_ANNUAL` に `BOOK_C_BOOK` の凍結レートを
  追加 — 本doc末尾「Book C switch期待値の凍結配線」節。）
- `_collect_milestone_judgments` → `milestone[book] = {"weeks": {weeks: judgment}}`。
  メモリ上は int キー、ディスクは JSON 文字列キー。週次行 schema は
  `demo-loop-summary-v1.9` → `demo-loop-summary-v1.10`（行の意味変更の慣行どおり）。
- `_milestone_already_judged` は v1.10 形状と t_d33aef07 単一判定形状の両方を読む
  （本番の週次行は現在全行 milestone=null のため旧形状の行は実在しないが、
  古いファイルが「未判定」に見える事故を防ぐ）。

## 3. 意図しなかった差分・既知の限界

- ディスクの週キーが文字列になるため、`weekly_summary.jsonl` を読む将来の消費側は
  `str(weeks)` で引くか両対応すること（読み取り側は `_milestone_already_judged` が実装済み）。
- 12/26 を保留した週は milestone ブロック自体が現れない。「未達」ではなく**未判定**の意味。
  保留の理由（ベンチDD浅い等）は週次行の比較行（`dd_vs_benchmark_ratio` 等）から読める。
- ベンチマークが窓内で一回も下落しない場合、12週DD比は原理的に評価不能のまま保留が
  続く（26週判定が次の機会。設計 §4-2 の「次の12週で判定」に相当）。4週の INSUFFICIENT
  記録と違い黙って見えない点は docs/DEMO_LOOP_V1_OPS.md §2 の v1.10 項に明記済み。

## 4. テスト（`tests/test_paperloop.py`、新規5本）

1. `test_milestone_12w_and_4w_record_in_one_summary_row` — (a) 同一runが4週と12週の
   締切を跨ぐと1行に両方残る（戻り値とディスク行両方。ディスクは文字列キー）+
   (b) 12週締切前の週次では12週判定が付かない + 両週のdedupe。
2. `test_milestone_26w_shortfall_gate_is_signed_and_defers_until_facts` — (c) 締切時に
   shortfall 事実が欠けると 26週は記録されない（4/12は通常判定される）、事実が揃うと
   1回だけ記録される。値が**署名付き**（3×-100bps → 上界 -100 ≤ 15 で PASS。
   abs() 版なら 100bps で偽FAILになる）であることを実測で固定。
3. `test_milestone_12w_holds_while_benchmark_drawdown_is_shallow` — ベンチDD 3%
   (<10%) では `_milestone_dd_criterion` が `pass=None` + reason
   `benchmark_drawdown_shallow_hold`、判定は記録されない。ベンチDD 15% なら通常判定。
4. `test_milestone_12w_switches_gate_fails_and_book_c_gap_is_recorded` — 11回反転 >
   2×(15.5×84/365)→FAIL 記録。Book C は switches ゲート未配線、観測値をファクト記録、
   判定自体は配線済みゲートで出る。
5. `test_milestone_already_judged_reads_legacy_row_shape` — 旧単一判定形状と新形状の
   両方で dedupe が効くこと（文字列キー含む）。

既存 milestone テスト（t_d33aef07 の6本）は `milestone[book]["weeks"][4]` アクセサの
み更新（判定内容のassertは無変更）。summary schema assert を v1.9→v1.10 に更新（1行）。

## 5. 判定の挙動（summary-v1.10）

| 週次run | 4週 | 12週 | 26週 |
|---|---|---|---|
| 締切前 | なし | なし | なし |
| 締切時・事実揃い | **記録**（PASS/FAIL/INSUFFICIENT） | **記録**（PASS/FAIL のみ） | **記録**（PASS/FAIL のみ） |
| 締切時・事実欠け | **INSUFFICIENT を記録**（既存挙動） | 記録なし（次runで再判定） | 記録なし（次runで再判定） |
| 以降のrun | なし（dedupe） | 事実欠けの間なし → 揃ったら1回記録 | 同左 |

4週と12週の締切が同じrunで重なった場合は `milestone[book]["weeks"]` に**両方**載る
（旧単一スロットの後勝ち消滅を改修。トラップ#1）。

## 6. 検証

- VPS: 新規テスト5本 + 既存 milestone 系 pytest 全部green（実測ログはカード完了報告に添付）。
- 4週無変更の証明: HEAD版 `paperloop.py`（`git show f712c4b:src/yowayowa/paperloop.py`）と
  本変更版を同一合成ジャーナルで `_milestone_judgment(root, rows, book, 4, week_end)` に
  掛け、戻り dict の完全一致を確認（4週・Book C INSUFFICIENT 路線両方）。
- 実データドライ確認（本番 `data/demo-loop/` は無変更・コピー先でのみ実行）:
  最新週（2026-10-26）の週次生成で milestone が付かないこと、12週締切日を仮想的に
  詰めても12週が保留（ベンチDD浅い）で記録されないことを実測。
- `make verify`（ruff format/lint・mypy・pytest 全量・openapi）は **TSUKUMO** で実行
 （VPSでテスト全走させない規律）。結果はカード完了報告へ。

## 7. 判定が出たときの読み方（人間のゲート）

- 判定は record-only。発注・journal行・state遷移・live promotion は一切ない
  （実コードはジャーナルを1行も書かない。テストで journal 行数不変を確認済み）。
- `verdict=PASS` は「設計 §4 の基準を満たした記録」であって採用証拠ではない。
  実資金投入は別の人間の判断（t_af2b0af6 の G3/G4/G5）。
- `verdict=FAIL(switches)` のときの対応は設計 §4-2「変更」欄どおり人間が読む
  （影の帳簿の昇格検討。機械は何もしない）。

---

# rev2（2026-10-28・独立レビュー第1回 AS-M126-01..05 の是正）

監査（audit-sol・別系統）が候補 80511ce で5件を再現し差し戻し。本節は rev1 の記述の
うち以下を**置き換える**。凍結数値（`MILESTONE_RULES`）は無変更、record-only、
実注文・push・本番 `data/demo-loop/` 無変更は rev1 から継続。

## AS-M126-01 — 必須ゲートの削除で Book C が偽PASS（重大）

- rev1 の「C の switches ゲートは criterion に入れず、観測値をファクトとして記録する」
  （§2）は**廃止**。必須基準の未評価は欠損として扱い、criterion に
  `pass=None, reason="no_frozen_expected_rate"` を**残す**。全凍結ゲート
  （`MILESTONE_RULES[weeks]` の全キー）が criteria に存在し全て確定するまで
  総合判定は記録しない（`_milestone_long_judgment` に missing/undecided ガード）。
  rev1 は残り2ゲートで `verdict=PASS` を記録・dedupe しており、レート確定後も
  再判定できなかった。
- C の12週判定はレート凍結判断が降りるまで**毎週の週次runで保留として再試行**される
  （記録は出ない。fail-closed）。レートの凍結は数値凍結判断なので別カードへ起票する。
  （2026-10-28 追記: レート凍結判断が降りた — t_be52f5bc 決定A。Book C は凍結レートを
  持つためこの保留は解消し、12週判定は実判定として記録される。`pass=None` の保留規約は
  レートを持たない帳簿・資産向けに存続。本doc末尾節参照。）
- §5 の表の「締切時・事実欠け → 12/26 記録なし」に加えて「**必須ゲート未確定 →
  記録なし（保留継続）**」が C の12週の常態になる。

## AS-M126-02 — DD の期間不一致で偽PASS が永久固定（重大）

- rev1 の「帳簿とベンチを独立に抽出」した DD ゲート（§2 dd_vs_benchmark）は**置き換え**。
  **終点**は両側に窓最終日（start+weeks*7−1）の mtm mark を要求し、欠けている側を
  `book/benchmark_no_mark_at_window_end` で保留する。v1.9（t_94687d03）の終点不一致拒否を
  frozen 窓に適用したもの（frozen 窓の短縮はそもそも選べない）。
  **起点**は v1.9 の共通ベース契約（窓内で両側が mark を持つ最初の日）を採り、
  `comparison_start` に記録する。窓初日の mark を両側に強制しないのは、実データで
  株式ベンチが帳簿Cより4日遅く mark を開始しているため（窓初日強制は C の DD を
  永久保留にする）。共通起点なら両側同じ土俵で、短縮も記録に見える。
  帳簿の元本→起点までの損失は cum ゲート（元本1.0起点）が担う。
  criterion は `comparison_start` / `comparison_end` /
  両側の `*_mark_span` を記録する。
- mark は `mtm_date`（対象日）で帰属するため、遅れて書かれた窓内 mark で後の週次runが
  同一 frozen 窓の判定を完了する。rev1 の「帳簿 day7 まで vs ベンチ day83 まで →
  比率0.15でPASS確定」の反例は構造的に存在しなくなり、正しい同期間比 0.75（FAIL）が
  後のrunで1回だけ記録される。
- 12/26 双方に正常・不一致・遅延到着の回帰テストを追加
  （`test_milestone_dd_gate_requires_the_frozen_window_on_both_sides`）。

## AS-M126-03 — 累積損益の分母が初回 mtm（重大）

- rev1 の「最初と最後の mtm equity から」累積損益を取る実装（§2 cum_pnl_min）は
  **置き換え**。初回 mtm には初日のコスト・損益が既に含まれるため、リベースは
  元本1.0から初回 mtm までの損失を消していた（初回0.99・最終0.895で −9.596% と
  読み、−10%ゲートを偽PASS）。新実装は**窓内最終 equity − 1.0**（週次行
  `cum_return` の既存契約と同一、`value_basis="inception_principal_1.0"` を記録）。
  窓最終日の mark も要求する（途中日までの累積に12週ラベルを付けない）。
- DD 側の立ち位置を明記: DD の peak はReviewed な t_dddae39e 契約どおり day-0 mark
  出発（両側同じ土俵）。元本1.0→day-0 close の移動は cum ゲートが担う（両ゲートで
  無断に消さない役割分担）。初回 mtm≠1.0 の −10% 境界テストを追加
  （`test_milestone_cum_pnl_anchors_to_inception_principal`・−10.5% が FAIL）。

## AS-M126-04 — 資産別切替上限の合算（要修正）

- rev1 の「年率**合計** × 窓日数/365 ≤ 2.0倍」（§2 switches_multiple）は**置き換え**。
  設計 §2-3 は資産別（「帳簿Aの BTC なら12週で期待値は約2回、警告は5回以上」）なので、
  各資産の反転数を**その資産自身の**凍結年率×窓日数/365×2.0倍と比較し、
  `per_asset`（observed/expected/max/pass）を記録してから集約する。
  反例（監査再現）: BTC 6回は合算上限 7.134 では PASSだが BTC 自身の上限 4.050 では
  FAIL。ETH の静穏で BTC の過剰反転を相殺しない回帰を追加
  （`test_milestone_12w_switches_per_asset_no_cross_asset_offset`）。
- 反転の無い資産も `by_asset` に載る（ゼロも可視化）点は rev1 どおり。

## AS-M126-05 — 正直な遅延 mark で26週保留が解消しない（要修正）

- rev1 の26週コストゲートは mark の `recorded_at` で窓へ帰属させていた（§2）。
  実 writer `mark_up` は計測時刻を `recorded_at` に書くため、締切後に測定された
  窓内実行の計測は永久に除外され、保留が解消しなかった（監査が実 writer で再現:
  締切時 n=1 → 翌日2件追記 → ゲート n=1 のまま）。
- **期間帰属規約（明文化）**: mark の窓帰属は**決定の実行日**
  （decision 行の `decision_date`＝対象時点）で決める。`recorded_at`（記録時点）は
  証跡でしかなく、遡及させない（writer 契約・テストでも禁止）。criterion は
  `attribution="decision_execution_date"` と `late_recorded`（記録時点が窓外の
  採用mark数）を記録。decision 行の無い mark は `shortfall_errors` に
  `mark_decision_row_missing` で報告（v1.7 C-03 の無言捨て禁止契約を継承）。
  週次行の `shortfall_*`（記録時点集計）は**無変更**（`_week_shortfalls` は
  検証部分だけ `_shortfall_values` に共通化し、振る舞いは同一）。
- 既存 journal の改変は無し。実 writer 回帰
  （`test_milestone_26w_shortfall_late_marks_arrive_via_real_writer`）は `mark_up` を
  実呼び出しし（市場データ照会とシグナル再計算だけをテストダブル化）、
  recorded_at を遡らせずに n=1 → n=3 の復旧を示す。

## rev2 テスト

- 改修: `test_milestone_12w_switches_gate_fails_and_book_c_gap_is_recorded` →
  `test_milestone_12w_switches_gate_is_per_asset_and_book_c_holds`（C は PASS ではなく
  保留を期待するよう変更）。
- 追加4本: 上記 AS-M126-02/03/04/05 の回帰。rev1 の5本
  （同時締切・早期判定なし・署名付き+保留・浅いベンチ保留・旧形状互換）は
  fixture に窓端の mark を足した上で維持。
- 既存4週判定（criteria/verdict/notes）の無変更は rev1 の証明方法と同じく
  HEAD 版との dict 完全一致で再確認（実ジャーナルコピーの A/C）。

---

# rev3（2026-10-28・独立レビュー第2回 AS-M126-02-R2 / AS-M126-06 の是正）

監査（audit-sol・別系統）が候補 7218c36 で2件を実行再現し差し戻し。凍結数値
（`MILESTONE_RULES`）無変更・record-only・実注文/push/本番 `data/demo-loop/` 無変更は
従来どおり。前回5指摘の再確認（監査報告）: AS-M126-01/03/04/05 は是正確認、
02 は「手書きの対象日 mark 到着後の偽PASS解消」まで確認・運用復旧が未完だった。

## AS-M126-02-R2 — 窓最終日欠測が通常 writer では復旧しない（重大）

- rev2 の回帰は過去の窓最終日の mtm 行を**手書き注入**していた。実 writer `run_daily_mtm`
  は `mtm_day = now.date()` しか書かないため、価格が後日復旧しても過去の欠測日は生成されず、
  12/26 双方で `book_no_mark_at_window_end` が永久に残る（監査が実 writer で再現:
  12週の欠測 2026-03-25 → 復旧後の run が書くのは 2026-04-02 の mtm のみ。26週も同様）。
- **是正（実運用経路）**: `run_daily_mtm` に**復旧パス**（`_recover_unpriced_mtm`）を追加。
  当日の行を書く前に、各ブックの過去日で `mtm_unpriced` 記録が残り mtm 行が無い日を、
  **その日の時点の状態**で再評価する:
  - 状態再生は `_rebuild_state_as_of` — fill は**決定の実行日**（26週 shortfall 帰属と同じ
    対象時点契約）で、mtm は各行の `mtm_date` で再生する。**現在の保有を過去日に当てない**。
    decision 行の無い fill は実行日が確定できないため、そのブックのその日は復旧しない
    （fail-closed: 出所の無い過去評価を作らない）。
  - 価格は当日パスと同じ規約（`_latest_finalized_close` — 対象日より前の確定足）。
    当日の `mtm_unpriced` 行に記録された保有と状態再生の保有の**どちらの視点にあっても
    全て評価できたときだけ**行を書く（片方でも欠ければ復旧しない。量は状態再生から、
    必要な評価対象は両視点の和集合から取る）。
  - 書かれる行は `recorded_at` = 復旧runの実時刻（**遡及しない**）+ `recovered_from_unpriced:
    true`。元の `mtm_unpriced` 行は「何が起きたか」の証跡として残る。B2 の
    (book, mtm_date) 一意は維持（既に mtm がある日は何もしない）。cum_cost / cum_turnover /
    equity_gross は `_cost_accumulators_as_of` で**その日時点**の値を計算する
    （復旧日より後の行で過去日の連鎖を作らない）。
  - **loop が一度も走っていない日は作らない**: 復旧は記録された失敗（`mtm_unpriced`）を
    完遂するだけで、writer が見ていない日の事実は捏造しない
    （docs/DEMO_LOOP_V1_OPS.md §2 summary-v1.10 項・§3 C-04 項に明記）。
  - 既知の限界: 欠測日〜復旧日の間に既に書かれた中間日の mtm 行は書き換えない
    （ジャーナル追記のみ）。それらの `equity_gross` 等の diagnostic 連鎖は欠測日を
    スキップしたまま凍結される。DD/累積の各ゲートは equity mark を直接読むため影響しない。
- 回帰（**実経路のみ**。手書き mtm 行は使わない）:
  `test_run_daily_mtm_recovers_an_unpriced_milestone_window_end_12w` /
  `test_run_daily_mtm_recovers_an_unpriced_milestone_window_end_26w` — 実 writer が
  `mtm_unpriced` を書くところから始め、価格復旧後の**次の正直な 1 回の run_daily_mtm**
  （cron が呼ぶのと同じ呼び出し）で対象日が復旧すること（`recorded_at` は復旧run時刻・
  equity は対象日の保有×対象日の確定足・price_bar_date は対象日の根拠足・B2 冪等）、
  その後**同じ凍結窓が 1 回だけ**記録されること。26w は AS-M126-06 の未計測 fill 復旧と
  合成している（1 回の復旧run + 1 回の実 `mark_up` で窓が完成する）。

## AS-M126-06 — 未計測約定が残っても 26週 PASS を永久確定（重大）

- n>=2 は上界計算の最低件数であって「窓内の対象事実が揃った」ことを意味しない。
  監査が実行再現: 窓内 fill 3件・mark 2件（両方 −100bps）で締切日に **PASS 確定**
  （n=2・上界 −100bps）。翌日の実 `mark_up` が残り1件（+1000bps）を正直な recorded_at で
  追記すると全3件の上界は **869.7966…bps > 15（FAIL）** に変わるが、`already_judged=True`
  のため部分サンプルの PASS が永久に残った（AS-M126-05 の「n=1→3 で保留解消」は
  n=2 で先に確定すると届かない）。
- **是正**: `_milestone_shortfall_criterion` に**完全性照合**を追加。窓内の対象 fill 集合
  （event=fill・mark と同じ実行日帰属）を照合し、`completeness`
  （`target_fills` / `measured` / `unmeasured` / `unusable` を decision_id 付きで）を
  criterion に記録する。**未計測が1件でも残る間は `pass=None,
  reason="shortfall_unmeasured_fills"` で判定を確定しない**（記録なし・dedupe されない →
  実 writer で残りの計測が到着すれば同じ凍結窓が判定される）。
  使えない mark（shortfall_bps 欠落・非数）は v1.7 C-03 契約どおり統計から外して
  `shortfall_errors` に報告し、**保留にはしない**（writer が mark を書き直すことは無いため
  使えない行による永久保留は復旧不能で、本配線の目的（永久未評価の排除）に逆行する）。
  decision 行の無い fill は実行日が確定できないため未計測扱いでブロック（fail-closed:
  壊れたジャーナルの上で金銭ゲートを確定させない）。部分サンプルの上界値自体は
  `value` に事実として残る（pass は出ない）。
- 回帰3本:
  `test_milestone_26w_holds_while_a_target_fill_is_unmeasured`（3fill/2mark → 記録なし・
  再試行で dedupe されない・completeness 記録）/
  `test_milestone_26w_unmeasured_fill_completes_via_real_writer_then_judges_once`
  （実 `mark_up` が3件目を正直に追記 → **同じ窓**を FAIL として 1 回だけ記録
  （上界 869.7966…bps）・以後重複なし・summary 行上も 1 件）/
  `test_milestone_26w_pass_control_when_every_target_fill_is_measured`
  （未計測の無い正常 PASS は締切日に 1 回記録される対照）。

## rev3 テストと検証

- 新規5本（上記）+ 既存 milestone 系・C-04・cost/DD ユニットは無変更で green。
- `MILESTONE_RULES` の数値・4週ブロックは無変更（4週本体は rev1/rev2 と同じく
  AST・実ジャーナル dict 等価で再確認）。
- `make verify`（ruff format/lint・mypy・pytest 全量・openapi）は **TSUKUMO** で実行
  （VPSでテスト全走させない規律）。ログはカード完了報告へ。

# rev4（2026-10-28・独立レビュー第3回 AS-M126-02-R3 / AS-M126-07 の是正）

監査（audit-sol・別系統）が候補 7ff9f51 で2件（いずれも HIGH）を独立再現し差し戻し。
前回までの是正確認（監査報告）: AS-M126-01/03/04/05/06 と 02 の「単純な窓最終日欠測」は
実 writer 復旧まで確認済み。凍結数値・4週ブロック・record-only・本番無変更は従来どおり。

## AS-M126-02-R3 — 復旧が元の評価より後の同日約定を混ぜ、FAIL を PASS に変える（重大）

- rev3 の復旧は `_rebuild_state_as_of` で「その暦日の終わり」の状態を作っていた。監査の
  実行再現: 窓最終日の 00:00 に実 writer が `mtm_unpriced`（BTC .01 / cash 0）→ 同日 01:00
  の決定が 03:00 に実 `resolve_fills` で半分売却（BTC .005 / cash .49925）→ 後日 03-24
  close 85 が到着し復旧runが走ると、`executed <= day` の条件が**後の約定を含み**、
  equity = .92425（DD比 .37875・累積 −7.575%）で **12週 PASS** を記録。00:00 の失敗評価が
  見ていた保有を正しく評価すれば equity = .85（DD比 .75・累積 −15%）の **FAIL**。
  欠測の無い対照（00:00 mark → 同日の売却）では .85 / FAIL が記録され、後の売却は
  既存 mark を書き換えない — つまり復旧だけが別のスナップショットを評価していた。
- **是正（元の評価の時点への回帰）**: 復旧の評価対象は **`mtm_unpriced` 行自身の
  スナップショット**（units/cash）。peak/halt・cum_cost/cum_turnover・`equity_gross` の
  各鎖は **ジャーナル上その行より前の prefix**（`_unpriced_boundary`）から再生する。
  暦日の終わりの状態は使わない。`decision_date` は意図的に参照しない（決定の日付であって
  評価の時点ではない — 実約定時刻と決定日の区別は「日付で状態を作らない」ことで守る）。
  整合性検査: prefix 再生が行のスナップショット（units と cash の両方）を再現できない
  ジャーナルは回収を**拒否**する（どちらの読みにも賭けない fail-closed）。
  必要な評価対象はスナップショットの保有と行の `unpriced_assets` の和集合（rev3 の
  「両視点の和集合」を、評価対象がスナップショットに固定された形で継承）。
  `recorded_at` は復旧run時刻・`recovered_from_unpriced: true`・B2 冪等・元の行の残置は
  rev3 から不変。同日の後続執行は翌日の mark に初めて反映される（欠測の無い日と同じ）。
- 回帰: `test_run_daily_mtm_recovery_values_the_failed_snapshot_not_same_day_later_fills`
  （実 writer の `mtm_unpriced` → 実 `resolve_fills` の同日売却 → 復旧は .85 を書き
  PASS を書かないこと・day91 行に売却後の状態が初めて載ること・無欠測対照と
  verdict/DD比/累積が完全一致すること）。
  `test_unpriced_snapshot_refusal_keeps_the_window_unresolved`（スナップショットが
  prefix と不一致のジャーナルでは復旧しない + 下記 AS-M126-07 の保留が判定を止め続ける）。

## AS-M126-07 — 窓途中の既知未解決欠測を無視した部分 DD PASS の永久確定（重大）

- 端点の一致は内部の完全性の証拠ではない。監査が **12週・26週の双方**で実行再現:
  day0 equity 1.0、day40 で実 writer が `mtm_unpriced`。締切日にはその前日の close 97 だけ
  先に届いているため通常 writer は day40 を復旧できず、窓端 equity .97 で
  DD比 .15 → **PASS 確定**。後日 day39 close 70 が届き実 writer が正直に day40 を復旧
  （equity .70）すると同じ窓の DD比は 1.5（FAIL）だが、`already_judged=True` で最初の
  PASS が永久に残った。
- **是正**: `_unresolved_mtm_gaps` を追加し、DD基準が**凍結窓内の帳簿とベンチマーク双方**
  に残る既知の未解決 `mtm_unpriced`（同一 (book, mtm_date) の `mtm` 行がまだ無いもの）を
  確定前に照合する。1件でも残れば `pass=None, reason="unresolved_mtm_gap_in_window"` で保留し、
  criterion の `unresolved_mtm_gaps` に該当 (book, mtm_date) を明記する。判定は記録されないので
  dedupe も起きず、窓が完成した週次runが同じ凍結窓を1回だけ記録する。
  窓短縮・欠測日のゼロ騰落扱い・未試行日の捏造はしない。
  （初版 rev4 では照合を「暫定共通起点より前は数値に入らないため」比較区間に限定したが、
  この根拠は rev5 の AS-M126-07-R4 で**誤りと実証された** — 暫定起点より前の欠測は復旧時に
  起点自身を前へ動かし比較全体を作り直す。現行仕様は rev5 節のとおり**凍結窓全体**を照合する。）
- 回帰（実 writer 経路）: `test_unresolved_interior_mtm_gap_defers_12w_until_the_writer_recovers`
  / `test_unresolved_interior_mtm_gap_defers_26w_until_the_writer_recovers` — 双方とも
  「実 writer の途中欠測 → 締切日に記録なし（26週は shortfall ゲート確定済みでも DD の
  保留だけで記録しない）→ 遅延価格 + 通常 writer の復旧 → **同じ凍結窓**を FAIL 1回だけ
  記録 → 重複なし」＋**欠測の無い完全データの PASS 対照**（同一ゲートを通る PASS が
  1回記録され、`unresolved_mtm_gaps == []` を載せる）。

## rev4 テストと検証

- 新規4本（上記）。既存 rev3 の復旧2本（12w/26w）は新契約でも無変更で green
  （単純な窓最終日欠測では prefix 再生 = スナップショットなので数値不変）。
- `MILESTONE_RULES` の数値・4週ブロックは無変更。`make verify`（ruff format/lint・mypy・
  pytest 全量・openapi）は **TSUKUMO** で実行（VPSでテスト全走させない規律）。
  実データのドライ確認（本番 `data/demo-loop/` は無変更・scratchコピー）も実施。
  ログはカード完了報告へ。

# rev5（2026-10-28・独立レビュー第4回 AS-M126-02-R4 / AS-M126-07-R4 の是正）

監査（audit-sol・別系統）が候補 0208333 で2件（いずれも HIGH）を独立再現し差し戻し。
AS-M126-01/03/04/05/06 と 02-R3（翌日以降の復旧）・07（共通起点以降の途中欠測）の
是正維持は監査報告で確認済み。凍結数値・4週ブロック・record-only・本番無変更は従来どおり。

## AS-M126-02-R4 — 同日価格復旧が元の失敗スナップショットを迂回する（重大）

- rev4 の復旧（`_recover_unpriced_mtm`）は `day < mtm_day` の**過去日**しか扱わず、同日の
  通常パスは `mtm_unpriced` 行を重複防止に使うだけで**現在の保有**を評価していた。監査の
  実行再現: 窓最終日 00:00 に欠測（BTC .01 / cash 0）→ 01:00 決定を 03:00 の実
  `resolve_fills` で半分売却（BTC .005 / cash .49925）→ 前日 close 85 が**同日 06:00** に
  届くと、同日の再実行が equity = .92425（DD比 .37875）で **12週 PASS** を記録。元の失敗
  スナップショットなら .85（DD比 .75）の **FAIL** で、欠測の無い対照も翌日復旧も実際に
  .85 / FAIL — 価格の**到着日**が評価対象のスナップショットを選んでいた。
- **是正**: (1) `_recover_unpriced_mtm` の対象を `day <= mtm_day` に拡張し、**同日の欠測も
  過去日と同一の経路**（行自身のスナップショット + prefix 再生 + 整合性拒否 + 正直な
  `recorded_at` + `recovered_from_unpriced`）で完遂する。(2) `run_daily_mtm` の当日パスは、
  当日の `mtm_unpriced` 行がまだ `mtm` 行なしで残っている間は**現在保有を評価して黙って
  埋めない**（`continue`）。復旧が完遂できない日（バー未到着・スナップショット不整合）は
  その日は未評価のまま立ったままになり、以降のrunが同じスナップショットを再試行する
  （価格が評価できる限り永久に閉じない）。欠測の無い日と同様、当日の後続執行は翌日の
  mark に初めて載る。`unpriced` 行の重複防止はこのゲートが担うため、v1.8 C-04 の
  書込ブロックから `not unpriced_recorded` の条件は撤去（到達時点で常に未記録）。
- 回帰: `test_same_day_price_restoration_completes_the_failed_snapshot_not_current_holdings`
  （実 writer の欠測 → 実 `resolve_fills` の同日売却 → **同日**価格到着 → 同日再実行は
  .85 を書き .92425 を書かない・B2 で1回・正直な recorded_at・無欠測対照と
  verdict/DD比/累積が完全一致・売却は翌日 mark に初めて載る）。

## AS-M126-07-R4 — 暫定共通起点より前の凍結窓内欠測で偽 PASS が永久確定（重大）

- rev4 の欠測照合は `base_day..last_day`（**暫定**共通起点..窓端）だけを見ていた。監査が
  **12/26双方**で独立再現: 窓初日を実 writer が `mtm_unpriced`（締切時点で未解決）、
  day7 と窓端は正直に mark。締切週次は comparison_start = day7・`unresolved_mtm_gaps=[]`
  で総合 **PASS** を記録。締切後に初日の価格が届き通常 writer が day0 を復旧（equity 1.2）
  すると共通起点は day0 に移り、同じ窓の比は .958 > .6 の **FAIL** — しかし
  `already_judged=True` で PASS が永久残存。**復旧は最初の共通 mark 自体を前へ動かす**ので、
  「起点より前は数値に入らない」という rev4 の限定根拠は成立しない。
- **是正**: `_unresolved_mtm_gaps` の照合範囲を**凍結窓全体**（`start`..窓最終日・帳簿と
  ベンチ双方）に拡張する。暫定起点より前の既知欠測が1件でも残る間は判定を記録せず保留
  （記録なしなので dedupe されず、窓が完成した週次runが同じ凍結窓を1回だけ記録）。
  未試行日（遅れて開始した帳簿・ベンチ）は `mtm_unpriced` 行を持たないため保留しない
  — 保留するのは**記録された失敗**だけで、初日 mark の一律必須化はしない（既存 C 契約は
  変更なし）。docs の rev4 節の誤った根拠も是正済み。
- 回帰: `test_prebaseline_unresolved_gap_holds_the_window_until_the_writer_recovers_12w` /
  `..._26w`（実 writer で窓初日欠測 → 締切日に記録なし（26週は shortfall ゲート確定済み
  でも DD の保留だけで記録しない）→ 遅延価格 + 通常 writer の復旧（equity 1.2）→ 起点
  day0 で比 .958 / FAIL を1回だけ記録 → 重複なし）/
  `test_benchmark_side_prebaseline_gap_holds_and_a_late_common_base_still_judges`
  （**ベンチ側**の起点候補欠測でも同様に保留・解消後に判定、＋ 遅れて**開始するだけ**の
  ベンチは欠測行を持たず保留しない正常対照 = 遅い共通起点で PASS 記録）。

## rev5 テストと検証

- 新規4本（上記）。既存 rev2/rev3/rev4 の回帰（復旧2本・スナップショット等価・内部欠測
  2本・拒否1本ほか）は新契約でも無変更で green。`MILESTONE_RULES` の数値・4週ブロックは
  無変更。`make verify`（ruff format/lint・mypy・pytest 全量・openapi）は **TSUKUMO** で
  実行（VPSでテスト全走させない規律）。実データのドライ確認（本番 `data/demo-loop/` は
  無変更・scratchコピー）も実施。ログはカード完了報告へ。

# rev6（2026-10-28・独立レビュー第5回 AS-M126-08 の是正）

監査（audit-sol・別系統）が候補 001023b で1件（HIGH）を独立再現し差し戻し。
AS-M126-01〜07（02-R3/R4・07-R4 を含む）の是正維持は監査報告で確認済み。
凍結数値・4週ブロック・record-only・本番無変更は従来どおり。

## AS-M126-08 — 過去MTM復旧が現在equityを巻き戻し、後続の実fill量を変える（重大）

- 復旧行はジャーナルの**末尾**に追記されるが、対象日（`mtm_date`）は過去日である。既存の
  消費側 `rebuild_state` は「**最後に追記された** `mtm` 行を現在 equity として採用」する
  契約で、当日の mark が既にあると B2 で当日行は再追記されないため、過去 equity が
  現在値として残る。監査の実行再現（2回同値）: 帳簿A は BTC .01/cash 0、day40（02-10）に
  実 writer が `mtm_unpriced`。day83（03-25）00:00 に前日 close 97 で通常 MTM が
  equity **.97**。同日 06:00 に 02-09 close 70 が届き実 `run_daily_mtm` が day40 を
  **.70** で復旧追記（当日03-25分は既存のため追加なし）→ `rebuild_state.equity` が
  **.70 に巻き戻り**、07:00 の同一売却決定（delta_weight=-.5）を 08:00 bar open 100 で
  実 `resolve_fills` が約定させると **equity_at_fill=.70 / traded_notional=.35 /
  units_after=.0065**。欠測なし正常対照は **.97 / .485 / .00515** で、売却額は
  **27.835051546391743% 小さい**（コード集計）。表示やgross診断鎖の制限ではなく、
  実ペーパー保有と将来成績を変える。
- **是正（消費側契約・対象日順と追記順の区別）**:
  - `rebuild_state`: 保有（units/cash）は従来どおり fill の追記順。`mtm` 行から採用する
    順序敏感な現在値（**equity・halted**）は**対象日順**（最大 `mtm_date`・同日は後の
    追記が勝つ＝`_equity_as_of` が既に適用する規約と同一）に統一する。過去日の復旧行は
    現在状態を巻き戻さない。順序非敏感な集計（**peak** = max、**stopped** = sticky OR）
    は全行対象のまま（履歴の集計であって「現在」ではない）。
  - `_cost_accumulators`: `recovered_from_unpriced` の行は gross 鎖の**ステップではない**。
    as-written の鎖は欠測をまたいで既に複利合成済み（day40 欠測なら day41 の行は day39 から
    直接chain）で、追記順にステップを打つと翌日の移動を復旧した**過去**equityで割り、
    書き済み履歴を再定義してしまう。復旧行自身の鎖列は復旧時の prefix 再生値を保持する。
    （**rev7 で精緻化**: この「一律スキップ」は不完全だった — スキップが正しいのは
    **遅延復旧**のみで、最新対象日で次の mark より前に完遂した復旧はステップに残す。
    下記 rev7 節を正とする。）
  - `_unpriced_boundary`: 上記2つの規約を鏡像する（prefix 内に過去の復旧行があっても、
    halted は対象日順・鎖ステップは復旧行をスキップ）。これで復旧の prefix 再生が
    as-written の鎖と一致し続ける。（rev7 の2分岐を鏡像するように拡張。）
  - **守った制約**: 当日の mark が既にある日の復旧も**放棄しない**（対象日拡張は rev5 の
    まま `day <= mtm_day`）・当日 `mtm` の二重追記なし（B2）・過去行の改変なし
    （追記専用）・`recorded_at` の遡及なし（復旧runの実時刻）。
- 回帰: `test_recovered_past_mark_does_not_rollback_current_state_or_fill_sizing`
  （監査の反例を全工程**実 writer** で再現: day40 実欠測 → day83 00:20 実 mark .97 →
  同日 06:00 実復旧 → `rebuild_state` は **.97** を維持（巻き戻し)=.70 は書かない）→
  07:00 決定を実 `resolve_fills` で 09:00 約定 → **equity_at_fill .97 /
  traded_notional .485 / units_after .00515**＝無欠測対照と完全一致。復旧行の鎖列は
  対照の当日行と完全一致（equity/equity_gross/cum_cost_assumed/cum_turnover/peak_equity）。
  翌日の実 writer の行も対照と全列一致（gross 鎖が復旧行をステップ扱いしていない証拠。
  rev5 の追記順ウォークでは翌日行の equity_gross が対照と乖離する）。復旧値は
  **70（現在markより低い）と 120（高い）の両方**で parametrize。
  `test_unpriced_boundary_skips_a_recovered_mark_inside_the_prefix`（prefix 内の復旧行が
  halted を巻き戻さず鎖ステップにもならない契約をユニット固定）。

## rev6 テストと検証

- 新規2本（+parametrize で実行体3件）。既存 rev2〜rev5 の回帰（同日復旧・翌日復旧・
  12/26 保留→復旧→1回記録・4週互換ほか）は新契約でも無変更で green。
  `MILESTONE_RULES` の数値・4週ブロックは無変更。`make verify`（ruff format/lint・mypy・
  pytest 全量・openapi）は **TSUKUMO** で実行（VPSでテスト全走させない規律）。
  実データのドライ確認（本番 `data/demo-loop/` は無変更・scratchコピー）も実施。
  ログはカード完了報告へ。

# rev7（2026-10-28・独立レビュー第6回 AS-M126-09/AS-M126-10 の是正）

監査（audit-sol・別系統）が候補 55ef1be で2件（MEDIUM2・HIGH0）を独立再現し差し戻し。
AS-M126-01〜08（08 の実 fill 誤量を含む）の是正維持は監査報告で確認済み。凍結数値・
4週ブロック・record-only・本番無変更は従来どおり。監査明記のとおり金銭ゲートの
偽 PASS や実 fill 誤量の残存を主張するものではなく、別 consumer の金融レポート回帰。

## AS-M126-09 — パネルの as-of 消費側が過去 MTM を当日の値として採用する（中）

- rev6 で `rebuild_state`/`_equity_as_of` は対象日順に統一したが、**public panel の
  2読取関数が残っていた**: `paperloop_panel._equity_on`/`_equity_gross_on` は
  「対象日 <= 照会日の**最後の追記**」を採用するため、末尾追記の過去復旧行が後の日の
  行に載る。監査の実行再現（run5/run6）: day83 の正常 mark（equity .968545）の後に
  day40 を .69895 で正直に復旧追記し、public `build_panel(start=end=03-25)` を引くと
  03-25 行が **.69895**（乖離 **26.9595pp**）。同じ週次行は `books.A.cum_return=-.031455`
  なのに public `weekly_summary` の実 Q4 が `by_bucket.*.mean_excess=-.30105`。
  Q4 は informational でマイルストーン判定や PAUSE には影響しない（監査明記）。
- **是正**: 2読取関数を「**最大 `mtm_date` <= 照会日**・同日は後の追記が勝つ」の
  対象日順に統一（`rebuild_state`/`_equity_as_of` と同一規約）。過去行改変・復旧放棄・
  当日 MTM 二重追記・記録時刻遡及はなし。
- 回帰: `test_panel_as_of_reads_target_day_order_not_append_order`（実 writer 全工程・
  照会日の自分の mark を読むこと・無欠測対照と day83 equity 完全一致・復旧値は
  載らない）＋ `test_weekly_q4_excess_reads_the_marked_day_not_the_recovered_past`
  （public `weekly_summary` の Q4 が n=1/unjudgeable=0 で `mean_excess == cum_return`・
  無欠測対照と同一ブロック）。

## AS-M126-10 — 最新対象日の復旧まで gross 鎖から一律除外し、翌日の費用寄与を失う（中）

- rev6 は「復旧行は鎖ステップではない」を**一律**適用した。後続の書き済み mark がある
  遅延復旧では正しいが、**最新対象日で次の mark より前に完遂した復旧**までスキップすると、
  欠測を跨いで合成されるはずだった初回の費用ステップとその後の複利寄与が消える。
  監査の実行反例（2回同値）: 初回実 buy（費用 .0015）→ day40 00:00 欠測 → **同日 06:00**
  に価格 70 で復旧（equity .69895/gross .70045・対照一致）→ 同日 07:00 決定を 09:00 に
  実 sell（notional .349475/費用 .0005242125）→ day41 の価格 140 で実 MTM。net equity と
  累積費用は両経路同一（1.2575857875/.0020242125）なのに、gross は対照 **1.26081** に対し
  復旧経路 **1.25961**（.0012 欠落）。cost_drag は正常 **.25572548599709366pp** に対し
  **.16070152666302073pp**（**37.15858% 過少**）。独立式
  `(E40+Cbefore40)/E40*(E41+C40to41)` = **1.26081** は正常対照と一致する。
- **是正（鎖契約の2分岐）**: 復旧行のステップは「**追記時点で as-written の鎖が既に
  対象日を越えていたか**」で分岐する。実装は鎖を追記順に辿り、復旧行は**対象日がそれまで
  に見た非復旧 mark の最大対象日より新しいときだけ**ステップになる:
  - **最新対象日の復旧**（後続 mark 未書込）→ ステップ。次の行は復旧 equity から
    chain し、各セグメントの費用だけをそのステップで add-back する（独立式と一致）。
  - **遅延復旧**（より後の実 mark の書込後に追記）→ ステップではない。その書き済み
    ステップが既に欠測を跨いで複利合成済みで、追記順にステップを打つと書き済み履歴を
    復旧した**過去** equity で割って再定義する（AS-M126-08 の禁止を維持）。
  - 対象日が解釈不能な復旧行は順序づけできないためステップにしない（fail-closed）。
    復旧行自身の鎖列（復旧時の prefix 再生値）は両分岐で不変。
  - `_cost_accumulators` と `_unpriced_boundary` が同じ規約を鏡像する。復旧の
    書き手（`day <= mtm_day`・失敗スナップショット・prefix 再生・整合性拒否・正直な
    `recorded_at`・B2・追記専用）は rev5/rev6 から無変更。
- 回帰: `test_cost_accumulators_step_branch_for_recovered_marks`（2分岐のユニット契約・
  非ゼロ費用で両分岐を区別）、`test_recovery_before_the_next_mark_keeps_the_next_days_gross_step`
  （監査反例を全工程**実 writer** で再現: 同日復旧 → 同日実 sell → 翌日 mark。翌日 gross は
  無欠測対照と独立式に一致・cost_drag も対照一致・B2 1回）、
  `test_unpriced_boundary_chain_branches_for_recovered_marks`（旧
  `..._skips_a_recovered_mark_inside_the_prefix` を2分岐の契約で区別できるよう
  非ゼロ費用付きに拡張し直したユニット）。既存 rev2〜rev6 の回帰（遅延復旧の非ステップ・
  AS-M126-08 の .97/.485 維持・12/26 保留→復旧→1回記録・4週互換ほか）は新契約でも
  無変更で green。

## rev7 テストと検証

- 新規4本＋境界ユニット1本の契約差し替え。`MILESTONE_RULES` の数値・4週ブロックは
  無変更。`make verify`（ruff format/lint・mypy・pytest 全量・openapi）は **TSUKUMO**
  で実行（VPSでテスト全走させない規律）。実データのドライ確認（本番
  `data/demo-loop/` は無変更）も実施。ログはカード完了報告へ。

---

# Book C switch期待値の凍結配線（t_5a2489f5・2026-10-28）

帳簿C（S-02）の12週switchesゲートについて役員判断（t_be52f5bc・cio-fortuna）で
**決定A（凍結）**が確定したため、採番カード t_b63497a9 の成果物の値を
`MILESTONE_SWITCH_EXPECTED_ANNUAL` へ配線した。凍結値そのものは本カードでは
一切再計算していない（逐語の採番）。

- **凍結値（1dp・ROUND_HALF_UP・逐語）**: SPY=5.6 / IWM=7.7 / JNJ=10.5 / PG=14.6。
- **出所**: 実装定義ミラー（SMA200クロス・資産別カーソル）、窓2021-01-04..2026-09-28、
  rate = switches / span_days × 365。採番スクリプト
  `scripts/research/book_c_switch_rate_numbering.py`（コミット
  6d9459664e044cf085572525d521488afab05bd0）＋成果物JSON
  `scripts/research/book_c_switch_rate_numbering_result.json`
  （sha256 3c14ffb1d0941ff762b6e46edfada7a2b0dbc7b1f27acf2712eb83c46ba9f23b、
  `results.<SYMBOL>.primary.rate_1dp` を逐語使用）。クロスバリデーション
  （カーソル法×符号列法）8/8一致・第三実装スポット検証一致は採番カード完了報告参照。
- **TR注記（データbasis）**: 採番は**生価格basis**（ランタイムのデータストアと同一:
  `data/stock-ohlcv/<SYMBOL>/ohlcv.jsonl`・provider=alpaca・TR調整なし）。
  ランタイムのデータbasisがTR調整に変わった場合（**AS-TR-01/02 の是正が降りた時点**）
  はBOOK_Cのレートを**再採番して凍結し直す**（凍結値の前提が崩れるため。成果物JSONの
  `data_basis.store` も同一トリガを明記）。S-01の8.8/6.7は設計doc凍結値であり
  再採番の対象外。
- **コード**: `paperloop.py` 定数への `BOOK_C_BOOK` 追加＋定数近傍コメントへの出所明記・
  `_milestone_switches_criterion` docstring・モジュールdocstring（summary-v1.10 rev2項）
  への凍結済み追記。判定ロジック（資産別判定・保留規約）自体の変更はなし。
- **テスト**: `test_milestone_12w_switches_gate_is_per_asset_and_book_c_holds` を
  `test_milestone_12w_switches_gate_is_per_asset_and_book_c_judges` へ改修 — Book C の
  12週判定が**記録**される（verdict、`criteria.switches_multiple` に expected/max/pass、
  `reason` なし）期待値へ。TZ=UTC の実測結果はカード完了報告へ。
- **完了条件との対応（t_be52f5bc 決定A）**: (a) 当該テストは
  `reason="no_frozen_expected_rate"` を期待しない（`reason is None` を固定）。
  (b) C の週次行の switches は観測値ファクトではなく**評価される**（凍結レート有り）。
  (c) 12週分蓄積後の最初の週次runで C の milestone 12週総合判定が記録され、
  `criteria.switches_multiple.pass` が True/False（Noneでない）になる。
- **設計doc（repo外）**: SHORT_TERM_STRATEGY_DESIGN.md §2-3（board添付
  attachments/t_421158aa/）はrepo外のため本変更では更新しない。凍結値の反映は
  COO/オーケストレータ経由で行う（本カードのコメントに注記済み）。
