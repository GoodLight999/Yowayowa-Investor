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
