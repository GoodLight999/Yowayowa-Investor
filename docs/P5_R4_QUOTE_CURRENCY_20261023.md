# P5 R4 — quotes/valuation の通貨欠落を直す（応答契約と重複ルートの実測）

- カード: t_2355c8b3（承認識可済みキューから自動供給。親: t_65bed735）
- 出典: `docs/P5_DEADEND_AUDIT_20261023.md` §3 R4（P1）
- 対象リビジョン: `agent/commercial-foundation` @ `08e41cf`（＋本カード作業ツリー）
- 実測日時: 2026-10-23 12:59 JST（修正前）／13:2x–13:3x JST（修正後）
- 実測環境: ローカル API `uvicorn yowayowa.api.app:app` を `127.0.0.1:8123`、実データ `data/yowayowa.db`、
  実 provider（Yahoo Finance / yfinance 1.7.0）。推定値・合成値は本ドキュメントに混ぜていない
  （試験だけがスタブを使う。スタブであることは §6 に明記）。

## 1. 結論

R4 の症状は**2つの独立した原因**で、どちらも「provider は値を持っているのに経路で捨てていた」ものだった。

1. **quotes の `currency` は、そもそも一度も設定されていなかった。**
   `YahooMarketProvider._quotes_from_frame` は `symbol/price/previous_close/as_of` しか組んでおらず、
   `MarketQuote.currency` は既定値 `None` のまま返っていた。provider 側（`fast_info`）は
   `7203.T=JPY` / `AAPL=USD` を実際に返す（実測 0.07–0.17 秒/銘柄）。
2. **`company_name` のシンボル退化と valuation 側の通貨欠落は、同じ1行が原因。**
   `YahooFundamentalsProvider._safe_history_metadata` が `isinstance(value, dict)` で判定していたが、
   yfinance 1.7.0 の `get_history_metadata()` は `dict` ではなく
   `yfinance.scrapers.history.HistoryMetadata`（`collections.abc.Mapping`）を返す。判定が False になり
   正常な metadata（`longName`・`currency`）が毎回捨てられ、`company_name` は
   `metadata.get("symbol")` に、決算の通貨は接尾辞ヒント（`.T`→JPY）に落ちていた。

修正後、実 API で 4 銘柄すべての通貨が応答から確定し、`company_name` は実社名になった。
`/v1/valuation/{symbol}` の `currency` は quote の値をそのまま写す（価格通貨の出所は1箇所）。

## 2. 実測（修正前）

```
$ curl -s '127.0.0.1:8123/v1/markets/quotes?symbols=2901.T,6758.T,7203.T,AAPL'
2901.T price=76.0     currency=null
6758.T price=4135.0   currency=null
7203.T price=3103.0   currency=null
AAPL   price=348.22   currency=null
```

```
$ curl -s '127.0.0.1:8123/v1/valuation/7203.T'
{
  "symbol": "7203.T", "company_name": "7203.T", "price": 3103.0,
  "shares_diluted": 13033274000.0, "market_cap": 40442249222000.0,
  "annual_period_end": "2026-03-31",
  "metrics": { ...15指標すべて非null... "price_to_earnings": 10.509672368531154 ... }
}
```
→ `currency` キー自体が存在せず、`company_name` はシンボル文字列。

provider の実値（同じ時刻・同じプロセス外で直接実測）:

```
yf.Ticker("7203.T").fast_info["currency"]                  -> "JPY"   (0.07s)
yf.Ticker("AAPL").fast_info["currency"]                    -> "USD"   (0.07s)
yf.Ticker("7203.T").get_history_metadata()["longName"]     -> "Toyota Motor Corporation"
yf.Ticker("7203.T").get_history_metadata()["currency"]     -> "JPY"
type(...get_history_metadata())                            -> <class 'yfinance.scrapers.history.HistoryMetadata'>
isinstance(..., dict)                                      -> False
isinstance(..., collections.abc.Mapping)                   -> True
```
すなわち「取れない」のではなく「取った値を捨てていた」。

## 3. 修正

### 3.1 provider: quote に通貨を載せる（`src/yowayowa/providers/yahoo.py`）

- `_quote_currency(provider_symbol)` を追加。`yf.Ticker(ps).fast_info["currency"]` の値だけを使い、
  接尾辞・取引所名からの推測は**しない**。
- 解決済みの通貨は TTL キャッシュ（`max(ttl, 12h)`、銘柄キー）に保持。**失敗はキャッシュしない**
  （一過性の障害を固定化しない）。
