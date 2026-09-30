# US stock daily OHLCV via Alpaca (P4-F)

Alpaca Market Data API から米株の日次確定足（1Day bars）を取得し、provenance付きで
JSONLに永続化する personal-only acquisition surface。

## コマンド

```bash
uv run yowayowa stock-fetch                 # P1バックテスト基準14銘柄を取得・永続化
uv run yowayowa stock-fetch AAPL --days 30  # 銘柄・日数を指定（5-3650日）
uv run yowayowa stock-fetch --days 3650      # 最大約10年分（Alpaca subscription/license limits apply）
uv run yowayowa stock-ohlcv AAPL --limit 5  # 永続化済み行を表示（source別・未統合）
uv run yowayowa stock-ohlcv AAPL --json     # JSON出力
```

- Store: `data/stock-ohlcv/{SYMBOL}/ohlcv.jsonl`（crypto store と平行構造・混ぜない。
  1行=1レコード、全量provenance）
- 再実行は (provider, currency, as_of) 単位で冪等。APIの欠落日は zero/fwd-fill しない。
- 各行に `vwap`（Alpaca vw）と `trade_count`（n）を保持。crypto行には無いフィールド。
- `research_ask` と朝ブリーフのEvidenceに自動取り込み済み
  (`services/ohlcv_evidence.py` 経由・読み取り専用。AIツール `get_ohlcv` でも参照可)。

## シンボル

米株は `^[A-Z]{1,5}(\.[A-Z])?$` の pattern のみ（検証済みsetは持たない）。
BRK.B 等のクラス株は live probe（2026-09-24）で Alpaca SIP がそのまま応答することを確認済み。
未知だが形式が正しいtickerは upstream で 404/422 → LookupError になる（fail-closed）。

## API

- `GET /v1/stocks/{symbol}/bars?provider=alpaca&format=json|csv&limit=30`
- `GET /v1/stocks/{symbol}/bars/latest`（store最新行1件）
- 両ルートとも **store読み出しのみ・network fetch禁止**。空なら404。
- personalモード専用。public表示・public API はFalseのため404 fail-closed。
- `/latest` は `/{symbol}/bars` より先に宣言（P4-Aの/lates型path先食き事故の教訓）。

## 設計判断（CTO固定、2026-09-24）

1. **LicenseClass.PERSONAL_ONLY（保守的判定）**: Alpacaは商用ブローカー/データベンダーであり、
   ECB参照レートのような公式参照レートではない。SIP履歴は Alpaca の規約上の個人利用の範囲内で
   使用（free planは15分遅延のSIP）。`SOURCE_POLICIES` に `access=REGISTERED_KEY` で登録済み。
2. **Provider 1本**: `providers/alpaca.py` (`/v2/stocks/bars`, timeframe=1Day, feed=sip)。
   trading API (`paper-api.alpaca.markets`) とは接続しない。binance.py と同じ構造
   （TTLCache + 共有httpx.Client + ProviderDescriptor + enforce_provider_policy）。
   レート制限（free枠200 req/min）は明示的に書かず、連続呼出に0.35s pacingを内蔵。
3. **request window は end=一昨日(UTC) まで**: free plan は「直近SIPデータ」への照会を禁止しており、
   window に当日を含めると HTTP 403 "subscription does not permit querying recent SIP data"
   になる（live probe 2026-09-24 実測）。end=一昨日とすることで直近SIP制約から余裕を取り、
   返る足がすべて確定セッションであることを保証する。取得器の当日実行では2026-09-30の2日前
   （2026-09-28）が最終取得日となり、UTC昨日（2026-09-29）の足は未取得のままになる。
   CTOの最初のprobeが200だったのは
   end指定なし（=Alpaca側で自動的に履歴のみ返る）のため。
4. **adjustment=raw を明示**: OHLCVは取得時点でのprovider返却値を未調整価格として保存する。
   分割調整値・配当調整値とraw値を混ぜない。株式リターンにはpoint-in-time corporate actionが
   別途必要であり、現時点の調整後値を過去時点に遡及適用しない。
5. **next_page_token pagination 対応**: 10000本/ページで続きトークンを追従。
6. **cryptoへの3rd source追加は Beyond scope**（P4-F後続タスクに回す・CTO判断）。

## Alpaca FX について

**実装しない。** 本operatorキーは FX rates (`v1beta1/forex/rates`) の権限が不足しており
HTTP 403 insufficient grants（実測 2026-09-24）。公式参照レートは既存の
frankfurter（ECB、OFFICIAL_PUBLIC）経路を使う。将来キーの権限拡張があれば
provider追記で対応する。

## cron（運用）

JST 08:30（=US東部 16:00 直後。SIP 15分制約の余裕を見て前日分確定値を取得）:

```
hermes cron add "30 8 * * *" --name yowayowa-alpaca-daily \
  --script yowayowa_alpaca_daily.sh --no-agent
```

script: `/root/.hermes/scripts/yowayowa_alpaca_daily.sh`（鍵は `/root/.hermes/.env` から
grep で読み、`YOWAYOWA_ALPACA_*` 環境変数として渡す。echoしない）。
`AAPL MSFT NVDA --days 5` を冪等追記し、stdoutに「+N rows」サマリを出す。既定ユニバースはP1バックテスト基準の
AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JNJ, JPM, PG, XOM, SPY, QQQ, IWM。

## Historical coverage and survivorship

P1のUS株対象は上記の**現在指定された14銘柄**だけであり、過去時点のS&P 500構成銘柄ではない。
従って当該データを銘柄横断の過去成績比較に使う場合、現存銘柄に偏るsurvivorship biasがある。
上場廃止・合併・過去の指数除外銘柄はこの取得リストに含まれず、過去の指数構成を復元したものではない。
Alpacaの返却範囲は契約/plan、上場日、銘柄ごとの利用可能履歴に依存する。要求開始日と返却最古日を
個別に照合し、coverage不足は不足として扱う（前方補完・ゼロ埋めは禁止）。

日付範囲はUTCの完了済み日足に合わせる。営業日欠損の判定はNYSE営業日カレンダーを用い、土日・公式休場日を
欠損として数えない。臨時休場日やprovider側の欠配は別途原データと照合する。
基準休場日は[NYSE Holidays & Trading Hours calendar](https://www.nyse.com/markets/hours-calendars)を参照。
2025-01-09のJimmy Carter National Day of Mourning closureも除外済み。
