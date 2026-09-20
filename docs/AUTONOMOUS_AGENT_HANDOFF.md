# Autonomous Agent Handoff

Last materially updated: 2026-09-20

This document is the compact operational handoff for a long-running autonomous coding agent (Hermes, Codex, or equivalent). Read `AGENTS.md` first; it is the stable contract. This file intentionally contains more current state and may change frequently.

## Current state

The active implementation is Draft PR #1:
https://github.com/GoodLight999/Yowayowa-Investor/pull/1

At the time this handoff was written:
- active branch: `agent/commercial-foundation`
- base: `main`
- latest verified product head: `5865c1c1a03d5e8105bbbc3ae7f47d0bbea45418`
- production: https://yowayowa-investor.vercel.app
- GitHub Actions run #948: verify SUCCESS, real-Chrome browser E2E SUCCESS (64 browser tests), deploy-production SUCCESS
- Vercel deployment `dpl_AJbzxxR9io6h9u88V7Xx72i3STJw`: READY and promoted to the production alias
- production `/v1/health` and `/v1/settings/status` returned HTTP 200 after deployment
- recent Vercel runtime-error scan after deployment was clean
- production currently reports `edinet: false`: the automatic EDINET cron bootstrap code is deployed but cannot run unattended until a server-side EDINET key is configured
- durable production database status is not proven by the current health surface; do not assume the EDINET filing index persists across Vercel instances until `DATABASE_URL`/storage is verified

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

## Recently completed engineering task

### EDINET shared recent-history bootstrap and stale-index guard

Implemented and verified:
- shared EDINET daily-list index bootstrap; no per-candidate historical scanning;
- default target: latest 550 completed Japan calendar days;
- each maintenance run fills at most 31 missing days, newest-first;
- repeated runs are resumable and only fetch still-missing dates;
- manual `edinet index-sync` remains the fast explicit bootstrap path;
- exact Kiyohara strategy enrichment refuses to call an annual report “latest” unless every calendar day from that filing date through the latest completed Japan day has been indexed;
- incomplete coverage falls back to conservative strategy metrics rather than silently using a stale report;
- coverage state is exposed in cron operation output and documented in `docs/EDINET.md`.

Operational activation is still external:
- production currently has no server-side EDINET API key;
- durable database configuration must be verified before relying on unattended hosted index persistence.

## Current highest-priority engineering task

### P0 — US exact strategy enrichment from SEC filings

Goal:
extend the strategy evaluator so U.S. issuers can receive an exact or defensibly bounded Kiyohara-style net-cash enrichment from SEC Company Facts / filings without double-counting assets.

Correctness constraints:
- do not add “marketable securities” values already included in current assets;
- distinguish current vs non-current investments and securities;
- require compatible filing period, scope, units/currency, and provenance for arithmetic;
- taxonomy aliases must be evidence-backed and narrow; do not sum vaguely similar concepts merely to improve coverage;
- if a clean exact mapping is unavailable, preserve the existing conservative NCR and expose a bound rather than guessing;
- derived cash-neutral P/E must preserve the exact/bound semantics already implemented for Japan.

Execution:
1. inspect current SEC normalization and canonical financial definitions;
2. research official US-GAAP concepts and representative issuer filings;
3. design a typed supplement boundary analogous to EDINET rather than embedding SEC quirks in strategy math;
4. implement service/API/CLI/UI behavior only where it adds user value;
5. test overlapping concepts, missing facts, differing periods, units, restatements, and duplicate contexts;
6. pass full verify + Chrome E2E + production verification.

## Queue after US exact enrichment

1. **World/IFRS strategy enrichment** — taxonomy/provider-specific mappings with fail-closed semantics; never invent a universal accounting mapping.
2. **Public market-data licensing path** — personal Yahoo/yfinance remains personal-only. Public launch needs redistributable/licensed market data without weakening provenance.
3. Continue product completeness work from the canonical Notion specification: research/news/calendars/alerts, portfolio analytics, valuation/KPI depth, and cross-asset support where still incomplete.

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

## Minimal launch instruction for a fresh autonomous agent

Give the agent repository access and this instruction:

```text
Work autonomously on Yowayowa-Investor. First read AGENTS.md and docs/AUTONOMOUS_AGENT_HANDOFF.md, then resolve the current Draft PR #1 head and verify repository/deployment state. Execute the current P0 end-to-end without waiting for micromanagement: research authoritative sources, make correctness constraints explicit, implement, test, debug, verify real browser behavior, deploy through the existing gated pipeline when appropriate, verify production, update canonical docs/Notion, and rewrite the handoff so the next agent can continue. Ask the user only for genuinely external credentials/authorization or an irreducible product decision.
```

