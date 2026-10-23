# P5 Dead-end Audit — 主要導線（research → screening → valuation → portfolio → morning brief）

- 監査日時: 2026-10-23 10:20–10:50 JST（実走は 10:32–10:45 JST）
- 対象リビジョン: `agent/commercial-foundation` @ `d7404bf`（`git status` は clean ではない。下記「作業ツリーの状態」参照）
- カード: t_64253b31（本カードは**診断＋最小修正1件**。修正は §4 の1件のみ）
- 一次情報: `AGENTS.md` / `docs/PRIVATE_OPERATOR_ROADMAP.md`（P5）/ `docs/WORKFLOW_DEADEND_AUDIT.md`（2026-09-28 の全工程監査）/ `docs/AUTONOMOUS_AGENT_HANDOFF.md`

## 0. この監査の位置づけ（既存監査との関係）

2026-09-28 の `docs/WORKFLOW_DEADEND_AUDIT.md`（`5a87ce7`、23件、静的経路解析が主体で
「ローカルサーバー起動による API 実打鍵は行っていない」）の**再制作はしない**。
本カードは (a) その23件の**現況を実測で差分確認**し、(b) カード指定の5導線を
**実際に API/CLI で走らせ**、(c) 行き止まり1件を最小修正して同一入力で回帰させたもの。

再制作を避けるため、静的に判定できる既知項目（UI 参照の有無など）は
`grep` 1回の再測定に留め、時間は**実行時の挙動**に割いた。

### 作業ツリーの状態（本監査の変更と無関係な既存差分）

```
 M pyproject.toml                     （他カード: ruff per-file-ignore 追加）
 M src/yowayowa/services/research_ask.py      （他カード: プロンプト予算）
 M src/yowayowa/services/research_brief.py    （他カード: プロンプト予算）
?? .t45probe.txt / .coverage.* / .worktrees/ / config/data_universe.json /
   docs/CIO_STRATEGY_PROPOSALS_20261002.md / docs/STATE_P4_REMEDIATION_20261001.md /
   scripts/lint_data_sources.py / scripts/research/ / tests/test_research_*_prompt_budget.py
```

本監査が触れたファイルは §4 の4本＋本ドキュメント＋`docs/PRIVATE_OPERATOR_ROADMAP.md`＋
`docs/INDEX_ja.md` のみ。上記の既存差分には触れていない。

## 1. 方法と限界（先に明記）

実走した面:

1. ローカル API: `uvicorn yowayowa.api.app:app` を `127.0.0.1:8123` で起動（既定の 8000 は
   他カードと衝突しないよう回避）。`/openapi.json` 取得済み。
2. CLI: `.venv/bin/yowayowa`（`PYTHONPATH=<repo>/src`）で 5導線の実コマンドを実行。
3. 実データ: `data/yowayowa.db`（SQLite 実測）、`data/edinet-daily.jsonl`（6,176行）、
   `data/stock-ohlcv/`（16銘柄）、`data/crypto-ohlcv/`（BTC/ETH）。
4. 出力は `/root/.hermes/profiles/worker-go/cache/scratch/p5audit/` に保存（下記 §5）。

**辿れなかった範囲（意図的）:**

- **yowayowa 内蔵 LLM は bot から呼ばない**（カード制約）。したがって
  `research-ask`（1エージェントround）と `research-brief`（生成）は**実行していない**。
  代わりに (a) `/v1/research/ask` が UI から呼ばれていること（`web/static/ai.js:412`）、
  (b) 直近 brief の**永続化済み実物**（`research-brief-latest`、LLM 非依存の読み取り）で
  導線の到達点を確認した。
- **実ブラウザ操作・実楽天セッション・ChatGPT device auth** は行っていない（カード制約・
  従来どおり NEED-HUMAN）。UI 到達性は `grep` の参照数で判定した。
- **重い試験は TSUKUMO/Colab** の原則に従い、VPS では対象テストモジュール1本＋関連6本のみ
  （pytest 78件、合計 20 秒未満）。`make verify` 全量は走らせていない（§4.5）。

## 2. 実走ログ（導線 × 入力 × 実測）

