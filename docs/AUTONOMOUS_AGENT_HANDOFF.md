# Autonomous Agent Handoff — Hermes Primary

Updated: 2026-09-22

This is the operational handoff for Hermes or another long-running autonomous coding agent.

Read in this order:

1. `AGENTS.md`
2. `docs/PRIVATE_OPERATOR_ROADMAP.md`
3. this file
4. subsystem docs only when touching that subsystem

Canonical product specification and historical decision log:
https://app.notion.com/p/sugoi-daizu/Yowayowa-Invester-3bab2a2d631a80f6844fee5f8b76d64e

## Mission

Yowayowa-Investor is a **Private / Family Operator** investment research and execution system.

General-public SaaS development is frozen indefinitely unless the operator explicitly reopens it.

Do not spend autonomous development capacity on:
- public billing/subscriptions;
- advertising;
- affiliate flows;
- anonymous-user onboarding;
- public redistribution parity;
- public-provider parity;
- generic SaaS polish.

The system should maximize useful information advantage and automation for the operator/family while preserving financial correctness, provenance, interpretability, security, and safe execution.

Primary loop:

**discover -> investigate -> falsify -> compare -> size -> propose execution -> execute -> observe outcome -> recalibrate**

Current weighted Private / Family Operator v1 completion baseline: **68%**.

## Development ownership

Hermes is now the **primary autonomous development agent**.

ChatGPT remains useful for:
- product-direction discussion;
- architectural review;
- external research;
- debugging support;
- user-facing explanation;
- occasional targeted implementation.

Do not assume ChatGPT will maintain continuous execution state. Hermes must keep repository and Notion state sufficient to continue without reconstructing chat history.

### Autonomy rule

Do not wait for the operator except when a task truly requires external human action.

Examples:
- ChatGPT device-code authorization;
- broker login/MFA/device approval;
- obtaining a new credential;
- destructive or financially consequential live action requiring explicit approval;
- an irreducible product decision not settled by current product principles.

When blocked by one of these:
1. record the exact human action required;
2. leave a deterministic verification checklist;
3. move immediately to the highest-priority unblocked task.

Never let one OAuth/login/MFA step stop the entire development queue.

---

## Repository / delivery state

Repository:
`GoodLight999/Yowayowa-Investor`

Active implementation branch:
`agent/commercial-foundation`

Base:
`main`

Draft PR:
https://github.com/GoodLight999/Yowayowa-Investor/pull/1

Production:
https://yowayowa-investor.vercel.app

GitHub Actions deploys production only after verify + real-Chrome E2E succeed.

Repository secret:
`VERCEL_TOKEN` already exists. Never ask the operator for it, print it, move it, or commit it.

### Verified production code checkpoint

The most recent fully verified production-code checkpoint before this handoff refresh is:

`6ad34406c6da2f132821aa2863e517c5fdd1704b`

Production deployment:
`dpl_CVaZAa4zL4rP4nbMKF4wwSa6ebXf`

At that checkpoint:
- verify succeeded;
- real Chrome E2E succeeded;
- production deploy succeeded;
- production `/internal/debug/runtime` reported the matching source revision;
- production `/v1/ai/codex/status` reported hosted Codex bridge enabled;
- request correlation / runtime diagnostics were working.

Subsequent commits may be documentation-only. Always resolve the live branch head and current CI/deployment before editing.

---

## Product architecture invariants

### 1. API-first

FastAPI/service logic is the canonical boundary.

Browser UI, REST API, CLI, tests, and AI agents must share service/domain logic.

Do not put meaningful business logic only in JavaScript/templates.

Every meaningful GUI capability should have a machine-usable service/API path where appropriate.

OpenAPI is not merely human documentation. It is also an agent contract.

### 2. Financial correctness

Missing data is not zero.

Do not combine:
- incompatible reporting periods;
- incompatible currencies;
- incompatible filings;
- vague accounting concepts merely to improve apparent coverage.

Derived metrics must remain reproducible and provenance-aware.

### 3. Provenance

