# P5 R3 — 誤作成したポートフォリオを削除できるようにする（API / CLI / 試験 / 実測）

- 実測日時: 2026-10-23 12:20–13:00 JST
- カード: t_08b40e50（出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 R3・P1）
- 対象: `agent/commercial-foundation` @ `08e41cf` ＋ 本カードの未コミット変更
  （`services/portfolios.py` / `api/routes.py` / `cli.py` / `domain.py` の `PortfolioDeletion` /
  `tests/test_portfolio_delete.py` / 本ドキュメント / `docs/INDEX_ja.md`）
- 作業ツリーには他カード（P5 R4 の通貨欠落・research のプロンプト予算）の同時変更が同居している。
  本カードは上記の差分だけを持ち、他カードの変更には触れていない（`domain.py` / `routes.py` は共有ファイル）
- 実測は**実データ DB の読取コピー**に対して行った（原本は変更していない）

## 0. 結論

`portfolio create` で誤作成しても消せなかった行き止まり（`grep -rn 'delete_portfolio' src/` → 0件）を、
service / API / CLI の3面で塞いだ。削除は財務データを消す操作なので **fail-closed** にした。

- 存在しない id → **404**（DB 変化なし）
- 保有・スナップショット・約定適用台帳が1行でも残っている → **409**。何が何行残っているかを本文に列挙し、`force=true` を要求する
- `force=true` のときだけ、`positions` / `portfolio_snapshots` / `broker_execution_applications` と
  ポートフォリオ本体を**同一トランザクション**で削除し、消した行数を応答で返す
- 2回目の削除は **404**（黙って成功にしない）
- 他のポートフォリオ（base currency 違いを含む）は一切変わらない

## 1. 追加したもの

| 面 | 追加 |
| --- | --- |
| domain | `PortfolioDeletion`（`portfolio_id` / `name` / `force` / `deleted_positions` / `deleted_snapshots` / `deleted_applied_executions`） |
| service | `delete_portfolio(session, portfolio_id, *, force=False)` と `PortfolioNotEmptyError`（`src/yowayowa/services/portfolios.py`） |
| API | `DELETE /v1/portfolios/{portfolio_id}?force=<bool>` → 200 `PortfolioDeletion` / 404 / 409（`src/yowayowa/api/routes.py`） |
| CLI | `yowayowa portfolio delete <id> [--force] [--yes]`（`src/yowayowa/cli.py`）。`--yes` 無しは**何も送らず** exit 1 |
| 試験 | `tests/test_portfolio_delete.py`（12件） |

`delete_portfolio` の追加位置は `create_portfolio` の直後。`/portfolios` 系の既存ハンドラ
（一覧・取得・作成・保有 upsert/削除・analytics・snapshots・export.csv）には手を入れていない。

## 2. 関連行の扱い（cascade 範囲の決定）

`portfolios.id` を参照する外部キーは DB モデル上ちょうど3つ（`grep -n "portfolios.id" src/yowayowa/db.py`）で、
いずれも `ON DELETE CASCADE`。ただし SQLite の外部キー強制は接続設定に依存するため、
**ORM の関係カスケードに任せず、3テーブルを明示的に DELETE してから本体を消す**（1トランザクション）。

| 従属テーブル | 既定（force 無し） | `force=true` |
| --- | --- | --- |
| `positions`（保有一覧） | 1行でもあれば 409 で拒否 | 削除（行数を報告） |
| `portfolio_snapshots`（日次スナップショット＝履歴） | 1行でもあれば 409 で拒否 | 削除（行数を報告） |
| `broker_execution_applications`（約定の二重適用防止台帳） | 1行でもあれば 409 で拒否 | 削除（行数を報告） |

判断の理由:

- スナップショットだけ残る状態を許すと「ポートフォリオの無い履歴行」が orphans として残り、
  後段の集計が壊れる。だから保有が0でもスナップショットがあれば既定は拒否（fail-closed）。
- 約定適用台帳を残すと、削除後に同じ `execution_id` を再適用しようとした際に
  「適用済み」と誤判定し得る。よって本体と一緒に消す。
- 逆に、何も従属していない誤作成（名前の typo など）は `force` 無しで消せる。これが本来の用途。

## 3. API の実測（実データ DB の読取コピー・実 HTTP ステータス）

環境:

