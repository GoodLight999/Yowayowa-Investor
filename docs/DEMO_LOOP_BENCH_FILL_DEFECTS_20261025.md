# デモ運用の計測欠陥2件: EQUITY_EW ベンチ未起動（C比較が永遠null）と C 初回約定の1.5日遅延 — 原因・再現・受入実測

- カード: t_d0fbc804（親: t_76501096 の分析レポート `DEMO_LOOP_LOSS_ATTRIBUTION_20261025.md` §11/§12 の附随発見）
- 作成: 2026-10-25（worker-flash）。実行ホスト: ubuntu（VPS）。実発注 0 件・実資金 0 円。本作業の全書き込みはジャーナル既存行に触れない（sandbox は kanban workspace 配下）。
- 検証コード: メイン checkout の working tree から凍結スナップショット（`sandbox/src_snap`、`src/yowayowa/paperloop.py` sha256 `053000c626a2483f45c19438d70b60aff54768da87e2a4fb95ab911c91ca392d`）。並行カード t_52c4aa20（A_HALF）が同一ファイルを編集中のため、スナップショット固定で実施（実行前後で live ファイルの sha256 同一を確認）。
- 行番号・時刻は特記なければ `data/demo-loop/journal.jsonl`（307行時点）と stock-ohlcv 各銘柄 `ohlcv.jsonl` の実測。

## 1. 結論

1. **欠陥1（EQUITY_EW が一度も決定しない）は実行時のコード不具合ではない。** 2026-10-19（月）の decide 時点で EQUITY_EW は `BOOKS_ENABLED` に入っていなかった。有効化コミット d94514d は **2026-10-23 09:28 +0900**（t_dddae39e）で、次の月曜が 2026-10-26 なので初決定はそこで起こる設計になっている。タスク本文の「BOOKS_ENABLED に入っている（現行 paperloop.py:191）のに落ちている」という前提は、現行ツリーを 10-19 時点のツリーと見比べたアナクロニズム。
2. **欠陥2（C の初回約定が約1.5日遅れ+無言）の原因は、株価日次バーの store 到着遅延。** 10-19（月）セッションのバーは **2026-10-21T00:21:22Z**（close から +28.4h）に store へ到着し、2026-10-20T02:05Z の fill run は 10-19 バーを持っていなかった。旧コードは約定候補なしの保留を**行も残さず** skip したため、run 行 :196-:201 に C の痕跡が一切ない。バー到着後の 10-21T02:05:49Z run で C は設計どおり 10-19T13:30Z の寄り値で約定した（:218-:221）。

## 2. 欠陥1 の根拠（3系統の実測）

**(a) git（有効化の時系列）**

| コミット | 日時 (+0900) | `BOOKS_ENABLED` |
|---|---|---|
| 3a48814（10-19 13:57 JST decide run の `code_rev` の committed ベース） | 2026-10-18 14:24 | `(A, CRYPTO_1N)` |
| 79a3bc9（Book C デプロイ内容。dirty tree 経由で 10-19 13:57 JST の decide に効いていた） | 2026-10-19 13:59 | `(A, CRYPTO_1N, C)` |
| d94514d（t_dddae39e「enable EQUITY_EW benchmark and per-book comparison line」） | 2026-10-23 09:28 | `(A, CRYPTO_1N, C, EQUITY_EW)` |

**(b) journal（mtm は `for book in BOOKS_ENABLED` で書かれるため、有効化前の帳簿は行を1行も書かない）**

- 10-19〜10-22 の mtm: A / CRYPTO_1N / C のみ（:166-:168, :181, :192-:194, :211-:213, :238-:240）。
- EQUITY_EW の最初の行は **:260（mtm, 2026-10-23, 10-23T00:21Z run）**。decision も halt_check も 10-25 時点で 0 行。

**(c) 再現 R0（era sandbox・オフライン・実データ）**

- 条件: journal を :174 で切断（10-19T02:05Z fill run の終端まで）、stock/crypto 両 store を `retrieved_at ≤ 2026-10-19T04:57:50Z` で切断（全4銘柄の newest セッション = **2026-10-16T04:00:00Z**）、`BOOKS_ENABLED=(A, CRYPTO_1N, C)`（era 値）を注入して `run_daily_decide(2026-10-19T04:57:50Z)`。
- 結果: 書かれた decision は **実際の :177-:180 と同一の decision_id 4件**（`C:{SPY,IWM,JNJ,PG}:2026-10-19`）のみ。**EQUITY_EW の行は 0 件**（decision/mtm/halt_check/fill 全て）。→ 10-19 の本番結果をコード・データともに再現し、EQUITY_EW だけが欠ける唯一の差分が有効化セットであることを示した。
- 同じ sandbox を現行の `BOOKS_ENABLED` で 10-26（月）に回すと（R3）、EQUITY_EW は halt_check 1件（trips 無し）+ 買い4件（delta 0.25 ずつ、fill_status=pending、regime_state=not_applicable）を書く。prereg 済み（`prereg.jsonl:6`）、`closes_ok`（SMA200+1 以上の履歴）も満たす。**prereg チェックやテープ参照で落ちるパスは存在しない。**

