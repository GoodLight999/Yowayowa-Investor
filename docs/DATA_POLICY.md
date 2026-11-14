# Data provenance and licensing policy

A provider must declare its source and redistribution class before it can serve data.

## Public-mode rule

`LicenseClass.PERSONAL_ONLY` is denied in `YOWAYOWA_MODE=public`. Adding a new public market-data provider requires recording its redistribution/commercial-use basis and mapping it to `LICENSED_REDISTRIBUTABLE` or another appropriate class.

## SEC EDGAR

The application uses `data.sec.gov` Company Facts and SEC ticker metadata, identifies itself using a configured User-Agent, and defaults below the SEC's published 10 requests/second fair-access ceiling.

## EDINET

EDINET API Version 2 is key-gated. The application accepts the user's key and retains source metadata. XBRL parsing is intentionally separated from document acquisition so Arelle can be used without rewriting the provider layer.

## FRED

FRED access is BYOK. A series may inherit restrictions or attribution requirements from the underlying source, so the application retains the series/source reference instead of treating every FRED observation as freely redistributable.

## Yahoo / yfinance

The adapter is useful for the personal-first product but is marked personal-only. It is not the commercial redistribution strategy.

## Synthetic data

Fabricated data never carries a real venue's provider name. `LicenseClass.SYNTHETIC` (`synthetic`) marks locally generated boards and fixtures; the orderbook seed generator uses `provider="synthetic-seed-orderbook"` and states in its source string that it is not an exchange feed.

`OrderbookService.get_snapshot` returns a synthetic board only when no snapshot has been captured (that board is persisted as the baseline) or when the persisted file exists but cannot be read. In the unreadable case the damaged file is left byte-for-byte untouched, the failure is appended to `data/orderbooks/corrupt_reads.jsonl` with the file's size and sha256, and the synthetic board is returned without being persisted over the original.

## Daily strategy signals

Daily screening rejects duplicate or gapped sessions, stale members, invalid
closes/derived returns, missing provenance, and mixed-provider histories per
symbol. Alerts retain source metadata for every lookback observation. See
[`STRATEGY_SIGNAL_QUALITY.md`](STRATEGY_SIGNAL_QUALITY.md) for the observed-session
calendar, relative-freshness limitations, rejection reasons, and regression gates.