Preserve:
- provider;
- source;
- source URL where available;
- retrieved-at;
- as-of/effective date;
- license/access class;
- approximation/lower-bound/upper-bound semantics.

### 4. AI-led but interpretable

AI should lead:
- candidate discovery;
- prioritization;
- evidence gathering;
- falsification;
- next-research-step selection;
- portfolio/execution proposal formation.

AI must not become the untraceable source of truth.

Keep facts, deterministic calculations, and model inference separable.

### 5. Private connectors are first-class

Scraping/private protocols/authenticated sessions are valid architecture in Private / Family Operator mode.

Absence of a public API is not a reason to discard a useful source.

Authentication/MFA/access controls are not bypass targets.

---

## Important implemented subsystems

### Core research workstation

Substantial support already exists for:
- instrument search;
- instrument pages;
- market overview;
- charts and technical indicators;
- financial statements/facts;
- valuation;
- screening;
- comparison;
- watchlists;
- portfolio analytics;
- news;
- calendar/events;
- macro data;
- alerts;
- provenance/source display.

Do not reimplement these blindly. Audit current code first.

### Kiyohara strategy

Built-in strategy:
`kiyohara_global_value_growth`

Japanese:
`清原達郎モード`

Key definitions:

Yowayowa conservative net cash:
`current_assets - liabilities`

Kiyohara net cash:
`current_assets + investment_securities * 0.7 - liabilities`

Cash-neutral P/E:
`P/E * (1 - NCR)` only when NCR < 1.

NCR >= 1 => cash-neutral P/E is undefined/null.

If investment securities cannot be established exactly:
- NCR may remain a conservative lower bound;
- cash-neutral P/E may remain an upper bound.

Never erase these semantics merely to produce a complete-looking number.

### Interpretable research priority

Current conceptual factor groups:
- Value;
- Growth;
- Quality;
- Evidence.

The score is **research-attention allocation**, not expected return.

Historical observations are versioned.

Do not silently mutate old scoring semantics; new calibrated rules require new scoring versions.

### Point-in-time strategy history / outcomes

Foundation exists for:
- point-in-time strategy snapshots;
- scoring version;
- same-day deduplication;
- forward outcomes;
- next-trading-session entry logic;
- benchmark-relative excess return;
- AI access to strategy history/outcomes.

This is the base for later walk-forward calibration.

### AI infrastructure

Implemented/partially implemented:
- OpenAI-compatible provider path;
- Anthropic-style provider path;
- BYOK;
- structured agent tool loop;
- tool trace;
- operation proposals;
- strategy triage;
- strategy history/outcomes tools;
- external-AI prompt packet generation;
- hosted Codex bridge;
- ChatGPT device-code authentication UI/route;
- OpenAPI contract protection for agent-facing endpoints.

Important:
- Codex ChatGPT subscription usage must not silently fall back to OpenAI API-key billing.
- Hosted browser-authenticated Codex remains subject to real-account end-to-end verification.

### Observability / debugging

Maintain:
- `/internal/debug/runtime`;
- `x-yowayowa-request-id`;
- source-revision visibility;
- structured runtime logging without secrets;
- request -> runtime log -> deployment -> exact SHA traceability.

Do not log:
- API keys;
- cookies;
- Authorization headers;
- DB URLs;
- raw OAuth credentials;
- broker credentials;
- trading passwords.

### Private acquisition foundation

Existing:
- `services/private_http.py`
- `operator_bridge/web_session.py`
- private connector descriptors/models
- same-origin authenticated private HTTP
- JSON acquisition
- HTML scraping boundary
- persistent Chromium profile
- browser-context cookie sharing with same-session HTTP requests

The foundation is useful, but private-data acquisition is still underexploited.

### Broker foundation

Existing:
- provider-neutral broker models;
- broker execution interlocks;
- MARKET SPEED II RSS mapping;
- Windows Excel/xlwings bridge;
- Rakuten RSS connector;
- persistent browser-session foundation.

The real Linux Rakuten Web connector is not finished.

---

## Current roadmap

The authoritative current roadmap is:

`docs/PRIVATE_OPERATOR_ROADMAP.md`

