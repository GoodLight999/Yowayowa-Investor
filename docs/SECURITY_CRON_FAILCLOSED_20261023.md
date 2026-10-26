# /internal/cron/daily の設定依存 fail-open: 呼び出し元の実測と適用判断

作成: 2026-10-23 (JST) / カード t_f8a7dd8f / 実測者: worker-goat

## 1. 結論

**コードは変更していない。** 実測の結果、このエンドポイントには「認証ヘッダを付けずに到達し、
現在 fail-open の通過に依存して成立している呼び出し元」が実在した（Vercel Cron）。
カード手順 4 に従い、変更を入れず実測・影響・適用手順をここに記録して終端する。

あわせて、実測で判明した**現状のほうが監査時の想定より悪い**点を先に書く。

- 本番 `https://yowayowa-investor.vercel.app` は `mode=personal` で動いている
  （`GET /v1/health` → `{"status":"ok","mode":"personal",...}`）。
- 本番には `CRON_SECRET` が設定されていない（下記 3-1 の実測で確定）。
- したがって**インターネットから誰でも無認証で日次メンテナンスを起動でき、実際に DB 書込みまで走る**
  （実測: 不正 Bearer 付き GET が 200 で `portfolios_snapshotted:[1]` を返した）。
- つまり問題は「personal モードでローカル運用している場合だけの設定依存」ではなく、
  **本番でいま開いている**。

## 2. 対象コード（変更前の現状）

`src/yowayowa/api/app.py:324-330`

```python
def _authorize_cron(request: Request, settings: Settings) -> None:
    if settings.cron_secret:
        if request.headers.get("authorization") != f"Bearer {settings.cron_secret}":
            raise HTTPException(status_code=401, detail="Invalid cron authorization")
        return
    if settings.mode == "public":
        raise HTTPException(status_code=503, detail="CRON_SECRET is required in public mode")
```

secret 未設定かつ `mode != "public"` のとき、何もせず関数を抜ける（＝fail-open）。
ハンドラ本体 `daily_maintenance`（`app.py:333-405`）は認証通過後に
EDINET 索引同期・アラート評価・イベント購読評価・ポートフォリオスナップショット記録（DB 書込み）と
上流取得（Yahoo / EDINET）を行う。

secret の供給元は環境変数 `CRON_SECRET` のみ（`config.py:19-20`, `config.py:79`）。

## 3. 呼び出し元の棚卸し（実測）

棚卸し範囲と方法: ホストの crontab、`/root/.hermes/cron/jobs.json`（Hermes cron 登録）、
`/root/.hermes/scripts/` の yowayowa 関連シェル、`deploy/systemd/` の unit、
repo 内の `/internal/cron` 参照（`.worktrees` / `.git` を除く）、
および本番への実 HTTP 到達確認。

| # | 呼び出し元 | 実体 | 認証ヘッダ | 現在の挙動 |
|---|---|---|---|---|
| 1 | Vercel Cron | `vercel.json:29-34`（`path: /internal/cron/daily`, `schedule: 0 22 * * *`） | **なし**（プロジェクトに `CRON_SECRET` が無いため Vercel は Bearer を付与しない） | 200 で通過（fail-open に依存して成立） |
| 2 | ホスト crontab | `crontab -l` に yowayowa / internal 系の行は 0 件 | — | 呼び出しなし |
| 3 | Hermes cron | `/root/.hermes/cron/jobs.json` に yowayowa / internal 参照 0 件 | — | 呼び出しなし |
| 4 | `/root/.hermes/scripts/` | `vercel_yowayowa_logs.sh`（`/`と`/v1/health`のみ）、`yowayowa_brief_daily.sh`、`yowayowa_edinet_daily.sh`、`yowayowa_macro_daily.sh`。`grep -rn "internal/cron" /root/.hermes/scripts/` → 0 件 | — | 呼び出しなし |
| 5 | systemd | `deploy/systemd/yowayowa-api.service`（YOWAYOWA_MODE=personal, 127.0.0.1:8001）、`yowayowa-snapshot.service`（CLI 実行）。`/internal/cron` 参照 0 件 | — | 呼び出しなし |
| 6 | repo 内の他参照 | `vercel.json:31`、`app.py:333`、docs（`EDINET.md:93`、`WORKFLOW_DEADEND_AUDIT.md:90`、`OPENAPI_DRIFT_20261023.md`）のみ | — | 呼び出しなし |

補足: `/root/current-work/yowayowa-wt-*` の `internal/cron` 参照は同一 repo の worktree コピーであり、
独立した呼び出し元ではない。

### 3-1. 「本番に CRON_SECRET が無い」ことの実測

