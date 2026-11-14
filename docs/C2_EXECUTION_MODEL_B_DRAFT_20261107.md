# C2 ペーパー執行モデルB — 実装ドラフトと反映手順（2026-11-07 作成 / 未反映）

カード: t_07172f52（親 t_88623ba6 判定書 §5.4）
状態: **コードは実装・検証済み。本番ツリー（`/root/current-work/yowayowa-investor`）には未反映。**
理由: カード本文の「2026-11-10 09:40 JST の観測が終わるまで適用しない」。本番ツリーは
`c2_paper_weekly.sh` が直接実行するツリーなので、置いた時点で 11-09 週次から挙動が変わる。

---

## 1. 置き場所

- ブランチ: `wt/t_07172f52`（worktree: `.worktrees/t_07172f52`）
- `941266d` snapshot: 本番working tree の C2 ファイル（他カードの未コミット差分）を固定した土台。
  HEAD(277ae01) 単体では C2 スイートが動かない（`compute_features(include_target_date=)` が
  HEAD に無い）ため、検証可能な土台として必要。
- `f1c8926` feat: 本カードの差分（これだけを適用する）。
- パッチ: 添付 `t_07172f52_execution_model_b.patch`（`f1c8926` の内容）

## 2. 変更点（3つ + テスト）

1. `resolve_reference_prices()`（`c2_runner.py`）
   - 発注直前に 4 銘柄の直近価格を取得し `reference_price` を差し替える。
   - 優先順: latest trade → latest quote の mid → 前営業日終値。
   - 指値は `ref × (1 + 50bps)` のまま（上限コスト $500 は不変）。
   - 前営業日終値から **±20%** を超える print は異常値として捨て、終値を使う（理由を記録）。
   - データ API 例外は握って `stale_close` に落とす（発注経路を止めない）。
2. `run_market_fallback()` + `scripts/paper_c2/c2_fallback_batch.py`
   - 10:00 ET 以降、当日セッションの未約定 C2 指値を **1件ずつ** `cancel_order(order_id)` で
     取り消し（`DELETE /v2/orders/{id}`）、同じ qty を `type=market` / `TIF=day` で再発注。
   - 再発注の前に「取消が効いたか」を open orders で再確認し、残っていたら発注しない
     （キャンセル失敗時に二重建玉を作らない）。
   - 二重実行防止: `state.last_market_fallback_date` と `orders.jsonl` の
     `reference_price_source="market_fallback"` 行の両方でガード。
   - 実行しない条件: kill switch 発動中 / 起算前 / セッション日不一致 / 10:00 ET 前 /
     16:00 ET 以降 / 対象の未約定注文なし / `--dry-run`。
3. `orders.jsonl` に `reference_price_source`（`live_quote` / `stale_close` /
   `market_fallback`）と `reference_price_as_of_utc` / `reference_detail` /
   `stale_close_price` / `replaces_order_id` を追加。
4. `cancel_all_orders(reason=...)` は `reason="kill_switch"` 以外で `PermissionError`。
   非テストコードの呼び出しが kill switch の1箇所だけであることを AST で固定
   （`tests/test_c2_execution_model_b.py`）。

## 3. 検証済み（実測）

```
# ブランチ worktree 内（本番ツリーを汚さない）
.venv/bin/python -m pytest tests/test_c2_*.py -q      → 43 passed
ruff check（変更5ファイル）                            → All checks passed
ruff format --check（変更5ファイル）                    → 5 files already formatted
```
- 新規テスト15件: live_quote / quote mid / データAPI失敗時のstale_close / ±20%異常値棄却 /
  未約定→取消→成行→filled の遷移 / dry-run / 10:00 ET前 / 引け後 / kill switch /
  取消未確認時に発注しない / 冪等 / 呼び出し箇所AST監査 / 単票取消URL。
- `make verify` は共有ツリーの既存 lint 28件（他カードのファイル）で落ちるため、本カードの
  5ファイルが ruff 0件であることを個別に確認する形にした。全スイート実行は別途。

## 4. 反映手順（2026-11-10 09:40 JST の観測後）

```bash
cd /root/current-work/yowayowa-investor
git log --oneline -1                     # 作業前に必ず確認（並行workerのコミット混入防止）
git cherry-pick -n f1c8926               # 本カードの差分だけを適用
git status --short scripts/paper_c2 tests # 想定5ファイル
.venv/bin/python -m pytest tests/test_c2_*.py -q   # 43 passed を確認
```
- `git cherry-pick` がコンフリクトした場合: `c2_runner.py` は他カード（t_277a5061）が
  同じ関数群を触っている可能性がある。**手で解いてはいちど中断**し、`c2_runner.py` の
  現在の内容と本ドラフトの意図（§2）を突き合わせてから進める。
- 反映後もデータは書き換えない（次の週次まで `orders.jsonl` は増えない）。
- fallback の cron 登録は**このカードの範囲外**（COO 裁可が必要）。

## 5. 時刻の換算（重要・カード本文と食い違う点）

