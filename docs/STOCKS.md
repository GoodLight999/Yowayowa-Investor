# US stock daily OHLCV via Alpaca (P4-F)

Alpaca Market Data API から米株の日次確定足（1Day bars）を取得し、provenance付きで
JSONLに永続化する personal-only acquisition surface。

## コマンド

```bash
uv run yowayowa stock-fetch                 # AAPL,MSFT,NVDA × alpaca を取得・永続化
uv run yowayowa stock-fetch AAPL --days 30  # 銘柄・日数を指定（5-3650日）
uv run yowayowa stock-ohlcv AAPL --limit 5  # 永続化済み行を表示（source別・未統合）
uv run yowayowa stock-ohlcv AAPL --json     # JSON出力
```

- Store: `data/stock-ohlcv/{SYMBOL}/ohlcv.jsonl`（crypto store と平行構造・混ぜない。
  1行=1レコード、全量provenance）
- 再実行は (provider, currency, as_of) 単位で冪等。APIの欠落日は zero/fwd-fill しない。
- 各行に `vwap`（Alpaca vw）と `trade_count`（n）を保持。crypto行には無いフィールド。

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
3. **request window は end=昨日(UTC) まで**: free plan は「直近SIPデータ」への照会を禁止しており、
   window に当日を含めると HTTP 403 "subscription does not permit querying recent SIP data"
   になる（live probe 2026-09-24 実測）。end=昨日とすることで 403 を回避しつつ、
   返る足がすべて確定セッションであることを保証する。CTOの最初のprobeが200だったのは
   end指定なし（=Alpaca側で自動的に履歴のみ返る）のため。
4. **next_page_token pagination 対応**: 10000本/ページで続きトークンを追従。
5. **cryptoへの3rd source追加は Beyond scope**（P4-F後続タスクに回す・CTO判断）。

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
`AAPL MSFT NVDA --days 5` を冪等追記し、stdoutに「+N rows」サマリを出す。