入力の同定（すべて 2026-10-23 JST）:

| 入力 | 実体 | 時刻/hash |
|---|---|---|
| EDINET daily | `data/edinet-daily.jsonl` 6,176行（submitDateTime 2026-10-13〜10-22） | mtime 2026-10-23 06:31:00 JST / sha256 `fc71256a…3cb2719` |
| 機械スクリーニング | 上記 jsonl ＋ Yahoo スクリーナー（実行時取得） | 実行 2026-10-23 10:41 JST / 3.83s |
| 永続化候補 | `screening_candidates` run_date=2026-10-23 163行（edinet_filing 117 / market_screener 46） | 表ダンプ sha256 `0ed89cc6…20f7d7` |
| 銘柄 | 2901.T（ウェルディッシュ）/ 7203.T / 6758.T | — |
| brief | `research_briefs` 25行（最新 run_date 2026-10-23） | generated_at 2026-10-23T00:01:27Z |

### 2.1 research（企業研究）

| 実行 | 結果 |
|---|---|
| `GET /v1/research/2901.T` | **200**、profile/analyst/insiders/esg/actions/filings/fund の7セクション返却（`ownership` のみ `YFNotImplementedError` を `errors` に明示） |
| `GET /v1/research/2901.T?sections=profile,financials` | **422** `Unknown research sections: financials`（有効値は profile/analyst/ownership/insiders/esg/actions/filings/fund。`financials` は**セクション名として存在しない**） |
| `fundamentals 2901.T` | exit 0、PL/BS/CF 実値（Equity 5,937,595,000 @2026-02-28 等） |
| `quotes 2901.T` | exit 0（76 / 前日 82 / -7.32% / as_of 2026-10-23T00:00Z） |
| `/v1/research/ask` | **未実行**（内蔵LLM禁止）。UI からの呼び出しは `web/static/ai.js:412` に存在（前回監査 I2 は解消済み） |

### 2.2 screening（機械スクリーニング）

| 実行 | 結果 |
|---|---|
| `screening-run` | **exit 0**、3.83s、163候補を永続化（edinet_filing 117 / market_screener 46、run_date 2026-10-23） |
| `screening-candidates ALL --limit 5` | exit 0（表出力。Code 列は5桁 `29010`） |
| `screening-candidates 14140` | **修正前 exit 1** `No screening candidates persisted for 14140`（実在する id=56 の行を見落とし）→ **§4 で修正** |
| `screen 7203.T 6758.T --filter price_to_earnings:lt:20` | exit 0（ただし指標辞書が1セルに丸ごと出る。可読性の摩擦であり行き止まりではない） |

### 2.3 valuation（評価）

| 実行 | 結果 |
|---|---|
| `valuation 7203.T` | exit 0、15指標（PER 10.40 / PBR 1.00 / 営業利益率 7.86% ほか）＋ `Annual basis: 2026-03-31` |
| `valuation 2901.T` | exit 0 |
| `valuation 29010`（候補の5桁コードをそのまま） | **exit 1**、`HTTPStatusError: Client error '404 Not Found' for url '…/v1/valuation/29010'` ＋ rich のスタックトレース表示 |
| `GET /v1/valuation/2901.T` | 200 だが **`currency` フィールドが存在しない**（`price`/`market_cap` の通貨が応答から読めない）。`company_name` は `"2901.T"`（= シンボル文字列に退化） |
| `GET /v1/markets/quotes?symbols=2901.T`<br>`…=29010` | 前者 200（`quotes.2901.T.price=75`）だが **`currency: null`**。2901.T / 6758.T / 7203.T / AAPL の4件すべて `null` を実測（2026-10-23T01:51Z）。後者（5桁コード）は **200 + `unavailable_symbols:["29010"]`** で欠測を明示（この面だけが正直） |

### 2.4 portfolio（保有・サイズ）

