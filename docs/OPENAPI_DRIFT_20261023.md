# OpenAPI 契約ドリフト監査（2026-10-23・読み取り専用）

対象: `openapi.json`（作業ツリー直下）と `src/yowayowa/api/*.py` の実装ルート。
制約: コード・仕様ファイルの変更なし。サーバ起動なし。外部 HTTP なし。実注文・送信なし。
本ドキュメントは報告のみで、リポジトリのコードには一切手を入れていない。

---

## 1. 結論

- 実装のルート表は 180 組（method, path）、そのうちスキーマ（OpenAPI 文書）に出るのは 155。
- 手元の `openapi.json` は 153 operations。**実装だけに存在する operation が 2 件、仕様だけに存在する operation が 0 件**。合計差分 2 件。
- 差分の直接原因は `openapi.json` が **git 管理外（`.gitignore:14`）のローカル生成物**で、最終生成が 2026-10-11 02:16:13、サイズ 577,613 bytes のまま更新されていないこと。CI にも差分ゲートが無い（`.github/workflows/ci.yml:64,66` は生成して「空でないこと」を見るだけ）。
- 件数としては小さいが、**危険度の高い乖離は「ルート差分」ではなく認証境界にある**。仕様に載っているのに実装にトークン依存が無い operation が 5 件（すべて orderbook）、仕様外の無認証 internal が 2 件、仕様側に securitySchemes が 0 個（＝トークン必須 150 本が外部クライアントから見えない）。

---

## 2. 実行方法と環境

比較スクリプト（リポジトリ外に置いた読み取り専用スクリプト。リポジトリは変更しない）:

```
/root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift_compare.py   # 本体（ルート表・パラメータ・レスポンス・認証）
/root/.hermes/profiles/worker-goat/cache/scratch/openapi_extra_checks.py    # 補助1（component 名の増減・operationId 重複・anonymous allowlist 評価）
/root/.hermes/profiles/worker-goat/cache/scratch/deep_schema_diff.py        # 補助2（component スキーマの内容差分）
/root/.hermes/profiles/worker-goat/cache/scratch/schema_ops_map.py          # 補助3（変更スキーマ→影響 operation の対応）
/root/.hermes/profiles/worker-goat/cache/scratch/license_exposure.py        # 補助4（LicenseClass の推移的露出数・$ref 差分）
/root/.hermes/profiles/worker-goat/cache/scratch/ref_lookup.py              # 補助5（$ref 参照元の特定）
/root/.hermes/profiles/worker-goat/cache/scratch/allowlist_runtime_shapes.py # 訂正1（anonymous allowlist を実リクエスト形状で再測定・付録C）
```

実行コマンド（リポジトリ直下、共有ツール venv。サーバは起動しない）:

```bash
cd /root/current-work/yowayowa-investor
PYTHONPATH=src ./.venv/bin/python \
  /root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift_compare.py \
  --spec openapi.json --out /root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift.json
```

補助スクリプトも同じ前置き（`cd` と `PYTHONPATH=src ./.venv/bin/python <path>`）で実行した。

実測環境: Python 3.12.3 / fastapi 0.141.1 / pydantic 2.13.5。
実装側のルート表は `yowayowa.api.app:app` をプロセス内で import して取得（`app.routes` の再帰。FastAPI 0.141 は include 済みルーターを `_IncludedRouter` で保持するため、その wrapper を展開して prefix を復元している）。仕様側は `openapi.json` をそのまま読み、比較用の「現在の実装が生成する仕様」は `app.openapi()`（`make openapi` が書き出すのと同じペイロード）を使った。**`openapi.json` への書き込みは行っていない**（実行前後で mtime `2026-10-11 02:16:13.608850157 +0900` / size `577,613` が不変であることを確認）。

成果物の生データ: `/root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift.json`（本文末尾の付録に、主要な数値を単体で再検証できる最小スクリプトを掲載）。

---

## 3. 実測値

ルート件数:

- 実装ルート総数（method, path の組）: **180**
- 実装のうちスキーマに出る operation: **155**
- 実装のうち `include_in_schema=False`（スキーマ外）: **25**
- 仕様（`openapi.json`）の paths: **141** / operations: **153**
- 実装のみに存在する operation: **2**
- 仕様のみに存在する operation: **0**
- ルート差分件数（合計）: **2**

スキーマ差分:

- パラメータ差分のある operation: **1**
- レスポンスのステータスコード差分: **0**
- リクエストボディ差分: **0**
- レスポンススキーマ（`$ref` 名レベル）差分: **0**
- component スキーマ: 仕様 233 / 実装 236（両方に存在 232、内容が変わったもの **5**、追加 **4**、削除 **1**）
- operationId の重複・欠落: 仕様 0 / 実装 0

認証:

