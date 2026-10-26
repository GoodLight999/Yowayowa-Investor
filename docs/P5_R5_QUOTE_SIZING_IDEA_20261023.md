# P5 R5 — quoteからサイズ検討用ideaを生成し、価格・通貨・出典JSONの手入力をなくす

- 日付: 2026-10-23 (JST)・**再検証完了 2026-10-24 00:57 JST**
- カード: t_463a2a9c（worker-flash）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §2.4/§3 R5（P1）、`docs/P5_R4_QUOTE_CURRENCY_20261023.md` §3/§7、
  `docs/PRIVATE_OPERATOR_ROADMAP.md` の discover→size と P5 API/CLI/agent parity、AGENTS invariants 1/2/3/7
- 監査指摘の原文（R5）: 「`sizing-proposals` は entry/stop/lot/`price_provenance` を**手書きJSONで要求**。
  quote/valuation から idea を組み立てる経路が無い」

## 1. 結論

R5を実装した。`POST /v1/sizing/quote-idea`（または `yowayowa sizing-idea SYMBOL --stop-price … --lot-size …`、
または agentツール `build_quote_sizing_idea`）が既存providerのquoteを1回取得し、
**entry_price・通貨・出典（provenance）をすべて取得quoteから採証**して `PortfolioSizingIdea` を1個返す。
利用者の入力は `symbol` / `stop_price` / `lot_size` の3項目のみで、stop/lotは推奨値を生成しない。
生成JSONはそのまま既存 `sizing-proposals`（--idea/API/agent）へ渡ることを実測で確認した。
価格・通貨・証跡が採証できない場合は常に理由付きfail-closed（422/404/403/502）で、
不明価格を0や直前値で埋める経路は無い。発注・DB書込・組み込みLLM呼び出しは0。

## 2. 変更ファイルとコードhash（sha256・未commit）

初回記録 2026-10-23 20:2x JST。**2026-10-24 00:05 JST の再検証で8ファイル全部のhashが初回と同一であることを確認**
（並行runの変更を一切含まず・以下のhashは現時点の実測値）:

| ファイル | 変更 | sha256 |
| --- | --- | --- |
| `src/yowayowa/sizing_models.py` | `QuoteSizingIdeaRequest` 追加（extra=forbid、symbol/stop_price(有限Decimal>0)/lot_size(int 1..1,000,000)のみ。entry/通貨/provenanceは入力禁止） | `47649c9066282f6e4b9cb0415fbbcc4f706b078a1f86f5d8f4667e11623947f7` |
| `src/yowayowa/services/sizing_idea.py` | 新規。`build_quote_sizing_idea(provider, request)`。provider.quotesを**1回だけ**呼び失敗は固定code+固定文でfail-closed | `f941dce95019c794ddc42959e32fb0e6942a56418bb0f279dfe9c6beb3332d8e` |
| `src/yowayowa/sizing_cli.py` | 新規。CLI本体。成功時stdoutはJSON 1個のみ（Rich無し）。HTTP失敗はhttpx例外としてcli_shimの既存安全経路へ | `620e4bdb198498341b43593cdc8a504725b7ec4ac4bbba3e21a54b793395f5a6` |
| `src/yowayowa/api/portfolio_sizing_routes.py` | `POST /v1/sizing/quote-idea` 追加（認証共通境界・response_model=PortfolioSizingIdea・固定detail）。※別runによるvercel_observability追記はR5と無関係（§9） | `d2d53095f4a969530e0480a360dbf627954ef1e75fb89fddd8be4c6c6cf64d57` |
| `src/yowayowa/cli_entry.py` | 登録2箇所のみ（import + `app.command(name="sizing-idea")`）。R10の未commit差分は保持 | `126cb9c21fb3a8ad7d3e5b55ad16af66f41e6985e6cf6f08eac52258cb8764fd` |
| `src/yowayowa/services/ai_agent.py` | `build_quote_sizing_idea` ToolSpec + `_tool_build_quote_sizing_idea`（33追加行のうちR5関連7行）。既存 `propose_portfolio_sizing` 不変 | `621134f63bb68a61f2bb58112e0afa12ce2064d4c14c3f0dbd624c209690f367` |
| `tests/test_sizing_idea.py` | 新規。service/model/agentツール契約 | `f81c1b23d3dfa75c53ac03644981119ecc1d18e0e5f729d975f178ac8060fa4f` |
| `tests/test_sizing_idea_api.py` | 新規。API/CLI境界・OpenAPI・認証・提案結合 | `9d0d4bbcdbb848637743690dd15b48b8bcbe72054bd705a7c8699a830b03699c` |