| 実行 | 結果 |
|---|---|
| `portfolio list` | `[]`（実測0件から開始） |
| `portfolio create p5-audit-20261023 --base-currency JPY` | exit 0（id=1） |
| `portfolio position 1 2901.T 100 --average-cost 2500 --currency JPY` | exit 0 |
| `portfolio value 1` | exit 0、net/gross 7,600 JPY、day -600、unrealized -242,400（76円×100株で内部整合） |
| `portfolio history 1` | exit 0、`value` 実行時に**スナップショットが自動記録**され1行返る |
| `portfolio risk 1` | exit 0、年率 -89.21% / vol 75.46% / MDD -86.78% / VaR 7.51% / benchmark ^GSPC 相関 0.030 |
| `portfolio risk 1 --benchmark ^TOPX` | exit 0 だが `Benchmark correlation —`（**警告なしの縮退**） |
| `portfolio sizing-proposals 1 --idea '{…}' --risk-budget-pct 0.01 --max-position-pct 0.2` | 1回目 **exit 1**（`license_class` の enum 外 `public_provider` で 422）→ `personal_only` に直して exit 0。**entry/stop/lot/price_provenance を手書きした JSON が必須** |
| `portfolio remove` | **保有の削除のみ**。**ポートフォリオ自体を削除する API/CLI は存在しない**（`grep -rn 'delete_portfolio\|delete("/portfolios'` → 0件） |

### 2.5 morning brief（朝ブリーフ）

| 実行 | 結果 |
|---|---|
| `research-brief-latest` | exit 0、run_date 2026-10-23（provider `openai_compatible` / `glm-5.3-flash`、generated_at 2026-10-23T00:01:27Z） |
| 内容の実測 | EDINET セクションが「**対象：2026-09-24分**」「2026-10-23分は未取得」、信用残「候補0件」、マクロ 0件 |
| 4日分の傾向 | `citations[0].source_url` が 10-20/10-21/10-22/10-23 の**4日連続で同一** `…documents.json?date=2026-09-24&type=2` |
| 配信 | `/root/.hermes/scripts/yowayowa_brief_daily.sh` に `--send` なし、cron ジョブは `deliver: local`（前回監査 B1 未解消） |

## 3. 行き止まり一覧（現況・実測ベース）

重要度は「P5 終了条件（外部サービスへ離脱せず完結）」への影響で付けた。
**R1/R2/R6 は今回新規または実測で確定したもの**、それ以外は前回監査の再測定。

| # | 導線の継ぎ目 | 症状（実測） | 重要度 | 再現コマンド |
|---|---|---|---|---|
| R1 | screening/brief → research・valuation | 候補・brief が示す5桁EDINETコード（`29010`）は下流で通らない。同じ誤コードに対する応答が面ごとにバラバラ: `valuation` = **404＋スタックトレース**、`/v1/markets/quotes?symbols=29010` = **200 + `unavailable_symbols`（正直）**、`/v1/research/29010` = **200 だが全セクション空**。コード→ティッカー（`2901.T`）の変換関数はリポジトリに存在しない | **P0** | `yowayowa valuation 29010` |
| R2 | research | 未知名の銘柄でも `/v1/research/{symbol}` が 200 を返し、`sections.profile == {}`。「データが無い」と「銘柄が違う」を区別できない（fail-closed でない） | **P0** | `curl /v1/research/29010` |
| R6 | brief | 朝ブリーフが**最古のEDINET提出を毎日掲載**する。`assemble_edinet_summary` は jsonl を追記順に読み `filings[:_MAX_EDINET_SIGNALS]`（=12）で切るため、6,176行の先頭＝2026-09-18/24分が選ばれる。10-13〜10-22 の訂正系は約300件あるが1件も出ない | **P0** | `research-brief-latest` / `sqlite3 … "select json_extract(payload,'$.citations[0].source_url') from research_briefs where run_date='2026-10-23'"` |
| R3 | portfolio | ポートフォリオを作成できるが**削除できない**（API/CLI とも DELETE 不在）。誤作成が永続化する | P1 | `grep -rn 'delete_portfolio' src/` → 0件 |
| R4 | valuation → sizing | `/v1/valuation/{symbol}` に通貨フィールドが無く、`company_name` がシンボル文字列に退化する。さらに `/v1/markets/quotes` は **`currency` が全銘柄 `null`**（2901.T / 6758.T / 7203.T / AAPL で実測）。価格だけが返り、通貨が応答から確定できない | P1 | `curl '/v1/markets/quotes?symbols=7203.T'` |
| R5 | valuation → sizing | `sizing-proposals` は entry/stop/lot/`price_provenance` を**手書きJSONで要求**。quote/valuation から idea を組み立てる経路が無い | P1 | `yowayowa portfolio sizing-proposals 1 --idea '{…}'` |
| R7 | brief → 通知 | brief は生成されるが配信されない（`--send` 無し・`deliver: local`）。読むには CLI/DB を開く必要がある | P1 | `grep -n 'send' /root/.hermes/scripts/yowayowa_brief_daily.sh` → 0件 |
| R8 | falsify | `research_ask` は `invalidation_conditions` を返すが `investment_hypotheses` へ書かない（`grep -c hypothesis src/yowayowa/services/research_ask.py` → 0）。仮説は CLI 手入力のみ（実測 0行） | P1 | `sqlite3 data/yowayowa.db "select count(*) from investment_hypotheses"` → 0 |
| R9 | discover | 機械スクリーニング候補を表示する画面が無い（`src/yowayowa/web/` 内 `/v1/screening` 参照 0件） | P1 | `grep -rn 'v1/screening' src/yowayowa/web/` → 0件 |
| R10 | portfolio risk | ベンチマークが取れない場合に相関が `—` になるだけで、理由も欠測フラグも出ない（無音の縮退） | P2 | `portfolio risk 1 --benchmark ^TOPX` |
| R11 | screening 表示 | `screen` が指標辞書を1セルに丸ごと表示（可読性。行き止まりではないが運用摩擦） | P2 | `yowayowa screen 7203.T` |