```
cp data/yowayowa.db <scratch>/yowayowa.db        # 原本 sha256 a4eb6c64…c50540（コピーも同一）
sha256sum 原本/コピー → 一致
YOWAYOWA_MODE=personal YOWAYOWA_DATABASE_URL=sqlite:///<scratch>/yowayowa.db \
  python -m uvicorn yowayowa.api.app:app --host 127.0.0.1 --port 8127
GET /v1/health → 200
GET /openapi.json → DELETE /v1/portfolios/{portfolio_id}
  operationId=delete_portfolio_by_id_v1_portfolios__portfolio_id__delete, parameters=[portfolio_id, force, authorization]
```

準備（`R3 Live Mistake` id=1 / JPY・保有2件、`R3 Live Keeper` id=2 / USD・保有1件。
両方に snapshot 1行と broker 適用台帳1行を ORM で投入）:

```
before: portfolios=2 positions=3 snapshots=2 applied=2
```

| 操作 | 応答 | 操作後の DB 行数 |
| --- | --- | --- |
| `DELETE /v1/portfolios/999999` | **404** `{"detail":"Portfolio 999999 not found"}` | portfolios=2 positions=3 snapshots=2 applied=2（不変） |
| `DELETE /v1/portfolios/1` | **409** `{"detail":"Portfolio 1 still owns dependent rows (positions=2, snapshots=1, applied_executions=1); pass force=true to cascade the delete"}` | 同上（不変） |
| `GET /v1/portfolios/1`（409 の後） | **200**（消えていない） | — |
| `DELETE /v1/portfolios/1?force=true` | **200** `{"portfolio_id":1,"name":"R3 Live Mistake","force":true,"deleted_positions":2,"deleted_snapshots":1,"deleted_applied_executions":1}` | portfolios=1 positions=1 snapshots=1 applied=1 |
| `GET /v1/portfolios/1` | **404** | — |
| `GET /v1/portfolios/1/snapshots` | **404** | — |
| `GET /v1/portfolios/2` | **200** `{"id":2,"name":"R3 Live Keeper","base_currency":"USD","symbols":["AAPL"]}` | keeper: positions=1 snapshots=1 applied=1（不変） |
| `DELETE /v1/portfolios/1`（2回目） | **404** `{"detail":"Portfolio 1 not found"}` | portfolios=1 positions=1 snapshots=1 applied=1 |

force 削除後に残った行は keeper（id=2）の3行のみで、id=1 側の3テーブルはすべて0行。

## 4. CLI の実測（同じサーバー・同じコピー DB）

```
$ yowayowa portfolio delete 2 --base-url http://127.0.0.1:8127
Refusing to delete portfolio 2 without --yes (nothing was sent). Re-run with
--yes, and with --force if it still owns holdings or snapshots.        exit=1
  → DB 不変（positions=1 snapshots=1 applied=1 のまま）

$ yowayowa portfolio delete 999 --yes
Delete failed (404): Portfolio 999 not found                          exit=1

$ yowayowa portfolio delete 3 --yes            # id=3 は保有1件
Delete failed (409): Portfolio 3 still owns dependent rows (positions=1); pass
force=true to cascade the delete                                      exit=1
  → 実測 positions(portfolio_id=3)=1 のまま

$ yowayowa portfolio delete 3 --yes --force
Deleted portfolio 3 (R3 CLI Target) · positions 1 · snapshots 0 · applied
executions 0 · force True                                             exit=0
  → portfolios=1 positions=1（keeper のみ）
```

`--yes` は対話プロンプトではなく明示フラグにした（headless 実行で待ちに入らないため）。

## 5. 自動試験

`tests/test_portfolio_delete.py`（12件・rc=0）:

1. 未知 id → `LookupError`
2. 保有あり → `PortfolioNotEmptyError`、`counts` が `{positions:1, snapshots:0, applied_executions:0}`、**行は減らない**
3. スナップショットだけ残る場合も拒否（`snapshots:1`）
4. `force=True` → positions2/snapshots1/applied1 を削除、4テーブルすべて0行、以後 `LookupError`
5. 従属なしは `force` 不要で削除でき、他ポートフォリオ（USD）の3テーブルが残る
6. `record_portfolio_snapshot` で作った履歴行が force 削除で消える（orphan を作らない）
7. API: 404 → 409（本文に `positions=1`/`snapshots=1`/`force=true`）→ 200（counts 一致）→ GET 404 → snapshots 404 → 2回目 404 → keeper の3行と `base_currency=USD` が不変（DB 行数を直接検証）
8. API: 従属なしの誤作成は `force` 無しで削除でき、一覧が空になる
9. CLI: `--yes` 無しは API を呼ばない（呼び出し記録0件・exit 1）
10. CLI: `--yes` で DELETE パスと `params={"force": False}`、表示に counts
11. CLI: `--force` で `params={"force": True}`、409 は exit 1 で detail 表示
12. CLI: `portfolio delete --help` が exit 0 で `--force`/`--yes` を出す

関連スイート（新規12件＋portfolio/API/CLI/OpenAPI/licensing/alerts の計15ファイル）も **97 passed**:

```
pytest tests/test_portfolio_delete.py tests/test_cli.py tests/test_p3_cli.py tests/test_cli_shim.py \
  tests/test_openapi_contract.py tests/test_api.py tests/test_import_routes.py \
  tests/test_portfolio_persistence.py tests/test_portfolio_csv.py tests/test_portfolio_analytics.py \
  tests/test_portfolio_risk.py tests/test_portfolio_short.py tests/test_portfolio_sizing_api.py \
  tests/test_alerts.py tests/test_licensing.py
  → 97 passed, 1 warning in 17.89s
```

## 6. 静的検証

- `ruff check` / `ruff format --check`（変更5ファイル）→ pass
- `mypy src/yowayowa` → **31 errors / 5 files（既存）**。内訳は `paperloop_panel.py` 13・
  `services/jp_value_quality_backtest.py` 10・`paperloop_verify.py` 4・`services/jp_value_quality.py` 3・
  `paperloop.py` 1 で、**いずれも本カードが触っていないファイル**（worktree でも未変更＝HEAD と同一内容）。
  変更5ファイルに mypy エラーは0件。

## 7. 残る穴（本カードの範囲外・次カード候補）

- **UI にポートフォリオ削除導線が無い**。作成は `portfolio.html` + `static/portfolio.js` からできるが、
  削除ボタンは無い（`grep -n "delete" src/yowayowa/web/static/portfolio.js` は保有削除のみ）。
  誤作成は UI 上で起きるので、UI 側の削除（確認ダイアログ + i18n + 実ブラウザ E2E）は別カードで扱う。
  → **子カード t_ad8d18f1 で解消済み**（`docs/P5_R3_UI_DELETE_20261023.md`。API は無変更）。
- 本カードは API/CLI/試験/本ドキュメントのみで、スキーマ変更はしていない（不要だった）。

## 8. 再現手順

```bash
# 1) 読取コピーでサーバー起動
cp data/yowayowa.db $TMPDIR/r3/yowayowa.db
YOWAYOWA_MODE=personal YOWAYOWA_DATABASE_URL=sqlite:///$TMPDIR/r3/yowayowa.db \
  PYTHONPATH=src .venv/bin/python -m uvicorn yowayowa.api.app:app --port 8127

# 2) 誤作成を作る
curl -s -X POST localhost:8127/v1/portfolios -H 'content-type: application/json' \
  -d '{"name":"Mistake","base_currency":"JPY"}'      # → id
curl -s -X PUT localhost:8127/v1/portfolios/<id>/positions -H 'content-type: application/json' \
  -d '{"symbol":"7203","quantity":"100","currency":"JPY"}'

# 3) 境界
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8127/v1/portfolios/999999   # 404
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8127/v1/portfolios/<id>     # 409
curl -s -X DELETE 'localhost:8127/v1/portfolios/<id>?force=true'                          # 200 + counts
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE localhost:8127/v1/portfolios/<id>     # 404

# 4) CLI
PYTHONPATH=src .venv/bin/yowayowa portfolio delete <id> --base-url http://127.0.0.1:8127             # exit 1・未送信
PYTHONPATH=src .venv/bin/yowayowa portfolio delete <id> --yes --force --base-url http://127.0.0.1:8127 # exit 0
```

行数の確認は `sqlite3 <copy> "select (select count(*) from portfolios),(select count(*) from positions),(select count(*) from portfolio_snapshots),(select count(*) from broker_execution_applications);"`。