- トークン依存あり: **150**（うち `require_api_token` 単独 118、`require_api_token` + `require_private_connectors` 32）
- スキーマに出るのに認証依存が無い operation: **5**（全件 orderbook）
- スキーマ外で認証依存が無い operation: **25**
- 仕様の `components.securitySchemes`: **0 件**、operation レベルの `security` 指定も 0 件
- 仕様の `info.version`: `0.1.0`（実装も `0.1.0`。ドリフトしても版が上がらない）

---

## 4. 危険度順の一覧

### P1: `/v1/orderbook/*` の 5 本が無認証（うち 1 本はディスク書込み）

- 該当: `GET /v1/orderbook/{symbol}`、`GET /v1/orderbook/{symbol}/depth`、`GET /v1/orderbook/{symbol}/metrics`、`POST /v1/orderbook/{symbol}/impact`、`POST /v1/orderbook/{symbol}/snapshots`
- 根拠: `src/yowayowa/api/orderbook_routes.py:16` の `APIRouter(prefix="/v1/orderbook", tags=["orderbook"])` に `dependencies=[Depends(require_api_token)]` が無い。他ルーターは `src/yowayowa/api/routes.py:89` などのようにルーターレベルで付けている。アプリ全体の認証ミドルウェアも存在しない（`app.py` に `add_middleware` は無く、`vercel_observability.py:115` の観測用ミドルウェアのみ）。
- 実害: 仕様にも security 無しで載っているため、外部からは「認証不要で叩ける API」として見える。`POST /v1/orderbook/{symbol}/snapshots` は `services/orderbook_service.py:269` `record_snapshot()` で `latest.json` を**ディスクに書く**（読み取り専用の照会ではない）。
- 追加で確認を要する点（コード読解のみ・未実行）: `_symbol_path()`（`orderbook_service.py:265-267`）は `"/"` と `":"` しか置換せず、`OrderbookSnapshot.symbol`（`orderbook_models.py:38`）に検証パターンが無い。`symbol=".."` を与えると保存先が基準ディレクトリの外へ出る経路が読める。実サーバでの再現確認は行っていない（サーバ起動禁止の制約による）。

### P1: `/internal/cron/daily` が設定次第で素通り（fail-open）

- 根拠: `src/yowayowa/api/app.py:324-330` `_authorize_cron()`。`settings.cron_secret` があれば Bearer 照合、無ければ `mode == "public"` のときだけ 503、**personal モードで `CRON_SECRET` 未設定なら何もせず通過**する。
- 実害: このハンドラはポートフォリオのスナップショット記録、アラート評価、EDINET 索引同期、イベント購読評価を行う（DB 書込み + 上流取得）。`app.py:333` でスキーマ外（`include_in_schema=False`）なので、契約を見ても存在に気付けない。
- 位置づけ: 「personal はローカル専用だから許容」という設計判断があり得るため、断定ではなく**設定依存の fail-open として記録**する。公開環境へこのモードのまま出ると無認証の状態変更点になる。

### P2: `/internal/debug/runtime` が完全無認証でビルド情報を返す

- 根拠: `src/yowayowa/vercel_observability.py:162` のハンドラに認証が一切無く、`app.py:244` で無条件にインストールされる。返す内容は `package_version`、`source`（`runtime_source_info()`：コミット SHA 等）、`vercel` ランタイム情報。
- 実害: 情報漏えい（版・改ざん検知の手掛かり）。スキーマ外なので契約にも出ない。

### P2: 仕様に securitySchemes が 0 個 — トークン必須 150 本が「認証不要」に見える

- 根拠: `deps.py:100-111` `require_api_token` は `Authorization: Bearer` を**手動ヘッダ比較**しており、FastAPI の security scheme として宣言されていない。よって生成仕様に `securitySchemes` も `security` も出ない（実測 0 件）。
- 実害: クライアント生成器・外部実装者は 150 本の必須トークンを知り得ない。認証が「実装の偶然」に依存しており、契約としては嘘をついている状態。`require_api_token` は `settings.api_token` 未設定時に**何もしない**（`deps.py:107-108`）ため、設定漏れで全公開になる経路も同時に存在する。

### P2: 契約が VCS 管理外で、CI に差分ゲートが無い（今回の直接原因）

- 根拠: `.gitignore:14` に `openapi.json`。`git ls-files` でも未追跡。`.github/workflows/ci.yml:64` で `uv run yowayowa export-openapi`、`:66` で `test -s openapi.json` のみ。`tests/test_openapi_contract.py` は「主要パスの存在」と「operationId の一意性」を固定するが、生成仕様と基準仕様の一致は検査しない。
- 実害: 契約の陳腐化が誰にも検出されない（今回は 12 日分）。加えて `info.version` が `0.1.0` 固定なので、破壊的変更と追加の区別が版でできない。

