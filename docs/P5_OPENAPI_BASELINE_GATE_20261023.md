# P5: 生成OpenAPI仕様の基準をVCS管理に置き、CIに差分ゲートを入れた（2026-10-23）

カード: t_142aa7f0
出典: `docs/OPENAPI_DRIFT_20261023.md` §4 P2・§9-5、`docs/PRIVATE_OPERATOR_ROADMAP.md` P5（API/CLI/agent parity）、`AGENTS.md` 不変条件1。
恒久手順は `docs/OPENAPI_BASELINE.md` が正本。本書は実測記録。

---

## 1. 結論

- 生成仕様の基準を `docs/openapi_baseline.json` としてVCS管理下に置いた（155 operations / 142 paths / 590,707 bytes / sha256 `5a2f0d02473e24d36de6c945451d3d2851634d2267defb7b865d2fee05b44f5b`）。
- ゲートを3経路に同じロジックで入れた: CI の `verify` ジョブ、`make verify`（`make openapi-check`）、`pytest`（`tests/test_openapi_baseline.py`）。
- 実測: 無変更なら exit 0、基準を意図的に古くした3種のコピー（単一値 / operation 削除 / enum 削除）でいずれも exit 1。`tests/test_openapi_baseline.py` + `tests/test_openapi_contract.py` は 15 passed。
- CI 全体（GitHub Actions）は未実走。§8 に、実走していない事実と理由、未実測の残リスクを明記した。

---

## 2. 変更ファイル

新規:

- `src/yowayowa/openapi_baseline.py` — 正規化（canonical JSON）・operation 棚卸し・JSON pointer 差分レポート。ゲート/CI/pytest が共有する唯一の実装。
- `scripts/check_openapi_baseline.py` — ゲート本体。exit 0=一致 / 1=差分 / 2=基準なし。`--write` で基準更新、`--baseline` で任意ファイルと比較、`--max-details` で表示上限。
- `docs/openapi_baseline.json` — 基準仕様（canonical: sort_keys / indent=2 / UTF-8 / 末尾改行）。
- `tests/test_openapi_baseline.py` — pytest 側のゲートと差分レポートの回帰ピン（9 test）。
- `docs/OPENAPI_BASELINE.md` — 恒久の運用手順（更新手順・禁止事項・ゲートがやらないこと）。
- `docs/P5_OPENAPI_BASELINE_GATE_20261023.md` — 本書。

追記のみ（既存行は不変）:

- `.github/workflows/ci.yml` — `verify` ジョブに2行。`browser` / `deploy-production` ジョブは無変更。
- `Makefile` — `.PHONY` に3語、`openapi-check` / `openapi-baseline` の2ターゲット、`verify` の依存に `openapi-check` を追加。

`tests/test_openapi_contract.py` の既存ピンは触っていない（実行して通過を確認）。

---

## 3. CI 設定と Makefile の diff

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -64,6 +64,8 @@ jobs:
       - run: uv run yowayowa export-openapi
       - name: Confirm OpenAPI generated
         run: test -s openapi.json
+      - name: OpenAPI baseline drift gate
+        run: uv run python scripts/check_openapi_baseline.py
 
   browser:
```

```diff
--- a/Makefile
+++ b/Makefile
@@ -16,7 +16,7 @@
-.PHONY: install bootstrap dev test lint typecheck openapi verify
+.PHONY: install bootstrap dev test lint typecheck openapi openapi-check openapi-baseline verify
@@ -47,4 +47,13 @@
 openapi: | bootstrap
 	$(V)/bin/python -m yowayowa.cli_shim export-openapi
 
-verify: lint typecheck test openapi
+# Fail when app.openapi() no longer matches the committed baseline
+# (docs/openapi_baseline.json). Intentional contract changes: make
+# openapi-baseline, review the diff, commit it with the API change.
+openapi-check: | bootstrap
+	$(V)/bin/python scripts/check_openapi_baseline.py
+
+openapi-baseline: | bootstrap
+	$(V)/bin/python scripts/check_openapi_baseline.py --write
+
+verify: lint typecheck test openapi openapi-check
```

---

## 4. ゲートの設計（何を比較し、何を比較しないか）

- 比較対象は `app.openapi()` の全体（paths / operations / components / info）。除外なし。
- 判定はパース後の**意味比較**（Python の dict 等価）。キー順や整形の違いでは落ちない。
- 基準ファイルは canonical（`sort_keys=True`, `indent=2`）で保存する。無変更で再生成すると**バイト一致**するため、`git diff` が「意図した契約変更だけ」を示す。この性質は `test_committed_baseline_is_canonically_formatted` で固定。
- 差分表示は2層: operation の増減は1件1行（`+ POST /v1/...`）、値差分は JSON pointer で `baseline -> generated`。増減した path 配下の子差分は親行に畳んで出さない（ノイズ除去）。
- ゲートは `openapi.json` に依存しない（自分で `app.openapi()` を呼ぶ）。既存の `yowayowa export-openapi` ステップはそのまま残した。
- ゲートは「drift の検出」だけを行う。契約内容の妥当性（認証境界など）は判定しない。

---

## 5. ローカル再現コマンドと実測（無変更時）

```bash
cd /root/current-work/yowayowa-investor

