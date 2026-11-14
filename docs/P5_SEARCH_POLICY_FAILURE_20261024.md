# P5: 銘柄検索で provider 拒否を「該当0件」と偽らず、固定 403 で区別する

- カード: t_5a078314（yowayowa P5 / priority 8、親 t_4918bac4）
- 出典: `docs/SILENT_FAILURE_AUDIT_20261023.md` §3 #4、`docs/PRIVATE_OPERATOR_ROADMAP.md` P5 failure flows、`AGENTS.md` 不変条件 2（欠測は0ではない）・9（成功は正しさの証拠ではない）
- 実施日: 2026-10-24
- 対象: `GET /v1/instruments/search`（`src/yowayowa/api/routes.py::search_instruments`）
- 結論: **ライセンスポリシーによる拒否だけが理由で0件のときは 403 と固定文**を返す。
  一般障害は従来どおり 502（拒否と混在しても 502 優先）、**真の0件は従来どおり 200 `[]`**、
  1件以上 merged された部分成功は従来どおり 200 の list。正常系・引数検証・認証は**バイト一致で不変**。

## 1. 修正前の実測（再現）

`TestClient`（実 app・provider はスタブ・外向き socket は全遮断）で 13 シナリオ × limit 2 種を実測。
修正前の生値は `before.json`（30 レコード）。

| シナリオ | before status | before body |
|---|---|---|
| SEC空 + Yahoo拒否 | **200** | `[]` ← 拒否が「該当0件」に化ける（本カードの主題） |
| SEC拒否 + Yahoo空 | 502 | 固定 upstream 文 |
| 両方拒否 | 502 | 固定 upstream 文 |
| 真の両方空 | 200 | `[]` |
| SEC結果 + Yahoo拒否 | 200 | SEC の list |
| Yahoo結果 + SEC拒否 | 200 | Yahoo の list |
| 一般障害 + 拒否（両順） | 502 | 固定 upstream 文 |
| 両方一般障害 | 502 | 固定 upstream 文 |
| 通常 merge / Yahoo のみ / SEC のみ | 200 | list |

原因（`routes.py` 修正前 124–133 行）:

- SEC 側の `except Exception` が `ProviderPolicyError` も巻き取り、**ライセンス判断を「上流障害」として 502** にしていた。
- Yahoo 側は `except ProviderPolicyError: pass` で握り潰し、`errors` にも積まないため
  `if not merged and errors` を通過せず **200 + `[]`** になっていた。

つまり同じ「この情報源は規約上使えない」が、provider によって **502（障害）** と **200 0件（データ無し）** の
2 通りの嘘に割れていた。どちらも運用者には「検索して0件」と区別できない。

## 2. 修正内容

`src/yowayowa/api/routes.py` のみ（+62 −13 のうち、本カード分は +15 −1。残りは着手前から存在する
R3 削除・502 秘匿の承認済み未コミット差分で、**1 hunk も巻き戻していない**＝§5）。

- 局所定数を1つ追加:
  `SEARCH_POLICY_UNAVAILABLE_DETAIL = "Search is unavailable under the configured provider policy"`
- `search_instruments` の両 provider 試行で `ProviderPolicyError` を generic 例外と別に分類
  （`policy_denied` フラグ）。SEC 側にも `except ProviderPolicyError` を generic の**前**に置いた。
- 判定順は据え置きの 502 を先に、その後に 403:
  - `not merged and errors` → 従来どおり `log_upstream_exception` + 502 固定文（拒否が混在しても 502 優先）
  - `not merged and policy_denied` → **403** 固定文
  - それ以外 → 従来どおり `list(merged.values())[:limit]`
- 例外メッセージ・provider 名・URL・秘密は本文に出さない（固定文のみ）。拒否 provider のデータを
  取りに行く迂回・再試行・代替取得は追加していない（各 provider への呼び出しは 1 リクエスト 1 回）。
- 変更していないもの: schema / DB / OpenAPI の responses / UI / CLI / 新 API ルート / 診断基盤 /
  ライセンス規則 / mode / `q`・`limit` 検証 / API 認証 / `ai_agent` の `search_instruments` ツール。

`tests/test_search_policy_failclosed.py`（新規・20 件）

