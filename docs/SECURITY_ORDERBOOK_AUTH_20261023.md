# /v1/orderbook/* 5本の認証境界を既存トークン境界へ揃える（t_f1023629）— 実測記録

- 実施日: 2026-10-23 JST
- 担当: worker-goat / 実装ホスト: VPS(ubuntu) / 対象: yowayowa-investor `agent/commercial-foundation`
- 出典: `docs/OPENAPI_DRIFT_20261023.md` §4 P1（`/v1/orderbook/*` の5本が無認証）と §9-1（次に起票すべき作業1）
- 位置づけ: **監査の再制作ではない**。§9-1 の1件（ルーターレベル認証の付与）だけを実施し、着手時に監査の数値（無認証5本）を実測で取り直した記録。

## 1. 結論

- 修正前の実測: `/v1/orderbook/*` の5操作はルータ依存が空（`deps=[]`）で、**`api_token` を設定した状態でも トークン無し=200／誤トークン=200**。監査の「無認証5本」は現在のツリーでもそのまま再現した。
- 修正後: 5操作すべて **トークン無し=401／誤トークン=401／正トークン=200**。`require_api_token` 付き operation は 150/185 → **155/185**。
- 呼び出し側の実測: `src/yowayowa/web/`・`browser_tests/` に orderbook 参照は **0件**。CLI（`orderbook_cli.py`）と内蔵AIエージェント（`services/ai_agent.py`）は **HTTP ではなく `OrderbookService` を直接呼ぶ**。`/v1/orderbook` を HTTP で叩くのはテストだけ。**認証付与で壊れる呼び出し側は存在しない**（実測）。
- `deps.py` の「トークン未設定なら何もしない」挙動は**本カードでは変更していない**（カード制約）。したがって本修正は「トークンを設定してある運用」でのみ実効する（§7-1）。

## 2. 変更内容

| ファイル | 変更 |
|---|---|
| `src/yowayowa/api/orderbook_routes.py` | `APIRouter(...)` に `dependencies=[Depends(require_api_token)]` を追加（既存依存の付与のみ。`from fastapi import Depends` と `from yowayowa.api.deps import require_api_token` の import 追加）。**新しい認証機構・ミドルウェア・設定項目は作っていない** |
| `tests/test_orderbook_auth.py` | 新規・5件。境界の両方向（401×2種／200）＋ 未設定時 200 ＋ 他ルーター非影響 ＋ OpenAPI の operationId/認証ヘッダ pin |

触っていないもの: `services/orderbook_service.py`、`tests/test_orderbook.py`（他カードが編集中＝カード指定の競合ファイル）、`app.py`、`deps.py`、`web/`、CLI。
着手時の `git status --short` で `api/orderbook_routes.py` に差分は無かった（＝押し込みなしで編集可能）。`orderbook_service.py`・`tests/test_orderbook.py` の差分は他カードのもので、本カードは1行も変更していない。

## 3. 修正前の実測（監査数値の再確認）

測定スクリプト: `/root/.hermes/profiles/worker-goat/cache/scratch/orderbook_auth_probe.py`（読み取り専用・TestClient のみ・サーバ起動なし・注文/送信なし。orderbook の書込みは scratch の一時ディレクトリへ向けた）。生ログ: `docs/evidence/20261023/orderbook_auth_probe_before.txt`