### P2: 仕様に無い operation 2 件（うち 1 件は破壊的）

1. `DELETE /v1/portfolios/{portfolio_id}`（`src/yowayowa/api/routes.py:351`、`force: bool = False` クエリ付き）— ポートフォリオ削除。認証はルーターレベルで有り。破壊的 operation が契約から抜けている。
2. `POST /v1/sizing/quote-idea`（`src/yowayowa/api/portfolio_sizing_routes.py:72`）— 見積りからのサイジング案生成。認証はルーターレベルで有り。

いずれも `docs/P5_R3_PORTFOLIO_DELETE_20261023.md` 等で意図的な追加として説明されており、**実装の誤りではなく生成物の未更新**。ただし「外部から見た挙動」の観点では、契約どおりに実装したクライアントはこの 2 本を認識できない。

### 訂正: 初版 P3「anonymous allowlist の死に項目 2 件」は取り下げ（誤検出）

- 初版は `deps.py:55-56` の許可パターンを**ルート定義のテンプレート文字列** `/v1/filings/edinet/{doc_id}/financials` に `fullmatch` させ、「一致 0 件」と判定した。しかし `_anonymous_public_research_allowed`（`deps.py:93-96`）が評価するのは実リクエストの `request.url.path` であり、テンプレート中の `{doc_id}` という文字列が文字クラス `[A-Za-z0-9]{8}` に一致することは原理的にあり得ない（`{` `}` `_` が英数字でない）。**比較対象（入力）の取り違え**であり、実装の欠陥ではない。
- 再測定（付録C）: 実リクエスト形状の具体パスを与えると、21 パターンすべてが現行ルートに一致（**21/21、死に項目 0**）。`GET /v1/filings/edinet/S100INTX/financials` と `.../facts` は匿名許可が有効で、述語 `_anonymous_public_research_allowed` の直接実行でも `True`（`.../{doc_id}/financials` というテンプレート文字列を与えたときだけ `False`）。
- 正確に残る性質（欠陥ではない）: allowlist は doc_id を **8 文字英数字**に限定しており、経路側の `doc_id: str`（`edinet_routes.py:122,132`）には検証パターンが無い。EDINET の doc_id は 8 文字（`edinet_index_db.py:14` の `String(8)`）なので実リクエストは一致する。8 文字でない（＝そもそも不正な）ID は匿名許可の対象外となり、トークン必須側へ倒れる（fail-closed 方向の差であり、公開面が広がる側の差ではない）。
- したがって §9 の旧項目 6（allowlist を `{doc_id}` テンプレートに追随させる）は**取り下げ**。現行文面をそのまま「追随」させると、正規表現が文字列 `{doc_id}` そのものにしか一致しなくなり、動作している匿名許可を壊す実回帰になる。

### P3: component スキーマの実体差分 5 件（レスポンスの主要キー）

- `LicenseClass`: enum に `synthetic` を追加（`official_public` / `personal_only` / `user_key` / `licensed_redistributable` → +`synthetic`）。`docs/DATA_POLICY.md:27` の方針変更に対応する意図的追加。**推移的に 57 operation のレスポンスに波及**（仕様側は 56、差分は新規 1 operation 分）。閉じた enum を前提にしたクライアントは壊れる。
- `MarketQuote`: `currency_reason` / `currency_source` を追加（`GET /v1/markets/quotes`）。
- `ValuationSnapshot`: `currency` / `currency_reason` / `currency_source` を追加（`GET /v1/valuation/{symbol}`）。
- `PortfolioRiskAnalytics`: `benchmark_correlation_missing` / `benchmark_correlation_missing_reason` を追加（`GET /v1/portfolios/{portfolio_id}/risk`）。
- `PortfolioSizingRequest`: `ideas[]` の `$ref` が `PortfolioSizingIdea` → `PortfolioSizingIdea-Input` に変化（`POST /v1/portfolios/{portfolio_id}/sizing-proposals`）。fastapi 0.141 の Input/Output 分割によるスキーマ名変更で、**名前で型を参照する生成コードには破壊的**。
- component の追加 4 件: `PortfolioDeletion`、`PortfolioSizingIdea-Input`、`PortfolioSizingIdea-Output`、`QuoteSizingIdeaRequest`。削除 1 件: `PortfolioSizingIdea`（仕様側では参照元 operation が無い孤立スキーマだったことも確認済み）。

上記の差分はいずれも**追加のみ**（フィールド削除・必須化はなし）で、読み手側の後方互換は保たれる（例外は enum 追加と `$ref` 名変更で、どちらも「閉じた集合」を前提にした生成コードには影響する）。

### P4: `GET /v1/screening/candidates` の `code` クエリが仕様に無い

