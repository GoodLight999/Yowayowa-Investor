# P5: Yahoo 財務諸表の「取得失敗」と「データ無し」の分離

- 日付: 2026-10-23 (JST)
- カード: t_5f0f572b（承認済みキューからの自動供給カード）
- 出典: `docs/SILENT_FAILURE_AUDIT_20261023.md` §3 #6 / #7（影響「中」）
- 対象: `src/yowayowa/providers/yahoo_fundamentals.py`（provider 内で完結）＋ 専用テスト
- 触っていないファイル: `services/research_ask.py` / `services/research_brief.py` / `api/routes.py` / `domain.py`

## 1. 結論

`_safe_frame` は yfinance の例外を `except Exception: return pd.DataFrame()` で握り潰していた。
その空フレームは後段で `LookupError("No Yahoo financial statements available for …")` になり、
API では **404（この銘柄は財務諸表が無い）** として返っていた。つまり **Yahoo の障害・レート制限・
スキーマ変更が「データ無し」と同一視**されていた（AGENTS.md 不変条件2「欠損はゼロではない」違反）。

修正後は provider 内で次の4系統を区別する。

| 系統 | 実測される挙動 | 下流 |
|---|---|---|
| 正常 | `Fundamentals` を返す。degradation ノート 0 件 | 200 |
| yfinance 例外（レート制限・一時障害） | `YahooStatementsUnavailableError`（`RuntimeError` 派生、**`LookupError` ではない**）。`failures` に 6 件分の「statement/frequency: 型: メッセージ」 | 502 + `http.exception` ログに件数と理由 |
| 本当に空の DataFrame | `LookupError("No Yahoo financial statements available for <symbol>")`。メッセージは**修正前と同一** | 404（従来どおり） |
| スキーマ変更で列（または行）が無い | `YahooStatementsUnavailableError`（`schema_frames` に件数）。行名だけ変わった場合は `unmapped_frames` | 502 |
| 一部だけ失敗（部分成功） | `Fundamentals` を返しつつ、既存の `provenance.notes` に「n of 6 フレームが取得できなかった（理由…）」を追記 | 200（数値は壊さない） |

## 2. 修正前後の実測（同一 fixture・ネットワーク呼び出しなし）

`git show HEAD:src/yowayowa/providers/yahoo_fundamentals.py` を scratch に取り出し、
同じスタブ ticker で修正前モジュールと修正後モジュールを並べて実行した実測（生出力）:

```
[BEFORE (git HEAD)] normal                             -> OK metrics=3 notes=6 degradation_notes=0
[BEFORE (git HEAD)] yfinance exception (429)           -> LookupError(LookupError=True) | No Yahoo financial statements available for 7203.T
[BEFORE (git HEAD)] genuinely empty DataFrame          -> LookupError(LookupError=True) | No Yahoo financial statements available for 7203.T
[BEFORE (git HEAD)] schema change: no period columns   -> LookupError(LookupError=True) | No Yahoo financial statements available for 7203.T
[AFTER (working tree)] normal                             -> OK metrics=3 notes=6 degradation_notes=0
[AFTER (working tree)] yfinance exception (429)           -> YahooStatementsUnavailableError(LookupError=False) | Yahoo statements unavailable for 7203.T: 6/6 raised, 0/6 had no period columns, 0/6 had no recognized line item, 0/6 were empty (income_statement/yearly: RuntimeError: HTTP Error 429: Too Many Requests; balance_sheet/yearly: RuntimeError: HTTP Error 429: Too Many Requests; cash_flow/yearly: RuntimeError: HTTP Error 429: Too Many Requests; income_statement/quarterly: RuntimeError: HTTP Error 429: Too Many Requests)
[AFTER (working tree)] genuinely empty DataFrame          -> LookupError(LookupError=True) | No Yahoo financial statements available for 7203.T
[AFTER (working tree)] schema change: no period columns   -> YahooStatementsUnavailableError(LookupError=False) | Yahoo statements unavailable for 7203.T: 0/6 raised, 6/6 had no period columns, 0/6 had no recognized line item, 0/6 were empty
```