# ゲート（CI と同じスクリプト）
$ PYTHONPATH=src ./.venv/bin/python scripts/check_openapi_baseline.py
OpenAPI baseline matches the generated document (155 operations, /root/current-work/yowayowa-investor/docs/openapi_baseline.json).
exit=0

# Makefile 経由（make verify の一部）
$ make openapi-check
/root/current-work/yowayowa-investor/.venv/bin/python scripts/check_openapi_baseline.py
OpenAPI baseline matches the generated document (155 operations, .../docs/openapi_baseline.json).
exit=0

# pytest 経由（契約ピンと併走）
$ PYTHONPATH=src ./.venv/bin/python -m pytest tests/test_openapi_baseline.py tests/test_openapi_contract.py -q
...............                                                          [100%]
15 passed
```

プロセス間の決定性（CI は毎回新しいプロセスで生成するため必須）:

```bash
$ PYTHONPATH=src ./.venv/bin/python scripts/check_openapi_baseline.py \
    --baseline /root/.hermes/profiles/worker-goat/cache/scratch/second_gen.json --write
$ cmp docs/openapi_baseline.json /root/.hermes/profiles/worker-goat/cache/scratch/second_gen.json
（差分なし）
$ sha256sum docs/openapi_baseline.json
5a2f0d02473e24d36de6c945451d3d2851634d2267defb7b865d2fee05b44f5b  docs/openapi_baseline.json
```

---

## 6. mutation 実測（ゲートが落ちること）

基準のコピーを3通り作って1箇所だけ古くし、`--baseline` で差し替えて実行した。**3件とも exit 1**。

再現手順（どこでも同じ3コマンドで作れる）:

```bash
cd /root/current-work/yowayowa-investor
# 例: info.version だけを古くしたコピーを作る
./.venv/bin/python - <<'PY'
import json
spec = json.load(open("docs/openapi_baseline.json"))
spec["info"]["version"] = "0.1.0-stale"
json.dump(spec, open("/tmp/mut_a.json", "w"), indent=2, ensure_ascii=False, sort_keys=True)
PY
PYTHONPATH=src ./.venv/bin/python scripts/check_openapi_baseline.py --baseline /tmp/mut_a.json
```

A. 単一の値だけ（`info.version`）

```
$ PYTHONPATH=src ./.venv/bin/python scripts/check_openapi_baseline.py --baseline .../mut_a.json
OpenAPI baseline drift detected: the generated document differs from .../mut_a.json.
schema or value differences: 1 (baseline -> generated)
  /info/version: "0.1.0-stale" -> "0.1.0"
If the change is intentional, run `python scripts/check_openapi_baseline.py --write` and commit the refreshed baseline together with the API change. Procedure: docs/OPENAPI_BASELINE.md
exit=1
```

B. operation を1本だけ削除（監査 §4 P2 で欠けていた `POST /v1/sizing/quote-idea`）

```
$ ... --baseline .../mut_b.json
OpenAPI baseline drift detected: the generated document differs from .../mut_b.json.
operations added or removed: 1
  + POST /v1/sizing/quote-idea
exit=1
```

C. enum の値を1つ削除（監査 §6 の `LicenseClass: synthetic` 追加の逆方向）

```
$ ... --baseline .../mut_c.json
OpenAPI baseline drift detected: the generated document differs from .../mut_c.json.
schema or value differences: 1 (baseline -> generated)
  /components/schemas/LicenseClass/enum/4: (absent in baseline) -> "synthetic"
exit=1
```

mutation B の初回実行では、削除した operation 配下の子差分が16行そのまま出ていた（抑制用 prefix の組み立てが `"/paths" + escaped` で区切りの `/` を落としていた）。原因を修正し、`test_removed_operation_is_reported_without_child_noise` を回帰ピンとして追加した。修正後の出力が上の2行。**mutation を実測しなければ気付けなかった不具合**であり、以後は pytest が固定する。

---

## 7. 初回登録が取り込んだ差分（監査 §6「追加のみ・既存キー不変」の独立確認）

監査が「追加のみ」と実測した項目を、今日の生成物で直接確認した（`PYTHONPATH=src ./.venv/bin/python <probe>` の出力）:

| 監査 §6 の項目 | 今日の生成物での実測 |
| --- | --- |
| 仕様に無かった operation 2件 | `DELETE /v1/portfolios/{portfolio_id}`（`force` あり）、`POST /v1/sizing/quote-idea` ともに存在 |
| `GET /v1/screening/candidates` の `code` | `query:code` あり（実装は `Query(default=None, max_length=5)`） |
| `LicenseClass` に `synthetic` 追加 | enum = `official_public, personal_only, user_key, licensed_redistributable, synthetic` |
| `MarketQuote` の通貨3項目 | `currency` / `currency_source` / `currency_reason` あり |
| `ValuationSnapshot` の通貨3項目 | `currency` / `currency_source` / `currency_reason` あり |
| `PortfolioRiskAnalytics` の benchmark 欠測2項目 | `benchmark_correlation_missing` / `benchmark_correlation_missing_reason` あり |
| `PortfolioSizingRequest.ideas[].$ref` | `#/components/schemas/PortfolioSizingIdea-Input` |
| operation 総数 | 155（142 paths） |