```
== 1. static router dependencies: 185 operations, 150 with require_api_token ==
  GET   /v1/orderbook/{symbol}                       deps=[]
  GET   /v1/orderbook/{symbol}/depth                 deps=[]
  GET   /v1/orderbook/{symbol}/metrics               deps=[]
  POST  /v1/orderbook/{symbol}/impact                deps=[]
  POST  /v1/orderbook/{symbol}/snapshots             deps=[]
  control (must keep require_api_token, unchanged by this card):
  POST  /v1/screening/run                            deps=['require_api_token']
  GET   /v1/portfolios                               deps=['require_api_token', 'db_session']

== 2. OpenAPI parameters ==
  GET   /v1/orderbook/7203                     params=['symbol']
  GET   /v1/orderbook/7203/depth               params=['symbol', 'max_levels']
  GET   /v1/orderbook/7203/metrics             params=['symbol']
  POST  /v1/orderbook/7203/impact              params=['symbol']
  POST  /v1/orderbook/7203/snapshots           params=['symbol']
  GET   /v1/portfolios                         params=['authorization']

== 3. live statuses with api_token configured (no-token / wrong-token / valid) ==
  GET   /v1/orderbook/7203                     200 / 200 / 200
  GET   /v1/orderbook/7203/depth?max_levels=5  200 / 200 / 200
  GET   /v1/orderbook/7203/metrics             200 / 200 / 200
  POST  /v1/orderbook/7203/impact              200 / 200 / 200
  POST  /v1/orderbook/7203/snapshots           200 / 200 / 200

== 4. live statuses with api_token unset (deps.py fail-open, unchanged) ==
  （5本すべて 200）
```

読み取り: 監査 §4 P1 の「5本が無認証」は**現行ツリーで再現**（deps=[]、認証ヘッダ無しの OpenAPI パラメータ、トークン有無に関わらず 200）。一方で操作総数は監査時の180から185へ増えており、`require_api_token` 付きは150で一致した（他カードの追加分）。

## 4. 呼び出し側の実測（先に測った）

カード手順2のとおり、修正前に呼び出し側を実測した。

```
$ grep -rni "orderbook" src/yowayowa/web/ | wc -l
0
$ grep -rni "orderbook" browser_tests/ | wc -l
0
$ grep -rn "/v1/orderbook" --include=*.py --include=*.js --include=*.html --include=*.ts .   # .git/.worktrees 除く
tests/test_orderbook_auth.py:1,54-58,137-141      # 本カードの新規テスト
tests/test_orderbook.py:121,129,134,141           # 既存テスト（トークン未設定で実行＝200 のまま）
src/yowayowa/api/orderbook_routes.py:18           # ルーター定義
$ grep -n "OrderbookService()|_service.get_snapshot|_service.estimate_impact" \
      src/yowayowa/orderbook_cli.py src/yowayowa/services/ai_agent.py
src/yowayowa/orderbook_cli.py:16,27,78,119
src/yowayowa/services/ai_agent.py:2115,2150
```

- **UI**: portfolio 画面/静的JS/テンプレートに orderbook 参照は 0件。orderbook を表示する UI は現状存在しない ＝ UI を壊す変更ではない。
- **CLI**: `orderbook show|metrics|impact` は `OrderbookService` をプロセス内で直接呼ぶ。HTTP を経由しないため、ルーター認証の影響を受けない。実測: `yowayowa orderbook metrics 7203` → exit 0、既存スナップショットの表示継続（終値・スプレッド・OFI が従来どおり出る）。
- **内蔵AI（`get_orderbook` / `estimate_orderbook_impact` ツール）**: 同じく `OrderbookService()` を直接呼ぶ。HTTP 呼び出しではないため影響なし。既存 `tests/test_orderbook.py::test_ai_agent_orderbook_tools` が緑のまま（§6）。
- **HTTP 経由の外部クライアント**: リポジトリ内には存在しない（テストのみ）。したがって「トークンを送れない呼び出し側」は無く、カード手順2の「認証を付けずに終端する」分岐には該当しない。

## 5. 修正後の実測

同じスクリプトを再実行（生ログ: `docs/evidence/20261023/orderbook_auth_probe_after.txt`）。

