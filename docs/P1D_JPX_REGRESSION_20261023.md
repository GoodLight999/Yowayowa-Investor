# P1D Regression — JPX daily margin ingestion (re-run on a different path)

Task: `t_038f26ea` (parent: `t_7a943308`, JPX daily margin production-format
inspection). Re-run date: 2026-10-23 (JST). Re-run by: `worker-go`; independent
review requested from `audit-glm-heavy`.

## 1. Result in one paragraph

**Match.** The parent run's persisted result (10 rows for application date
2026-04-23, ingested from the official JPX sample distribution) is reproduced
**exactly** by (a) an independent positional parser written from the
specification, with no project code imported, and (b) a fresh ingest through the
product path (`yowayowa jpx-margin-ingest`, EN then JP, own scratch DB):

- rows 10 / 10, application dates `["2026-04-23"]` on both sides;
- **0 field mismatches** over 10 rows × 18 business columns (180 cells);
- business-row digest identical on all three sources
  (`e7206a4c…cfb93a08`), numeric-only digest identical for EN and JP
  (`6fd5767e…6cb6f322`);
- 0 null cells, all 12 numeric columns `int`, `total = 一般 + 制度` violated 0
  times (4 checks per row), value/volume ratio constant 100 JPY/share (sample
  dummy data);
- the only differences are volatile provenance (`retrieved_at`, per run) and
  company-name language (EN distribution vs JP distribution, by design);
- the documented artifact hashes are still reproducible live today: sample ZIP
  `08e9209a…b368db47d`, public daily PDF `405532ab…65616b7a`;
- `pytest tests/test_jpx_margin.py` → 32 passed; API read-back 4/4 routes 200.

The paid machine-readable daily file is still contract-gated (live API `401`
re-confirmed today), so the production bytes remain unexercised — see §8.

## 2. Inputs compared (all hashes re-computed on 2026-10-23)

| Input | Path / URL | Bytes | SHA-256 |
|---|---|---|---|
| Official sample package | scratch `sample_outstanding_margin.zip` (`…/t13vrt000001k10u-att/sample_outstanding_margin.zip`) | 2,870 | `08e9209acbec3747bd6cf1b1914ac18c808d27306910ebf76c72ba4b368db47d` |
| ↳ EN CSV in ZIP | `Web&DFS/OutstandingMarginTradingByIssue.csv` | 2,281 | `38f9bd690a12627d3b9b612e0028fe3d0b64be45b7884f1b12d1e9b273388693` |
| ↳ JP CSV in ZIP | `Web&DFS/JP_OutstandingMarginTradingByIssue.csv` | 2,103 | `6b1bf5c2df40a563973f940d379a9f04839678fabf80d1925ff030ca215828cd` |
| Repo EN fixture | `tests/fixtures/jpx/OutstandingMarginTradingByIssue.csv` | 2,281 | `38f9bd69…273388693` (identical to ZIP member) |
| Repo JP fixture | `tests/fixtures/jpx/JP_OutstandingMarginTradingByIssue.csv` | 2,103 | `6b1bf5c2…215828cd` (identical to ZIP member) |
| Live re-fetch of the sample ZIP (today) | same URL, HTTP 200 | 2,870 | `08e9209a…b368db47d` — unchanged |
| Live public daily table 2026-10-21 | `…/margin/tvdivq0000001rnl-att/20261021_mtall.pdf`, HTTP 200 | 1,741,441 | `405532ab228bc021fe89bf19c23915055cdd72722ce703b59535f19c65616b7a` — unchanged |

Parent-run output being compared: scratch `evidence.db` (t_7a943308),
`jpx_margin_balances` = 10 rows, 1 application date, `retrieved_at`
`2026-10-23T00:10:54.906117Z`, `source_url` = the JP member inside the ZIP.

## 3. Re-run paths (deliberately different from the parent's)

1. **Independent positional parse.** A standalone script re-implements §3.2.3 of
   the current specification: fixed 18-item order, literal header equality for
   the EN and JP files, CRLF, every cell quoted, NULL only as `""`, `int()`
   (which rejects signs, spaces and decimals) for the 12 numeric items. It does
   not import `yowayowa.providers.jpx_margin` or any other project module —
   header handling is positional, not header-mapped, which is the main
   difference from the production parser.
