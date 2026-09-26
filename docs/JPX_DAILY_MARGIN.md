# JPX daily margin balances (銘柄別信用取引残高・日次)

Status: **format confirmed, live data pending** (publication starts 2026-09-28).

This document records the observed publication facts and the ingestion contract
for the JPX daily issue-level margin balance service. It satisfies the P4-A
first report requirement (roadmap: "JPX daily margin balances") while live data
does not exist yet.

## Publication facts (verified 2026-09-24)

- Announced by JPX総研 on 2026-07-29:
  https://www.jpx.co.jp/corporate/news/news-releases/6020/20260729-01.html
- Coverage: all TSE-listed issues; balances as of the previous business day,
  split sell/buy and general (一般) vs standardized/system (制度) margin.
- Daily publication cadence: around 16:00 JST each business day, for the
  previous trading day's application date (申込日).
- Start date: 2026-09-28 (planned). Application-date data from 2026-09-25
  onward. Amount (value) columns exist only for application dates from
  2026-09-25 onward.
- Access is a **paid JPX総研 contract** (TMI reference service or J-Quants Pro).
  Distribution channels: website CSV download, FTP/SFTP data feed, Snowflake
  sharing, and the J-Quants Pro REST API.

## Official URLs

- TMI reference page (service + fees + specs):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/05.html
- Web usage manual (PDF):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/co3pgt0000004g0w-att/webhowto.pdf
- File specification (PDF, covers Web & DFS files):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/JPXReferenceWeb_2709.pdf
- Sample file (dummy data, official format):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip
- J-Quants Pro API reference (銘柄別信用取引残高・日次):
  https://jpx.gitbook.io/j-quants-pro-ja/api-reference/margin_interest
- Weekly predecessor (existing service, same issue-level concept):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/09.html

Do not confuse with the pre-existing 日々公表信用取引残高
(`/markets/daily_margin_interest` on J-Quants, 個別銘柄信用取引残高表 on the
JPX website), which only covers 日々公表銘柄 designated by TSE/日本証券金融.
The new service covers **all** margin-tradable TSE issues daily.

## Observed CSV format (from official sample ZIP, 2026-09-24)

ZIP layout: `Web&DFS/OutstandingMarginTradingByIssue.csv` (English),
`Web&DFS/JP_OutstandingMarginTradingByIssue.csv` (Japanese), `Readme.txt`.
Web and DFS (FTP/SFTP) distributions use the identical file.

- Encoding: English CSV is UTF-8 without BOM; Japanese CSV is CP932
  (Shift-JIS family, verified byte-level). Line endings CRLF.
- Header row present, then one row per issue, fully quoted.
- 18 columns, Japanese names in parentheses:

| # | EN header | JP header | Notes |
|---|-----------|-----------|-------|
| 1 | Record Date | 申込日 | YYYYMMDD (application date) |
| 2 | Local Code | 銘柄コード | 5-char JPX code, e.g. `13010`, `135A0` |
| 3 | Company Name (English) | 銘柄名 | |
| 4 | ISIN | ISIN | 12-char |
| 5 | Market Segment Code | 市場コード | e.g. `0111` Prime, `0109` ETF, `0113` Growth |
| 6 | Margin Code | 銘柄種別コード | `1` 信用, `2` 貸借, `3` その他 |
| 7 | Short Margin Outstanding (volume) | 売合計信用残高（株数） | |
| 8 | Long Margin Outstanding (volume) | 買合計信用残高（株数） | |
| 9 | Short Negotiable Margin Outstanding (volume) | 売一般信用残高（株数） | |
| 10 | Short Standardized Margin Outstanding (volume) | 売制度信用残高（株数） | |
| 11 | Long Negotiable Margin Outstanding (volume) | 買一般信用残高（株数） | |
| 12 | Long Standardized Margin Outstanding (volume) | 買制度信用残高（株数） | |
| 13 | Short Margin Outstanding (value) | 売合計信用残高（金額） | JPY; only ≥ 2026-09-25 |
| 14 | Long Margin Outstanding (value) | 買合計信用残高（金額） | JPY; only ≥ 2026-09-25 |
| 15 | Short Negotiable Margin Outstanding (value) | 売一般信用残高（金額） | JPY; only ≥ 2026-09-25 |
| 16 | Short Standardized Margin Outstanding (value) | 売制度信用残高（金額） | JPY; only ≥ 2026-09-25 |
| 17 | Long Negotiable Margin Outstanding (value) | 買一般信用残高（金額） | JPY; only ≥ 2026-09-25 |
| 18 | Long Standardized Margin Outstanding (value) | 買制度信用残高（金額） | JPY; only ≥ 2026-09-25 |

Sample row (dummy data): `20260423,13010,KYOKUYO CO.,LTD.,JP3257200000,0111,2,
3000,7000,1000,2000,3000,4000,300000,700000,100000,200000,300000,400000`.

Amount fields equal volume × (dummy) price in the sample; the value columns are
the JPY notional of the outstanding balance.

## J-Quants Pro API shape (same business data)

`GET https://api.jquants-pro.com/v2/markets/margin_interest` with `code` /
`date` / `from`+`to` / `pagination_key`. Field names map 1:1 to the CSV columns
(`Date`=申込日, `ShortMarginOutstanding`, `ShortMarginOutstandingValue`, ...).
The API additionally returns `PublishedDate` (公表日, delivery date) and
company/sector metadata. The website/API distinction is transport only; the
ingestion contract below applies to both.

## License and mode policy

- Contracted JPX reference data is **personal-only** for this operator
  (`LicenseClass.PERSONAL_ONLY`). Per `LICENSE_POLICY.md`, individual J-Quants
  access is not public redistribution permission.
- Public mode must fail closed: the provider descriptor is not redistributable,
  so `enforce_provider_policy` refuses it outside personal mode.
- Credentials (J-Quants Pro idToken flow or TMI web session) are operator
  configuration, never committed.

## Ingestion contract (fail-closed rules)

1. Missing data is not zero. Empty amount cells (pre-2026-09-25 dates) parse to
   `None`, never `0`. An unparseable numeric cell fails the ingest.
2. Consistency check per row: total = negotiable + standardized for both sides
   (sell and buy). A violation fails the batch (upstream corruption or format
   drift), it is never silently coerced.
3. Provenance is stored per record batch: provider `jpx_reference`, source URL
   (or transport note for FTP/API), retrieved-at, application date (as-of),
   license class `personal_only`.
4. Time series persistence: one row per (application_date, code); re-ingesting
   the same application date replaces that date's rows atomically (JPX states
   corrections are re-published; last write for an application date wins, and
   the raw file snapshot is kept for forward validation).
5. Derived fields (daily change, short/long ratio) are computed at read time
   from persisted balances only when the previous application date exists;
   otherwise `None`. Ratios with a zero denominator are `None`, never infinity.
6. Abrupt-change alerts: computed at read time from the persisted series
   (configurable relative-change threshold), never written back as data.

## Remaining before live ingestion (after 2026-09-28)

- [ ] Fetch the first real daily file/API response and re-verify encoding,
      header names, and the total = general + system identity against real data.
- [ ] Ingest real data end-to-end with provenance and snapshot the raw file.
- [ ] Surface supply/demand history, screener fields, and abrupt-change alerts
      on the instrument page once real history accumulates.
- [ ] Keep the weekly predecessor snapshots for forward validation of the new
      daily series.