```
== 1. static router dependencies: 185 operations, 155 with require_api_token ==
  GET   /v1/orderbook/{symbol}                       deps=['require_api_token']
  GET   /v1/orderbook/{symbol}/depth                 deps=['require_api_token']
  GET   /v1/orderbook/{symbol}/metrics               deps=['require_api_token']
  POST  /v1/orderbook/{symbol}/impact                deps=['require_api_token']
  POST  /v1/orderbook/{symbol}/snapshots             deps=['require_api_token']
  control: /v1/screening/run, /v1/portfolios の deps は変化なし

== 2. OpenAPI parameters ==
  GET   /v1/orderbook/7203                     params=['symbol', 'authorization']
  GET   /v1/orderbook/7203/depth               params=['symbol', 'max_levels', 'authorization']
  GET   /v1/orderbook/7203/metrics             params=['symbol', 'authorization']
  POST  /v1/orderbook/7203/impact              params=['symbol', 'authorization']
  POST  /v1/orderbook/7203/snapshots           params=['symbol', 'authorization']

== 3. live statuses with api_token configured (no-token / wrong-token / valid) ==
  GET   /v1/orderbook/7203                     401 / 401 / 200
  GET   /v1/orderbook/7203/depth?max_levels=5  401 / 401 / 200
  GET   /v1/orderbook/7203/metrics             401 / 401 / 200
  POST  /v1/orderbook/7203/impact              401 / 401 / 200
  POST  /v1/orderbook/7203/snapshots           401 / 401 / 200

== 4. live statuses with api_token unset (deps.py fail-open, unchanged) ==
  （5本すべて 200・意図どおり未変更）
```

- 401 の本文は既存ルーターと同一の `{"detail": "Invalid API token"}`（`deps.py:106-110` の既存文言。新文言は作っていない）。
- **401 はリクエスト検証より先に出る**（実測）。トークン無し＋不正ペイロード/不正クエリでも 422 ではなく 401 が返るため、未認証の相手にボディ・クエリのスキーマを探らせない:

```
POST /v1/orderbook/7203/impact                   body 無し                -> 401 Invalid API token
POST /v1/orderbook/7203/snapshots                body 無し                -> 401 Invalid API token
GET  /v1/orderbook/7203/depth?max_levels=999     （ge=1,le=100 違反）      -> 401 Invalid API token
```

- 拒否時は**ハンドラが実行されない**ため、`POST /snapshots` の `latest.json` 書込みも `get_snapshot` の合成ボード生成も走らない。新規テストはこれを scratch ディレクトリが空のままであることで固定している（監査 §4 P1 の「うち1本はディスク書込み」への実効的な封鎖）。
- `_PUBLIC_READ_ONLY_ROUTES`（`deps.py:37-59`）に orderbook は含まれないため、public モードでも匿名許可にならない（同リストを読んで確認。リスト自体は無変更）。

## 6. 試験

方針: 先に新テストを書いて **RED**（＝修正前の状態を検出できること）を実測し、修正後に **GREEN** を確認した。

RED（`docs/evidence/20261023/orderbook_auth_pytest_before.txt`）:

```
FAILED tests/test_orderbook_auth.py::test_orderbook_routes_reject_missing_and_wrong_token
  E assert 200 == 401     # GET /v1/orderbook/7203 without token
FAILED tests/test_orderbook_auth.py::test_openapi_contract_pins_and_auth_parameter
  E assert 'authorization' in {'symbol'}
2 failed, 3 passed   (rc=1)
```

GREEN（`docs/evidence/20261023/orderbook_auth_pytest_after.txt`）: `5 passed` rc=0

関連スイート（既存の他ルーター・契約 pin・CLI 表示・エラー契約を同時に通す）:

```
$ pytest tests/test_orderbook.py tests/test_orderbook_auth.py tests/test_openapi_contract.py \
         tests/test_licensing.py tests/test_api.py tests/test_api_error_contract.py \
         tests/test_cli_http_errors.py --deselect \
         tests/test_api_error_contract.py::test_internal_exception_message_is_not_echoed_in_error_body
148 passed, 1 deselected, 1 warning in 84.49s   (rc=0)
```