- 根拠: `src/yowayowa/api/screening_routes.py:90` に `code: str | None = Query(default=None, max_length=5)`。仕様側の parameters に `query:code` が無い（実測差分 1 件、本監査で唯一のパラメータ差分）。

### P4: バージョン規律が無い

- 仕様 `info.version` = 実装 `0.1.0`。155 operations・236 スキーマに育った状態でも版が動かないため、契約の変化を版で追えない。

---

## 5. ルート差分の全件

実装のみ（2 件、いずれもスキーマ掲載対象）:

| method | path | 認証 | 備考 |
|---|---|---|---|
| DELETE | `/v1/portfolios/{portfolio_id}` | あり（`require_api_token`） | `force` クエリ付き。破壊的 |
| POST | `/v1/sizing/quote-idea` | あり（`require_api_token`） | 見積り→サイジング案 |

仕様のみ: 0 件。

スキーマ外（`include_in_schema=False`）の 25 件 — 「仕様に無い」が**意図的**なもの:

- HTML ページ 20 件: `/`、`/ai`、`/alerts`、`/broker-execution`、`/calendar`、`/charts`、`/compare`、`/discover`、`/edinet`、`/institutional`、`/instrument/{symbol}`、`/licenses`、`/macro`、`/markets`、`/news`、`/portfolio`、`/rates`、`/research/{symbol}`、`/screener`、`/settings`
- 配信アセット 3 件: `/assets/i18n/{locale}/{digest}.js`、`/assets/page/{digest}/{key}.js`、`/assets/static/{digest}/{filename}`
- 内部 2 件: `/internal/cron/daily`、`/internal/debug/runtime`（この 2 件だけは P1/P2 の対象）

---

## 6. パラメータ・必須/任意・レスポンス主要キーの食い違い（機械抽出の全件）

機械比較で検出できたのは次の 6 件で、これが全件。「上位 20 件」の枠は埋まらない（推測で水増ししない）。

1. `GET /v1/screening/candidates` — 実装のみ `query:code`（任意、`max_length=5`）。必須/任意の反転は 0 件。
2. `LicenseClass` enum に `synthetic` 追加（57 operation に波及）。
3. `MarketQuote` に `currency_reason` / `currency_source` 追加。
4. `ValuationSnapshot` に `currency` / `currency_reason` / `currency_source` 追加。
5. `PortfolioRiskAnalytics` に `benchmark_correlation_missing` / `benchmark_correlation_missing_reason` 追加。
6. `PortfolioSizingRequest.ideas[].$ref`: `PortfolioSizingIdea` → `PortfolioSizingIdea-Input`。

必須フィールドの増減は 0 件（`required` 配列は上記 4 スキーマすべてで一致）。レスポンスのステータスコード集合の差分も 0 件。

---

## 7. 認証境界の実測（まとめ）

- ルーターレベルで `require_api_token` を付けているもの: `routes.py:89`、`screening_routes.py:38`、`portfolio_sizing_routes.py:23` ほか。付いていないもの: `orderbook_routes.py:16`（唯一の `/v1` ルーター）。
- `require_private_connectors` は 32 operation に付与（`deps.py:114`、personal + `private_connectors_enabled` でないと 403）。
- `_PUBLIC_READ_ONLY_ROUTES`（`deps.py:37-59`）は public モード限定の匿名許可。**実リクエスト形状の具体パスで評価すると 21 パターンすべてが現行ルートに一致（死に項目 0、付録C）**。初版の「19 一致 / 2 不一致」はルート定義のテンプレート文字列と比較した誤測定で、§4 のとおり取り下げた。
- 仕様側は security を一切宣言していない（`securitySchemes` 0 件）。実装のトークン必須は契約から観測不能。
- `require_api_token` は `settings.api_token` が空だと何もしない（`deps.py:107-108`）。モード既定は `personal`（`config.py:50`）、`public` では起動時バリデータが `api_token` 必須にする（`config.py:109`）。

---

## 8. 未確認範囲

- 実サーバを起動していないため、HTTP 経由のステータスコード・ヘッダ・実レスポンス本文は未検証。比較はあくまで「ルート表 + 生成仕様 + ルート定義の依存関係」の静的比較。
- public モードでの HTTP 経由の実挙動は未実行（`mode="public"` を立てるには `api_token` 等の環境設定が必要で、本監査の制約に反する）。ただし匿名許可の**述語そのもの**は `_anonymous_public_research_allowed` を直接呼ぶ形で実測した（付録C: 実形状パスで 21/21 が許可、`_PUBLIC_READ_ONLY_ROUTES` の 21 パターンすべてが現行ルートに対応）。HTTP 層・ミドルウェア・ルーティング順序を経た挙動は依然として未検証。
- orderbook のパストラバーサル可能性はコード読解による指摘で、リクエスト再現は未実施。
- Vercel 本番（`https://yowayowa-investor.vercel.app`）に対する実測は行っていない（外部 HTTP 禁止）。本番の `openapi.json` がどの版で生成されたかは未確認。
- `openapi.json` が最終生成された時点の fastapi 版は不明（ファイルに生成メタデータが無い）。`PortfolioSizingIdea` の孤立や `-Input`/`-Output` 分割は fastapi 0.141 系の挙動であり、差分の一部は**ライブラリ更新由来**である可能性がある。この切り分けは未実施。
- 破壊的変更の有無は「追加/削除」の集合比較による。フィールドの意味変更（例: 単位・通貨の解釈変更）は機械比較では検出できない。