## 3. 失敗コード表（service固定code → APIステータス）

| code | 意味 | status |
| --- | --- | --- |
| `symbol_invalid` | 記号文法違反（正規化は既存normalize_symbolのみ） | 422 |
| `quote_unavailable` | quote不在・unavailable_symbols掲載・キー不一致 | 404 |
| `quote_symbol_mismatch` | quote.symbolが要求symbolと不一致 | 404 |
| `price_invalid` | 価格0/負/NaN/Inf/非数 | 422 |
| `currency_missing` | quote.currency無し（currency_reasonの有無を問わず） | 422 |
| `currency_conflict` | currency有り＋currency_reason有り（矛盾） | 422 |
| `currency_source_missing` | currency_source空（R4契約） | 422 |
| `currency_invalid` | ISO風3文字でない | 422 |
| `provenance_insufficient` | provider/source空・as_of欠落・取得時刻/as_ofがnaive | 422 |
| `stop_not_below_entry` | stop_price >= entry_price | 422 |
| `provider_policy` | provider policy | 403 |
| `upstream_provider_failed` | 上流障害 | 502 |

失敗detailは固定のcode/reason文のみ。URL・ヘッダ・token・生例外・上流bodyは出ない（テストで検証済み）。

## 4. 固定入力（fixture）と実データの区別

- **固定入力（テスト内のfixture）**: `tests/test_sizing_idea.py` は 7203.T price=3105.5 JPY as_of=2026-10-22、
  AAPL price=348.2200012207031 USD などの**宣言fixture**をStubProviderで再生する。外部取得を一切含まない。
  **55テスト全合格は2回の独立実行で確認**（初回 2026-10-23 20:1x JST、再検証 2026-10-24 00:0x JST、ともに rc=0・§6）。
- **実データ（外部取得）**: §5のライブ実測は yahoo/yfinance（既存providerのみ）の実取得を用いた。
  取得値は実行時刻ごとに変わるため、以下には取得時点の値と `retrieved_at` を併記する。
  外部取得不能をfixture成功と偽ってはいない（ライブ取得が実際に成功した記録が§5）。
  初回実装時（2026-10-23 20:18 JST）の実測（7203.T 3105.0 / AAPL 348.2200012207031）とは別に、
  **2026-10-24 00:13-00:15 JST の再実測で同じ契約挙動を再現した**（価格は市場変動で別値。§5）。

## 5. 実測（2026-10-24 00:13-00:15 JST・VPS・既存yahoo providerの実取得・再検証ラン）

### 5.1 採取quote（基準。`provider.quotes(["7203.T","AAPL"])` 直呼び・1回・15:15:42Z）

- 7203.T: price **3084.0**（previous_close 3094.0）/ JPY / currency_source `yahoo:fast_info` /
  currency_reason null / quote as_of **2026-10-22T00:00:00Z**
- AAPL: price **347.56500244140625** / USD / currency_source `yahoo:fast_info` / currency_reason null /
  quote as_of **2026-10-23T00:00:00Z**
- batch provenance: as_of **2026-10-23T00:00:00Z**、retrieved_at **2026-10-23T15:15:42.085023Z**、
  license_class `personal_only`、notes 3文（batch既定 + R4通貨注記 + personal_only注記）
- **ポイント**: 7203.Tは「quote単独のas_of（10-22）」と「batchのas_of（10-23）」が意図的に異なる。
  生成ideaの `price_provenance.as_of` が 10-22 になっていることは、現在時刻でもbatch値でもなく
  **単一要求quoteのas_ofを明示した**証拠になる（§5.2の通り実際そうなった）。
  AAPLはこの時点でquote as_of＝batch as_of（10-23）だった。生JSON: scratch `r5_baseline2.json`。

### 5.2 API実行（uvicorn実サーバ 127.0.0.1:8217・scratch新規DB `r5_live2.db`・token認証・15:13:51-52Z）

