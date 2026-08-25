# EDINET financial research

Yowayowa-Investor uses the Financial Services Agency EDINET API v2 as the official source for Japanese disclosure research.

## Data paths

The normal financial-fact path is:

`EDINET documents API -> type=5 XBRL-to-CSV ZIP -> normalized EdinetFact -> canonical financial metrics`

The application deliberately consumes EDINET's own XBRL-to-CSV conversion for the regular path instead of reimplementing XBRL taxonomy resolution. The optional `xbrl`/Arelle dependency remains an extension boundary for future taxonomy-sensitive workflows that cannot be represented safely by the official conversion.

The type=5 ZIP is parsed in memory and only `XBRL_TO_CSV/*.csv` members are considered. Defensive limits are applied to member count, individual uncompressed size, and aggregate uncompressed size before fact parsing.

Each normalized fact retains the original element ID, item label, context ID, relative year, consolidated/separate marker, duration/instant marker, unit ID, unit text, value, and source-file name. Canonicalization never discards the underlying source facts.

The company-history path is deliberately separate:

`EDINET daily document lists -> local SQL filing metadata index -> company/date history search -> original EDINET document on demand`

The index stores filing metadata only. Financial facts are still loaded from the selected original EDINET document, so the history cache never becomes a second accounting source of truth.

## Filing-history index

EDINET's official document-list endpoint is date-oriented. Yowayowa therefore persists those official daily lists and exposes a company-oriented index without repeatedly scanning the remote API during research.

Two SQL tables make coverage auditable:

- `edinet_filings` stores normalized filing metadata keyed by EDINET document ID.
- `edinet_index_days` records every calendar day successfully synchronized, including days with zero filings.

This distinction is intentional. A day with no filings is not the same as a day that was never fetched. History responses include `indexed_days`, `expected_days`, `coverage_complete`, `index_start`, and `index_end`; partial coverage is never silently presented as complete history.

Re-synchronizing a day is idempotent and reconciles stale local rows against the current official list. Each day commits independently so a temporary failure on one date does not erase successful dates in the same backfill.

Remote synchronization is bounded to 31 calendar days per API request. Local company-history queries can cover up to 3,660 days because they do not multiply EDINET network calls. The CLI chunks longer operator backfills into safe 31-day synchronization requests automatically.

Synchronization failure responses retain the failed date and exception type only. They do not echo provider exception strings that could contain request details or credentials.

## Canonical metrics

`src/yowayowa/services/edinet.py` currently maps common Japan-GAAP/IFRS aliases into these research families:

- revenue / net sales
- gross profit
- operating income
- ordinary income
- net income
- assets / current assets
- liabilities / current liabilities
- equity / net assets
- cash
- operating / investing / financing cash flow
- basic / diluted EPS

When several facts map to the same family, current-period observations rank ahead of prior-period observations, consolidated observations rank ahead of separate-company observations, and low-dimensional contexts rank ahead of member/segment/axis/scenario contexts. All matching observations remain available in the typed response; the ranking only defines the preferred first observation.

Missing canonical families are returned explicitly in `unavailable_metrics`. They are not synthesized from unrelated facts.

## API

- `GET /v1/filings/edinet/documents`
  - direct filing-date search with optional security code, EDINET code, document type, CSV-only and downloadable-only filters.
  - four-digit Japanese security codes are normalized to EDINET's five-digit form by appending the trailing zero.
- `GET /v1/filings/edinet/index/history`
  - local indexed company-history search by security code or EDINET code, date range, optional document type and CSV availability.
  - returns explicit synchronization coverage together with matching filing metadata.
- `POST /v1/filings/edinet/index/sync`
  - authenticated index maintenance for an inclusive range of at most 31 calendar days.
- `GET /v1/filings/edinet/{doc_id}/financials`
  - typed metadata, canonical metric observations, missing metric families, source files, parse warnings and provenance.
- `GET /v1/filings/edinet/{doc_id}/facts`
  - bounded search over raw normalized facts for taxonomy/detail inspection.

Public mode anonymously exposes the read-only EDINET research routes, including indexed history. Index synchronization remains a write/maintenance operation behind the normal API authentication boundary. Live EDINET document/fact access still requires the server to have `YOWAYOWA_EDINET_API_KEY`; an already populated local history index remains searchable without a live provider request.

## CLI and browser

CLI parity:

```bash
yowayowa edinet documents 2026-06-20 --security-code 7203 --csv-only
yowayowa edinet index-sync 2025-08-18 2026-08-17
yowayowa edinet history 7203 --start 2025-08-18 --end 2026-08-17 --csv-only
yowayowa edinet financials S100XXXX
yowayowa edinet facts S100XXXX --query NetSales
```

A newly deployed database should receive an intentional historical backfill with `index-sync`; after that, `/internal/cron/daily` synchronizes the previous completed Japan-calendar day whenever an EDINET API key is configured. It deliberately does not mark the current intraday date as complete.

The `/edinet` browser surface is available in Japanese and English and follows the existing compact research-workstation design system. Company filing history is the primary flow: security code + range -> indexed filings -> normalized financials -> raw facts. A separate one-day direct lookup remains available for immediate EDINET queries. Accounting logic stays server-side.

## Provenance and licensing

EDINET is registered as an `official_public` source in the executable licensing firewall. Responses preserve source/provider/retrieval metadata and the provider notes the Public Data License 1.0 processing boundary. The application does not vendor EDINET taxonomy assets as part of the normal conversion path.

The official CSV conversion can truncate very long narrative instance values. Workflows that require full narrative disclosure text should use the original XBRL package rather than treating the converted CSV as lossless narrative storage.

## Validation

Unit fixtures build the same UTF-16LE tab-separated CSV shape inside an in-memory ZIP and cover filtering, security-code normalization, canonical ranking, numeric normalization, caching, raw-fact search, invalid document IDs and malformed archives.

Index tests use in-memory SQL and cover idempotent multi-day synchronization, company-code normalization, date ordering, complete/partial coverage, safe range bounds and failure isolation. Playwright covers company-history search through canonical financials and raw fact search. The shared 390px regression also includes `/edinet` in mobile navigation reachability and page-level horizontal-overflow checks.