secret の値は一切出力せず、認証分岐の応答だけで判定した。

```
$ curl -s -o /dev/null -w "root=%{http_code}\n" https://yowayowa-investor.vercel.app/
root=200
$ curl -s https://yowayowa-investor.vercel.app/v1/health
{"status":"ok","mode":"personal","market_provider":"yahoo","capabilities":{"sec":true,"fred":false,"edinet":false,"ai_byok":false}}
$ curl -s -D headers_probe.txt -H "Authorization: Bearer __probe_definitely_wrong__" \
    https://yowayowa-investor.vercel.app/internal/cron/daily
HTTP/2 200
x-yowayowa-request-id: sin1::725dk-1792756621764-eff56530692e
{"ok":true,"alerts_checked":0,"event_subscriptions_checked":0,"event_inbox_unread":0,
 "event_failure":null,"edinet_index_days_synced":0,"edinet_index_indexed_days":0,
 "edinet_index_expected_days":0,"edinet_index_coverage_complete":false,
 "edinet_index_failure":null,"portfolios_snapshotted":[1],"portfolio_failures":{},
 "personal_market_tasks_skipped":false}
```

読み方:

- secret が設定されていれば Bearer 不一致は `401`、`mode=public` かつ secret 無しなら `503`。
  実際は **`200` かつハンドラの戻り値**なので、`cron_secret is None` かつ `mode=personal` が確定する。
- 戻り値の `portfolios_snapshotted: [1]` は
  `record_portfolio_snapshot()` が実行された証拠（＝無認証で DB 書込みが起きた）。
- この 1 回の GET は本番の日次メンテナンスを 1 回実行した。`config.py:10-14` の Vercel 既定は
  `sqlite:////tmp/yowayowa.db` だが、本番の `DATABASE_URL` 設定有無は本カードからは確認できない。
  実際に positions を持つ portfolio が存在して `[1]` がスナップショットされたため、永続 DB が
  設定されている可能性がある（`/internal/debug/runtime` は
  `database.positions_unique_constraint.applied=true` を返すが URL は出さない）。
  いずれにせよ「無認証の GET で DB 書込みが走った」という事実は変わらない。
- 参考: 本番のデプロイ revision は `4c86de9`（2026-10-06）。手元ブランチ先端より古いが、
  `_authorize_cron` は当該 revision でも同一の fail-open 実装（`git show 4c86de9:src/yowayowa/api/app.py`）。

### 3-2. ローカル systemd 個体

`systemctl is-active yowayowa-api` = active、`127.0.0.1:8001` で LISTEN。
unit に `CRON_SECRET` の指定は無い（`deploy/systemd/yowayowa-api.service:9-12`）ため
同じ fail-open 状態だが、bind は loopback のみで、呼び出し元も 0 件（表 #2-#5）。

## 4. 現状の認証分岐と回帰試験

| 入力 | 期待（変更前＝現状） | 実測 |
|---|---|---|
| secret 一致 | 通過 | `tests/test_cron.py:36` |
| secret 不一致 | 401 | `tests/test_cron.py:36-41` |
| secret 無し + `personal` | 通過（fail-open） | `tests/test_cron.py:19-21` |
| secret 無し + `public` | 503 | `tests/test_cron.py:24-33` |
| 認証通過後のハンドラ動作 | 変更前と同一 | `tests/test_cron.py:44-…`（イベント未購読時は provider 未生成、公開モードで personal provider を生成しない、EDINET 予算引渡し） |

```
$ .venv/bin/python -m pytest tests/test_cron.py -q
.......                                                                  [100%]
rc=0
```

本カードではコード・テストを変更していないため、上記は変更前後で同一の資産である。

## 5. なぜ今 fail-closed にしないか

カード手順 3 は「無認証で通過する呼び出し元が実在しない場合だけ fail-closed にする」。
実測では **Vercel Cron がまさにその呼び出し元**で、`CRON_SECRET` を Vercel プロジェクトに
入れない限り Bearer を持たない。この状態で fail-closed を入れると、本番の日次メンテナンスは
毎回 401 になり、運用が静かに止まる（カード手順 4 の禁止事項）。

fail-closed の適用自体は正しい。止めているのは順序だけ：**secret の配備が先、コードが後**。
Vercel プロジェクトの環境変数設定は当方の権限外（`VERCEL_TOKEN` は GitHub Actions secret で、
本カードは外部送信・設定変更を禁止）なので、ここで順序を飛ばさない。

未確認事項: Vercel Cron が実際に毎日発火しているかは、Vercel のログ・トークンが権限外のため
確認できていない。ただし現行コードでは「発火すれば 200 で通る」ことは GET 実測で確定しており、
Bearer を持たない呼び出し元が存在し得る以上、fail-closed を先行投入すると
発火中のジョブを 401 で止める側に倒れる。この非対称性（黙って壊す vs 現状維持）を避ける。