- `_with_price_currency(batch)` で、価格が取れた銘柄にだけ通貨を付ける（価格が無い銘柄に
  通貨照会はしない）。通貨照会の失敗は quote 自体を壊さず `currency_reason` に落とす。
- 1銘柄あたり実測 0.07–0.17 秒、4銘柄で合計 0.38 秒。セッション内は1銘柄1回。
- `_quotes_from_frame` は**静的メソッドのまま**（価格の抽出だけを担う純関数）にし、
  通貨は後段で付与する。既存の `_quotes_from_frame` 直接呼び出し試験（frame→価格の検証）は無変更で通る。

### 3.2 provider: metadata の型判定（`src/yowayowa/providers/yahoo_fundamentals.py`）

```diff
-        return value if isinstance(value, dict) else {}
+        return dict(value) if isinstance(value, Mapping) else {}
```
これで `company_name` が `longName` から入り、決算の通貨も metadata 由来になる
（metadata が本当に取れない場合のみ従来どおりシンボル名・接尾辞ヒントへフォールバック。
その挙動は `tests/test_yahoo_fundamentals.py::test_yahoo_metadata_failure_does_not_discard_japanese_statements`
が従来どおり固定している）。

### 3.3 応答モデル（`src/yowayowa/domain.py`）

`MarketQuote` と `ValuationSnapshot` に同じ3つ組を追加（既存キーは不変更・全て既定 `None`）:

| フィールド | 内容 |
|---|---|
| `currency` | provider が返した ISO-4217 コード。取れなければ `null` |
| `currency_source` | 解決元の識別子。解決時は `"yahoo:fast_info"`、未解決時は `null` |
| `currency_reason` | `currency` が `null` のときの機械可読な理由（下記）。解決時は `null` |

`currency_reason` の語彙は `src/yowayowa/quote_currency.py` に1箇所だけ置く:

| コード | 意味 |
|---|---|
| `provider_returned_no_currency` | provider の応答に通貨フィールドが無い/空。MS2 RSS の quote は常にこれ |
| `provider_returned_invalid_currency` | 値はあるが ISO 形式（英字3桁）でない |
| `provider_lookup_failed` | 通貨照会そのものが例外（レート制限・ネットワーク等） |

**規則**: 通貨は provider の実値のみ。`currency=null` のときは必ず理由コードが入る（無言の `null` を返さない）。
3文字英字コードの ISO  membership までは検証しない（provider の申告をそのまま通す。下記 §7 の残件）。

### 3.4 valuation は quote を写すだけ（`src/yowayowa/services/valuation.py`）

`ValuationSnapshot.currency/currency_source/currency_reason` は `quote.currency/...` を**無変換でコピー**する。
valuation 側で再導出・推測はしないため、`/v1/markets/quotes` と `/v1/valuation/{symbol}` が
同じ銘柄で違う通貨を出すことは構造的に起こらない。加えて provenance の note に、
使った通貨と出所、通貨不明時の「currency-unverified」を明記する（画面ではなく API メタデータ）。

### 3.5 MS2 RSS provider（`src/yowayowa/providers/ms2_rss_file.py`）

RSS エクスポートに通貨フィールドが無いため、`currency_reason="provider_returned_no_currency"` を明示。
新しい provider は足していない（既存の値の表現だけを契約に合わせた）。

## 4. 実測（修正後）

```
$ curl -s '127.0.0.1:8123/v1/markets/quotes?symbols=2901.T,6758.T,7203.T,AAPL'
2901.T price=77.0     currency=JPY  source=yahoo:fast_info  reason=null
6758.T price=4111.0   currency=JPY  source=yahoo:fast_info  reason=null
7203.T price=3099.0   currency=JPY  source=yahoo:fast_info  reason=null
AAPL   price=348.2200012207031  currency=USD  source=yahoo:fast_info  reason=null
```

```
$ curl -s '127.0.0.1:8123/v1/valuation/7203.T'
company_name=Toyota Motor Corporation  currency=JPY  source=yahoo:fast_info  reason=null  price=3100.0
metrics_nonnull=15/15  per=10.49951155090125          ← 修正前 15/15 per=10.509672368531154（価格差のみ）
$ curl -s '127.0.0.1:8123/v1/valuation/6758.T'
company_name=Sony Group Corporation    currency=JPY  source=yahoo:fast_info  reason=null  price=4111.0
metrics_nonnull=13/15  per=null                        ← annual_net_income=-326,865,000,000（負値。既存の仕様）
$ curl -s '127.0.0.1:8123/v1/valuation/2901.T'
company_name=Wel-Dish.Incorporated     currency=JPY  source=yahoo:fast_info  reason=null  price=77.0
metrics_nonnull=13/15  per=2.552592800177945
$ curl -s '127.0.0.1:8123/v1/valuation/AAPL'
company_name=Apple Inc.                currency=USD  source=yahoo:fast_info  reason=null  price=348.2200012207031
metrics_nonnull=13/15  per=46.64704586783573
```

