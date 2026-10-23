# JPX daily margin balances (銘柄別信用取引残高・日次)

Status: **format contract verified against the current official specification
(2026-10-23); live publication confirmed; machine-readable daily file still
contract-gated.** See `docs/P1D_JPX_MARGIN_DAILY_EVIDENCE.md` for the retrieval
log, hashes, ingest run and the free public-PDF alternative.

This document records the observed publication facts and the ingestion contract
for the JPX daily issue-level margin balance service. The paid daily CSV is
distributed through JPX総研 (TMI) or J-Quants Pro; the same business data for all
TSE issues is also published free of charge as a daily PDF on the JPX statistics
site (see "Live data availability").

## Publication facts (announced 2026-09-24; live since 2026-09-28, confirmed 2026-10-23)

- Announced by JPX総研 on 2026-07-29:
  https://www.jpx.co.jp/corporate/news/news-releases/6020/20260729-01.html
- Coverage: all TSE-listed issues; balances as of the previous business day,
  split sell/buy and general (一般) vs standardized/system (制度) margin.
- Daily publication cadence: around 16:00 JST each business day, for the
  previous trading day's application date (申込日).
- Start date: 2026-09-28 — **live, confirmed 2026-10-23** (daily files exist for
  2026-10-15, 10-16, 10-19, 10-20, 10-21). Application-date data from 2026-09-25
  onward. Amount (value) columns exist only for application dates from
  2026-09-25 onward.
- Access is a **paid JPX総研 contract** (TMI reference service or J-Quants Pro).
  Distribution channels: website CSV download, FTP/SFTP data feed, Snowflake
  sharing, and the J-Quants Pro REST API.

## Official URLs (re-verified 2026-10-23; these are the live paths)

- Product page — 銘柄別信用取引残高 (daily + weekly, specs, samples, fees):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/05.html
- Web-service file specification (PDF, current; §3.2.3 is this dataset):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/co3pgt0000004g0w-att/JPXReferenceWeb.pdf
- DFS (FTP/SFTP) file specification (PDF):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/co3pgt0000004g0w-att/DFSSpecificationJP.pdf
- Web usage manual (PDF):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/co3pgt0000004g0w-att/webhowto.pdf
- Sample file (dummy data, official format):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip
- Fee schedule — JPX公式データ (PDF; 月額税抜):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/tvdivq00000024wc-att/fee_schedule_ref_data.pdf
- J-Quants Pro API reference (銘柄別信用取引残高・日次):
  https://jpx.gitbook.io/j-quants-pro-ja/api-reference/margin_interest
- Free public daily table (same business data, PDF only):
  https://www.jpx.co.jp/markets/statistics-equities/margin/01.html
- Weekly predecessor (paid, same issue-level concept):
  https://www.jpx.co.jp/markets/paid-info-equities/reference/09.html

Superseded paths (dead as of 2026-10-23): the previously recorded file-spec URL
`.../t13vrt000001k10u-att/JPXReferenceWeb_2709.pdf` returns an HTTP 404 page.

Do not confuse with the pre-existing 日々公表信用取引残高
(`/markets/daily_margin_interest` on J-Quants, 個別銘柄信用取引残高表 on the
JPX website), which only covers 日々公表銘柄 designated by TSE/日本証券金融.
The new service covers **all** margin-tradable TSE issues daily.

## Observed CSV format (from official sample ZIP, 2026-09-24)

ZIP layout: `Web&DFS/OutstandingMarginTradingByIssue.csv` (English),
`Web&DFS/JP_OutstandingMarginTradingByIssue.csv` (Japanese), `Readme.txt`.
Web and DFS (FTP/SFTP) distributions use the identical file.

- Encoding: English CSV is UTF-8 without BOM; Japanese CSV is CP932
  (Shift-JIS family, verified byte-level on the sample). Line endings CRLF.
  The production file's encoding is unverified until a contract exists (the
  specification does not state one).
- Header row present, then one row per issue, fully quoted.
- 18 columns, Japanese names in parentheses:

| # | EN header | JP header | Notes |
|---|-----------|-----------|-------|
| 1 | Record Date | 申込日 | YYYYMMDD (application date) |
| 2 | Local Code | 銘柄コード | 5-char JPX code, e.g. `13010`, `135A0` |
| 3 | Company Name (English) | 銘柄名 | |
| 4 | ISIN | ISIN | 12-char |
| 5 | Market Segment Code | 市場コード | `0111` Prime, `0112` Standard, `0113` Growth, `0109` その他・投信等, `0105` TOKYO PRO Market |
| 6 | Margin Code | 銘柄種別コード | `1` 制度信用銘柄（貸借以外）, `2` 貸借銘柄, `3` その他（非貸借・非制度信用） |
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

## Specification verification (2026-10-23, `JPXReferenceWeb.pdf` §3.2.3)

Confirmed against the current official specification (SHA-256
`5f53d0a8…10a18a`, fetched 2026-10-23) — these points are part of the contract
the parser enforces:

- The first line is the header: 「項目名称」がファイルの一行目に設定されます ("item
  names" are written on the first line), so header-name mapping is correct.
- Delimiter is a single-byte comma and **every** value is wrapped in half-width
  double quotes; a NULL is emitted as an empty quoted cell (`""`) — never as 0.
- Field lengths: 申込日 8, 銘柄コード 5, 銘柄名 240, ISIN 12, 市場コード 4,
  銘柄種別コード 1, and 20 for each of the 12 numeric items.
- Delivery/retention (web service): updated at 16:00 JST each business day and the
  service keeps 過去 10 営業日分 (10 business days).
- 市場コード values are market segments, not just "Prime/Growth": `0111`
  プライム, `0112` スタンダード, `0113` グロース, `0109` その他・投信等, `0105`
  TOKYO PRO Market.
- 銘柄種別コード `1` is 貸借銘柄以外の制度信用銘柄 (standardized-margin-only,
  not "margin trading" in general), `2` 貸借銘柄 (loan-eligible), `3` その他.
- Aggregate ("合計レコード") rows no longer exist: from 2026-09-28 the file is
  issue-level only, and the old subtotal rows can be rebuilt from 市場コード ×
  銘柄種別コード (G/H/I/J/K…/S aggregate table in the spec).
- Encoding is **not** specified in the document. The sample is UTF-8 without BOM
  (EN) / CP932 (JP); the production file's encoding is unverified until a
  contract exists, and the parser fails closed if neither decodes.

## Live data availability (verified 2026-10-23)

- The paid dailies are live: J-Quants Pro `GET /v2/markets/margin_interest` and
  the SFTP/Snowflake feeds started 2026-09-28; the endpoint answers
  `401 UnauthorizedException` without a contract token, and no credential is
  configured in this environment.
- JPX also publishes the **same business data, all TSE issues, free, as a daily
  PDF** at `https://www.jpx.co.jp/markets/statistics-equities/margin/01.html`
  (`…/tvdivq0000001rnl-att/YYYYMMDD_mtall.pdf`), one file per application date at
  16:00 JST, ~1 week of dates visible (no archive), with 修正前/修正後 `_01`
  files when JPX republishes a correction. The 2026-10-21 table carries 4,249
  issues with both 株数 (1 share) and 金額 (1 JPY) and satisfies the
  total = 一般 + 制度 identity on every row we could tokenize.
- The PDF is print-oriented (wrapped rows, page headers), so it is not the CSV
  transport; a reader for it is a separate provider surface.
- Details, hashes and full evidence: `docs/P1D_JPX_MARGIN_DAILY_EVIDENCE.md`.

## J-Quants Pro API shape (same business data)

`GET https://api.jquants-pro.com/v2/markets/margin_interest` with `code` /
`date` / `published_date` / `from`+`to` / `pagination_key`. Field names map 1:1
to the CSV columns (`Date`=申込日, `ShortMarginOutstanding`,
`ShortMarginOutstandingValue`, ...). The API additionally returns `PublishedDate`
(公表日, delivery date) and `IssueType` with the same 1/2/3 semantics as
銘柄種別コード above.

API-specific facts recorded from the reference (2026-10-23) that matter for
ingestion semantics:

- `PublishedDate` exists only for application dates from 2026-09-25 onward
  (null before); filtering by `published_date` returns only rows that have it.
- Application dates before 2026-09-25 contain only weekend (weekly) balances,
  identical to the weekly dataset; the six 金額 fields are null before
  2026-09-25.
- Corporate actions are **not** applied retroactively to past volumes.
- Issues listed only on local exchanges (non-TSE) are out of scope.
- One of `code` / `date` / `published_date` is required; `published_date` cannot
  be combined with `date`/`from`/`to`.

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

- [x] Verify encoding, header names, and the total = general + system identity
      against official material: format contract confirmed against the current
      specification and the unchanged official sample package (2026-10-23); the
      *production bytes* remain unverified because the machine-readable file is
      contract-gated. Real-value identity confirmed on the free public daily PDF
      (4,249 issues, 2026-10-21). Evidence:
      `docs/P1D_JPX_MARGIN_DAILY_EVIDENCE.md`.
- [x] Ingest the official distribution file end-to-end with provenance and verify
      the replacement semantics — done with the official sample package
      (dummy data) into a scratch DB: parse → persist → CLI → API, 10 rows /
      1 application date, re-ingest replaces the date, 32 tests green.
- [ ] Obtain one real business day of the machine-readable file. Blocked by the
      paid contract (TMI / J-Quants Pro); no credential is configured. Two paths
      are recorded in the evidence file: (a) contract the feed, (b) build a
      reader for the free public daily PDF (needs its own parser/tests).
- [ ] Surface supply/demand history, screener fields, and abrupt-change alerts
      on the instrument page once real history accumulates.
- [ ] Keep the weekly predecessor snapshots for forward validation of the new
      daily series.