Do not invent a parallel roadmap.

### Immediate autonomous sequence

#### P0 — Close AI access without blocking

Complete automated verification around hosted Codex.

Real ChatGPT authorization requires the operator. If unavailable:
- write the exact manual verification steps;
- continue immediately.

Do not spend another long cycle polishing provider plumbing after the path is operational.

#### P1 — Exploit scraping/private access

This is currently underdeveloped and high value.

Work in this order:

1. reusable authenticated acquisition/debug framework;
2. Rakuten Web read-side ingestion;
3. generic company-IR monitoring and structured extraction;
4. legitimate authenticated private information sources where useful.

Prefer:
official API -> internal JSON/XHR -> GraphQL/WebSocket -> downloads -> embedded state -> HTML -> DOM.

Every connector needs:
- auth/session assumptions;
- provenance;
- freshness/cache semantics;
- parser/schema assumptions;
- explicit failure state;
- reauthentication state;
- debug/API inspection surface;
- snapshot/diff support where valuable.

#### P2 — Linux broker execution

First real broker path:
Rakuten Securities Web.

Must cover Japanese and U.S. equities.

Read-side before write-side.

Then:
- preview;
- submit;
- status;
- cancellation;
- fill reconciliation;
- portfolio update.

Live submission must retain:
- explicit arming;
- notional limit;
- daily order limit;
- idempotency;
- duplicate-submit protection;
- append-only audit trail.

#### P3 — Profitability learning loop

Build aggregate calibration over point-in-time research snapshots/outcomes.

Start simple and statistically honest:
- count;
- median/mean total return;
- median/mean excess return;
- positive-excess hit rate;
- score/factor deciles;
- rank IC / Spearman where appropriate;
- minimum-sample thresholds;
- confidence/bootstrap intervals where useful.

Use walk-forward/out-of-sample evaluation.

Do not optimize and evaluate new weights on the same sample.

AI should be able to inspect calibration evidence and state uncertainty.

#### P4 — Japan edge

EDINET:
- verify durable production storage;
- verify unattended maintenance/key operation;
- preserve same-filing/currency constraints.

JPX daily margin:
- announced start 2026-09-28 if migration proceeds;
- inspect live production format before writing final parser;
- persist daily history;
- instrument-page supply/demand;
- screener/compare fields;
- abrupt-change alerts;
- point-in-time snapshots for outcome analysis.

#### P5 — Product completion audit

Audit real workflows rather than feature names.

Find what still forces unnecessary hopping among:
- TradingView;
- Yahoo Finance;
- Investing.com;
- TipRanks;
- broker web;
- company IR pages.

Do not clone everything. Remove high-friction gaps.

#### P6 — Private / Family Operator v1 freeze

Freeze only after:
- end-to-end workflows work;
- machine/UI parity holds;
- broker execution is safe/audited;
- provenance is stable;
- CI and real Chrome are green;
- hosted portions are production verified;
- local operator components are real-machine verified;
- runtime errors are clean;
- docs/Notion/handoff are current.

---

## Scraping/private-data direction

Private scraping permission materially changes product strategy.

The goal is no longer:

> find an official/public API or omit the data.

The goal is:

> if the operator can legitimately access decision-relevant information, acquire it through the most robust available transport and preserve provenance.

High-value targets:

### Broker context

Ingest:
- balances;
- buying power;
- cash;
- margin availability;
- positions;
- acquisition prices;
- unrealized P/L;
- open orders;
- executions;
- fees;
- margin/collateral state.

This should feed portfolio-aware AI research and sizing.

### Company IR

Monitor:
- earnings presentations;
- guidance revisions;
- dividend revisions;
- buybacks;
- medium-term plans;
- monthly operating data;
- backlog/bookings;
- ARR/subscriber metrics;
- store counts;
- utilization;
- shipments;
- company-specific KPI tables.

Desired pipeline:

**discover source -> detect new material -> acquire -> extract -> diff -> store provenance -> attach to instrument timeline -> AI investigates change**

### Private/subscription sources

Allowed when:
- operator/family legitimately has access;
- use remains personal/private;
- credentials/session state is handled securely;
- source terms/security posture are respected.