補足の実測（追加のみの性質の裏づけ）: 作業ツリー直下の `openapi.json` は本カード開始時点で既に再生成済みだった（mtime `2026-10-23 20:37`、155 operations、sha256 `8775dbdd1ac5dffa7fce06602aaf7fa759aa155902df07430342f62838eba4f5`）。監査時の 2026-10-11 版（153 operations / 577,613 bytes）ではないため、**監査 §3 の「2 operation 欠落」を同じ手順では再現できない**。そこで現行コードの生成物とこのファイルを比較したところ、差分は13 leaf で、内訳は「`anyOf` による null 許容の追加（11件）」と「`description` の追加（2件）」だった（対象は `BrokerOrder.quantity` / `BrokerOrder.filled_quantity` / `BrokerPosition.quantity`）。既存の非 null 制約（`type: integer` / `type: string` + `pattern`）は `anyOf[0]` に温存されており、クライアントから見て緩和方向の変更である。

したがって初回登録は「監査が追加のみと実測した分＋本カード開始時点のコードの実装」をそのまま基準にしており、既存キーの削除・必須の反転は含まない。監査 §6 の「required 配列は 4 スキーマすべてで一致」という実測を引き継ぐ。

---

## 8. 未実走・残課題（正直な記載）

1. **GitHub Actions は未実走。** 本カードの受入はローカル実測（§5・§6）で満たした。CI の初回実行はレビュー／マージ時のワークフローに委ねる。
2. `make verify` 全体（pytest 全件）も未実走。VPS で重い処理をしない規律と、カードの「CI 全体の実行は任せてよい」に従った。
3. `make lint` / `make typecheck` は**本カード外の既存差分**で失敗する（実測）: ruff 36 errors（`tests/test_paperloop_v12.py` 16、`src/yowayowa/paperloop_panel.py` 11、`tests/test_paperloop.py` 4、`tests/test_jp_value_quality_backtest_units.py` 3、`src/yowayowa/paperloop.py` 1、`src/yowayowa/loop_cli.py` 1）、mypy 31 errors / 5 files（`paperloop*`、`services/jp_value_quality*`）。本カードの3ファイルは `ruff check` / `ruff format --check` / `mypy` すべて通過。この既存失敗は他カードの作業中差分であり、本カードでは触っていない。
4. **インタプリタ差の残リスク**: CI は Python 3.13、ローカルは 3.12.3。`fastapi 0.141.1` / `pydantic 2.13.5` は `uv.lock` と一致していることを実測したが、3.13 で生成物が同一になるかは未実測（ホストに 3.13 が無い）。CI 初回で差分が出た場合は「生成物がインタプリタ依存」という新しい事実として原因を特定する。**CI 側で基準を上書きして通すことはしない。**
5. security 境界の解消（orderbook の認証欠落、`securitySchemes` 不在、`_authorize_cron` の fail-open、`/internal/debug/runtime`）は本カードの範囲外（監査 §9-1〜3 の別カード）。ゲートは検出のみで、現状の契約を「正しい」とは判定しない。実測でも `securitySchemes` は 0 件、`/v1/orderbook/{symbol}` に `security` は無い。
6. `info.version` の運用規則（監査 §9-7）は未決定のまま。基準は版に依存せず内容で判定する。

---

## 9. 基準更新手順（要約・正本は docs/OPENAPI_BASELINE.md）

1. 実装を先に変える。
2. `make openapi-baseline`（= `python scripts/check_openapi_baseline.py --write`）。
3. `git diff docs/openapi_baseline.json` を**契約変更の記録として**レビューする。
4. 実装変更と基準更新を**同じコミット**にまとめ、追加/削除/緩和を commit message か PR 本文に書く。
5. 削除を伴う変更は破壊的変更として明記する。

禁止: 基準の手編集、API 変更を伴わない基準の再生成、ゲートの Skipped 化（`|| true` / `continue-on-error`）、同一ツリーで生成した基準との自己比較。
