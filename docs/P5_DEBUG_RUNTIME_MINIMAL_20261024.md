# P5: /internal/debug/runtime の最小化 — 実測記録 (t_697a94c4)

- 日付: 2026-10-24 (JST)
- 出典: `docs/OPENAPI_DRIFT_20261023.md` §4 P2 / §9-4
- 方針 (CTO 確定設計): 認証境界は「詳細はトークン保持時だけ」。
  `settings.api_token` 設定済みかつ `Authorization: Bearer` 完全一致のときだけ
  現行の完全 body を返す。それ以外 (未設定・不一致・ヘッダ無し) は 200 で
  `{"status":"ok","request_id":…}` のみ (liveness 維持、401 にしない)。

## 変更内容

| ファイル | 変更 |
| --- | --- |
| `src/yowayowa/api/deps.py` | `require_api_token` 直後に helper `operator_token_held()` を追加 (+17 行)。比較式は `require_api_token` と同一の `Bearer <token>` 完全一致を再利用。新たな crypto・設定項目・ミドルウェア・security scheme なし |
| `src/yowayowa/vercel_observability.py` | debug ルート (`vercel_runtime_debug`) のみ変更: `source` / `vercel` を `operator_token_held()` の真のときだけ payload に載せる (+docstring)。import 1 行追加。他カードの未コミット差分 47 行 (`UPSTREAM_UNAVAILABLE_DETAIL` / `_REQUEST_CONTEXT` / `log_upstream_exception`) は無傷のまま保全 |
| `tests/test_vercel_observability.py` | 旧 `test_runtime_debug_exposes_safe_vercel_metadata` を 4 ケース行列テスト `test_runtime_debug_full_body_only_with_operator_token` に更新。既存アサーション (source_revision / vercel 全項目 / secret 非露出) は弱めずトークン一致ケースへ移動し、無認証時に `source` / `vercel` が無いことを新規固定 |
| `tests/test_vercel_entrypoint.py` | エントリポイント経由のテストをトークン設定形式へ更新。一致時は full body、無認証時は最小 body を新規固定 |
| `tests/test_api.py` | 実 app の統合テスト内: 無認証時は `source` / `vercel` 無いことを固定し、トークン一致時は full body を追加検証 (既存の request_id 一致アサーションは維持) |

ミドルウェアの `http.request` ログ、`x-yowayowa-request-id` ヘッダ、
未処理例外 500 の request-id 付与は不変。他ルート・HTML・`/static`・
`/internal/cron/daily` の認証挙動は未変更。

## 実測 (プロセス内 TestClient・サーバ起動なし・DB は scratch sqlite)

実測スクリプト: `GET /internal/debug/runtime` を 4 条件で実行。
ローカルのため Vercel 環境変数は未設定 (vercel 各項目は null)。

### 変更前 (全ケースが無認証で詳細を返す＝問題の実測)

- トークン未設定 / ヘッダ無し・誤 Bearer、トークン設定済み / ヘッダ無し・誤 Bearer
  の **すべて** で同一の完全 body:

```json
{"request_id": "d4a32c2999be4a5f8ee405884fb8ddd2", "source": {"package_version": "0.1.0", "source_revision": null}, "status": "ok", "vercel": {"deployment_id": null, "environment": null, "production_url": null, "project_id": null, "region": null}}
```

(本番 `https://yowayowa-investor.vercel.app` では Vercel 環境変数が入るため、
deployment_id / project_id / production_url 等の実値が無認証で読めた。)

### 変更後 (4 ケース行列)

| ケース | 条件 | status | body |
| --- | --- | --- | --- |
| 1 トークン一致 | `YOWAYOWA_API_TOKEN` 設定済み + 正しい Bearer | 200 | 完全 body (下記) |
| 2 不一致 | 設定済み + 誤 Bearer | 200 | 最小 body |
| 3 未設定 | トークン未設定 + ヘッダ無し / 誤 Bearer | 200 | 最小 body |
| 4 ヘッダ無し | 設定済み + ヘッダ無し | 200 | 最小 body |

最小 body ( ケース 2・3・4 共通。request_id はヘッダ `x-yowayowa-request-id` と一致 ):

```json
{"request_id": "de5a225d11fb4e6b9d781aff41fc2a99", "status": "ok"}
```

ケース 1 の完全 body (ローカル実測。source/vercel は従来どおり):

```json
{"request_id": "23a47fed97594ebe8a79e5e7e77946d3", "source": {"package_version": "0.1.0", "source_revision": null}, "status": "ok", "vercel": {"deployment_id": null, "environment": null, "production_url": null, "project_id": null, "region": null}}
```

全ケースで `"secret"` (fixture の DATABASE_URL 内) および fixture トークン文字列の
応答非露出を確認。

## 試験結果

| 試験 | 結果 |
| --- | --- |
| `pytest tests/test_vercel_observability.py tests/test_vercel_entrypoint.py tests/test_api.py tests/test_api_error_contract.py` (TZ=UTC) | rc=0、98 テスト (2+92+1+3) 全パス。整形後再実行でも rc=0 |
| `ruff check` (変更 5 ファイル) | All checks passed |
| `ruff format --check` (変更 5 ファイル) | 全ファイル formatted |
| `mypy src/yowayowa` | rc=0 (Success: no issues found in 242 source files) |
| OpenAPI baseline gate (`check_openapi_baseline.py`) | rc=0 (155 operations, match — ルートは `include_in_schema=False` のため schema 不変) |

request-id 系 (500/502) の回帰テストは上記 4 ファイル
(`test_api_error_contract.py` 92 件を含む) で rc=0。

## docs/OPENAPI_BASELINE.md の確認

§「What this gate deliberately does not do」の記述
(無認証 `/internal/debug/runtime` は drift doc §9 items 1–4 で別途追跡) は、
ゲート自体の性質の記述として現在も正。ただし本修正で §9-4 の露出は解消されたため、
日付付き追記 (Update 2026-10-24, t_697a94c4) を同節に追加済み。
スキーマ baseline は不変 (gate 再実行 rc=0)。

## 本番で未検証の範囲

- 本カードはデプロイ・本番書き込みを行っていない。Vercel 環境変数が入った状態での
  完全 body (deployment_id / project_id / production_url 等の実値がトークン保持時のみ
  出ること) はローカルでは null のため未検証。
- 本番モニタリングがこのエンドポイントを無認証で叩いている場合、応答は最小 body へ
  変わる (liveness としての 200 と request_id ヘッダは維持)。監視側が source/vercel
  を利用していないかは運用側確認事項。
- `x-vercel-id` / `x-request-id` 経由の request_id フォールバック実挙動は未検証
  (ローカル TestClient にこれらのヘッダが無い)。
- deps.py の「api_token 未設定時に /v1 全体が無認証で通る」早期 return は本カードの
  対象外 (運用判断が要るため別カード)。
