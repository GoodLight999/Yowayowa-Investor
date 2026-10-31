# P1D Evidence — JPX daily margin balances: live production format

Task: `t_7a943308` (roadmap P4 item "JPX daily margin: live production format
inspection"). Verified: 2026-10-23 (JST). Verifier: `worker-go`
(implementation + self-verification); independent review requested from
`audit-glm-heavy`.

## 1. Result in one paragraph

The production **format contract is confirmed against the current official
specification** and against the official distribution package fetched today
(byte-identical to the files committed on 2026-09-24), and a one-application-date
ingest through parse → persist → CLI → API runs green on the real distribution
bytes (dummy sample data, scratch DB). The **live machine-readable daily file was
not ingested**: JPX distributes it only under a paid JPX総研 (TMI) / J-Quants Pro
contract, no credential exists in this environment, and the API answers
`401` without one. What *is* published free is the same business data as a daily
PDF (all TSE issues), which we fetched and used to check the ingestion contract's
semantics against real values.

## 2. Retrieval log (all fetched 2026-10-23; times JST unless marked Z)

| Artifact | URL | HTTP | Bytes | SHA-256 |
|---|---|---|---|---|
| Official distribution package (sample) | `https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip` | 200 | 2,870 | `08e9209acbec3747bd6cf1b1914ac18c808d27306910ebf76c72ba4b368db47d` |
| ↳ `Web&DFS/OutstandingMarginTradingByIssue.csv` (EN) | inside the ZIP | — | 2,281 | `38f9bd690a12627d3b9b612e0028fe3d0b64be45b7884f1b12d1e9b273388693` |
| ↳ `Web&DFS/JP_OutstandingMarginTradingByIssue.csv` (JP) | inside the ZIP | — | 2,103 | `6b1bf5c2df40a563973f940d379a9f04839678fabf80d1925ff030ca215828cd` |
| ↳ `Web&DFS/Readme.txt` | inside the ZIP | — | 718 | `903d9a67f2f1d66509db01178a46c5d53ed8d53ebc25a55eda6c3f4e664a0996` |
| Web-service file specification (current) | `https://www.jpx.co.jp/markets/paid-info-equities/reference/co3pgt0000004g0w-att/JPXReferenceWeb.pdf` | 200 | 666,992 | `5f53d0a8b1da19554de07f3a538a388ba8aeaa06692cf092342939747710a18a` |
| Product page (銘柄別信用取引残高, paid reference) | `https://www.jpx.co.jp/markets/paid-info-equities/reference/05.html` | 200 | 36,064 | — |
| Fee schedule (JPX公式データ, 月額税抜) | `https://www.jpx.co.jp/markets/paid-info-equities/reference/tvdivq00000024wc-att/fee_schedule_ref_data.pdf` | 200 | 282,555 | `16f58390fcf285427cde5d3deb2e7c0e2b6977e26c0468952ba0ce6ed23c262f` |
| Public daily table, 2026-10-21 申込 | `https://www.jpx.co.jp/markets/statistics-equities/margin/tvdivq0000001rnl-att/20261021_mtall.pdf` | 200 | 1,741,441 | `405532ab228bc021fe89bf19c23915055cdd72722ce703b59535f19c65616b7a` |
| Public table index | `https://www.jpx.co.jp/markets/statistics-equities/margin/01.html` | 200 | 31,305 | — |
| J-Quants Pro API (no token) | `https://api.jquants-pro.com/v2/markets/margin_interest?code=86970` | **401** | 55 | body `{"message":"The incoming token is invalid or expired."}`, `x-amzn-requestid: e2b5f3c1-269d-42c4-b738-9f468661ead2`, 00:04:22Z |

The distributed CSV files in today's ZIP are **byte-identical** to
`tests/fixtures/jpx/*.csv` (same SHA-256), and the ZIP entries carry mtimes
2026-09-15 14:48/16:53 — the sample package has not changed since the format was
first recorded on 2026-09-24.

The file-specification URL recorded in `docs/JPX_DAILY_MARGIN.md`
(`.../t13vrt000001k10u-att/JPXReferenceWeb_2709.pdf`) is **dead**: it returns an
HTTP 404 HTML page (21,245 bytes). The current spec is `JPXReferenceWeb.pdf`
(above); `DFSSpecificationJP.pdf` and the sample ZIP are unchanged. The doc now
carries the live URLs.

## 3. Format verification against the official specification

Read from `JPXReferenceWeb.pdf` §3.2.3 銘柄別信用取引残高 (and §3.2, §2.1, §2.2):

| Contract point | Specification | Matches our parser/doc? |
|---|---|---|
| Header row | 「項目名称」がファイルの一行目に設定されます (item names are the first line) | yes — header-mapped parse is correct |
| Delimiting | comma, every value wrapped in half-width double quotes | yes |
| NULL | NULL is emitted as `""` (empty quoted cell) | yes — empty → `None`, never 0 |
| Items | 18 items, in the documented order | yes (18/18) |
| Lengths | 申込日 8; 銘柄コード 5; 銘柄名 240; ISIN 12; 市場コード 4; 銘柄種別コード 1; 12 numeric items 20 each | consistent (no int overflow risk) |
| 市場コード | 0105 TOKYO PRO Market / 0111 プライム / 0112 スタンダード / 0113 グロース / 0109 その他・投信等 | doc previously said "0109 ETF" — corrected to その他・投信等, 0105 added |
| 銘柄種別コード | 1 = 貸借銘柄以外の制度信用銘柄, 2 = 貸借銘柄, 3 = その他（非貸借・非制度信用銘柄） | parser's `JPX_MARGIN_CODE_MEANINGS` maps 1/2/3 to margin/loan_consolidated/other; doc wording corrected |
| Delivery | updated 16:00 (symbols: 銘柄マスタ 19:30, 四本値 16:30) | matches 16:00 in doc |
| Retention (web) | 過去 10 営業日分 | new fact, recorded |
| Aggregate rows | rows that existed before 2026-09-28 ("合計レコード") are gone; they can be reconstructed from 市場コード × 銘柄種別コード (table in §3.2.3) | new fact: our file is issue-level only, no subtotal rows to skip |
| Encoding | not stated in the spec | English CSV observed UTF-8 w/o BOM, Japanese CSV CP932 — **only verified on the sample**; the production file's encoding is unverified until a contract exists |

## 4. Ingest verification (real distribution bytes, dummy data)

Every command below was executed; `DATABASE_URL` pointed at a **scratch** SQLite
file so the operator database stays clean (the sample is dummy data and must
never be treated as market data — `tests/fixtures/jpx/README.md`).

```
$ export DATABASE_URL=sqlite:///<scratch>/evidence.db YOWAYOWA_MODE=personal
$ yowayowa jpx-margin-ingest '<scratch>/sample/Web&DFS/OutstandingMarginTradingByIssue.csv' \
    --source-url 'https://www.jpx.co.jp/.../sample_outstanding_margin.zip#Web&DFS/OutstandingMarginTradingByIssue.csv'
Ingested 10 rows for 1 application date(s): 2026-04-23

$ yowayowa jpx-margin-ingest '<scratch>/sample/Web&DFS/JP_OutstandingMarginTradingByIssue.csv' \
    --source-url '.../sample_outstanding_margin.zip#Web&DFS/JP_OutstandingMarginTradingByIssue.csv'
Ingested 10 rows for 1 application date(s): 2026-04-23
$ sqlite3 evidence.db "select count(*), count(distinct application_date) from jpx_margin_balances;"
10|1        # same date re-ingested (CP932 file) -> replaced, not duplicated
```

Schema verification on the persisted rows (12 numeric + 6 descriptive fields):

- all 12 numeric fields persist as `int`, 0 nulls, identity `total = 一般 + 制度`
  holds for sell and buy on all 10 rows (0 violations);
- `application_date` = `2026-04-23` (date), `code` = `13010`-style 5-char,
  `isin`/`market_code`/`margin_code` stored, JP run stores the Japanese
  `company_name` (`極洋　普通株式`);
- value/volume ratio is a constant 100 JPY per share across all rows — consistent
  with JPX's stated dummy data, and with 株数 being shares and 金額 being JPY;
- provenance stored per row: `provider=jpx_reference`,
  `license_class=personal_only`, `source_url` = the ZIP URL with the member path,
  `retrieved_at=2026-10-23T00:10:54.906117Z`, `as_of=2026-04-23`.

API read-back on the same DB (personal mode): `GET /v1/jpx/margin/13010` → 200
with the point above and `short_change=null` (no previous application date);
`GET /v1/jpx/margin/date/2026-04-23` → 200, 10 series (13010, 13050, 135A0,
13770, 14070, 14180, 18410, 21640, 36560, 36810); `/latest` → 200, 10 series;
batch `?codes=13010,135A0` → 200.

Test suite for this surface: `pytest tests/test_jpx_margin.py` → **32 passed**.

## 5. Live publication status (real data, public PDF)

- Publication is live: the paid API/SFTP/Snowflake services started 2026-09-28
  per the J-Quants Pro reference; the public statistics page carries daily files
  for application dates 2026-10-15, 10-16, 10-19, 10-20, 10-21 (16:00 JST each
  business day) — i.e. a ~1-week window, no archive.
- The 2026-10-21 table (published 2026-10-22) covers **4,249 issues** and carries
  both 株数 (unit: 1 share) and 金額 (unit: 1 JPY) per issue, split sell/buy and
  一般信用/制度信用.
- Corrections are re-published: 2026-10-19 and 2026-10-20 have 「修正前」/「修正後」
  files (`*_mtall.pdf` and `*_mtall_01.pdf`), consistent with the contract's
  "last write for an application date wins".
- Semantic check of the ingestion contract against real values (pdftotext
  `-layout` dump, row identity `total = 一般 + 制度`):
  **0 identity violations, 0 duplicates** over 3,846 volume rows and 3,876 value
  rows; e.g. 13010 → sell 9,500 = 300 + 9,200 shares / ¥44,734,000, buy 123,500 =
  38,400 + 85,100 / ¥556,194,000.
- Coverage caveat (extraction, not data): PDF row wrapping (investment-trust
  names, 受益証券 in the 種別 column) splits code/ISIN/unit markers across lines,
  so the quick probe attributed 3,846 of 4,249 rows and dropped 3 rows with 11 of
  12 numeric cells. A product-grade PDF reader needs multi-line stitching and
  page-header handling — that is follow-up work, not a defect in JPX data.

## 6. Why the real machine-readable daily file was not ingested

- The daily CSV (web download / DFS / Snowflake) and the J-Quants Pro
  `GET /v2/markets/margin_interest` endpoint are **contract-only**. No
  J-Quants Pro / TMI credential exists in `.env`, `config/`, or the environment
  (`grep -ri jquants` over the repo and home config finds only docs).
- Unauthenticated API call returns `401 UnauthorizedException` (evidence above).
- Fee schedule (月額税抜): 銘柄別信用取引残高(日次) 100,000円 (総合取引参加者向け,
  単一法人) / 250,000円 (その他法人向け, 単一法人) / 500,000円 (系列法人); the
  weekly variant is 50,000円 (単一法人) / 125,000円 (系列法人) / 125,000円 (会員制
  サービス外部配信) / 175,000円 (オープン型サービス外部配信). Plans are written for
  corporate subscribers (単一法人/系列法人). A 75% early-contract discount runs
  for contracts signed by 2026-12-31 (fees through 2027-03).
- **This is an operator (cost/contract) decision, not an engineering gap**, so no
  data was invented, synthesised, or back-filled to make the card look complete.

## 7. Confirmation commands (reviewer)

```bash
# 1. contract-gated machine-readable feed (expect 401)
curl -sS -i 'https://api.jquants-pro.com/v2/markets/margin_interest?code=86970' | head -12

# 2. official sample package unchanged (expect the documented SHA-256)
curl -sS -o /tmp/jpx_sample.zip \
  'https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip'
sha256sum /tmp/jpx_sample.zip   # 08e9209a...368db47d
unzip -p /tmp/jpx_sample.zip 'Web&DFS/OutstandingMarginTradingByIssue.csv' | sha256sum
                                # 38f9bd69...3388693 == tests/fixtures/jpx/OutstandingMarginTradingByIssue.csv

# 3. live public daily table (expect 200, PDF, ~1.7 MB) and its semantics
curl -sS -o /tmp/mtall.pdf \
  'https://www.jpx.co.jp/markets/statistics-equities/margin/tvdivq0000001rnl-att/20261021_mtall.pdf'
sha256sum /tmp/mtall.pdf        # 405532ab...616b7a
pdftotext -layout /tmp/mtall.pdf - | grep -m1 '総合計'

# 4. parser/persistence/API/CLI contract (offline, official fixtures)
.venv/bin/python -m pytest tests/test_jpx_margin.py -q     # 32 passed
```

## 8. Alternatives and recommendation

1. **Contract the paid feed** (TMI web/DFS or J-Quants Pro) and keep the existing
   pipeline: `yowayowa jpx-margin-ingest` needs no change for the CSV transport;
   the J-Quants Pro JSON transport would need one new mapping layer
   (field-for-field, plus `PublishedDate`) and a raw-response snapshot.
   Accuracy and machine-readability are best here; it costs from 100,000円/month
   (税抜) and requires a corporate contract.
2. **Build a public-PDF reader** for the free daily table: same business data,
   free, but the layout is print-oriented (row wrapping, page headers, no
   archive beyond ~1 week, corrections as `_01` files). The identity check in §5
   shows the semantics line up; a production reader needs its own parser,
   fixtures and tests, plus a daily fetch/snapshot job.
3. **Do nothing further**: the weekly issue-level series already scraped
   (`docs/CREDIT_MARGIN.md`) plus the public daily PDF for spot checks.

Recommendation: (2) first — it produces the all-issue daily series at zero
contract cost and can be validated against the same identity the current parser
enforces; (1) as the upgrade if the operator is willing to contract. Both need
an operator decision on cost/effort, so the roadmap item stays open with the
facts above instead of a guessed implementation.

## 9. Artifacts and hygiene

- Committed by this task: this document and the URL/spec corrections in
  `docs/JPX_DAILY_MARGIN.md`. No code path changed.
- Not committed: downloaded PDFs/ZIP, `pdftotext` dumps, probe scripts and the
  evidence SQLite DB — all under
  `/root/.hermes/profiles/worker-go/cache/scratch/jpx/` and disposable.
- No credentials were used or printed; the public PDF was fetched for inspection
  only and is not redistributed by this repository.