- `POST /v1/sizing/quote-idea {"symbol":"7203.T","stop_price":"2800","lot_size":100}` → **200**
  - entry_price `3084.0` / JPY / lot 100 / stop 2800
  - price_provenance: provider `yahoo/yfinance`・source `Yahoo Finance`・
    license_class `personal_only`・retrieved_at `2026-10-23T15:13:51.921284Z`・**as_of `2026-10-22T00:00:00Z`**（§5.1の通りquote単独値）
  - notes: batch由来3文＋ `Price currency JPY resolved from currency_source yahoo:fast_info.` ＋
    `Stop price and lot size are caller-supplied assumptions; the entry price is the latest retrieved
    close/price and is not a guarantee of an executable price.`
- `POST /v1/sizing/quote-idea {"symbol":"AAPL","stop_price":"300","lot_size":1}` → **200**
  - entry_price **`347.6600036621094`**（provider実値を丸めない・15:13:52Z時点の実値） / USD /
    as_of **`2026-10-23T00:00:00Z`** / retrieved_at `2026-10-23T15:13:52.110209Z`

### 5.3 正式CLI subprocess実行（`yowayowa sizing-idea`・rc=0両方）

- `yowayowa sizing-idea 7203.T --stop-price 2800 --lot-size 100 --base-url … --token …` → **rc=0**、
  stdoutは **JSON 1個のみ**。内容は§5.2のAPI出力と**全フィールド一致**
  （retrieved_at `15:13:51.921284Z` 含む完全一致・providerのTTLキャッシュ窓内の同一取得を写している）。
- `yowayowa sizing-idea aapl --stop-price 300.25 --lot-size 1` → rc=0。
  小文字 `aapl` は正規化されAAPLとして取得。`--stop-price 300.25` は**Decimal精度のまま**payloadへ
  （"300.25"、float化しない）。entry_priceはAPIと同一の `347.6600036621094`。

### 5.4 失敗系実測（実サーバ・2026-10-24 00:13 JST）

| ケース | 期待 | 実測 |
| --- | --- | --- |
| 未知シンボル `NOSUCH9` | 404 + `quote_unavailable` | **404** `{"code":"quote_unavailable","reason":"Provider returned no usable quote for the requested symbol."}` |
| stop 99999 >= entry | 422 + `stop_not_below_entry` | **422** 同code |
| lot_size欠落 | 422 | **422**（pydantic `lot_size: Field required`） |
| `currency`/`entry_price` を入力に混入（provenance注入） | 422拒否 | **422** `extra_forbidden`（currency・entry_price両方） |
| 間違ったtoken | 401・provider未呼び | **401** `Invalid API token` |
| CLI未知シンボル | 非ゼロ終了・stdout JSON無し・固定文言 | **rc=1** stdout 0バイト stderr `Yowayowa API request failed: HTTP 404 (no such target)` |
| CLI間違ったtoken | 非ゼロ終了・固定文言 | **rc=1** stdout 0バイト stderr `… HTTP 401 (authentication or permission required)` |

## 6. 自動テスト（対象モジュール・VPS）

**初回（2026-10-23 20:1x JST）と再検証（2026-10-24 00:03-00:10 JST）の2回とも同結果:**

- `pytest tests/test_sizing_idea.py tests/test_sizing_idea_api.py` → **55 passed / rc=0**
  （JPY/USD成功・正規化のみで5桁変換無し・価格0/負/NaN/Inf・通貨欠落/矛盾/出典無し/非ISO・
  空証跡・naive時刻・quote不一致・stop>=entry・lot省略/小数/0/範囲外・extra禁止・
  policy→403・upstream→502漏洩無し・認証401・OpenAPI `$ref=PortfolioSizingIdea-Output`・
  agentツール直呼び・provider呼び1回・入力/元オブジェクト非破壊を網羅）
- `ruff check`（R5の8ファイル）→ **0エラー**、`ruff format --check`（同8ファイル）→ **8 files already formatted**
- `mypy`（R5のsrc5ファイル）→ **Success: no issues found in 5 source files**
- agentツールは直呼びテストのみで組み込みLLMは呼ばない（カード制約どおり）

## 7. 生成idea → 既存sizing-proposals 実測（scratch DB・隔離fixture・発注0・2026-10-24 00:14 JST再実行）

