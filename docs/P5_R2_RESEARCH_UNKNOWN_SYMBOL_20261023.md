# P5 R2: 未知シンボルの research 応答を fail-closed にする

- カード: t_65bed735（yowayowa P5 / R2・P0）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 R2、§7 推奨2
- 実施日: 2026-10-23
- 対象: `GET /v1/research/{symbol}`
- 結論: **未知シンボルは 200 の空成功をやめ、404＋機械可読な理由を返す**。
  既知シンボル（`2901.T`）の 200 応答は**正規化ハッシュ一致**で不変。
- 全量試験は VPS では走らせていない（カード指示どおり対象モジュール＋research 系のみ）。全量は TSUKUMO。

## 1. 修正前の実測（再現）

ローカル API を `127.0.0.1:8123` で起動（既定 8000 は他カードと衝突）。

```
$ cd /root/current-work/yowayowa-investor
$ .venv/bin/python -m uvicorn yowayowa.api.app:app --host 127.0.0.1 --port 8123
$ curl -s 'http://127.0.0.1:8123/v1/research/29010'
HTTP=200  bytes=932
{"symbol":"29010","sections":{"profile":{},"analyst":{"price_targets":{},...,"growth_estimates":[]},
 "insiders":{"purchases":[],"transactions":[],"roster":[]},"esg":[],"actions":{"dividends":null,...},
 "filings":{},"fund":{}},
 "errors":{"ownership":"YFNotImplementedError: Have not implemented fetching 'shares' from Yahoo API"},
 "provenance":{...}}
```

- `sections` は 7 セクション（`ownership` は例外で欠落）、**中身はすべて空**。
- status は **200**、`errors` は ownership のみ。→ 「データが無い」と「銘柄が違う」を区別できない。
- 上流ログにも `Quote not found for symbol: 29010` / `$29010: No data found, symbol may be delisted`
  が出ているが、yfinance は例外ではなく空 dict を返すため、現行コードはそれを成功として扱っていた。

参考（既知シンボル・修正前）:

```
$ curl -s 'http://127.0.0.1:8123/v1/research/2901.T'
HTTP=200  bytes=4406
non_empty_sections=['profile','analyst','insiders','actions']
errors={"ownership":"YFNotImplementedError: ..."}
```

## 2. 修正内容

`src/yowayowa/providers/yahoo_deep_research.py`

- `section_has_data(value)` を追加。正規化後のセクションを再帰的に見て、
  `None` / `{}` / `[]` / 空白文字列だけの値は「データなし」と判定する（`0` は実データとして扱う）。
- `research()` は全セクションを読み終えた後に判定し、**どのセクションにもデータが無ければ例外**にする。
  - 1つ以上のモジュールが応答していて中身が全部空 →
    `ResearchSymbolUnavailable`（`LookupError` 派生 / HTTP **404** / `code=research_symbol_unavailable`
    / `reason=every_requested_section_empty`）
  - 全セクションが例外 →
    `ResearchUpstreamUnavailable`（HTTP **502** / `code=research_upstream_unavailable`
    / `reason=every_requested_section_failed`）。ティッカーが正しくても起きるので 404 にしない。
- 例外の `as_detail()` が機械可読な本文（`code` / `symbol` / `reason` / `sections_requested` /
  `sections_failed` / `message`）を返す。
- **一部セクションだけ欠ける既知銘柄は挙動不変**: 例外は `errors` に積み、残りを 200 で返す。
  空だが例外でないセクションを `errors` に足すこともしない（既存の応答形を変えないため）。

`src/yowayowa/api/research_routes.py`

- `except ResearchUnavailable as exc: raise HTTPException(status_code=exc.status_code, detail=exc.as_detail())`
  を `ProviderPolicyError` / `LookupError` より前に追加（それ以外の 4xx 規約はそのまま）。

`tests/test_research_unknown_symbol_failclosed.py`（新規・22 件）

| 試験 | 内容 |
|---|---|
| `test_section_has_data_boundaries`（14 ケース） | `None`/`{}`/`[]`/`""`/`"   "`/入れ子の空 dict/空 list は偽、文字列・数値・値入り list は真、`0` は実データ |
| `test_unknown_symbol_raises_research_data_unavailable` | 未知シンボルで `ResearchSymbolUnavailable`、`LookupError` 互換、`code`/`symbol`/全 8 セクション/`ownership` の失敗型が本文に出る |
| `test_unknown_symbol_detail_is_machine_readable` | `as_detail()` の各フィールド（`code`/`reason`/`sections_requested`/`sections_failed`） |
| `test_subset_request_that_is_entirely_empty_raises` | `?sections=profile` だけ要求して空 → 例外（要求セクション基準で判定） |
| `test_known_symbol_with_one_missing_section_still_answers` | 既知銘柄＋ownership 未実装 → 例外にせず 200 相当。`errors` は ownership のみ、空セクションは errors に足さない |
| `test_provider_outage_on_every_section_raises_upstream_unavailable` | 全滅は 502 系（`LookupError` ではない） |
| `test_api_provider_outage_returns_502_with_reason` | HTTP 502＋`code`/`reason`/`sections_failed`（全 8） |
| `test_api_unknown_symbol_returns_404_with_reason` | HTTP 404＋構造化 `detail`（機械可読） |
| `test_api_known_symbol_keeps_success_shape` | HTTP 200、`sections` のキー集合・`errors`・`esg==[]`・`provenance.provider` が従来どおり |