## 3. 欠陥2 の根拠

**(a) バー到着の実測（`retrieved_at`、4銘柄同一パターン・SPY 例）**

| as_of（セッション） | retrieved_at（store 到着） | close からの遅延 |
|---|---|---|
| 2026-10-16（金） | 2026-10-18T23:31:03Z | +51.5h |
| **2026-10-19（月）** | **2026-10-21T00:21:22Z** | **+28.4h** |
| 2026-10-20（火） | 2026-10-22T00:21:08Z | +28.4h |
| 2026-10-23（金） | 2026-10-24T23:30:30Z | +27.5h |

IWM/JNJ/PG も同一（10-19 バーは 10-21T00:21:22〜23.4Z）。**02:05Z の fill run に必要な「前日セッションのバー」は、日次 fetch（00:21Z）が取りこぼすと翌々日 00:21Z まで届かない実測がある。**

**(b) 10-20T02:05Z run の無言スキップ**

- その時点の store newest = 2026-10-16（R1 で実証。`retrieved_at ≤ 2026-10-20T02:05:48.783325Z` フィルタで再構成）。
- C の decision recorded_at は 2026-10-19T04:57:50.269304Z（:177-:180）。`_first_fill_candidate` は「decision 後の最初のセッション寄り」を日次 store から探すが、newest が 10-16 のため候補なし → 旧コードは `continue` するだけで**何も書かなかった**。
- run 行 :196-:201 の内容は A×2 + CRYPTO_1N×2 の fill と run start/end のみ。C の fill_wait/fill_rejected も無い。10-20 / 10-21 の C mtm は現金 1.0 のまま（:194, :213）＝実運用なら約2日ノーポジ。

**(c) 10-21T02:05:49Z run（バー到着後）**

- :218-:221 で C 4件が約定。bar = 2026-10-19T13:30:00Z、価格 = SPY 767.23 / IWM 271.82 / JNJ 254.43 / PG 157.87（いずれも 10-19 の寄り。決定日に約定したことにする設計どおりのバー選択）。
- `fill_lag_sec = 30729` は `fillバーのopen − decisionのrecorded_at`（13:30:00 − 04:57:50.269 = 30729.7s の切り捨て）。決定→約定バー確保までの所要。**決定から約定計上までは 45h08m**（10-19T04:57:50Z → 10-21T02:05:49Z）。

## 4. 修正（本カードの diff: `evidence/fix_t_d0fbc804.diff` 110行）

1. **paperloop.py — `fill_wait` 行（v1.9, t_d0fbc804）**: 約定候補がない保留は、理由 `no_fill_bar` + `needed_after` + `store_newest`（`daily_newest_session` / `tape_newest_open_ts`）+ run_id を書いてから待つ。診断行であり decision を完結させない（B1 が読むのは fill/fill_rejected のみ）、`rows_written` には不含（halt_check と同じ扱い）。
2. **ops/demo-loop/fill_v2.sh — fill run 直前の株価日次更新（v1.2, t_d0fbc804）**: `stock-fetch SPY IWM JNJ PG --days 10`（凍結された EQUITY_UNIVERSE と同一銘柄）を resolve_fills の直前に実行。00:21Z fetch の取りこぼしに対する第二次チャンスになる。ベンダー自身が 02:05Z を跨ぐ遅延のときは fill_wait で可視化され、翌日 run で約定する。
3. **paperloop.py — `_KNOWN_JOURNAL_EVENTS` へ `fill_wait` を登録（v1.9・監査 Round1 指摘）**: 新 event `fill_wait` を既知イベント集合に追加する（発注を持たない診断行として `mtm_unpriced` と同一扱い・1行）。未登録のままでは凍結 §4 のマイルストーン判定 `orders`（未知 event を発注経路として fail-closed で数える・判定は1回限り）が fill_wait を発注経路とみなし、帳簿Cの4週判定が偽 FAIL に固定される（実測: 判定対象フィクスチャ journal 内の fill_wait 196行が `orders` 値として計上・マイルストーン系テストが RED→GREEN で反転）。2026-11-09 00:50Z の weekly run までに反映（hard: 11-16）。
4. **変えないもの**: 判定ロジック・凍結パラメータ（SMA200 / コスト15bps / 絶対停止線 / C の週運用）、約定バー選択（`_first_fill_candidate`）、既存ジャーナル行。すべて不変。

## 5. 再現手順（全部 sandbox・オフライン・実データ）