実サーバ上に隔離fixtureポートフォリオを作成（scratchの新規 `r5_live2.db` のみ。原本DB・journalは未触）。

1. JPYポートフォリオ（6758.T 1000株）を作成。既存valuationサービスがyahoo providerで実評価
   （gross **4,081,000** JPY・15:14Z時点の実値）。
2. 生成idea（§5.2の7203.T・entry 3084.0）を `POST /v1/portfolios/{id}/sizing-proposals` へ**そのまま**渡す → **200**、
   quantity **200** / notional **616,800** / stop損失 **56,800** / `executable: false`
   （検算: risk 2%×4,081,000=81,620、1株risk 3084-2800=284、81,620/284=287→lot切捨て200。
   notional 200×3084=616,800、stop損失 200×284=56,800。limiting=risk_budget）
3. 同一内容の**手書きidea**（entry/stop/currency/lot/provenanceを生成JSONから複製）を別callで渡す →
   **12フィールド全部が一致**（symbol, quantity, proposed_notional_base, estimated_stop_loss_base,
   risk_limited_quantity, position_limited_quantity, limiting_constraints, price_provenance,
   entry_price, stop_price, currency, lot_size）。→ 生成JSONは手書きJSONと**完全に等価**で既存面を通る。
4. 異通貨拒否: 同じJPY ideaをUSDポートフォリオへ → **422**
   `Buy idea 7203.T uses JPY; candidate currency must match portfolio base currency USD
   (cross-currency candidates fail closed)`（既存処理のまま・未変更）
5. ゼロ元本: ポジション0件のポートフォリオ → **422**
   `A positive, fully valued holdings basis is required; cash and liabilities are not included`
   （架空元本での成功扱いは起きない・既存処理のまま）
6. 書込確認: scratch DBの `broker_execution_applications` は **0行**（sqlite直接COUNTで実測）。
   order/orderbookエンドポイントは一度も呼んでいない。既存 `executable=False`・
   `Proposal only: no order…` ノートは不変。

## 8. TSUKUMO既存環境での全量make verify（rc分離・2026-10-24 00:12-00:57 JST再実行）

TSUKUMO `/home/good-light/r5-verify-t_463a2a9c`（**本ツリーの再rsync**・hash照合済み:
R5の8ファイルのsha256がVPSと同一・`uv sync --extra dev --extra operator-ir --no-install-project`
済みの既存 `.venv312`・`make V=$PWD/.venv312` で実行・TZ=UTC・PYTHONPATH=src はMakefileがexport）。

| ステージ | rc | 結果と分離 |
| --- | --- | --- |
| `make lint` | **1** | ruff **36エラー**、全てpaperloop/jp_value_quality系の別run WIP（`tests/test_paperloop_v12.py` 16・`paperloop_panel.py` 11・`tests/test_paperloop.py` 4・`tests/test_jp_value_quality_backtest_units.py` 3・`paperloop.py` 1・`loop_cli.py` 1）。**R5の8ファイルは0エラー**（VPSでも同一実測） |
| `make typecheck` | **2** | mypy **31エラー**、全て別run WIP（`paperloop_panel.py` 13・`services/jp_value_quality_backtest.py` 10・`paperloop_verify.py` 4・`services/jp_value_quality.py` 3・`paperloop.py` 1）。**R5のsrc5ファイルは0エラー** |
| `make test`（素のまま） | **2** | **collection中断で1件も実行されず**: 別run WIPの `tests/test_cusum_watch.py`（`from tests.test_paperloop import …`・`tests/__init__.py` は存在しないため素のpytestでは解決不可）と `tests/test_paperloop_v12.py` の2ファイルがcollection error。**R5ファイルは無関係** |
| make testと同一起動で上記2ファイルのみ `--ignore` | **1** | **14 failed / 2795 passed / 4 skipped / 5 xfailed（44:49）**。14の失敗は全てR5スコープ外（下表）。`FAILED` 行にsizing/cli_entry/ai_agent/portfolio_sizing関連は **0件** → R5の55テストは全量ランでも合格 |
| `make openapi` | **0** | openapi.json生成成功 |
| `make openapi-check` | **0** | `OpenAPI baseline matches the generated document (155 operations)`。baseline `docs/openapi_baseline.json`（別カードが生成・`/v1/sizing/quote-idea` を含むことをgrepで確認）と生成物が一致 |