**残存件数: 行き止まり10件（P0 3 / P1 6 / P2 1）＋ 行き止まりではない運用摩擦1件（R11）。**
うち1件（旧: 候補コードのページ外見落とし）は §4 で解消済み。

### 前回監査（2026-09-28・23件）からの差分（実測で確認したもの）

| 前回ID | 現況 | 根拠 |
|---|---|---|
| S1 サイズ機能なし | **解消** | `services/portfolio_sizing.py` ＋ CLI `portfolio sizing-proposals` ＋ API `/v1/portfolios/{id}/sizing-proposals`。今回 1件実走（exit 0） |
| F1 反証の永続化なし | **一部解消** | `investment_hypotheses` テーブル＋`hypothesis add/list/show`。ただし R8 の通り研究からの自動記録は無い |
| I1 instrument→research | **解消** | `instrument.html`/`instrument*.js` に `/research/` 参照 2件 |
| I2 research_ask の UI 不在 | **解消** | `web/static/ai.js:412` が `/v1/research/ask` を呼ぶ |
| E1/O3 broker の UI 不在 | **一部解消** | `web/templates/broker_execution.html` ＋ `web/static/broker_execution.js` が存在（`web/` 内 broker 参照 60件） |
| O1 約定が観測に入らない | **解消** | `services/order_inquiry_service.py` に `executions` 参照 3件 |
| O2 約定→portfolio | **解消** | `services/executions_reconciliation.py`（portfolio 参照 19件）だが `web/static/portfolio.js` の broker 参照は 0件 |
| S2 broker-read→portfolio | **解消** | 同上（`services/portfolios.py:270` に未適用約定の適用エラー文言） |
| G2 EDINET daily が毎回0件 | **解消** | jsonl が 10-13〜10-22 を保持（6,176行、本日 06:31 取得）。ただし R6 で brief 側が古い行を拾う |
| G1 EDINET index 未注入 | **未解消** | `edinet_index_days` / `edinet_filings` とも 0行（実測） |
| D1/D3/B1/B2/B3/B4/R1/R2(前回) | **未解消** | D1・B1・B4・R1 は本監査の R9/R7/R11 系として再掲。`price_alerts` 0件、`strategy_research_snapshots` 447行（前回0行から増加） |

## 4. 塞いだ行き止まり（1件）

### 4.1 対象

**screening 候補のコード指定が「1ページ目だけ」を見て『存在しない』と誤答する**（§3 表の旧項目）。

