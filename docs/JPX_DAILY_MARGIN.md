# JPX daily margin data

Status: **live free publications integrated and fixture-verified** (2026-10-03).

Yowayowa supports both the paid JPX総研 reference feed and the free
JPX/TSE publications. They share the canonical daily all-issue balance table;
publication-specific facts remain separate auxiliary facts with their own
provenance and timestamps.

## Source families

### Free official publications

- all-issue daily margin balance:
  https://www.jpx.co.jp/markets/statistics-equities/margin/01.html
- 日々公表銘柄等信用取引残高:
  https://www.jpx.co.jp/markets/statistics-equities/margin/index.html
- 品貸料:
  https://www.jpx.co.jp/markets/statistics-equities/margin/02.html
- 信用取引売買比率:
  https://www.jpx.co.jp/markets/statistics-equities/margin/03.html

The all-issue balance and watch-list publications are previous-business-day
facts published around 16:00 JST. The margin-trading ratio publication carries
same-day trading-flow observations and is published around 16:30 JST. Keep
`as_of`, `published_at`, and `retrieved_at` distinct: a backtest must not
use a balance before it was actually published.

### Paid reference feed

JPX総研 TMI / J-Quants Pro remains supported by
`providers/jpx_margin.py`. Its 18-column CSV contract is the canonical
structured representation for:

- application date;
- exact five-character JPX local code;
- company / ISIN / market / margin code;
- short and long totals;
- negotiable (一般) and standardized (制度) components;
- the corresponding JPY values.

Individual contracted access remains `PERSONAL_ONLY`.

## Live free all-issue PDF verification

The 2026-10-01 application-date production PDF was inspected against the real
JPX artifact, not the pre-launch sample.

Acceptance facts:

- declared total: 4,250 issues;
- parsed: 4,250 issues;
- unique exact local codes: 4,250;
- Prime 1,553 / Standard 1,553 / Growth 595 / 投信等 549;
- 貸借 2,671 / 制度信用 1,565 / その他 14;
- every volume row satisfies total = general + standardized on both sides;
- every value row satisfies the same identity;
- legitimate non-zero fifth-character codes are preserved, including class
  securities such as `25935`, `92015`, `92025`, `94345`, and `94346`.

Never normalize the five-character JPX local code to a four-character symbol:
doing so can merge a common share with a preferred/class security.

The free PDF is parsed with `pdfminer.six` using layout parameters tuned for
the JPX table. Parsing is still fail-closed: document-level issue counts,
section counts, margin-type counts, row identities, duplicate codes, and
required numeric cells must all validate before any canonical day is replaced.

## Cross-source validation

The same-date 日々公表 XLSX is used as an independent structured cross-check
when the canonical all-issue day is already persisted. For the supplied
2026-10-01 fixture, all 429 overlapping issue rows agreed on the corresponding
non-null balance/change fields.

The 品貸料 workbook uses four-character source codes. These are **not**
blindly converted to a five-character key. Yowayowa resolves only the exact
ordinary-code candidate `SOURCE_CODE + "0"` when that exact five-character
code exists in the same-date canonical universe. Class securities are never
guessed.

A `*****` premium charge means missing/not-applicable data and remains
`None`; a numeric zero remains zero.

The 信用取引売買比率 PDF stores status markers such as `規`, `日`, and
`○` separately from the issue name. A published `-` remains `None`.
The supplied 2026-10-02 artifact contains 31 issues across three trade dates
(93 observations).

## Persistence and provenance

`JpxMarginBalanceRecord` remains the canonical balance history, regardless of
whether a day came from the paid structured feed or the free PDF.

`JpxMarginAuxRecord` stores source-specific facts:

- `balance_source`: last-writer provenance for a canonical public-PDF row;
- `balance_detail`: source-reported daily changes, listed-share ratios,
  section and source labels;
- `watch`: 日々公表 / JSF state and its published balance detail;
- `premium`: stock-loan shortage / premium-charge facts;
- `flow`: same-day new-margin sales/purchase ratios.

Raw downloaded artifacts are content-addressed under
`YOWAYOWA_JPX_PUBLIC_RAW_CACHE_DIR` using SHA-256. Re-fetching or a source
correction can therefore be audited and reparsed.

A complete artifact is parsed and validated before the affected date is
deleted/replaced. Persistence is transactional; malformed input must leave the
previous successful day untouched.

## Derived read semantics

Daily changes derived from canonical persisted balances use the immediately
preceding persisted application date. The API applies `limit` only after that
previous row is resolved, so `limit=1` still returns a correct latest daily
change when a prior day exists.

Missing data is never zero. Ratios with a zero denominator remain `None`.

## Supply/demand screens

`services/jpx_margin_signals.py` exposes transparent, source-backed scans:

- `crowded-long`
- `crowded-short`
- `long-unwind`
- `short-cover`
- `borrow-stress`
- `flow-buy`
- `flow-sell`
- `buy-flow-divergence`
- `sell-flow-divergence`
- `squeeze-watch`
- `watch-flags`

There is no opaque composite score. Each result carries its measured inputs,
reason, source provenance and `available_at`. In particular,
`squeeze-watch` requires an observed, non-null same-date buy-flow value;
missing flow can never qualify.

## API / CLI

API:

- `POST /v1/jpx/public/sync`
- `GET /v1/jpx/public/details/{code}`
- `GET /v1/jpx/public/watch/{code}`
- `GET /v1/jpx/public/premium/{source_code}`
- `GET /v1/jpx/public/flow/{code}`
- `GET /v1/jpx/public/signals/{signal}`

CLI:

- `yowayowa jpx-public-sync`
- `yowayowa jpx-public-ingest FILE --kind ... --source-url ...`
- `yowayowa jpx-margin-scan SIGNAL`
- `yowayowa jpx-margin-detail CODE`
- `yowayowa jpx-margin-watch CODE`
- `yowayowa jpx-margin-flow CODE`
- `yowayowa jpx-premium SOURCE_CODE`

## License / mode policy

The free artifacts are official public JPX/TSE publications, classified
`OFFICIAL_PUBLIC` for provenance. That classification does **not** itself
grant redistribution rights. Until explicit public-display/API rights are
reviewed and recorded, the provider descriptor remains
`redistributable=False` and Yowayowa serves these routes only in personal
mode.

The paid JPX reference feed remains `PERSONAL_ONLY`; its contract is a
separate source-rights boundary.
