# OpenAPI baseline and drift gate

The OpenAPI document is generated, so it is not committed: `openapi.json` stays
git-ignored (`.gitignore:14`). That is why the contract is pinned by a committed
baseline instead, and why CI now fails when the generated document and the
baseline disagree.

Background: `docs/OPENAPI_DRIFT_20261023.md` §4 P2 measured 12 days of silent
drift — the previous workflow only asserted that the freshly generated file was
non-empty (`.github/workflows/ci.yml:64,66`), so two implementation-only
operations (`DELETE /v1/portfolios/{portfolio_id}`, `POST /v1/sizing/quote-idea`)
and one parameter were invisible to every client and to CI.

## Files

| File | Role |
| --- | --- |
| `docs/openapi_baseline.json` | Committed baseline of `app.openapi()`. Canonical JSON: sorted keys, 2-space indent, UTF-8, trailing newline. |
| `src/yowayowa/openapi_baseline.py` | Comparison logic shared by the script, CI and pytest: canonical serialization, operation inventory, JSON-pointer difference report. |
| `scripts/check_openapi_baseline.py` | Gate entry point. Exit 0 = identical, 1 = drift, 2 = baseline missing. |
| `tests/test_openapi_baseline.py` | The same gate inside `pytest`, plus pins for the difference report and the operations the baseline absorbed on first registration. |

## Where the gate runs

- CI: `.github/workflows/ci.yml`, `verify` job, step `OpenAPI baseline drift gate`
  (immediately after `yowayowa export-openapi`). The `browser` and
  `deploy-production` jobs are unchanged.
- Local / `make verify`: `make openapi-check` (added to the `verify` prerequisites).
- `pytest`: `tests/test_openapi_baseline.py::test_committed_baseline_matches_generated_document`.

Comparison is semantic on the parsed JSON, so key order inside the file does not
affect the verdict. The baseline file itself is additionally kept canonical
(`test_committed_baseline_is_canonically_formatted`) so that regenerating it
without a contract change produces a byte-identical file and an empty diff.

## Updating the baseline after an intentional contract change

1. Change the API/service code first.
2. Regenerate the baseline: `make openapi-baseline`
   (equivalently `python scripts/check_openapi_baseline.py --write`).
3. Review `git diff docs/openapi_baseline.json`. That diff is the record of the
   contract change; read it as such, not as an incidental artifact.
4. Commit the code change and the baseline **together**, and describe the
   contract change (added / removed / widened operations and fields) in the
   commit message or PR body.
5. If the change removes an operation, a parameter or a field, treat it as a
   break: say so explicitly in the change description.

Do not hand-edit `docs/openapi_baseline.json`, do not regenerate it in a commit
that contains no API change, and never make the gate pass by skipping it
(`|| true`, `continue-on-error`) or by comparing a baseline generated from the
same tree at the same moment — the file in VCS is the reference.

## What this gate deliberately does not do

- It does not repair security-boundary drift. The missing `require_api_token` on
  the `orderbook` router, the absent `components.securitySchemes`, the
  `_authorize_cron` fail-open path and the unauthenticated
  `/internal/debug/runtime` remain tracked separately
  (`docs/OPENAPI_DRIFT_20261023.md` §9 items 1–4). The gate only detects
  contract drift; it does not decide that the current contract is correct.
  - Update 2026-10-24 (t_697a94c4): the §9 item 4 exposure of build
    identification (`source` / `vercel`) on unauthenticated
    `GET /internal/debug/runtime` responses is fixed in code — anonymous
    callers now receive only `{"status", "request_id"}` and the full body is
    returned solely with the operator Bearer token. The route stays
    `include_in_schema=False`, so the schema baseline is unchanged (gate
    re-run after the fix: 155 operations, match). See
    `docs/P5_DEBUG_RUNTIME_MINIMAL_20261024.md`. The other §9 items remain
    open.
- It does not version the contract: `info.version` stays `0.1.0`
  (§9 item 7, still open).
- It does not track `openapi.json` in VCS; that artifact stays local.