実測（修正前、同じ入力）:

```
$ yowayowa screening-candidates 14140        # id=56 に実在する行
No screening candidates persisted for 14140
EXIT=1
$ yowayowa screening-candidates 14140 --limit 250
（1行表示される） EXIT=0
```

原因は `screening_candidates` が `read_screening_candidates(session, limit=limit)` で
**先頭 limit 行を取ってから** Python 側で `row["code"] == wanted` を絞っていたこと。
サービス層には最初から `code=` フィルタがあり（`services/screening_pipeline.py:702`、
`research_ask` が使用）、CLI がそれを使っていなかった。候補が 250 件を超えると
**API 経由でも到達不能**になるため、同じ欠陥クラスが API にも潜在していた。

### 4.2 修正（4ファイル、+35/−8 行相当）

- `src/yowayowa/screening_cli.py`: コード絞り込みを**クエリ**へ移動（`code=None if ALL else wanted`）。
- `src/yowayowa/api/screening_routes.py`: `GET /v1/screening/candidates` に `code` クエリを公開
  （サービス層の既存機能を表面に出すだけで、ロジックの二重実装はしない＝API-first 維持）。
- `src/yowayowa/services/ai_agent.py`: エージェントツール `get_screening_candidates` に `code` 引数を追加
  （CLI/API/AI の3面で同じ検索ができる）。
- `tests/test_screening_pipeline.py`: 回帰3件（CLI ページ外見落とし／API `?code=` のクエリ内絞り込み／
  エージェントツールの `code`）。

```diff
--- a/src/yowayowa/screening_cli.py
+++ b/src/yowayowa/screening_cli.py
@@ -68,14 +68,20 @@ def screening_candidates(
+    wanted = code.strip().upper()
     session = get_session()
     try:
-        rows = read_screening_candidates(session, limit=limit)
+        # The code filter belongs in the query, not in a slice of the first
+        # ``limit`` rows: filtering after the limit reported a persisted
+        # candidate as absent whenever it fell outside the first page
+        # (workflow dead-end audit 2026-10-23, doc P5_DEADEND_AUDIT_20261023).
+        rows = read_screening_candidates(
+            session,
+            code=None if wanted == "ALL" else wanted,
+            limit=limit,
+        )
     finally:
         session.close()
-    wanted = code.strip().upper()
-    if wanted != "ALL":
-        rows = [row for row in rows if row["code"] == wanted]
```

（API 側は `code: str | None = Query(default=None, max_length=5)` の追加と
`read_screening_candidates(..., code=code)` への受け渡しのみ。AI 側は schema に `code` を足し、
`_tool_screening_candidates` で `code=code` を渡すのみ。）

### 4.3 なぜこれが「最小」か

- 新しいロジックを書いていない。**既にサービス層にあった `code=` フィルタを3つの表面に配線しただけ**。
- DB スキーマ・API の応答形（既存キー）・`code` の表記規則（"never repaired"）を一切変えない。
- R1（5桁コード→ティッカー変換）を選ばなかった理由: 変換関数の新設は
  `screening_models.ScreeningCandidate` の「code は発行された表記のまま。装飾は修復しない」
  という規約と衝突し、影響範囲が4導線に広がる。値の意味を変える修正は別カードで裁可を取るべき。
- R6（brief の古い提出）を選ばなかった理由: 修正自体は小さいが、**同じ入力での回帰確認に
  brief の再生成（＝内蔵LLM呼び出し）が必要**で、本カードの制約下では「回帰試験を再実行した」
  と実測で言えない。よって P0 のまま残し、根因と最小修正案を本ドキュメントに固定した。

### 4.4 回帰（同一入力・実DB）