2. **Product-path re-ingest.** `yowayowa jpx-margin-ingest` (EN file, then JP
   file) into a fresh scratch DB, parsed by the production parser and persisted
   through the production service — the same user-visible path the operator
   would use, repeated from scratch rather than trusting the parent DB.

Comparison is row-keyed on `(application_date, code)` over all 18 business
columns, plus provenance columns reported separately. Digest definition:
`sha256(("\n".join(json.dumps([field values], ensure_ascii=False)) + "\n"))`
over rows sorted by `(application_date, code)`.

## 4. Comparison result

| Check | Parent (t_7a943308) | Re-run (independent parse) | Re-run (product path, fresh DB) | Match |
|---|---|---|---|---|
| Rows | 10 | 10 | 10 (after EN ingest 10, after JP re-ingest still 10) | yes |
| Application dates | `2026-04-23` | `2026-04-23` | `2026-04-23` (1 distinct) | yes |
| Codes | 13010, 13050, 135A0, 13770, 14070, 14180, 18410, 21640, 36560, 36810 | identical | identical | yes |
| Business-row digest | `e7206a4c9048ef2213be849d6acd7b457aba1cfd2e199d547dda7436cfb93a08` | `e7206a4c…cfb93a08` | `e7206a4c…cfb93a08` | yes |
| Numeric-only digest (EN vs JP) | — | EN = JP = `6fd5767efb7bcc410fda9603607e55494557aeb700ccdb812ee459783cb6f322` | same | yes |
| Business field mismatches | — | 0 of 180 cells | 0 of 180 cells | yes |
| Keys only on one side | — | none | none | yes |
| Null cells (12 numeric columns) | 0 | 0 | 0 | yes |
| Column types | all `int` | all `int` | all `int` (SQLite INTEGER) | yes |
| `total = 一般 + 制度` (sell/buy × volume/value) | 0 violations | 0 violations | 0 violations | yes |
| Value / volume ratio | 100 JPY/share | 100.0 (all rows) | 100 (all rows) | yes |
| Market codes present | 0111/0112/0113/0109 | same | same | yes |
| Margin codes present | 1/2/3 | same | same | yes |
| Unit semantics | 株数 = shares, 金額 = JPY | same (ratio + identity) | same | yes |

Provenance: `provider=jpx_reference`, `license_class=personal_only`,
`as_of=2026-04-23`, `source_url` = the JP member URL — identical on both sides.
The single expected difference is `retrieved_at` (parent `2026-10-23
00:10:54.906117`, re-run `2026-10-23 00:51:49.944072`): it is a per-run
observation timestamp, not data.

EN vs JP distributions: same 10 keys, **0 non-name mismatches**, identical
numeric digest. Names differ only by language (e.g. 13010 =
`KYOKUYO CO.,LTD.` / `極洋　普通株式`), which is the distributions' purpose, not
drift. Because the JP ingest runs last, the re-run DB (like the parent DB)
holds the JP names only — the replace-per-application-date behaviour reproduced
exactly (10 rows before and after the second ingest, 1 distinct `source_url`).

## 5. Fail-closed / atomicity probe (re-run path)

A copy of the JP file with one header renamed (`申込日` → `申込日X`) was ingested
through the CLI into the re-run DB:

- exit code **2**, message `Unrecognized JPX margin CSV header '申込日X'; refusing
  to guess column semantics (format drift). … Expected the official English or
  Japanese headers.`
- row count 10 → 10, digest unchanged ⇒ a rejected file cannot partially replace
  an application date.

## 6. Live re-fetch and real-value semantics (second data path)

- Sample ZIP re-fetched today: HTTP 200, 2,870 bytes, SHA-256 unchanged
  (`08e9209a…`), so the format contract checked by the parent is not stale.
- Public daily table for application date 2026-10-21 re-fetched today: HTTP 200,
  1,741,441 bytes, SHA-256 unchanged (`405532ab…`). Its `13010` rows (an
  independent tokenization of the `pdftotext -layout` dump) give:

  - 株数: sell 9,500 = 300 (一般) + 9,200 (制度); buy 123,500 = 38,400 + 85,100;
  - 金額: sell ¥44,734,000 = ¥1,351,000 + ¥43,383,000; buy ¥556,194,000 =
    ¥173,287,000 + ¥382,907,000;
  - both identities hold; implied unit prices ≈ ¥4,708.84 / ¥4,503.60 per share.

  These are exactly the values the parent recorded, and the implied unit prices
  (≫ 100 JPY/share) confirm the sample's constant 100 ratio is dummy data, not a
  scaling bug: 株数 is shares and 金額 is JPY in the real publication too.