---

## 9. 次に起票すべき作業（提案・本監査では実施しない）

1. orderbook ルーターへ `require_api_token` を付与し、`symbol` に検証パターンを入れる（P1、セキュリティ変更なので別系統レビュー必須）。
2. `_authorize_cron` を fail-closed にする（`CRON_SECRET` 未設定時は personal でも 401/503）（P1）。
3. `/internal/debug/runtime` に認証を付けるか、personal モード限定にする（P2）。
4. `require_api_token` を `HTTPBearer` security scheme として宣言し、仕様に security を出す（P2、150 operation の契約が変わる）。
5. CI に「生成仕様と基準仕様の差分ゲート」を追加する（基準を VCS 管理下に置くか、`git diff --exit-code` 相当で検査）（P2）。
6. ~~allowlist の edinet 2 パターンを `{doc_id}` テンプレートに追随させる（P3）~~ **取り下げ**（初版 P3 は誤検出。§4 の訂正を参照。現行パターンは実リクエスト形状で有効に一致しており、追随させると匿名許可を壊す）。代えて起票するなら「経路側 `doc_id` に 8 文字の検証パターンを与え、allowlist と形状を明示的に一致させる」程度（実害なし・任意）。
7. `info.version` の運用規則を決める（P4）。

---

## 付録: 実行コマンドと出力（末尾）

```
$ cd /root/current-work/yowayowa-investor
$ PYTHONPATH=src ./.venv/bin/python /root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift_compare.py \
    --spec openapi.json --out /root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift.json
wrote /root/.hermes/profiles/worker-goat/cache/scratch/openapi_drift.json
{
  "spec_file_operations": 153,
  "spec_file_paths": 141,
  "impl_routes_total": 180,
  "impl_operations_total": 155,
  "impl_only_schema_operations": 2,
  "spec_only_operations": 0,
  "impl_hidden_from_schema_operations": 25,
  "param_drift_operations": 1,
  "response_status_drift_operations": 0,
  "request_body_drift_operations": 0,
  "response_schema_drift_operations": 0,
  "auth_guarded_operations": 150,
  "unauthenticated_in_schema_operations": 5,
  "unauthenticated_hidden_operations": 25,
  "doc_declares_any_security": false,
  "doc_security_schemes": [],
  "component_schemas_doc": 233,
  "component_schemas_live": 236,
  "component_fingerprint_doc": "a55ce23a6eac41fa",
  "component_fingerprint_live": "6260bcbf9c9e4042",
  "openapi_version": "3.1.0",
  "app_version": "0.1.0"
}
```

```
$ PYTHONPATH=src ./.venv/bin/python .../openapi_extra_checks.py
schemas added (live only): ['PortfolioDeletion', 'PortfolioSizingIdea-Input', 'PortfolioSizingIdea-Output', 'QuoteSizingIdeaRequest']
schemas removed (doc only): ['PortfolioSizingIdea']
live duplicate/empty operationIds: 0
doc duplicate/empty operationIds: 0
allowlist entries matching no live route: ['GET /v1/filings/edinet/[A-Za-z0-9]{8}/financials', 'GET /v1/filings/edinet/[A-Za-z0-9]{8}/facts']
```

注（重要）: 最終行の `allowlist entries matching no live route` は、**ルート定義のテンプレート文字列**（例 `/v1/filings/edinet/{doc_id}/financials`）に対して照合した結果である。実リクエストは具体パス（例 `/v1/filings/edinet/S100INTX/financials`）で届くため、この行は「死に項目」の証拠にならない。実リクエスト形状での再測定は付録C（結果: 21/21 が現行ルートに一致、死に項目 0）。