カード本文は「10:00 ET（14:00 UTC）」と書いているが、**11月は EST（UTC-5）なので
10:00 ET = 15:00 UTC = 翌 00:00 JST**。実装は `America/New_York` で判定しており、
UTC をハードコードしていない（14:00 UTC = 09:00 EST で早すぎる）。
cron を作る場合は `0 0 * * 2`（JST 火曜 00:00 = 月曜 15:00 UTC）が該当。
ただし day TIF の注文は 16:00 ET に失効するので、**引け後は何もしない**ガードを入れてある。

## 6. 証拠の出るタイミング（cron を入れない場合）

発注は週次（月 22:00 JST = 13:00 UTC）だけなので、`reference_price_source="live_quote"` の
実データ行が出るのは **2026-11-16 22:00 JST の週次**（＝11-10 09:40 反映後、最初の週次）。
11-10〜11-11 に実測しようとしても注文が無い。完了条件3は 11-17 以降に確認する。

## 7. やっていないこと

- `data/paper_c2` への書き込み: 0（テストは全て tmp_path）
- 本番 cron の変更: 0 / 新規 cron: 0
- 実弾・live API・発注: 0
- データAPIの実打鍵: 0（この実行では heredoc 実行が承認ガードで止まったため未実施。
  latest trade/quote のレスポンス形は未実測で、欠損時は `stale_close` に落ちる実装）

## 8. 2026-11-10 反映記録（実測）

前提が変わっていた: 11-07 の snapshot（941266d）以降に t_dd5b684f（注文ライフサイクル）が
`c2_runner.py` に入り、`run_weekly_cycle` は session gate / queued order 置換 / positions 再読込を
持つ。f1c8926 の単純な cherry-pick は 2 箇所で衝突したため、HEAD `b03eacf` から
`wt/t_07172f52-v2` を作り 3-way で再構築した。

- 衝突(a) 関数追加位置: HEAD の `_run_order_lifecycle_step` を残し、その直後に
  `resolve_reference_prices` / `run_market_fallback` 群を置いた。
- 衝突(b) 発注ループ直前: HEAD の TIF/session gate・queued order 置換・positions 再読込を**そのまま**
  残し、`reference_prices` の解決をその後ろに挿入。gate が立っている回はデータAPIを叩かない。
- `alpaca_paper_client.py` の自動マージは `cancel_order` を二重定義したので 1 つに統合（F811）。
- 追加修正1: 11-10 の板は `c2_<sym>_<side>_<session>_a1`（日次ライフサイクルの再送）で、
  旧 `endswith(session)` では 1 件も対象にならなかった。セッション部分を正規表現で照合し、
  自前の置換 `_fb` だけ除外する形にした（+2 tests）。
- 追加修正2（重要）: 週次は月曜 08:00 ET に走るため、その時点の「latest trade」は多くの場合
  **前営業日（金曜）の引け print**で、置き換えようとしている stale close と同じ情報だった。
  そのまま `live_quote` と記録すると 11-02 の欠陥を隠す。**現在のプレマーケット開始
  （04:00 ET・平日）より後の print だけを live とし**、古い print は `stale_close` に落とす。
  trade と quote mid は固定優先ではなく**タイムスタンプの新しい方**を採る（プレマーケットでは
  quote の方が新しい）。+3 tests。実測プローブ（読み取りのみ、live data API）: 4/4 が
  `live_trade` で、print 時刻（`2026-11-09T20:59:59Z` 等）を返すことを確認。

commit: `e1dd91a`（モデルB本体）, `75aeb25`（`_a1` 対応）, `b219b1a`（freshness floor）。

本番ツリーへの反映は `git apply`（5ファイル）で行い、反映後の sha256:

| file | sha256 |
| --- | --- |
| scripts/paper_c2/c2_runner.py | 5fa814a02cec7d6e4996c00158749746722b9ed530a7723581c8bf9ef1521f24 |
| scripts/paper_c2/alpaca_paper_client.py | 6e866f04d083cc9b7160af834fa0f483e463de38a6cf3e0e98ef73b00674d5a4 |
| scripts/paper_c2/c2_fallback_batch.py | 1852c8b2d7e66deeb49cdd078ec43f9cfa3a699d5ccbe28030fc6c45e484ef11 |
| tests/test_c2_execution_model_b.py | 5f4fc1d403b5f04abf383b4b02f2aca56eda35847122184e208a9146387e1755 |
| tests/test_c2_kill_switch.py | d9f66a40c69d3f50d240c4a001d837712d859850dbeef4c44e47c426b66d7840 |

- 実測: 本番ツリーで `pytest tests/test_c2_*.py` = **88 passed**（反映前 68 + 本カードの新規20）、
  変更5ファイルの `ruff check` / `ruff format --check` = 0 件。
- 戻し方（backup は `/root/current-work/backup-t_07172f52-apply-20261110-1000/`）:
  `git checkout -- scripts/paper_c2/alpaca_paper_client.py scripts/paper_c2/c2_runner.py tests/test_c2_kill_switch.py`
  と `rm scripts/paper_c2/c2_fallback_batch.py tests/test_c2_execution_model_b.py`（新規2ファイルの削除）。
- cron は**未登録**。10:00 ET は EST 15:00 UTC / EDT 14:00 UTC なので、JST 火〜土 00:00 の1本で
  どちらも cutoff 後の窓に入る（EST 10:00 ET / EDT 11:00 ET）。登録は COO 裁可のカードへ切り出した。