未知名（R1 と同じ誤コード）:
```
$ curl -s -o body -w '%{http_code} %{size_download}\n' '127.0.0.1:8123/v1/valuation/29010'
404 51
$ cat body
{"detail":"SEC ticker mapping not found for 29010"}
```

指標の非退行: `price_to_earnings` が `null` になるのは「分母が負」の既存仕様（6758.T）であり、
通貨ゲートによる消失ではない。通貨ゲート（`_currency_compatible`）は決算側の `MetricPoint.currency` が
非 `null` のときだけ作用するが、**SEC / EDINET / Yahoo fundamentals のどの provider も
`MetricPoint.currency` を設定していない**（`grep -n currency src/yowayowa/providers/{sec,edinet}.py` → 0件）。
したがって本カードは既存指標の値を1つも落としていない。§7 にこの未設定自体を残件として記録した。

## 5. `/v1/valuation/{symbol}` の二重定義（どちらが生きているか）

`app.py` は `fundamentals_router`（`fundamentals_routes.py:102`）を `router`（`routes.py:156`）より
**先に** `include_router` している。FastAPI は同一パス・同一メソッドの先着ハンドラで解決するため、
実際に応答するのは `fundamentals_routes.valuation_anywhere` 側。

実測での確定（`tests/test_quote_currency_contract.py::test_valuation_is_served_by_the_fundamentals_handler`）:
`routes._sec` を「呼ばれたら失敗する」関数に差し替え、`fundamentals_routes` 側の provider を
スタブにした状態で `GET /v1/valuation/7203.T` が **200** を返す＝`routes.py` 側は一度も呼ばれていない。
`routes.py` 側は SEC 専用経路なので、非US銘柄（7203.T）で 200 を返すこと自体が決定的な反証になる。

**本カードは `routes.py` を編集していない**（R3 と衝突させないため。カード指示どおり）。
死んでいる側の扱いは次の提案として残す（実施は別カード/R3 完了後）:

- 提案A（削除）: `routes.py:156` の `valuation()` と `fundamentals()`（`routes.py:148`）を削除し、
  `routes.py` から `/v1/valuation/{symbol}`・`/v1/fundamentals/{symbol}` を1本化する。
- 提案B（意味の分離）: SEC 専用の意味を残したい場合だけ `routes.py` 側のパスを
  `/v1/sec/valuation/{symbol}` に改名して公開し、`/v1/valuation/{symbol}` は fundamentals 系に一本化。
- どちらも `routes.py` を触るため R3（portfolio delete）完了後に着手する。現状は「先着で fundamentals が勝つ」
  ことを試験で固定してあるので、どちらの整理をしても退行は検出できる。

## 6. 試験

新規: `tests/test_quote_currency_contract.py`（17件、ネットワーク不使用。`yf.download`/`yf.Ticker` をスタブ）

- JPY 銘柄→`JPY`、USD 銘柄→`USD`、`currency_source` が入る
- provider が通貨を返さない→`null` + `provider_returned_no_currency`
- 照会が例外→`null` + `provider_lookup_failed`（価格は返る）
- ISO 形式でない値→`null` + `provider_returned_invalid_currency`
- 解決済み通貨はキャッシュ（1銘柄1回）、失敗はキャッシュしない
- 価格が取れない銘柄は通貨照会をしない（`Ticker` 呼び出し 0回）
- provenance note に通貨規則が出る
- MS2 RSS quote は `provider_returned_no_currency`
- valuation が quote の通貨3つ組をそのまま写す／不明時は理由コード＋`currency-unverified` の注記
- quote 通貨と決算通貨が既知で食い違うときは P/E・earnings yield を出さない（Y04 の fail-closed）
- **生きているルートの確定**（§5 の実測）／`/v1/valuation/{symbol}` と `/v1/markets/quotes` の
  応答に通貨3つ組が出る（既存キーが残ることも同時に検証）
- yfinance 1.7 の `HistoryMetadata`（`dict` でない Mapping）から `longName`・`currency` が取れる（原因の回帰試験）