```
$ PYTHONPATH=src ./.venv/bin/python .../deep_schema_diff.py
schemas in both: 232
schemas with changed content: 5
  {"schema": "LicenseClass", "props_added": [], "props_removed": [], "bytes_doc": 129, "bytes_live": 142}
  {"schema": "MarketQuote", "props_added": ["currency_reason", "currency_source"], "props_removed": []}
  {"schema": "PortfolioRiskAnalytics", "props_added": ["benchmark_correlation_missing", "benchmark_correlation_missing_reason"], "props_removed": []}
  {"schema": "PortfolioSizingRequest", "props_added": [], "props_removed": []}
  {"schema": "ValuationSnapshot", "props_added": ["currency", "currency_reason", "currency_source"], "props_removed": []}
added: ['PortfolioDeletion', 'PortfolioSizingIdea-Input', 'PortfolioSizingIdea-Output', 'QuoteSizingIdeaRequest']
removed: ['PortfolioSizingIdea']
```

```
$ PYTHONPATH=src ./.venv/bin/python .../license_exposure.py
doc operations exposing LicenseClass (transitively): 56
live operations exposing LicenseClass (transitively): 57
PortfolioSizingRequest diff (doc -> live):
  value /properties/ideas/items/$ref '#/components/schemas/PortfolioSizingIdea' -> '#/components/schemas/PortfolioSizingIdea-Input'
```

```
$ git check-ignore -v openapi.json
.gitignore:14:openapi.json	openapi.json
$ stat -c "%y %s" openapi.json
2026-10-11 02:16:13.608850157 +0900 577613
$ grep -n "export-openapi\|test -s openapi.json" .github/workflows/ci.yml
64:      - run: uv run yowayowa export-openapi
66:        run: test -s openapi.json
```

変更していないことの確認: 監査開始時の `git status --short` は 72 行（変更済み 33 件 + 未追跡 39 件）、終了時は 73 行で、増えた 1 行は本ドキュメント `?? docs/OPENAPI_DRIFT_20261023.md` のみ。変更済み 33 件は不変、`openapi.json` の mtime とサイズも不変。

---

## 付録B: 主要数値の最小再検証スクリプト

上の本体スクリプトを置かずに、見出しの数値（180 / 155 / 153 / 差分2 / 無認証5 / トークン必須150）だけを独立に再現する。リポジトリ直下で
`PYTHONPATH=src ./.venv/bin/python <このファイル>` として実行する（サーバ起動・外部HTTPなし。書き込みなし）。

```python
import json
from pathlib import Path

from fastapi.routing import APIRoute

from yowayowa.api.app import app


def walk(routes, pre="", acc=None):
    acc = acc if acc is not None else {}
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            ctx = r.include_context
            walk(r.original_router.routes, pre + (ctx.prefix or ""), acc)
        elif isinstance(r, APIRoute):
            names = {d.call.__name__ for d in r.dependant.dependencies if getattr(d, "call", None)}
            for m in r.methods:
                acc[(m, pre + r.path)] = (r.include_in_schema, "require_api_token" in names)
    return acc


impl = walk(app.routes)
spec = json.loads(Path("openapi.json").read_text(encoding="utf-8"))
doc = {
    (m.upper(), p)
    for p, item in spec["paths"].items()
    for m in item
    if m in ("get", "post", "put", "delete", "patch")
}
schema_impl = {k for k, v in impl.items() if v[0]}
print("impl", len(impl), "in_schema", len(schema_impl), "doc", len(doc))
print("only_impl", sorted(schema_impl - doc))
print("only_doc", sorted(doc - schema_impl))
print("unauth_in_schema", sorted(k for k, v in impl.items() if v[0] and not v[1]))
print("guarded", sum(1 for v in impl.values() if v[1]))
```

実測出力（本監査で実行した結果そのまま）:

```
impl 180 in_schema 155 doc 153
only_impl [('DELETE', '/v1/portfolios/{portfolio_id}'), ('POST', '/v1/sizing/quote-idea')]
only_doc []
unauth_in_schema [('GET', '/v1/orderbook/{symbol}'), ('GET', '/v1/orderbook/{symbol}/depth'), ('GET', '/v1/orderbook/{symbol}/metrics'), ('POST', '/v1/orderbook/{symbol}/impact'), ('POST', '/v1/orderbook/{symbol}/snapshots')]
guarded 150
```

---

## 付録C: anonymous allowlist の再測定（実リクエスト形状・初版 P3 の訂正根拠）

初版は許可パターンを**ルート定義のテンプレート文字列**に照合して「死に項目 2 件」と判定した。依存 `_anonymous_public_research_allowed`（`deps.py:93-96`）が評価するのは `request.url.path`（具体パス）なので、各ルートの `{placeholder}` を現実的な具体値で埋めてから同じ `fullmatch` を掛け直した。

スクリプト: `/root/.hermes/profiles/worker-goat/cache/scratch/allowlist_runtime_shapes.py`
（`yowayowa.api.app:app` と `yowayowa.api.deps` をプロセス内 import。サーバ起動なし・外部 HTTP なし・書込みなし）

