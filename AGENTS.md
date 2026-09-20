# AGENTS.md

This repository is designed to be developed by autonomous coding agents. Treat this file as the repository-level operating contract.

## Product objective

Build Yowayowa-Investor into a private-operator-first investment research and execution workstation whose primary goal is to maximize the operator's useful information advantage and automation while preserving financial correctness and strong security. The general-public SaaS profile is secondary and may be intentionally limited.

The canonical product specification and implementation history live in Notion:
https://app.notion.com/p/sugoi-daizu/Yowayowa-Invester-3bab2a2d631a80f6844fee5f8b76d64e

Do not wait for the user to micromanage implementation. Research, design, implement, test, debug, document, and verify autonomously whenever the product direction is already established.

## Non-negotiable engineering invariants

1. **API-first modular monolith.** Browser UI, REST API, CLI, tests, and agents must share domain/service logic. Do not put business rules only in JavaScript/templates.
2. **Financial correctness before convenience.** Missing data is not zero. Units, currencies, periods, restatements, and provenance must be explicit and fail closed when arithmetic would be misleading.
3. **Provenance is part of the data model.** Preserve provider, source URL, license class, retrieved-at, and as-of information through normalization and derived metrics.
4. **Full/Operator mode is the primary product.** Personal-only sources, authenticated scraping, private protocols, local application bridges, and broker control are valid first-class capabilities in personal mode. Public mode remains a separate limited profile and must fail closed on rights/security boundaries.
5. **Official/maintained sources before reinvention.** Prefer official APIs and mature maintained libraries when licensing and quality fit. Build custom code where it creates product value or is necessary for correctness.
6. **No user-facing development notes.** Internal implementation rationale, debugging notes, developer caveats, provider plumbing, and internal provenance mechanics do not belong in normal UI unless the user needs them to operate or correctly interpret the feature. Put them in logs, API metadata, developer/admin surfaces, or docs.
7. **AI is additive, not the source of truth.** Deterministic tools and source-backed data remain usable without AI. Separate sourced facts from model inference. Natural-language operations should resolve to transparent structured operations.
8. **Do not optimize for an MVP.** Avoid deliberately weak placeholders, demo-only architecture, person-month framing, and staged shortcuts when a robust implementation is feasible now. Also avoid speculative infrastructure that has no current product value.
9. **Success is not evidence of correctness.** A green request, plausible number, or rendered page is insufficient. Verify definitions, edge cases, source semantics, and real browser/deployment behavior.
10. **Broker control is protocol-first, not browser-automation-first.** Prefer official/local programmable interfaces, then authorized private protocols, then structured scraping. UI click automation is only a fallback. Never bypass authentication/MFA/access controls.
11. **Preserve the product design identity.** Read `DESIGN.md` before substantial UI work. This is an analyst terminal / research notebook, not a generic SaaS dashboard.

## Working style for autonomous agents

- Start by inspecting current code, tests, docs, PR head, and relevant official documentation. Do not assume a memory summary is newer than the repository.
- Make reasonable product/engineering decisions without asking the user when the existing principles determine the answer.
- Ask only when blocked by a genuinely external decision, missing credential/authorization, destructive action with material consequences, or irreducibly ambiguous product intent.
- Prefer end-to-end completion over leaving TODO scaffolding.
- Fix defects discovered during implementation when they are causally related or threaten correctness.
- Keep commits coherent and explanatory.
- Never commit secrets, tokens, API keys, downloaded credentials, or local environment files.

## Required verification

For code changes, at minimum run or obtain equivalent CI evidence for:

```bash
make verify
```

GitHub CI is stricter than the local Makefile and also runs real Chrome browser E2E. Before calling browser/UI work complete, require the browser job to pass.

When a change is intended for production:
1. confirm the relevant GitHub CI jobs are green;
2. confirm the Vercel production deployment reaches READY;
3. fetch the affected production API/page and verify the changed behavior is actually present;
4. inspect recent runtime errors for regressions.

A deployment being READY is not by itself proof that the feature works.

## Current delivery topology

- Canonical implementation branch while Draft PR #1 remains open: `agent/commercial-foundation`
- Base branch: `main`
- Draft PR: https://github.com/GoodLight999/Yowayowa-Investor/pull/1
- Production: https://yowayowa-investor.vercel.app
- GitHub Actions deploys the Draft PR head to Vercel production only after verify + real-browser E2E pass.
- The Vercel credential is stored as the GitHub Actions secret `VERCEL_TOKEN`. Never print or move it.

Do not blindly work from `main` while PR #1 is the active product branch. Resolve the current PR head first.

## High-value documentation

Read these before changing the corresponding subsystem:

- `README.md` — product/repository entry point
- `DESIGN.md` — UI and interaction invariants
- `docs/ARCHITECTURE.md` — application structure
- `docs/OPERATOR_MODE.md` — Full/Operator scraping and broker-control contract
- `docs/DATA_POLICY.md` — provenance and data-rights policy
- `LICENSE_POLICY.md` — dependency/source licensing constraints
- `docs/EDINET.md` — Japanese disclosure ingestion/normalization
- `docs/SEC.md` — U.S. EDGAR normalization and same-filing strategy enrichment
- `docs/ESTAT.md` — Japanese government statistics integration
- `docs/AUTONOMOUS_AGENT_HANDOFF.md` — current execution queue and handoff state

## Strategy preset rules

The built-in `清原達郎モード` is a first-class strategy preset. Do not label it “unofficial”.

Keep the two net-cash concepts distinct:
- Yowayowa conservative net cash = current assets - liabilities
- Kiyohara net cash = current assets + investment securities * 0.7 - liabilities

Do not mix balance-sheet components from incompatible filings/currencies/sources merely to fill a value. Exact enrichment must preserve accounting and currency consistency; otherwise expose a conservative bound.

## Definition of done

A substantial task is complete only when:
- implementation is integrated across the necessary API/service/UI/CLI surfaces;
- tests cover both normal and failure/boundary behavior;
- static/type checks pass;
- real browser behavior is verified when applicable;
- production is checked when the task changes deployed behavior;
- durable docs/Notion are updated when architecture, operational procedure, or product semantics changed;
- the next autonomous agent can identify the next task without reconstructing the entire conversation.