## 6. 適用する場合の手順（順序厳守・各段で実測）

前提: 作業者は Vercel プロジェクトの環境変数を編集できる権限を持つこと。
secret の値は生成して Vercel にのみ置き、repo・docs・ログ・カードに書かない。

1. Vercel プロジェクト（production）に環境変数 `CRON_SECRET` を追加する（十分長い乱数）。
2. 再デプロイ後、**コードは未変更のまま** Vercel Cron が通り続けることを確認する。
   期待: 22:00 UTC の実行が 200 のまま（`Authorization` は Vercel が自動付与）。
   手元確認は `curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $CRON_SECRET" \
   https://yowayowa-investor.vercel.app/internal/cron/daily` → `200`。
   同時にヘッダ無しの GET が `401` になることも確認する（この時点で secret 経路が有効）。
3. `_authorize_cron` を fail-closed にする（下記 7 の差分）。secret 未設定時は
   既存の認証失敗規約に合わせて **401**（`detail="CRON_SECRET is not configured"` 等）。
   `public` の 503 は現行どおり残す（仕様として明示されている）。
4. 対象テストを更新する: `test_personal_mode_can_use_deployment_protection_without_cron_secret`
   は現状の fail-open を固定しているため、`personal` + secret 無し = 401 に置き換える。
   `tests/test_cron.py` の他ケース（一致・不一致・public 503・ハンドラ動作）は不変。
5. `make verify` と CI（real-browser E2E 含む）を通す。本番デプロイ後、
   Vercel Cron の実行結果が 200 のままであることと、ヘッダ無し GET が 401 であることを実測する。
6. 手順 5 が確認できるまで `CRON_SECRET` を Vercel から外さない（外すと 401 で止まる）。

## 7. 将来適用する場合の差分（このカードでは未適用）

```diff
--- a/src/yowayowa/api/app.py
+++ b/src/yowayowa/api/app.py
@@
 def _authorize_cron(request: Request, settings: Settings) -> None:
-    if settings.cron_secret:
-        if request.headers.get("authorization") != f"Bearer {settings.cron_secret}":
-            raise HTTPException(status_code=401, detail="Invalid cron authorization")
-        return
-    if settings.mode == "public":
-        raise HTTPException(status_code=503, detail="CRON_SECRET is required in public mode")
+    if settings.mode == "public" and not settings.cron_secret:
+        raise HTTPException(status_code=503, detail="CRON_SECRET is required in public mode")
+    if not settings.cron_secret:
+        # 未設定は「誰でも起動できる」ではなく「起動できない」に倒す（fail-closed）。
+        raise HTTPException(status_code=401, detail="CRON_SECRET is not configured")
+    if request.headers.get("authorization") != f"Bearer {settings.cron_secret}":
+        raise HTTPException(status_code=401, detail="Invalid cron authorization")
```

（`public` + secret 有りは Bearer 照合、`public` + secret 無しは 503、`personal` + secret 有りは
Bearer 照合、`personal` + secret 無しは 401。既存の 401 文言・503 文言は変えない。）

## 8. 付随して見つかった同型の fail-open（本カードの対象外）

`src/yowayowa/api/deps.py:107-108` の `require_api_token` は `settings.api_token` 未設定なら
何もせず通す。本番は `mode=personal` かつ `api_token` 未設定（`/v1/health` は無認証で応答）なので、
**全 `/v1` がインターネットから開いている**。`_authorize_cron` と同根の設計判断だが、
影響範囲が桁違いに広い。本カードはアプリ側の cron 認証分岐だけが対象なので手を入れていない。
別カード起票の候補として記録する。

## 9. 再現コマンド一覧（監査用）

```
crontab -l | grep -i yowa                     # 0 件
grep -rn "internal/cron" /root/.hermes/scripts/   # 0 件
jq -r '..|strings' /root/.hermes/cron/jobs.json | grep -c "internal/cron\|yowa"   # 0
grep -rn "internal/cron" deploy/ | wc -l      # 0
grep -rn "internal/cron" --include=*.py --include=*.json --include=*.md . \
  --exclude-dir=.worktrees --exclude-dir=.git # vercel.json / app.py / docs のみ
grep -n "cron_secret\|CRON_SECRET" src/yowayowa/config.py   # 19-20, 79
grep -c '^CRON_SECRET=' /root/.hermes/.env    # 0
grep -c '^CRON_SECRET=' .env                  # 0
.venv/bin/python -m pytest tests/test_cron.py -q   # 7 passed
```