```
$ cd /root/current-work/yowayowa-investor
$ PYTHONPATH=src ./.venv/bin/python /root/.hermes/profiles/worker-goat/cache/scratch/allowlist_runtime_shapes.py
allowlist patterns: 21
patterns with a concrete live route: 21
patterns with NO concrete live route: []
predicate GET /v1/filings/edinet/S100INTX/financials -> True
predicate GET /v1/filings/edinet/S100INTX/facts -> True
predicate GET /v1/filings/edinet/{doc_id}/financials -> False
OK  GET  /v1/instruments/search                               <= /v1/instruments/search  (filled: /v1/instruments/search)
OK  GET  /v1/fundamentals/[^/]+                               <= /v1/fundamentals/{symbol}  (filled: /v1/fundamentals/X)
OK  POST /v1/screen                                           <= /v1/screen  (filled: /v1/screen)
OK  GET  /v1/compare/metrics                                  <= /v1/compare/metrics  (filled: /v1/compare/metrics)
OK  POST /v1/compare                                          <= /v1/compare  (filled: /v1/compare)
OK  GET  /v1/macro/bls/catalog                                <= /v1/macro/bls/catalog  (filled: /v1/macro/bls/catalog)
OK  GET  /v1/macro/bls/[^/]+                                  <= /v1/macro/bls/catalog  (filled: /v1/macro/bls/catalog)
OK  GET  /v1/macro/bea/nipa/catalog                           <= /v1/macro/bea/nipa/catalog  (filled: /v1/macro/bea/nipa/catalog)
OK  GET  /v1/macro/bea/nipa/[^/]+                             <= /v1/macro/bea/nipa/catalog  (filled: /v1/macro/bea/nipa/catalog)
OK  GET  /v1/macro/estat/tables                               <= /v1/macro/estat/tables  (filled: /v1/macro/estat/tables)
OK  GET  /v1/macro/estat/[^/]+/meta                           <= /v1/macro/estat/{stats_data_id}/meta  (filled: /v1/macro/estat/X/meta)
OK  GET  /v1/macro/estat/[^/]+/data                           <= /v1/macro/estat/{stats_data_id}/data  (filled: /v1/macro/estat/X/data)
OK  GET  /v1/licensing/sources                                <= /v1/licensing/sources  (filled: /v1/licensing/sources)
OK  GET  /v1/filings/edinet                                   <= /v1/filings/edinet  (filled: /v1/filings/edinet)
OK  GET  /v1/filings/edinet/documents                         <= /v1/filings/edinet/documents  (filled: /v1/filings/edinet/documents)
OK  GET  /v1/filings/edinet/index/history                     <= /v1/filings/edinet/index/history  (filled: /v1/filings/edinet/index/history)
OK  GET  /v1/filings/edinet/index/issuers                     <= /v1/filings/edinet/index/issuers  (filled: /v1/filings/edinet/index/issuers)
OK  GET  /v1/filings/edinet/[A-Za-z0-9]{8}/financials         <= /v1/filings/edinet/{doc_id}/financials  (filled: /v1/filings/edinet/12345678/financials)
OK  GET  /v1/filings/edinet/[A-Za-z0-9]{8}/facts              <= /v1/filings/edinet/{doc_id}/facts  (filled: /v1/filings/edinet/12345678/facts)
OK  GET  /v1/rates/treasury/curve                             <= /v1/rates/treasury/curve  (filled: /v1/rates/treasury/curve)
OK  GET  /v1/institutional/13f/[^/]+                          <= /v1/institutional/13f/{cik}  (filled: /v1/institutional/13f/X)
```

方法と限界: 各ルートのプレースホルダを 9 種の具体値（`X` / `A` / `1` / `12345678` / `S100INTX` / `AAPL` / `7203` / `abc-123` / `2024-01-01`）で埋め、いずれかが `fullmatch` すれば「現行ルートに一致」と判定した。サンプリングなので「一致」は証拠になるが「不一致」は網羅的でない（今回は不一致 0 件なので問題にならない）。テンプレート文字列そのものは証拠に数えていない（`[^/]+` が文字列 `{symbol}` に一致してしまい、無意味な偶然になるため）。

補足（訂正2: 初版訂正1で書いた「冗長項目・実害なし」は誤り）: `/v1/macro/bls/[^/]+` と `/v1/macro/bea/nipa/[^/]+` の2行は、上の一覧が**最初に一致したルート（catalog）だけ**を表示する作りだったため catalog 専用に見えていた。実測ではこの2行は詳細ルート `/v1/macro/bls/{series_id}`・`/v1/macro/bea/nipa/{table_name}` にも一致する（両ルートは `macro_routes.py:15` の `dependencies=[Depends(require_api_token)]` ＝トークン必須、`openapi.json` にも `/v1/macro/bls/{series_id}`・`/v1/macro/bea/nipa/{table_name}` として存在）。つまり public モードでは、トークン必須の詳細ルート（BLS 系列・NIPA テーブル）の読み取りが匿名許可される。catalog 行は `[^/]+` 行に包含される（重複）。この包含が意図的な設計かは本監査では判定しない（公開面の広さ・レート制限・データ権利の観点を含むため、未検証事項として扱う）。