## 3. 修正後の実測

```
$ curl -s -w '\nHTTP=%{http_code}\n' 'http://127.0.0.1:8123/v1/research/29010'
HTTP=404
{"detail":{"code":"research_symbol_unavailable","symbol":"29010",
 "message":"research_symbol_unavailable: no usable Yahoo research data for 29010;
   every requested section was empty [profile, analyst, ownership, insiders, esg, actions, filings, fund];
   failed sections: ownership (YFNotImplementedError)",
 "reason":"every_requested_section_empty",
 "sections_requested":["profile","analyst","ownership","insiders","esg","actions","filings","fund"],
 "sections_failed":{"ownership":"YFNotImplementedError: Have not implemented fetching 'shares' from Yahoo API"}}}

$ curl -s -w '\nHTTP=%{http_code}\n' 'http://127.0.0.1:8123/v1/research/ZZZZZZ'   # 存在しないティッカー
HTTP=404  同じ構造（symbol だけ ZZZZZZ）

$ curl -s -w 'HTTP=%{http_code} bytes=%{size_download}\n' 'http://127.0.0.1:8123/v1/research/2901.T'
HTTP=200 bytes=4406

$ curl -s 'http://127.0.0.1:8123/v1/research/2901.T?sections=profile'
HTTP=200 bytes=2403
```

### 既知シンボル応答の同一性（hash）

`retrieved_at` / `as_of` だけを `<ts>` に正規化した canonical JSON の sha256 で比較
（正規化スクリプト: `research_hash.py`、比較は body 全体）。

| 対象 | 修正前 | 修正後 | 判定 |
|---|---|---|---|
| `2901.T` canonical_sha256 | `ef1b3fd7b4a0f70c6199f37525747c5078c179b6d4796cc40ca2879d47eecb5a` | `ef1b3fd7b4a0f70c6199f37525747c5078c179b6d4796cc40ca2879d47eecb5a` | **一致** |
| `2901.T` raw sha256 | `3b5a8101…f80e68` | `780ad1cb…4de337` | 差は取得時刻 2 フィールドのみ（canonical 一致で裏付け） |
| `2901.T` bytes | 4406 | 4406 | 一致 |
| `29010` | 200 / 932 bytes / 全セクション空 | 404 / 554 bytes / 構造化 detail | 期待どおり変化 |

## 4. 試験結果（実測 rc）

```
$ .venv/bin/python -m pytest tests/test_research_unknown_symbol_failclosed.py
22 passed, 1 warning in 3.17s          rc=0

$ .venv/bin/python -m pytest tests/test_research_unknown_symbol_failclosed.py \
    tests/test_yahoo_deep_research.py tests/test_yahoo_deep_research_gaps.py \
    tests/test_api.py tests/test_ai_agent.py tests/test_openapi_contract.py
63 passed, 1 warning in 19.35s         rc=0

$ .venv/bin/python -m pytest tests/test_research_presets.py tests/test_research_brief.py \
    tests/test_research_ask_adversarial.py tests/test_research_ask_prompt_budget.py \
    tests/test_research_brief_prompt_budget.py tests/test_cli_shim.py
53 passed, 1 warning in 12.01s         rc=0

$ .venv/bin/ruff check <変更3ファイル>            All checks passed!
$ .venv/bin/ruff format --check <変更3ファイル>   3 files already formatted
$ .venv/bin/mypy src/yowayowa/providers/yahoo_deep_research.py src/yowayowa/api/research_routes.py
Success: no issues found in 2 source files
```

`make verify` 相当は **VPS では回していない**（カード指示: VPS は対象モジュール＋research 系のみ、全量は TSUKUMO）。
参考として VPS で `mypy src/yowayowa` を回した結果は `Found 31 errors in 5 files` だが、
内訳は `paperloop*.py`（別カードの未コミット作業）と `services/jp_value_quality*.py` で、
**本カードで触れたファイルは 0 件**。`ruff check src tests` の 33 件も同じく paperloop 系のみ。

## 5. 残存件数・残り

- 行き止まり: **10 件 → 9 件**（P0 3 → 2 / P1 6 / P2 1）。R2 は解消。
  行き止まりではない運用摩擦（R11）は据え置き。
- R1（5桁EDINETコード→ティッカー変換の新設）は**本カード対象外**で未着手。
  ただし R1 が指摘した「同じ誤コードに対する research 面の応答」は
  `200＋全セクション空` → `404＋理由` に変わり、`/v1/markets/quotes` の
  `unavailable_symbols` と同じ「正直に不在を返す」方向へ揃った。
- R6（brief の EDINET 選択順）・R3・R4・R10 などは別カード。
- 未実行: 全量 pytest / CI / 本番確認。`completion_contract=local-only` のため push・deploy はしていない。

## 6. 触っていないもの（指示どおり）

- `src/yowayowa/services/research_ask.py` / `research_brief.py`（他作業の未コミット差分あり）。
- `pyproject.toml`（他作業の RUF 除外追加）、`paperloop*`、`jp_value_quality*`、`scripts/`、`config/`。
- 実口座・本番注文・送金・外部送信・課金・公開 push は無し。yowayowa 内蔵LLMは呼んでいない。
- 一時ファイル（API ログ、before/after の応答 JSON、hash スクリプト）は scratch 配下。
