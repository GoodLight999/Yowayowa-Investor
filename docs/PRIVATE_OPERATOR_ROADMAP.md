# Private / Family Operator Roadmap

Updated: 2026-09-22

## Product target

Yowayowa-Investor is now a **private / family operator investment research and execution system**.

General-public SaaS development is frozen. Existing public/safe-mode code may remain as a compatibility and security boundary, but it is maintenance-only unless the operator explicitly reopens public-product work.

Primary objective:

> Improve the operator's ability to make profitable investment decisions by combining broad data acquisition, interpretable deterministic analysis, AI-led research, portfolio context, and safe execution.

The product is not complete when it merely has many screens. It is complete when the operator can move through:

**discover -> investigate -> falsify -> compare -> size -> propose execution -> execute -> observe outcome -> recalibrate**

without routinely leaving Yowayowa.

## Progress baseline

Weighted completion baseline for **Private / Family Operator v1: 68%**.

| Area | Weight | Approx. completion | Completion meaning |
|---|---:|---:|---|
| Core research workstation | 25% | 90% | Search, instruments, charts, fundamentals, valuation, screeners, comparison, markets, portfolio, news/calendar/macro, provenance |
| AI-led / interpretable operation | 20% | 75% | Structured tools, operation plans, strategy triage/history/outcomes, BYOK, Codex bridge, tool traces |
| Broker / execution plane | 20% | 30% | Domain/interlocks/RSS foundation exist; real Linux broker-web execution remains major work |
| Private data advantage / Japan edge | 15% | 65% | SEC/EDINET/macros exist; authenticated scraping and private sources are underused |
| Reliability / API / agent parity | 15% | 80% | FastAPI/OpenAPI/CLI/tests/browser CI/observability exist; parity must remain enforced |
| Product closure / UX audit | 5% | 60% | Broad surface exists, but workflow dead ends and drift still require audit |

The percentage is a product-completion estimate, not a commit-count or test-count metric.

---

## Roadmap rules

1. **Private / Family Operator first.** Do not spend roadmap capacity on public billing, ads, affiliate flows, anonymous-user polish, public redistribution work, or public-provider parity.
2. **API-first is mandatory.** Every meaningful GUI capability must also exist through reusable service logic and an API/CLI/agent path where appropriate.
3. **Scraping is a first-class acquisition method.** If the operator can legitimately access useful information in a browser, lack of a public API is not by itself a reason to omit it.
4. **Prefer structured transport over DOM parsing.** Acquisition order:
   1. official API;
   2. authenticated internal JSON/XHR/fetch;
   3. GraphQL/WebSocket;
   4. downloadable CSV/XLS/JSON;
   5. embedded structured state;
   6. HTML parsing;
   7. DOM automation as the final fallback.
5. **Authentication controls are not bypass targets.** Normal login/MFA/device approval remains operator-controlled.
6. **AI is not the source of truth.** Facts and deterministic metrics remain separate from inference. AI should lead the research workflow, not fabricate evidence.
7. **Do not block autonomous development on external approval.** When a task requires the operator to log in, approve OAuth, provide credentials, or interact with a broker, record the exact verification step and immediately continue with the highest-priority autonomous task that does not require that action.
8. **Do not optimize for feature count.** Prefer end-to-end closure of high-value workflows.

---

## P0 — Close current AI access and agent infrastructure

### Objective

Make AI access boring and reliable, then stop spending disproportionate time on provider plumbing.

### Current state

- OpenAI-compatible and Anthropic-style provider support exists.
- Hosted Codex bridge exists in Vercel Services.
- Browser-only ChatGPT device-code flow is implemented.
- Production reports Codex hosted bridge as enabled.
- External-AI research-packet generation exists.
- Agent tool catalog includes research, strategies, portfolio, alerts, and outcome history.
- OpenAPI contract protects core machine-facing endpoints.

### Remaining work

- Complete one real production ChatGPT device-code authorization.
- Run one real end-to-end Codex research request that calls Yowayowa tools.
- Verify credential refresh/session renewal.
- Verify failure behavior for expired/revoked ChatGPT session.
- Document the exact operator reconnect flow.

### Block rule

Real ChatGPT authorization requires operator action. If it is not immediately available, **do not wait**. Leave a short verification checklist and continue to P1.

### Exit criteria