追加測定（`macro_detail_allowlist_check.py`。`app.routes` を `_IncludedRouter` 展開で辿り、述語 `_anonymous_public_research_allowed` を実リクエスト形状の具体パスに直接適用。サーバ起動なし・外部HTTPなし・書込みなし）:

```
$ cd /root/current-work/yowayowa-investor
$ PYTHONPATH=src ./.venv/bin/python /root/.hermes/profiles/worker-goat/cache/scratch/macro_detail_allowlist_check.py
live routes total: 180
live macro bls/bea routes (path, methods, token_guard):
  /v1/macro/bls/catalog ['GET'] guarded=True
  /v1/macro/bls/{series_id} ['GET'] guarded=True
  /v1/macro/bea/nipa/catalog ['GET'] guarded=True
  /v1/macro/bea/nipa/{table_name} ['GET'] guarded=True

predicate (same function the dependency uses):
  GET /v1/macro/bls/CUUR0000SA0 -> True  (patterns: ['/v1/macro/bls/[^/]+'])
  GET /v1/macro/bea/nipa/T10101 -> True  (patterns: ['/v1/macro/bea/nipa/[^/]+'])
  GET /v1/macro/bls/catalog -> True  (patterns: ['/v1/macro/bls/catalog', '/v1/macro/bls/[^/]+'])
  GET /v1/macro/bea/nipa/catalog -> True  (patterns: ['/v1/macro/bea/nipa/catalog', '/v1/macro/bea/nipa/[^/]+'])
```

具体値 9 種で埋めた測定でも、`/v1/macro/bls/{series_id}` と `/v1/macro/bea/nipa/{table_name}` は 9 種すべてが各 `[^/]+` 行に `fullmatch` した（catalog も同じ行に一致するため包含関係にある）。上の一覧行が catalog を表示していたのは「最初の一致のみ表示」という出力上の都合であり、catalog 以外に一致しないことの証拠ではない。本体結論（21/21 一致・死に項目 0・P3 取り下げ）は変わらないが、**この2行を「実害なしの冗長項目」と評価した訂正1の記述は取り下げる**。

独立再現（レビュー側の別スクリプト）: `/root/.hermes/profiles/audit-glm-heavy/cache/scratch/audit_t3b1e69ca_allowlist.py` を同じ前置きで実行すると `runtime check GET /v1/filings/edinet/S100INTX/financials -> True` / `.../facts -> True`。本監査の再測定と一致。

---

## 改訂履歴

- 2026-10-23 初版。
- 2026-10-23 訂正1（レビュー指摘 1 点）: 初版 §4 P3「anonymous allowlist の死に項目 2 件」を**取り下げ**（テンプレート文字列と比較した誤検出）。§4 の該当節、§7 の実測値、付録の出力注解、§9 提案 6 を修正し、付録C（実リクエスト形状での再測定・21/21 一致）と §8 の未確認範囲の記述を追加した。**変更したのは本ドキュメントのみ**で、コード・`openapi.json` は初版から不変（`openapi.json` mtime `2026-10-11 02:16:13.608850157 +0900` / 577,613 bytes / sha256 `4f2234ad…`）。
- 2026-10-23 訂正2（レビュー第2回指摘 1 点・付録C 補足のみ）: 訂正1 で書いた「`/v1/macro/bls/[^/]+` と `/v1/macro/bea/nipa/[^/]+` は catalog にしか一致しない冗長項目・実害なし」を**取り下げ**。実測では両行はトークン必須の詳細ルート `/v1/macro/bls/{series_id}`・`/v1/macro/bea/nipa/{table_name}` に一致し、public モードでこれらを匿名許可する（述語直接実行で `GET /v1/macro/bls/CUUR0000SA0 -> True` / `GET /v1/macro/bea/nipa/T10101 -> True`、live routes 180 本を `_IncludedRouter` 展開で列挙し 4 本の macro ルートがすべて `guarded=True` を確認）。誤りの原因は付録C 一覧スクリプトが「最初に一致したルートのみ表示」していたこと。追加測定 `macro_detail_allowlist_check.py` を付録C に記載。**本体結論（21/21 一致・死に項目 0・P3 取り下げ）は維持**。変更は本ドキュメントのみで、コード・`openapi.json` は初版から不変（`openapi.json` mtime `2026-10-11 02:16:13.608850157 +0900` / 577,613 bytes / sha256 `4f2234ad…`）。