正常系の metric 数（3）と note 数（6）が修正前後で一致していること＝既存の成功応答は変わっていない。
空 DataFrame のメッセージも 1 文字も変わっていない＝404 の後方互換は維持。

## 3. 変更点（provider 内で完結）

`src/yowayowa/providers/yahoo_fundamentals.py`

- `_safe_frame` を廃止し `_load_frame(loader, frequency, statement)` に置換（L326）。
  例外・再試行失敗・非 DataFrame 応答は `YahooStatementFetchError`（理由を保持）として送出。
  `freq=` → `frequency=` の旧 yfinance 互換リトライは維持。
- `_frame_state(frame)`（L359）で「使える / 本当に空 / 形が壊れている」を3値判定。
  空＝Yahoo が「無い」と答えた、形の破損＝読めない、を混同しない。
- `_statement_frames`（L375）は `_StatementSample` を返す。`attempted`（要求した 6 フレーム）に対し
  `failures` / `empty_frames` / `schema_frames` / `unmapped_frames` を別々に数える。
- `_merge_frame`（L414）は「認識できた行ラベル数」を返す。0 件のフレームは
  `unmapped_frames` として数え、Yahoo の行名変更が「データ無し」に化けないようにした。
- `company_facts`（L193）は metrics が空のとき、失敗・スキーマ破損・行名不一致があれば
  `YahooStatementsUnavailableError` を送出し、それ以外だけ従来の `LookupError` を送出する。
  部分成功時は `_degradation_notes`（L458）が既存の `provenance.notes` に件数と理由を追記する。
- 追加した型: `StatementFrameFailure`（L28）、`YahooStatementFetchError`（L64）、
  `YahooStatementsUnavailableError`（L74）、内部用 `_StatementSample`（L40）。

### 応答契約の非変更

- 応答キー・モデル（`Fundamentals` / `Provenance`）は一切変更なし。新しいフィールドは足していない。
  失敗の件数と理由は **既存の `provenance.notes`（list[str]）** と、既存の 502 ログ
  （`vercel_observability.log_upstream_exception` が `error_message=str(exc)` を残す）に出る。
- 数値・単位・period の生成ロジックは未変更（`_merge_frame` の戻り値以外は同一）。
- 失敗を成功や 0 で埋める経路はない（metrics を作らないので series そのものが生まれない）。

## 4. 呼び出し側への影響範囲（実測ベース）

| 経路 | 位置 | 影響 |
|---|---|---|
| `GET /v1/fundamentals/{symbol}` | `api/fundamentals_routes.py:96-104` | `LookupError`→404 は不変。障害/スキーマ破損は 502（`UPSTREAM_UNAVAILABLE_DETAIL`）に分離。502 は `log_upstream_exception` で `error_message` に件数・理由が残る |
| `GET /v1/valuation/{symbol}` | `api/fundamentals_routes.py:110-120` | 同上（404 と 502 が分離） |
| `POST /v1/screen` / `/v1/compare` | `api/fundamentals_routes.py:127-153` | 例外を symbol 単位で集約する既存形（`failures=["data_unavailable"]` / `unavailable: <symbol>`）は不変。個別理由は notes 側で見る |
| `ListingAwareSecClient.company_facts` | `providers/registry.py:41-44` | 例外をそのまま透過（変更不要） |
| `SecFirstFundamentalsProvider`（legacy） | `providers/fundamentals.py:92-96` | `LookupError` のときだけ fallback する契約は不変。**Yahoo 障害では fallback しない**（＝障害を隠さない。既存テスト `test_sec_first_provider_does_not_hide_primary_outage` と同じ方向） |
| AI エージェント tool | `services/ai_agent.py:1301,1316,1384,1512` | tool 例外として上位へ（型名と件数がメッセージに載る）。tool の成功応答形状は不変 |
| 戦略スナップショット | `services/strategy_snapshot_service.py:78-83` | `outcome.errors[symbol] = "型名: メッセージ"` に件数・理由が載る（既存フィールド） |
| チャート合成 | `services/charts.py:98` | 例外は上位へ。部分成功時は notes が増えるだけ |