- Real authenticated request succeeds in production.
- Tool calls and final response are traceable.
- No API-key billing fallback occurs.
- Reconnect flow is documented and tested where automation permits.

---

## P1 — Convert scraping permission into private data advantage

This is the largest underused opportunity created by the Private / Family Operator direction.

### P1A — General authenticated acquisition toolkit

Build a reusable acquisition layer around the existing private HTTP and persistent browser-session foundations.

Required capabilities:
- persistent local Chromium profile;
- authenticated-state detection;
- same-session HTTP/JSON requests;
- network request/response instrumentation;
- structured download capture;
- HTML parser adapters;
- parser/schema version metadata;
- cache/freshness policy;
- provenance and as-of timestamps;
- explicit stale/auth-expired/failure states;
- snapshot/diff support for changing pages;
- API/CLI surfaces for debugging individual connectors.

The acquisition framework must make it easy for an agent to inspect:
- what URL/transport was used;
- what was parsed;
- when the parser last succeeded;
- what changed since the previous snapshot;
- whether the source requires operator reauthentication.

### P1B — Broker read-side first

Before live execution, use authenticated broker sessions as a high-value read source.

First target: Rakuten Securities Web.

Ingest where legitimately available:
- account balances;
- buying power;
- cash/margin availability;
- positions;
- average acquisition prices;
- unrealized P/L;
- open orders;
- executions/fills;
- order history;
- fees;
- margin positions / collateral state;
- Japanese and U.S. equity account information.

Normalize these into the generic broker models and portfolio state.

Why first: it immediately joins research with the operator's real portfolio while carrying much lower operational risk than live order submission.

### P1C — Company IR acquisition

Add a general IR monitoring pipeline for companies where valuable information is not fully represented by SEC/EDINET/Yahoo.

Target documents/data:
- earnings presentations;
- medium-term plans;
- monthly operating updates;
- order backlog / bookings;
- store counts;
- ARR / subscriber / contract counts;
- utilization;
- shipments;
- guidance revisions;
- dividend revisions;
- buybacks;
- company-specific KPI tables.

Pipeline:

**IR source discovery -> new document detection -> HTML/PDF/XLS/CSV acquisition -> structured extraction -> previous-version diff -> provenance -> instrument timeline -> AI research**

Do not build company-specific parsers when a robust generic table/document extraction path is sufficient; allow small evidence-backed adapters where company-specific semantics add real value.

Implementation status (2026-09-23): implemented and verified end-to-end against
Nitori Holdings (`nitorihd.co.jp`); see the P1C checkpoint in
`docs/AUTONOMOUS_AGENT_HANDOFF.md` and the "Company IR acquisition (P1C)" section
of `docs/OPERATOR_MODE.md`. New modules: `acquisition/discovery.py`,
`acquisition/documents.py`, `acquisition/ir.py`,
`services/ir_monitor_service.py`, `api/ir_routes.py`, `ir_cli.py`; the existing
P1A toolkit and `diff_payloads` are unchanged.

### P1D — Authorized private information sources

For services the operator/family legitimately subscribes to or can access, allow personal-use authenticated connectors when useful.

Keep source-specific credentials/session state local whenever practical. Do not turn private access into redistribution.

### Exit criteria

- At least one authenticated broker read connector is useful in daily operation.
- At least one non-API IR workflow automatically detects and ingests new material.
- Private acquisition has debugger/API surfaces, not just hidden browser automation.
- Provenance and snapshot history survive normalization.

---

## P2 — Real Linux broker execution plane

### Objective

Close the loop from research to safe execution on the operator's actual broker.

First target remains Rakuten Securities Web because the required environment is Linux and both Japanese and U.S. equities matter.

Required:
- persistent authenticated browser profile;
- normal operator login/MFA;
- session-expiry detection;
- account/position/order reads;
- order preview;
- Japanese equity order submission;
- U.S. equity order submission;
- order status;
- cancellation;
- same-session private network calls where robust;
- DOM operations where private network contracts are too fragile;
- restart-safe client-order idempotency;
- duplicate-submit protection;
- live-order arm switch;
- single-order notional gate;
- daily order-count gate;
- append-only audit trail of intent/request/response/state.

MARKET SPEED II RSS remains an optional Windows accelerator for supported domestic products; it is not the primary architecture.

### Exit criteria

A real operator can safely complete:

