# Autonomous Agent Handoff

Last materially updated: 2026-09-20

This document is the compact operational handoff for a long-running autonomous coding agent (Hermes, Codex, or equivalent). Read `AGENTS.md` first; it is the stable contract. This file intentionally contains more current state and may change frequently.

## Current state

The active implementation is Draft PR #1:
https://github.com/GoodLight999/Yowayowa-Investor/pull/1

At the time this handoff was written:
- active branch: `agent/commercial-foundation`
- base: `main`
- latest known product head: `7e7417f55372d846684e4e1999c55c959d429696`
- production: https://yowayowa-investor.vercel.app
- production deploy for that head was verified READY
- `/v1/health`, `/`, `/discover`, and `/v1/strategy-presets` returned HTTP 200 after deployment
- recent Vercel runtime-error scan after deployment was clean

Always re-resolve the PR head before editing; the SHA above is a checkpoint, not a branch pin.

## Recent product work

### Kiyohara strategy preset

Built-in preset ID: `kiyohara_global_value_growth`

User-facing name:
- Japanese: `清原達郎モード`
- English: `Tatsuro Kiyohara mode`

Global discovery:
- one region at a time;
- discover low-P/E candidates;
- sort by market cap ascending within the chosen region rather than FX-converting a Japanese fixed small-cap ceiling;
- then evaluate net cash, growth, FCF, and related quality metrics.

Net-cash definitions:
- Yowayowa conservative net cash = `current_assets - liabilities`
- Yowayowa conservative NCR = conservative net cash / market cap
- Kiyohara net cash = `current_assets + investment_securities * 0.7 - liabilities`
- Kiyohara NCR = Kiyohara net cash / market cap
- cash-neutral P/E = `P/E * (1 - NCR)` only for NCR < 1
- NCR >= 1 => cash-neutral P/E is undefined/null and UI uses `NCR≥1`
- when investment securities are unavailable, the Kiyohara metric may fall back to the conservative value and must be marked as a lower bound; cash-neutral P/E then becomes an upper bound.

Japanese exact enrichment uses EDINET `jppfs_cor:InvestmentSecurities`. Current assets, liabilities, and investment securities must come from the same EDINET annual report and all use JPY. Do not combine EDINET investment securities with Yahoo balance-sheet terms.

### Production delivery

Vercel was not natively Git-connected. A gated GitHub Actions production deployment path now exists:
- verify + browser E2E must pass first;
- Actions authenticates with repository secret `VERCEL_TOKEN`;
- source is sent to Vercel for remote production build;
- do not reintroduce local `vercel build --prod` in GitHub Actions unless `uv`/secret semantics are intentionally handled.

This path was exercised end to end successfully.

## Current highest-priority engineering task

### P0 — Make exact EDINET strategy enrichment automatic at scale

Current limitation:
`strategy_edinet.py` can compute exact Japanese Kiyohara NCR only when the relevant annual report is already present in the local `EdinetFilingRecord` index.

Do **not** solve this by scanning 365 dates for every candidate. EDINET documents API is date-oriented, so naive per-candidate historical scans are an unacceptable N×days request pattern.

Desired outcome:
- deterministic historical/backfill mechanism for the EDINET filing index;
- efficient issuer/security-code -> latest annual report lookup after bootstrap;
- durable storage, resumability/idempotence, provenance, and clear freshness semantics;
- compatible with local development and hosted/serverless deployment constraints;
- exact Kiyohara NCR becomes automatic for a large fraction of Japanese screened stocks when EDINET credentials/data are available;
- failures remain partial/fail-closed rather than corrupting the broader screen.

Before implementing, inspect:
- EDINET DB models and indexing commands/services;
- cron/maintenance paths;
- `docs/EDINET.md`;
- deployment storage assumptions (Vercel filesystem is ephemeral; durable indexes require the configured database);
- current request-scoped BYOK EDINET key flow.

Then implement the smallest robust architecture, tests, CLI/operations surface if needed, docs, CI, and production verification.

## Next queue after EDINET bootstrap

1. **US exact strategy enrichment** — map SEC concepts for non-current investments/marketable securities without double-counting current assets. Preserve filing/period/currency consistency and provenance.
2. **World/IFRS strategy enrichment** — taxonomy/provider-specific mappings with fail-closed semantics; never invent a universal accounting mapping.
3. **Public market-data licensing path** — personal Yahoo/yfinance remains personal-only. Public launch needs redistributable/licensed market data without weakening provenance.
4. Continue product completeness work from the canonical Notion specification: research/news/calendars/alerts, portfolio analytics, valuation/KPI depth, and cross-asset support where still incomplete.

Do not interpret this queue as permission to ignore a higher-severity defect discovered in the active path.

## UI constraint that is easy to regress

Normal user UI must not display development notes or internal implementation trivia.

Good user-facing qualification:
- “≥0.80×” because the value is a lower bound and that changes interpretation.

Bad normal-UI copy:
- “the 20× threshold is a Yowayowa implementation default rather than a fixed rule from Kiyohara” when the filter itself already exposes 20× and the sentence exists mainly to explain developer provenance.

Keep such implementation rationale in docs/API metadata, not the normal UI.

## Autonomous execution loop

For each major task:

1. Resolve current PR head and deployment state.
2. Read the relevant code, tests, docs, and authoritative external docs.
3. Write down the correctness constraints before changing code.
4. Implement end-to-end, including API/service/UI/CLI surfaces where the capability belongs.
5. Add tests for failure modes and ambiguous financial-data cases, not just happy paths.
6. Run `make verify` and obtain GitHub browser-E2E evidence.
7. If deployed behavior changed, follow the production deploy to READY and query the real affected endpoint/page.
8. Check recent runtime errors.
9. Update durable docs and canonical Notion when semantics/architecture/operations changed.
10. Leave this handoff with a precise next task.

Do not stop merely because code compiles, tests are green, an endpoint returns 200, or Vercel says READY.
