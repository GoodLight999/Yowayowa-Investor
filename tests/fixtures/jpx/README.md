# tests/fixtures/jpx — official JPX sample files (dummy data)

Source: official sample ZIP published by JPX (日本取引所グループ / JPX総研):

    https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip

Retrieved: 2026-09-24 (format verification for the daily issue-level margin
balance ingestion; see docs/JPX_DAILY_MARGIN.md).

Files (the ZIP's `Web&DFS/` directory was flattened to this fixture
directory; Web and DFS distributions are documented as identical files):

- `OutstandingMarginTradingByIssue.csv` — English CSV, UTF-8 without BOM.
- `JP_OutstandingMarginTradingByIssue.csv` — Japanese CSV, CP932.

IMPORTANT: the data in these files is **dummy data** published by JPX for
format verification only. The Readme in the original ZIP states it does not
necessarily coincide with actual information. Live publication starts
2026-09-28; these fixtures must never be used as market data.
