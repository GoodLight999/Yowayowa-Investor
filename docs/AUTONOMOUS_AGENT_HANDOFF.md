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

## Recently completed strategy enrichment

### SEC exact/bounded enrichment

U.S. issuers now use a typed SEC strategy supplement:
- current assets and total liabilities must share the same period end, accession, USD unit, and instant balance-sheet scope;
- only direct `us-gaap:MarketableSecuritiesNoncurrent` is accepted as the exact investment-securities add-on;
- current marketable securities are never added because they are already inside current assets;
- vague or overlapping investment concepts are not summed to improve apparent coverage;
- absence of the direct noncurrent fact preserves the conservative NCR lower bound and cash-neutral P/E upper bound.

The API integration and source-isolation behavior are covered by tests.

### International Yahoo conservative enrichment

Personal-mode non-SEC listings now receive a period-consistent Yahoo strategy supplement:
- current assets and total liabilities must share one statement period, currency, and frequency;
- a newer unmatched statement value is not mixed with an older balance-sheet counterpart;
- Yahoo's broad investment rows are deliberately not treated as a universal IFRS/non-US equivalent of Kiyohara investment securities;
- where an evidence-backed exact mapping is unavailable, the evaluator exposes the conservative net-cash lower bound rather than guessing.

This also gives Japanese listings a safe conservative fallback when an exact EDINET supplement is unavailable.

## Current highest-priority engineering task

### P0 — product completion audit and closure

The project has accumulated substantial vertical functionality. Do not keep extending infrastructure or accounting aliases indefinitely while the product remains perceived as unfinished.

Execution:
1. compare the canonical Notion specification with the actual current browser/API/CLI surfaces;
2. identify missing, weak, disconnected, or duplicated user workflows;
3. prioritize gaps that materially reduce the need to jump between external finance sites;
4. complete each selected workflow end-to-end, including failure behavior and real-browser verification;
5. keep financial/provider correctness boundaries intact while avoiding speculative framework work;
6. update this handoff after each completed vertical so the remaining completion queue shrinks visibly.

## Scheduled high-priority data integration

### JPX daily margin balances — available from 2026-09-28 if migration proceeds

Tokyo Stock Exchange has announced that the all-issue margin-balance publication currently available weekly will become daily, with prior-business-day balances published around 16:00 each business day. The announced output includes sales/purchase balances, daily changes, ratio to listed shares, negotiable/standardized margin breakdowns, and balance values as well as share counts.

Once the production format is live:
1. inspect the real JPX output and usage/redistribution terms;
2. implement a provenance-aware JPX margin provider and persistent daily history;
3. surface margin supply/demand on Japanese instrument pages;
4. add useful screener/comparison fields and abrupt-change alerts;
5. keep personal-use acquisition rights distinct from public redistribution rights.

Canonical product/roadmap details are also recorded in Notion.

## Remaining strategic constraints

1. **Evidence-backed world/IFRS exact enrichment only** — add provider/taxonomy-specific exact mappings when semantics are demonstrably compatible; never invent a universal investment-securities mapping.
2. **Public market-data licensing path** — personal Yahoo/yfinance remains personal-only. Public launch needs redistributable/licensed market data without weakening provenance.
3. Higher-severity defects discovered in active workflows outrank planned feature work.

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