| 手順 | 修正前 | 修正後 |
|---|---|---|
| `yowayowa screening-candidates 14140`（実DB、id=56） | exit 1 / "No screening candidates persisted for 14140" | **exit 0 / 1行表示（2026-10-23, 14140, edinet_filing）** |
| `GET /v1/screening/candidates?code=14140` | （パラメータ自体が無く、250件取得して手で絞るしかない） | **200 / 1行（code=14140 のみ）** |
| `GET /v1/screening/candidates?code=14140&limit=1` | — | 200 / 1行（クエリ内絞り込みの証明） |
| `GET /v1/screening/candidates?limit=3` | 3行 | 3行（ALL 経路は不変） |
| `screening_candidates` 表の全件ダンプ sha256 | `0ed89cc6…20f7d7`（163行） | **同一** `0ed89cc6…20f7d7`（修正が読み取り専用であることの証明） |

テスト（RED→GREEN を実測）:

- 修正を巻き戻した状態で `pytest tests/test_screening_pipeline.py -k code_filter`
  → `FAILED …::test_cli_screening_candidates_code_filter_reads_past_the_first_page`（RED 確認）
- 修正適用後 → 3 passed（新規/拡張3件）
- 関連モジュール回帰: `tests/test_openapi_contract.py tests/test_screening.py
  tests/test_screening_pipeline.py tests/test_yahoo_screener.py tests/test_yahoo_screener_gaps.py
  tests/test_research_ask_prompt_budget.py` → **78 passed, RC=0**
- `ruff check` / `ruff format --check`（変更4ファイル）→ clean
- `mypy src/yowayowa` → **31 errors / 5 files**（`paperloop*`・`jp_value_quality*` に既存。
  変更ファイルには新規エラーなし。本ブランチの既存状態で、本修正とは無関係）

### 4.5 `make verify` を全量走らせていない理由

AGENTS.md は `make verify` を要求するが、同時に「重い試験は TSUKUMO/Colab、VPS で重処理をしない」
（Bot Swarm 方針）と、他カードの未コミット変更が同一ツリーにある状況を踏まえ、
VPS では**対象モジュール＋関連6モジュール**に限定した。全量（cov 付き pytest・openapi export）は
CI（GitHub Actions）で確認する前提。**この点は本カードの未達として明記する。**

## 5. 証跡（scratch・消える前提）

`/root/.hermes/profiles/worker-go/cache/scratch/p5audit/`

- `candidates_before.txt` / `candidates_after.txt` — 修正前後の `screening_candidates` 全件ダンプ（sha256 一致）
- `regression_after.txt` — 修正後の CLI 出力
- `regression_api_after.txt` — 修正後の `?code=` 応答
- `pytest_targeted.txt` — 関連78件の実行ログ

## 6. 作業による状態変化（正直な記録）

- `screening-run` を1回実行した結果、`screening_candidates` に **run_date 2026-10-23 の163行**が
  入った（実行前は 0 行）。これは当該導線の正常な出力であり、翌日以降の cron 実行で置き換わる。
  ただし EDINET 由来117行の証跡は **2026-09-24〜10-22 の提出**（本日分ではない）で、
  run_date だけが今日になっている点は R6 と同じ根の問題として残る。
- 監査用ポートフォリオ（id=1, `p5-audit-20261023`）は **作成後に削除機能が無い**ため、
  作成した3行（portfolios / positions / portfolio_snapshots 各1行）を直接 SQL で削除して原状復帰した。
  この「削除手段が無い」こと自体を R3 として記録している。
- リポジトリの他ファイル（他カードの未コミット変更）は変更していない。**push はしていない**。

## 7. 次の1手（推奨順）

1. **R6**: `assemble_edinet_summary` を `submitDateTime` 降順（最新12件）に変える。brief は
   LLM を伴うため、決定論層（`assemble_edinet_summary`）の単体回帰＋既存 brief テストで担保し、
   実配信の確認は TSUKUMO/手動で行う。
2. **R2**: `/v1/research/{symbol}` を「全セクション空＋上流エラー無し」のときに 404 か
   `errors` 明示へ変える（fail-closed）。
3. **R1**: 5桁EDINETコード→`.T` ティッカーの**派生フィールド**（`code` は不変）を候補・brief に足し、
   `screening-candidates`/brief から valuation/research へコピペで進めるようにする。裁定が必要。
4. **R9/R7**: 候補一覧の画面化と brief 配信（`--send`）は運用手順の変更を伴うため、
   マスターの可否判断が要る（現行方針では配信先の指定が未決）。