- 上記 deselect は**本カードと無関係な既存の XPASS(strict)**。`test_internal_exception_message_is_not_echoed_in_error_body` は「src は read-only なので報告のみ」として `xfail(strict=True)` が付いているが、他カードの作業で実際には通る状態になっており XPASS で失敗する。**本カードの変更を `git stash` で外した状態でも同じく rc=1 で再現**することを実測済み（`/root/.hermes/profiles/worker-goat/cache/scratch/xpass_without_my_change.txt`）。本カードは当該テストとそのマーカーに触れていない。
- 静的検査: `ruff check` / `ruff format --check` は変更2ファイルとも緑。`mypy` は変更2ファイル単体で `Success: no issues found`。パッケージ全体 `mypy src/yowayowa` は **31 errors / 5 files**（`paperloop*.py`・`services/jp_value_quality*.py`）で、いずれも他カードの未コミット作業に由来し **orderbook 関連は0件**。
- `make verify` 全量とブラウザ E2E は本カードでは未実施（VPS 実行制約＋UI 変更なし。§7-6）。

## 7. 残存リスク・未実施

1. **`api_token` 未設定なら実効ゼロ（fail-open）**。`deps.py:107-108` はトークン未設定時に何もしない。カード制約により本カードではこの挙動を変えていないため、`YOWAYOWA_API_TOKEN` を設定していない personal 運用では、今回の修正後も5本は誰でも叩ける。**解消にはトークン運用（設定）が前提**であり、deps.py 側の fail-closed 化は別カードの判断事項。
2. **OpenAPI に `securitySchemes` は無いまま**（監査 P2 未解消）。今回の追加は `authorization` ヘッダパラメータ（任意）として現れるだけで、仕様上は依然「必須の認証」とは読めない。他150本と同じ表現に揃えただけで、契約としての認証宣言は未解決。
3. **`symbol` の検証パターン／パストラバーサル**（監査 §9-1 後段、`orderbook_service.py:265-267`）は**本カードの範囲外**。認証で入口は閉じたが、認証済み operator が `..` を渡した場合の経路は未修正。
4. **`openapi.json` は未再生成**（gitignore されたローカル生成物）。生成物は次の `yowayawa export-openapi` 実行で 5本にヘッダパラメータが載る。
5. **`POST /v1/orderbook/{symbol}/snapshots` は依然ディスク書込み**。認証済み operator にのみ開放された点だけが変わった。
6. **本番（Vercel）は未検証**。外部 HTTP を出さない制約のため、production の `/v1/orderbook/*` が 401 になるかは未確認。CI とデプロイ後に確認が必要。
7. **`docs/INDEX_ja.md` への追記は見送り**。同ファイルは他カードが編集中（未コミット差分あり）で、hotspot を増やさないため。
8. 検収は別系統役員へ `kanban_request_review(reviewer='audit-gmn')` で依頼する（カード指定）。

## 8. 再現コマンド

```bash
cd /root/current-work/yowayowa-investor
source .venv/bin/activate

# 境界の静的＋ライブ実測（修正前後の比較に使用。サーバ起動なし）
PYTHONPATH=src python /root/.hermes/profiles/worker-goat/cache/scratch/orderbook_auth_probe.py

# 専用テスト
pytest tests/test_orderbook_auth.py

# 関連スイート
pytest tests/test_orderbook.py tests/test_orderbook_auth.py tests/test_openapi_contract.py \
       tests/test_licensing.py tests/test_api.py tests/test_api_error_contract.py \
       tests/test_cli_http_errors.py --deselect \
       tests/test_api_error_contract.py::test_internal_exception_message_is_not_echoed_in_error_body

# 静的検査
ruff check src/yowayowa/api/orderbook_routes.py tests/test_orderbook_auth.py
ruff format --check src/yowayowa/api/orderbook_routes.py tests/test_orderbook_auth.py
mypy src/yowayowa/api/orderbook_routes.py tests/test_orderbook_auth.py
```

証跡: `docs/evidence/20261023/orderbook_auth_probe_before.txt` / `orderbook_auth_probe_after.txt` / `orderbook_auth_pytest_before.txt` / `orderbook_auth_pytest_after.txt` / `orderbook_auth_targeted_148.txt`