- Contract gate re-confirmed: `GET https://api.jquants-pro.com/v2/markets/
  margin_interest?code=86970` → `401 UnauthorizedException`, 00:55:43Z,
  `x-amzn-requestid: 23b36e52-76ae-450b-9a6c-c10d1fefd1ca`.

## 7. Tests

`pytest tests/test_jpx_margin.py` → **32 passed** (exit 0, 2026-10-23). The suite
covers the parser (header map, encoding detection, NULL≠0, identity, duplicates,
column count, date format), persistence/replace/rollback, read paths, the four
API routes, and the CLI. No production code was changed by this task.

## 8. Remaining issues (honest limits of this regression)

1. **Production bytes still unexercised.** The paid daily CSV has never been
   ingested (contract-gated; `401` above). Consequently the following remain
   untested against production data: empty/NULL amount cells (the sample has
   none), production encoding (sample: UTF-8 no BOM EN / CP932 JP), multi-date
   files, correction (`_01` / 「修正後」) ordering semantics, and real file size
   (~4,249 issues instead of 10 rows).
2. **Regeneration path not implemented.** Parent's recommendation (a reader for
   the free public PDF, or the paid contract) is still an operator decision;
   this regression does not resolve it.
3. **Reproducibility window.** The sample ZIP hash is stable and re-checkable at
   any time, but the public PDF is published with ~1-week retention (2026-10-21
   was the latest table today), so §6's PDF hash will stop matching once JPX
   drops that file (expected after ~2026-10-29). Re-check then against the then
   current daily file, not this hash.
4. **Scratch-only evidence.** The ZIP/PDF, dumps and scripts used here live in
   scratch and are disposable; only the numbers in this document are durable.

## 9. Confirmation commands (reviewer)

```bash
# 1. artifact hashes still reproduce (expect the hashes in §2)
curl -sS -o /tmp/regr_sample.zip 'https://www.jpx.co.jp/markets/paid-info-equities/reference/t13vrt000001k10u-att/sample_outstanding_margin.zip'
sha256sum /tmp/regr_sample.zip        # 08e9209a…b368db47d
unzip -p /tmp/regr_sample.zip 'Web&DFS/JP_OutstandingMarginTradingByIssue.csv' | sha256sum
                                      # 6b1bf5c2…215828cd == tests/fixtures/jpx/JP_…

# 2. independent re-parse + parent-DB comparison (no project imports)
#    scripts: /root/.hermes/profiles/worker-go/cache/scratch/jpx/regr_independent_parse.py
#             /root/.hermes/profiles/worker-go/cache/scratch/jpx/regr_rerun_compare.py
.venv/bin/python <scratch>/regr_independent_parse.py | head -40   # digest e7206a4c…, 0 mismatches

# 3. product-path re-ingest into a throwaway DB (expect 10 rows, 1 date, replacement)
export YOWAYOWA_DATABASE_URL=sqlite:////tmp/regr.db YOWAYOWA_MODE=personal
.venv/bin/yowayowa jpx-margin-ingest '<scratch>/sample/Web&DFS/JP_OutstandingMarginTradingByIssue.csv' --source-url '<zip url>#Web&DFS/JP_OutstandingMarginTradingByIssue.csv'
sqlite3 /tmp/regr.db "select count(*), count(distinct application_date) from jpx_margin_balances;"   # 10|1

# 4. contract gate (expect 401)
curl -sS -i 'https://api.jquants-pro.com/v2/markets/margin_interest?code=86970' | head -6

# 5. test suite
.venv/bin/python -m pytest tests/test_jpx_margin.py -q     # 32 passed
```

## 10. Artifacts and hygiene

- Durable output of this task: this document (plus the one-line references added
  to `docs/README.md` and `docs/INDEX_ja.md`). No production code changed.
- Scratch (disposable):
  `/root/.hermes/profiles/worker-go/cache/scratch/jpx/` — sample ZIP, extracted
  CSVs, public PDF + `pdftotext` dump, parent `evidence.db`, re-run
  `regr_reingest.db`, `regr_*.py` scripts, `regr_result.json` /
  `regr_rerun.json` / `regr_pdf_check.json`.
- No credentials were used or printed; the public PDF was fetched once today for
  the hash/value check and is not redistributed.