Do not build redistribution features around private access.

---

## API / agent contract

Protect machine usability.

At minimum, preserve discoverability and functionality of:
- AI chat/status;
- Codex auth/session status;
- external prompt packets;
- strategy preset evaluation;
- strategy snapshots;
- strategy forward outcomes;
- operation planning;
- portfolio/watchlist operations;
- data/provider diagnostics.

When adding a GUI feature:
1. identify service/domain operation;
2. expose machine path where useful;
3. test it independently from browser rendering.

Do not let HTTP-specific concerns break direct Python/service calls.

---

## External actions that may block only one verification step

### ChatGPT hosted Codex

Human action:
- complete device-code authorization with the operator's ChatGPT account.

Hermes responsibility:
- prepare flow;
- expose exact instructions;
- capture non-secret status;
- test everything around it;
- continue other roadmap tasks if operator is unavailable.

### Rakuten broker session

Human action:
- legitimate login/MFA/device approval;
- explicit live-trading approval before financially consequential tests.

Hermes responsibility:
- build read-only path first;
- create session diagnostics;
- never request plaintext passwords in chat;
- never bypass MFA/CAPTCHA/device approval;
- provide exact real-machine verification checklist;
- continue unrelated work if operator is unavailable.

### EDINET / other credentials

If a credential is genuinely absent:
- report exact missing capability;
- implement/test with fixtures or browser/session BYOK where appropriate;
- continue other work.

Do not repeatedly ask for a credential that is already configured.

---

## Verification contract

For every substantial code change:

1. resolve current branch head;
2. inspect relevant code/tests/docs;
3. implement service/API/UI/CLI surfaces as appropriate;
4. add boundary/failure tests;
5. obtain `make verify` equivalent CI evidence;
6. obtain real Chrome E2E for browser changes;
7. when hosted behavior changes, wait for gated production deploy;
8. query the affected production API/page;
9. verify source revision;
10. inspect runtime errors;
11. update durable roadmap/handoff/Notion for material architecture or product-direction changes.

Do not declare success merely because:
- code compiles;
- unit tests pass;
- an endpoint returns 200;
- Vercel says READY.

---

## Documentation hygiene

Keep this handoff operational and current.

After each major milestone:
- update only current checkpoint, current blockers, and next work;
- remove obsolete P0 items rather than stacking contradictory histories;
- put long historical notes in canonical Notion if they matter;
- do not require the next agent to reconstruct old conversations.

`docs/PRIVATE_OPERATOR_ROADMAP.md` is the roadmap.
This file is the execution handoff.

---

## Hermes launch instruction

Use this verbatim when starting a fresh Hermes context:

```text
You are the primary autonomous development agent for Yowayowa-Investor.

Repository: GoodLight999/Yowayowa-Investor
Active branch: agent/commercial-foundation
Base: main
Draft PR: #1

First read:
1. AGENTS.md
2. docs/PRIVATE_OPERATOR_ROADMAP.md
3. docs/AUTONOMOUS_AGENT_HANDOFF.md
4. the canonical Notion Yowayowa-Investor page if available.

The product is Private / Family Operator first. General-public SaaS work is frozen unless the operator explicitly reopens it.

Work autonomously. Do not wait for micromanagement. Resolve the current branch/CI/deployment state, then continue the highest-priority unblocked roadmap task end-to-end. If OAuth, broker login/MFA, a credential, or explicit live-trading approval requires the operator, record the exact human step and verification checklist, then immediately continue the highest-priority task that does not require that action.

Preserve API-first architecture, financial correctness, provenance, agent/API parity, real-browser verification, and production/runtime verification. Scraping/private protocols/authenticated sessions are legitimate first-class Private Operator capabilities; prefer structured internal transports over DOM automation when robust.

Do not add public SaaS features, billing, advertising, affiliate flows, anonymous-user polish, or public redistribution work.

At the end of every major milestone, update docs/AUTONOMOUS_AGENT_HANDOFF.md and the canonical Notion page so another agent can continue without conversation history.
```