| 群 | 内容 |
|---|---|
| 403 | SEC空+Yahoo拒否 / SEC拒否+Yahoo空 / 両方拒否 = 403 かつ `{"detail": POLICY_DETAIL}` |
| 403 | 拒否 provider は再試行・裏口取得しない（2リクエストで各 provider 計2回だけ） |
| 200 `[]` | 両方正常かつ空 |
| 200 | SEC結果+Yahoo拒否 / Yahoo結果+SEC拒否 / SEC結果+Yahoo一般障害 |
| 502 | 一般障害+拒否 / 拒否+一般障害 / 両方一般障害（502 が拒否より優先） |
| 不変 | 重複 symbol の SEC 優先 merge（Yahoo 順→SEC 追加分、重複は1件・SEC 値）・`limit` 切り詰め・`limit` が両 provider へ同値で渡る・Instrument キー集合 |
| 不変 | 認証 401（provider は 0 回呼ばれる）・Bearer 付き 200・`q` 欠落/超過 422・`limit` 0/101 422 |
| 秘匿 | 注入した秘密マーカー・traceback・`site-packages` 等が 403/502 本文に無い |

## 3. 修正後の実測

RED→GREEN（同一ホスト・同一コマンド）:

```
# 修正前
$ .venv/bin/python -m pytest tests/test_search_policy_failclosed.py -q -p no:randomly
FFFF................                                                     [100%]
4 failed, 16 passed            rc=1
# 修正後
$ .venv/bin/python -m pytest tests/test_search_policy_failclosed.py -q -p no:randomly
20 passed                       rc=0
```

修正前後の差分（`before.json` → `after.json`、30 レコード）:

| シナリオ | limit | before | after | body |
|---|---|---|---|---|
| sec_empty_yahoo_policy | 20 / 1 | 200 | **403** | `4f53cda1…` → `cc098e5e…` |
| sec_policy_yahoo_empty | 20 / 1 | 502 | **403** | `3e7ce52a…` → `cc098e5e…` |
| both_policy | 20 / 1 | 502 | **403** | `3e7ce52a…` → `cc098e5e…` |
| both_truly_empty | 20 / 1 | 200 | 200 | 同一 |
| sec_rows_yahoo_policy | 20 / 1 | 200 | 200 | 同一 |
| yahoo_rows_sec_policy | 20 / 1 | 200 | 200 | 同一 |
| general_error_plus_policy | 20 / 1 | 502 | 502 | 同一 |
| policy_plus_general_error | 20 / 1 | 502 | 502 | 同一 |
| both_general_errors | 20 / 1 | 502 | 502 | 同一 |
| sec_rows_yahoo_general | 20 / 1 | 200 | 200 | 同一 |
| normal_merge | 20 / 1 | 200 | 200 | 同一 |
| normal_yahoo_only | 20 / 1 | 200 | 200 | 同一 |
| normal_sec_only | 20 / 1 | 200 | 200 | 同一 |
| validation: q欠落 / q超過 / limit 0 / limit 101 | — | 422 | 422 | 同一 |

- 変化は **拒否のみの 3 シナリオ（6 レコード）だけ**。残り **24 レコードは body sha256 まで一致**。
- 403 本文は 71 bytes / sha256 `cc098e5e9f732ee7b5a24433c39f574e3be33c514a1316a56c4cfcf1fb0cb953`
  （`{"detail":"Search is unavailable under the configured provider policy"}`）。
- 502 本文 sha256 `3e7ce52a…`、200 `[]` は `4f53cda1…`、通常 merge は `dd6476d4…`（340 bytes）で
  **before と同一**。
- 例外メッセージに `LEAK-MARKER-4b91:/root/private/vault.sqlite` を注入した 403/502 の本文に当該マーカーは
  **0 件**（probe が本文バイトで assert）。
- probe を 2 回実行して `after.json` の sha256 が一致（`712062f4…`）＝ 決定的。
- 403 は既存ミドルウェアの `http.request` ログに `status_code:403` として残る（本文・例外は出さない）。

## 4. 検証（host / rc 付き）

host: `ubuntu`（VPS）/ `.venv/bin/python` 3.12 / 2026-10-24 05:20–05:40 JST

