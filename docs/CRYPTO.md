# Crypto daily OHLCV (P4-E phase 1)

BTC/ETH の日次OHLCVをキー不要のpublic API（CoinGecko / Binance）から取得し、
provenance付きでJSONLに永続化するacquisition surface。

## コマンド

```bash
uv run yowayowa crypto-fetch            # BTC,ETH × coingecko,binance を取得・永続化
uv run yowayowa crypto-ohlcv BTC        # 永続化済み行を表示（source別・未統合）
uv run yowayowa crypto-fetch SOL        # → エラー（SUPPORTED_CRYPTO_ASSETS は BTC/ETH のみ）
```

- Store: `data/crypto-ohlcv/{SYMBOL}/ohlcv.jsonl`（1行=1レコード、全量provenance）
- 再実行は (provider, currency, as_of) 単位で冪等。APIの欠落日は zero/fwd-fill しない。
- `research_ask` と朝ブリーフのEvidenceに自動取り込み済み
  (`services/ohlcv_evidence.py` 経由・読み取り専用。AIツール `get_ohlcv` でも参照可)。

## API

- `GET /v1/crypto/ohlcv/{symbol}?provider=binance&format=csv&limit=30`
- `GET /v1/crypto/ohlcv?provider=...`（symbol横断）
- personalモード専用。public 表示・public API は両者ともFalseのため404 fail-closed

## 設計判断（CTO固定、2026-09-24）

1. **LicenseClass.PERSONAL_ONLY（保守的判定）**: CoinGecko/Binanceは商用アグリゲータ
   /取引所であり、ECB参照レートのような公式参照レートではないため
   `frankfurter`（OFFICIAL_PUBLIC）として扱わない。既存cryptoがYahoo経由で
   PERSONAL_ONLY運用なのと整合。`SOURCE_POLICIES` に両者を登録済み。
2. **providerファイル2本**: `providers/coingecko.py`
   (`/api/v3/coins/{id}/ohlc`, USD) と `providers/binance.py`
   (`/api/v3/klines`, USDT)。frankfurter と同じ構造（TTLCache + 共有
   httpx.Client + ProviderDescriptor + enforce_provider_policy）。
3. **2 sourceは混ぜない**: 同一日の CoinGecko(USD) と Binance(USDT) の値が
   違っても、CSV/JSONLにはprovider別で両方残す（自動平均なし）。
4. **BTC/ETH のみ**: `SUPPORTED_CRYPTO_ASSETS` を拡張しない（SOL/XRP等はscope外）。
5. **毎日cron登録（運用）**: 取り込みは日次で実行する。UTC 00:30に
   `yowayowa crypto-fetch` を実行する登録を想定（Binanceのdaily ケインは
   UTC 00:00に確定するため、00:30実行で前日分が確定値として取れる）:
   `0  0  * * *` 相当のローカルcronに
   `cd <repo> && YOWAYOWA_*設定環境付き ./venv/bin/yowayowa crypto-fetch` を登録。