既存試験の更新: `tests/test_yahoo_class_shares.py` の quote 試験に `Ticker` スタブを足した
（通貨照会が実ネットワークへ出ないようにするため。合わせて `currency == "USD"` を検証）。

実行結果（この作業ツリー、`PYTHONPATH=src .venv/bin/python -m pytest`）:

- 上記21モジュール（quote/valuation/provider/CLI/openapi/alerts/cron/portfolio 系を含む）:
  **267件 rc=0**（junit: tests=267 failures=0 errors=0 skipped=0）
- `ruff check src tests` … 本カードの変更ファイルは 0 件エラー（全体の48件は他カードの未コミット変更に由来）
- `ruff format --check` … 本カードの変更ファイルは適合
- `mypy src/yowayowa` … 本カードの変更ファイルにエラーなし。全体の31件は
  `services/jp_value_quality_backtest.py`・`paperloop_verify.py` など**本カードが触っていないファイル**の
  既存エラーで、コミット `10ee9ce` を単独チェックアウトした状態でも同じ31件が出る（＝本カード由来ではない）
- **コミット単体の検証**: `git worktree add /tmp/r4check 10ee9ce` した隔離ツリーで
  対象10モジュール **113件 rc=0**（junit: tests=113 failures=0 errors=0）・ruff クリア。
  すなわち本カードのコミットは他カードの未コミット変更に依存していない

## 7. 残件・提案（このカードでは実施しない）

1. **決算側の通貨が provider 未設定**（最重要）: `MetricPoint.currency` を設定する provider が無いため、
   Y04 の通貨ゲートは現状つねに「不明＝互換」に倒れる。Yahoo の `financialCurrency`
   （`Ticker.info` の quoteSummary 項目）を fundamentals provider で取得して設定するのが正攻法。
   **listing の価格通貨を決算通貨として流用してはならない**（例: 英国上場・USD 建て決算の銘柄で
   ゲートが誤って「一致」し、誤った倍数を出す）。本カードで一度実装→この理由で取り消した。
2. **CLI 表示**: `yowayowa quotes` / `yowayawa valuation` の表に通貨列が無い。API は確定したが
   端末からは見えない。`src/yowayowa/cli.py` は R3 が編集中のため本カードでは触っていない。
3. **ISO membership 検証**: `provider_returned_invalid_currency` は「英字3桁でない」ことだけを見る
   （`YEN` のような3文字非ISOは通る）。通貨コード表（`fx_models` に curated set あり）を流用した
   検証は、通貨不明を増やす副作用とセットで判断が必要。
4. **`financialCurrency` と価格通貨の差**: 上記1を入れると、`price ≠ financialCurrency` の銘柄で
   倍数が `null` になる。どの銘柄が該当するかを実測してから有効化する。
5. 全量 `make verify`・GitHub CI・実ブラウザE2E・本番確認は未実施（カード指示どおり TSUKUMO/CI 側の残件）。

## 8. 変更ファイル

```
 src/yowayowa/quote_currency.py                 (新規) 通貨3つ組の語彙
 src/yowayowa/domain.py                         MarketQuote / ValuationSnapshot に3フィールド追加
 src/yowayowa/providers/yahoo.py                fast_info から通貨解決＋キャッシュ＋理由コード
 src/yowayowa/providers/yahoo_fundamentals.py   Mapping 判定（company_name/通貨退化の原因）
 src/yowayowa/providers/ms2_rss_file.py         通貨なしを理由コードで明示
 src/yowayowa/services/valuation.py             quote の通貨を無変換で写す＋provenance note
 tests/test_quote_currency_contract.py          (新規) 17件
 tests/test_yahoo_class_shares.py               quote 試験に Ticker スタブ＋通貨検証
 docs/P5_R4_QUOTE_CURRENCY_20261023.md          (新規) 本ドキュメント
 docs/INDEX_ja.md                               本ドキュメントの索引（R4 の1ブロックのみ）
 docs/PRIVATE_OPERATOR_ROADMAP.md               P5 節に「R4 解消」を1箇所追記
```

`src/yowayowa/api/routes.py` は**未変更**（§5 の提案のみ）。
`src/yowayowa/domain.py` と `docs/INDEX_ja.md` は R3（portfolio delete）の未コミット変更と同居しているため、
コミットには**本カードのハンクだけ**を `git hash-object` / `git update-index` で積んだ
（R3 の `PortfolioDeletion` と索引エントリは未コミットのまま作業ツリーに残している）。