| コマンド | 結果 | rc |
|---|---|---|
| `pytest tests/test_search_policy_failclosed.py -q -p no:randomly`（修正前） | 4 failed / 16 passed（RED） | 1 |
| `pytest tests/test_search_policy_failclosed.py -q -p no:randomly`（修正後） | 20 passed | 0 |
| `pytest tests/test_search_policy_failclosed.py tests/test_api_error_contract.py tests/test_yahoo_search.py tests/test_openapi_contract.py` | 124 passed（43.84s） | 0 |
| `ruff check src/yowayowa/api/routes.py tests/test_search_policy_failclosed.py` | All checks passed | 0 |
| `ruff format --check --diff`（同上2ファイル） | 2 files already formatted | 0 |
| `mypy src/yowayowa` | Success: no issues found in 242 source files | 0 |
| `python scripts/check_openapi_baseline.py` | baseline 一致（155 operations） | 0 |

- `tests/test_api_error_contract.py` は**期待値を一切変更せず**そのまま通過（`search.ok` / `upstream_failure` /
  `timeout` / 422 / 401 の各行が不変であることの担保）。
- **OpenAPI baseline は更新していない**（`docs/openapi_baseline.json` sha256 `5a2f0d02…4f5b`、
  mtime 2026-10-23 22:52:25 のまま。検証実行は 05:34）。差分を baseline 更新で隠していない。
- **全量 `make verify` は VPS では未実施**（カード指示どおり小試験のみ）。TSUKUMO 既存環境または同等 CI で
  実施が必要。本カードの変更は routes.py 1 関数＋局所定数と新規テスト 1 ファイルに閉じており、
  対象 4 スイート・ruff・format・mypy・openapi は上表のとおり緑。

## 5. 既存差分の保持・非接触

- `src/yowayowa/api/routes.py` の着手前差分（`47 insertions / 12 deletions`）は R3 portfolio delete と
  502 秘匿（t_efafb717）の**承認済み未コミット差分**。本作業後の numstat は `62 / 13` で、
  追加分は本カードの `+15 −1` のみ。既存 hunk（imports / fundamentals / valuation / news / calendar /
  market_overview / market_quotes / market_history / fred_series / edinet_documents /
  post_portfolio / get_portfolio_analytics / post_alert_evaluation）は**全て残置**（`git diff -U0` の hunk 一覧で確認）。
- `docs/INDEX_ja.md` は t_b0c28ad2 との競合回避のため**触っていない**（カード指示）。
- 製品コードの commit / push はしていない（`git status` は `M src/yowayowa/api/routes.py`、
  `?? tests/test_search_policy_failclosed.py`、`?? docs/P5_SEARCH_POLICY_FAILURE_20261024.md` のみ追加）。
- `ai_agent` の `_tool_search_instruments`・CLI・UI は非接触（検索の HTTP 経路だけが変わる）。
  既存 UI は検索失敗時に `error.message` を表示する実装（`src/yowayowa/web/static/app.js:357` の
  パレット、同 `:408` のダッシュボード）なので、403 の固定文は**UI 側を変えなくても**そのまま出る。

## 6. 入力/コード hash

| 対象 | sha256 |
|---|---|
| `src/yowayowa/api/routes.py`（作業後） | `891ce25082cbee1ae4511b3ef3626dd6ef5dc60c50970270c4275aa674947a62` |
| `src/yowayowa/api/routes.py`（HEAD `f562163`） | `e918a868a532e5778376b8cd2707005c776e4939da2e1b8cbbf4c4ca5d47173e` |
| `tests/test_search_policy_failclosed.py` | `e24a7ae1c76848ad6b6b9cc38ef3eae724964c4332f69b4a65aa39e8e7b8ee8d` |
| probe 入力（before） | `25e2936317f572f796dc8cdc0b1a7bc172ef8bb67688752d22b83c209f9ca1e8` |
| probe 出力（after、2回一致） | `712062f4dc2eff14bf7cfd3a28428bb78008e0093c1c91c29d5885c36381fdfb` |

probe/比較スクリプトは scratch（`probe_search_policy.py` / `compare_probe.py` / `table_probe.py`）に置いた
使い捨てで、リポジトリには入れていない。

## 7. 残課題（別カード判断・本カードでは作らない）

- 拒否の**理由**（どの provider が・なぜ）は本文にもログにも出さない設計にした。運用時に切り分けが要るなら、
  秘匿済みの構造化ログ1行を足す別カードになる（本カードは「新しい診断基盤を作らない」ため対象外）。
- 同じ「policy 拒否を無言で通す」形が他の route に残っていないかの横断監査は別カード（本カードは検索のみ）。