### 8.1 全量ランの14失敗の内訳（いずれもR5の変更ファイルのimport閉包外）

| ファイル | 件数 | 性質 |
| --- | --- | --- |
| `tests/test_crypto.py` | 2 | binance klines系（R5が触れないcrypto provider系） |
| `tests/test_defensive_rotation.py` | 8 | `preimplementation_head` のgolden hash突合せ（実装前HEAD固定値 vs 現在の並行WIP大量uncommittedツリー。R5はstrategy系に非接触） |
| `tests/test_jp_value_quality_backtest_units.py` | 4 | 別run WIP（同じファイルがlint 3エラーを含む・上表のとおり） |

- R5起因の可能性の排除: 失敗3ファイルのどれも `sizing_models/sizing_idea/sizing_cli/portfolio_sizing_routes/cli_entry/ai_agent` を経由しない
  （`FAILED` 行のgrep実測 0件・§2のR5変更ファイル一覧との突合）。ツリー全体のgreenは
  並行runのWIP完了後の再実行が必要（正直な未達として記録）。
- 既知flake（メモ: `test_api_run_and_candidates` JST00時過ぎ）は今回の14件に含まれず。

## 9. 非変更範囲とhotspot

- 非変更: `cli.py`（R11 t_d8dcfdcf）・`cli_shim.py`（t_8262d080）・`research_ask.py`・
  `brief/routes.py`・`domain.py`・全provider・既存sizing計算
  （`services/portfolio_sizing.py` は**1行も変更していない**。gross basis・同通貨制約・
  `executable=False`・notesは既存のまま）・`api/app.py`（既存router経由のため登録追加なし）
- hotspot申告済み（コメント1517）: 稼働中に別runが `portfolio_sizing_routes.py` へ
  vercel_observability統合（UPSTREAM_UNAVAILABLE_DETAIL）を並行追記。R5のquote-ideaルート部分とは
  非依存で、R5側はquote-idea追加以外の同ファイル変更をしていない。TSUKUMOへrsyncした時点の
  スナップショットが同runの書き込み途中（importのみで使用箇所無し）を拾り一時的なF401を
  出したが、VPS現物では解消済み。
- `cli_entry.py` はR10（t_016c7014・検収済みdone）の未commit差分を保持したまま
  2行のみ追加。dependency blockは不要だった（開始時にdoneを確認）。

## 10. 残る項目（正直な申告・2026-10-24 00:57 JST時点）

- 全量 `make verify` は並行runのWIP（paperloop/jp_value_quality）によりlint(36)/typecheck(31)が通らず、
  素の `make test` は別run WIP2ファイルのcollection errorで中断（§8）。R5ファイルは
  対象モジュール検証（§6）と全量ランでの無失敗（§8・14失敗は全てR5スコープ外）で green だが、
  **ツリー全体のgreenは他runのWIP完了後の再実行が必要**。
- §5のライブ実測はyahoo providerの応答時点値であり、価格・as_of・retrieved_atは実行ごとに変わる
  （初回2026-10-23 20:18 JSTと再検証2026-10-24 00:13 JSTで契約挙動は同一・値は市場反映で別値）。
- browser E2Eは本機能のスコープ外（新UI無し・AGENTSのUI変更に非該当）。
- R1（5桁EDINETコード→ティッカー変換）等の他監査項目は本カードでは扱っていない
  （R5は正規化のみで変換・接尾辞補完を行わない設計。`29010` は `quote_unavailable` 404になる）。
- commit/pushはカード制約どおり実施していない（未commit差分のまま検収へ）。

## 11. 検収への依頼（同系統検収の限界）

- reviewer: audit-glm-heavy（実装worker-flashと同系統モデル）。**独立の再実行**を推奨:
  (1) 対象テスト2ファイルのpytest（55 passedの再現）、(2) R5の8ファイルのruff/mypy、
  (3) §5.4の失敗系curl、(4) §7のround-trip（scratch DBで再現可）、
  (5) 可能ならTSUKUMOでの§8ステージ分離の再確認。申告値の鵜呑みでなく実測での確認を求める。
