# Weekly credit margin (信用残・週次) — Yahoo!ファイナンス + 株探

Status: **live, scraped, personal-only** (P4-C; implemented 2026-09-24).

This document records the observed page formats and the ingestion contract
for the weekly 銘柄別信用取引残高 scraped from Yahoo!ファイナンス and 株探
(kabutan). It is the weekly companion of `docs/JPX_DAILY_MARGIN.md` (P4-A,
日次, paid JPX contract) but is a **separate persistence surface**: the two
sources below publish sell/buy totals only, with no 一般/制度 breakdown, so
the daily identity-bearing model is deliberately NOT reused (reusing it
would let zero-filled negotiable/standardized columns pass the daily
total-identity check).

## Observed formats (verified live 2026-09-24, UTC)

### Yahoo!ファイナンス

- URL: `https://finance.yahoo.co.jp/quote/{code}.T/margin` (HTTP 200,
  server-rendered history table inside the HTML; no auth/cookie needed).
- History table (`aria-label="信用残時系列のテーブル"`): weekly, latest
  **20 weeks**; columns 日付・売残・買残・売残増減・買残増減・信用倍率.
- Row markup (2026-09-24): `<th class="_Table__header_...">2026/9/11</th>`
  followed by five `<td class="_Table__data_...">` cells whose number text
  is nested in `<span class="_StyledNumber__value_...">299,300</span>`.
- Unit: **株 int** (comma-grouped). Negative 増減 carries a leading `-`.
- Row sample (6758): 2026/9/11 → 売残 299,300・買残 7,581,100.
- Same-day snapshot inside the page's RSC payload
  (`"items":{"name":"信用売残","primary":{"value":"2,625,400",...}}`) is
  high-volatility and deliberately NOT parsed — the history table alone
  yields 20 weeks.

### 株探 (kabutan)

- URL: `https://kabutan.jp/stock/?code={code}` (stock top page, HTTP 200).
- Section `<h2 class="mgt6">信用取引&nbsp;(単位:千株)</h2>` immediately
  followed by the credit table; columns 日付・売り残・買い残・倍率;
  latest **4 weeks**.
- Row sample (6758):
  `<tr><th scope='row'><time datetime="2026-09-11">09/11</time></th>
  <td>299.3</td><td>7,581.1</td><td>25.33</td></tr>` — numeric cells are
  comma-grouped with at most two decimals; decorative cells (SVG icons,
  link tables) do not match the row grammar.
- Unit: **千株 (thousand shares), one decimal** → persisted as shares by
  ×1000 (Decimal exact rounding; within ±100 shares of the true count).

### Cross-validation (2026-09-24, CTO acceptance probe)

4 codes (7203/6758/9984/8306): Yahoo 20 weeks × 4 pages and kabutan
4 weeks × 4 pages, all common as_of weeks matched within ±100 shares.
Rows parsed by the committed fixtures reproduce the live values.

Dead ends (do not use): `stocks.finance.yahoo.co.jp/stocks/margin/` and
`kabutan.jp/stock/margin` return 404.

## Access etiquette

- 1 URL = 1 request, sequential per code; no authentication, cookies,
  storage state, retries-with-backoff loops, or speculative access.
- Browser `User-Agent` string; nothing else is customized.
- 400/404/timeouts/parse failures raise (fail-closed). HTML format drift is
  never silently coerced: missing header, missing table, wrong column
  count, duplicate week, or non-numeric number cells all raise.

## Model and persistence contract

- Model `CreditMarginWeekly` (src/yowayowa/credit_margin_models.py):
  `(as_of_date, code, short_total, long_total)` plus provenance. **No
  amount (金額) fields** — the sources publish none, missing data is never
  zero-filled, and no column is invented.
- Unit: shares (株 int) for both providers (kabutan ×1000 at parse time).
- Identity: `(as_of_date, code)` unique (`credit_margin_weekly` table,
  `CreditMarginWeeklyRecord`). Persisting the same week again:
  value-identical → no-op; changed → UPDATE (values, retrieved_at,
  source_url) with a `再確認(値変化)` note; different weeks INSERT.
  A daily job fetching whatever the pages currently publish converges the
  weekly datapoints (latest 20 weeks from Yahoo, 4 from kabutan).
- Provenance: provider `yahoo_finance_margin` / `kabutan_margin`,
  `license_class=PERSONAL_ONLY`, retrieved_at (UTC), as_of=week date, and
  notes recording the source URL, that this is public-page scraping (no
  authenticated/XHR access), the 千株→株 unit conversion for kabutan, and
  the as-of week.
- Derived (増減・倍率) are computed at read time from persisted rows only
  (`CreditMarginWeeklyPoint`, same rules as the JPX daily points): no
  previous persisted week → `None`; zero long balance → ratio `None`,
  never infinity. Source 増減/倍率 columns are not persisted (they are
  derivable and would duplicate read-time logic).

## Surfaces

- API (personal mode; HTTP 403 otherwise, same gate as the JPX routes):
  - `GET /v1/credit/margin/{code}` — one code's weekly series, oldest
    first (`limit`, `date_from`, `date_to`).
  - `GET /v1/credit/margin/latest` — all codes for the most recent week.
  - `GET /v1/credit/margin/date/{date}` — one as-of week, code order.
- CLI: `yowayowa credit-margin-fetch 7203 6758 [--source yahoo|kabutan|both]`
  (fetch + persist; network access happens only here), and
  `yowayowa credit-margin 6758` (show persisted series).
- Tests: `tests/test_credit_margin.py` runs offline against verbatim live
  page snippets in `tests/fixtures/credit_margin/` (captured 2026-09-24).

## License policy

Scraped quote-page data is personal-only (`LicenseClass.PERSONAL_ONLY`,
`doc/LICENSE_POLICY.md`): not redistributable, served only in personal
mode, and never exposed through the public API surface.