## 5. 試験

新規（`tests/test_yahoo_fundamentals_failure_signal.py`, 10 件・fixture のみ・実ネットワーク呼び出しなし）:

- `test_statement_outage_raises_an_upstream_error_not_missing_data` — 429 想定。`LookupError` でないこと・失敗 6 件・理由に `429` が残ることを検証
- `test_genuinely_empty_statements_keep_the_missing_data_contract` — 空 DataFrame は従来どおり `LookupError`・メッセージ完全一致
- `test_statement_schema_break_is_not_missing_data[NoPeriodColumnsTicker|NoRowsTicker]` — 列なし／行なしの2形状
- `test_unrecognized_statement_rows_are_not_missing_data` — 行名変更（`unmapped_frames`）
- `test_partial_failure_keeps_metrics_and_counts_the_failure_in_notes` — 4/6 失敗でも metrics は保持し notes に件数と理由、既存 note と末尾 note は不変
- `test_normal_load_adds_no_degradation_note` — 正常時に余計な note を足さない
- API 3 件 — 障害は **502**、空は **404**（メッセージ同一）、部分成功は **200** で notes に件数（応答キーも不変）

更新（`tests/test_yahoo_fundamentals.py` の旧 `_safe_frame` 3 件）:
`_load_frame` の新契約（旧 `frequency=` リトライ維持 / 両方失敗は理由付き送出 / 非 DataFrame は拒否）へ書き換え。

実行結果（VPS・対象モジュール中心）:

```
tests/test_yahoo_fundamentals_failure_signal.py                    10 passed
valuation / research / strategy / fundamentals / currency / registry /
provider-parsing / ai_agent / api-contract の既存 338 件             338 passed in 65.01s
上記＋sec・chart を加えた広い対象（33 ファイル・583 件） 583 passed in 67.25s (rc=0)
```

`ruff check` / `ruff format --check` / `mypy src/yowayowa/providers/yahoo_fundamentals.py` はいずれも rc=0。

## 6. 既知の限界（本カードの範囲外・正直な申し送り）

yfinance はレート制限時に **例外を投げずに空フレームを返す**ことがある（内部で警告ログに落として
空を返す経路）。その経路は provider から見ると「Yahoo がデータ無しと答えた」と区別できないため、
本修正でも `LookupError`（404）側に残る。区別するには yfinance のセッション/ログ計測か、
HTTP 応答そのものを観測する層が要る（新規データ源なしの制約下では別カード相当）。
本カードは「例外として上がってくる失敗」「形が壊れた応答」を確実に分離するところまでを対象にした。

## 7. 再現コマンド

```bash
cd /root/current-work/yowayowa-investor
PYTHONPATH=src .venv/bin/python -m pytest tests/test_yahoo_fundamentals_failure_signal.py \
  tests/test_yahoo_fundamentals.py tests/test_yahoo_fundamentals_japan.py \
  tests/test_quote_currency_contract.py tests/test_registry_factories.py \
  tests/test_valuation.py tests/test_strategy_yahoo.py tests/test_strategy_snapshot_service.py
.venv/bin/ruff check src/yowayowa/providers/yahoo_fundamentals.py tests/test_yahoo_fundamentals*.py
.venv/bin/mypy src/yowayowa/providers/yahoo_fundamentals.py
```

修正前後の比較プローブ（scratch に `git show HEAD:…` を置いて同一 fixture で実行）:
`/root/.hermes/profiles/worker-goat/cache/scratch/probe_yahoo_failure_signal.py`