**research -> order proposal -> preview -> explicit approval/arming -> submission -> broker acknowledgement -> status/fill -> portfolio update**

for both Japanese and U.S. equities on the first broker path.

---

## P3 — Close the profitability / learning loop

### Objective

Make "稼げる" measurable without pretending that a score predicts returns.

Existing foundation:
- interpretable research-priority scoring;
- factor contributions;
- point-in-time strategy snapshots;
- scoring versions;
- 20/60/120-style forward outcomes;
- benchmark-relative outcomes;
- AI history/outcome tools.

Remaining:
- aggregate calibration by scoring version;
- sample counts and minimum-sample warnings;
- score-decile and factor-decile outcome summaries;
- median/mean total return;
- median/mean benchmark excess return;
- positive-excess hit rate;
- rank correlation / information coefficient where statistically meaningful;
- walk-forward / out-of-sample evaluation;
- bootstrap/confidence intervals where useful;
- explicit separation of research-priority score from expected-return forecasts;
- AI access to calibration evidence;
- portfolio-aware sizing proposals;
- hypothesis/invalidation records that can later be evaluated.

Never silently overwrite historical scoring semantics. New calibrated rules become new scoring versions.

### Exit criteria

The system can answer:
- what did the strategy know at the time?
- why was this candidate prioritized?
- what would have invalidated the thesis?
- what happened afterward?
- which factors have shown evidence out-of-sample?
- is the evidence sufficient to change the scoring version?

---

## P4 — Japanese-market information advantage

### EDINET

- verify durable hosted database configuration;
- verify unattended EDINET credentials/maintenance;
- keep exact same-filing / same-currency accounting constraints;
- expose stale/incomplete coverage explicitly.

### JPX daily margin balances

JPX announced daily all-issue margin-balance publication beginning 2026-09-28 if migration proceeds.

When the live production format exists:
- inspect actual format and semantics;
- ingest daily history with provenance;
- persist sell/buy balances and changes;
- capture negotiable/general vs standardized/system-margin breakdowns;
- capture balance values and listed-share ratios where provided;
- add instrument-page supply/demand history;
- add screener/comparison fields;
- add abrupt-change alerts;
- snapshot values for later forward validation.

### Additional private Japan sources

Use authenticated scraping or structured downloads when they materially improve operator information and the operator has legitimate access.

### Exit criteria

Japanese equities have a meaningful information advantage over the generic Yahoo-style baseline, especially in filings, supply/demand, company IR changes, and portfolio/broker context.

---

## P5 — Product-completion audit

Audit workflows, not feature names.

For each common operator task, start from intent and verify it can be completed without unnecessary external-service hopping.

Audit:
- discovery;
- instrument research;
- financial/valuation interpretation;
- technical context;
- news/event/filing context;
- company-specific KPI history;
- strategy triage;
- comparison;
- watchlists/alerts;
- portfolio context;
- broker state;
- execution;
- post-trade observation;
- AI assistance;
- API/CLI/agent parity;
- desktop/mobile usability;
- failure/reauthentication flows.

Use TradingView, Yahoo Finance, Investing.com, TipRanks and relevant broker/IR workflows only as comparison references for friction and missing information—not as a mandate to clone every feature.

### Exit criteria

The operator no longer routinely needs several external services for the same research decision, except where Yowayowa intentionally links to the original source.

---

## P6 — Private / Family Operator v1 freeze

v1 is reached only when:
- critical workflows are end-to-end;
- API/GUI/agent parity holds;
- financial definitions and provenance are stable;
- private connectors have explicit failure/auth states;
- broker execution has safety and auditability;
- real Chrome tests are green;
- static/type/unit/integration tests are green;
- production-hosted portions are verified against the deployed source revision;
- local Operator components have real-machine verification;
- runtime error review is clean;
- current docs and handoff are accurate.

After freeze, prefer measured improvement from actual use over speculative feature expansion.

---

## Frozen / non-critical work

Until explicitly reopened by the operator:
- public SaaS feature development;
- subscriptions/billing;
- advertising;
- affiliate implementation;
- anonymous-user onboarding polish;
- public redistribution support for every private source;
- public-provider parity;
- speculative microservices or infrastructure;
- broad broker expansion before the first broker path is excellent.

Existing public/safe-mode security boundaries should not be broken gratuitously, but they are not a product roadmap.