```
cd /root/current-work/yowayowa-investor
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/run_repro.py R0   # era 10-19 decide（§2(c)）
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/run_repro.py R1   # era 10-20T02:05Z fill run（欠陥2再現+修正後の可視化）
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/run_repro.py R2   # 同一瞬間、10-19バーあり → 同一runでC約定
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/run_repro.py R3   # 10-26（月）decide → EQUITY_EW 4決定
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/run_repro.py R3B  # 10-27T02:05Z resolve → equity 8保留が fill_wait
YOWAYAWA_MODE=personal .venv/bin/python \
  /root/.hermes/kanban/workspaces/t_d0fbc804/sandbox_r4.py     # weekly_summary(week_end 2026-10-26) の比較行
```

- 実行結果（5段階すべて OK・exit 0・ログは `evidence/R*.log`、機械可読は `evidence/r*.json`）:
  - **R0**: decision books = `['A','C','CRYPTO_1N']`、C の decision_id 4件は実 :177-:180 と一致、EQUITY_EW 行 0。
  - **R1**: C の `fill_wait` 4件（reason=`no_fill_bar`、`needed_after=2026-10-19T04:57:50.269304+00:00`、`daily_newest_session=2026-10-16`）、C の fill 0。旧コードなら無言だった箇所に痕跡が立つ。
  - **R2**: 同一 10-20T02:05:48Z run で C 4件が約定（bar 10-19T13:30Z、価格は :218-:221 の実績値と完全一致）。**バーが store にあれば翌 02:05Z run が当日分を計上する**ことの反実仌実証。
  - **R3**: EQUITY_EW が halt_check 1件（trips 全 false）+ 買い4件（0.25, pending, not_applicable）。mtm は A / A_HALF / C / CRYPTO_1N / EQUITY_EW の5帳簿（A_HALF は並行カード t_52c4aa20 の分）。C の mtm price_bar は 2026-10-23。
  - **R3B**: 火曜 02:05:49Z で equity 8保留（C+EQUITY_EW）に `fill_wait`（`daily_newest_session=2026-10-23`＝まだ月曜バーが無い状態の実挙動）。crypto 帳簿の wait は sandbox が 1h fetch をスタブしたことによる副産物（本番はネット取得あり）。
  - **R4**: `weekly_summary(week_end=2026-10-26)` → C `comparison_reason="benchmark_has_no_fill"`（妥当な理由文字列）・EQUITY_EW fills=0。EQUITY_EW の初約定後の最初の週次行から、C の比較行は比較開始日（= EQUITY_EW 初 fill 日）付きの実数値（reason=null）になる。A の sandbox 値は親カードの実測（cum_return −0.04314）と一致し、sandbox の妥当性を交差確認。

## 6. 受入と本番観測（月曜〜火曜に自動判定できる）

- **欠陥1**: 10-26（月）09:20 JST decide cron 後、
  `jq -r 'select(.event=="decision" and .book=="EQUITY_EW")|.asset' data/demo-loop/journal.jsonl | sort | uniq -c` → SPY/IWM/JNJ/PG が各1。R3 で事前実証済み。次の週次行から C の `comparison_reason` は「null（実数値）」または妥当な理由になり、永遠 null は解消する。
- **欠陥2**: 10-27（火）11:05 JST fill cron（fill_v2.sh 修正済み経路）で、
  (a) バーが間に合った場合: C+EQUITY_EW の8約定が **当日（10-26）の 13:30Z 寄り**で計上（fill_lag ≈ 12.6h）。
  (b) ベンダー遅延の場合: equity 8保留に `fill_wait`（reason + store_newest 記録）→ 翌水曜 run で約定。
  どちらでも**無言スキップは再発しない**。注: 実測の到遅延は close+27.5h〜+51.5h で幅があり、(b) を排除できない。火曜 run で「02:05Z 時点のベンダー可用性」の実測が1件増える。

## 7. 補足・限界

- 10-19 の decide が 13:57 JST（現行 cron は 09:20 JST）なのは、Book C デプロイ当日の初回実行だったため（run :175, job=daily, code_rev 3a48814+dirty）。
- `retrieved_at` は成功時のみ記録される。10-20T23:30Z 帯に fetch が「走って失敗した」のか「ベンダーに無かった」のかは store だけでは区別できない。本書は到着時刻の実測のみを主張する。
- 本番 `weekly_summary.jsonl` の既存行は schema v1.2 のまま（:14-:16）。v1.8/v1.9 のサマリー変更（week_end 打ち止め・endpoint_mismatch 等）は t_94687d03 系カードの範囲で、本カードの主張に含めない。
- evidence 一式: `/root/.hermes/kanban/workspaces/t_d0fbc804/evidence/`（fix diff、R0〜R3B ログと JSON、snap lineage、r4_weekly）。
