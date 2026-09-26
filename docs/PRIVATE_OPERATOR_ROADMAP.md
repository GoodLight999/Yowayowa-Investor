# Private / Family Operator Roadmap

Updated: 2026-09-26

## Product target

Yowayowa-Investor is now a **private / family operator investment research and execution system**.

General-public SaaS development is frozen. Existing public/safe-mode code may remain as a compatibility and security boundary, but it is maintenance-only unless the operator explicitly reopens public-product work.

Primary objective:

> Improve the operator's ability to make profitable investment decisions by combining broad data acquisition, interpretable deterministic analysis, AI-led research, portfolio context, and safe execution.

The product is not complete when it merely has many screens. It is complete when the operator can move through:

**discover -> investigate -> falsify -> compare -> size -> propose execution -> execute -> observe outcome -> recalibrate**

without routinely leaving Yowayowa.

## Progress baseline

Re-baselined 2026-09-26 from current code and real blocked criteria
(CG-20260925-001/002). Statuses below use exactly these words:
**DONE** (implemented, tested, merged on this branch),
**CODE-COMPLETE / REAL-SESSION BLOCKED** (implemented and green, but the
acceptance criterion requires a real operator session or live machine and is
gated there), **IN PROGRESS**, **NOT STARTED**, **FROZEN**.

Weighted completion baseline for **Private / Family Operator v1: 67%**
(was 68% on 2026-09-22). The percentage is deliberately not inflated:
the largest single deduction is real-session / real-machine / live-broker
acceptance, which no amount of code progress can satisfy. Percentages are
re-baselined in place — historical estimates live in git history, not in
stacked paragraphs.

| Area | Weight | Status | Completion meaning |
|---|---:|---|---|
| Core research workstation | 25% | DONE-equivalent (90%) | Search, instruments, charts, fundamentals, valuation, screeners, comparison, markets, portfolio, news/calendar/macro, provenance all implemented and tested |
| AI-led / interpretable operation | 20% | DONE-equivalent (80%) | Structured tools (25-tool agent catalog, CI-pinned), operation plans, strategy triage/history/outcomes/calibration, BYOK, Codex bridge (real-device-auth step: NEED-HUMAN), tool traces, cited research Q&A + morning brief (P5-A) |
| Broker / execution plane | 20% | CODE-COMPLETE / REAL-SESSION BLOCKED (20%) | Order domain, interlocks, audited submission transport, order-status inquiry, cancel design all code-complete; every write-side and selector step awaits the first real authenticated Rakuten session (see docs/BROKER_ACCEPTANCE_MATRIX.md) |
| Private data advantage / Japan edge | 15% | DONE-equivalent for code paths (65%) | P1A acquisition toolkit, P1B Rakuten read connector (selectors unverified), P1C IR pipeline, P1D mailbox, JPX margin, credit-margin weekly, machine screening, crypto/US OHLCV, MS2 RSS bridge; Rakuten read URLs remain real-session blocked |
| Reliability / API / agent parity | 15% | DONE-equivalent (80%) | FastAPI/OpenAPI with CI-pinned core machine paths + agent tool catalog (CG-003), CLI parity, contract tests, real-Chrome E2E in CI |
| Product closure / UX audit | 5% | IN PROGRESS (50%) | P5-A research surfaces shipped; full workflow dead-end audit not started |

Human-blocked items (explicit, not counted as complete):
- ChatGPT hosted Codex: one real device-code authorization + one tool-calling
  research request + refresh/revoke checks (CG-007, NEED-HUMAN);
- Rakuten Web: catalog URL/selector confirmation, first authenticated read,
  preview, submit, status, cancel, fill reconciliation
  (CG-004 matrix, NEED-HUMAN for every real-write row);
- JPX daily margin: live production format inspection (starts 2026-09-28).

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

### Status (2026-09-26): **CODE-COMPLETE / NEED-HUMAN for the real device-code acceptance.**

- OpenAI-compatible and Anthropic-style provider support: DONE.
- Hosted Codex bridge in Vercel Services: DONE (production reports enabled).
- Browser-only ChatGPT device-code flow: DONE.
- External-AI research-packet generation: DONE.
- Agent tool catalog (research/strategies/portfolio/alerts/outcome history,
  25 tools): DONE, pinned by CI.
- OpenAPI contract protecting core machine-facing endpoints: DONE,
  pinned by CI (CG-20260925-003).

### Remaining work (all NEED-HUMAN, see CG-20260925-007)

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

Current status (2026-09-26 re-baseline): **substantially DONE in code; the
Rakuten read-side URL/selector confirmation is CODE-COMPLETE /
REAL-SESSION BLOCKED.** The 2026-09-22 framing of P1 as "largest underused
opportunity" is outdated — P1A/C/D are implemented end-to-end against real
sources and P1B has a real connector foundation.

### P1A — General authenticated acquisition toolkit

**Status: DONE.**

Implemented (all verified by unit/integration tests):
- persistent local Chromium profile;
- authenticated-state detection (`AuthState`);
- same-session HTTP/JSON requests;
- network request/response instrumentation (`NetworkExchange`, provenance-safe);
- structured download capture (`DownloadCapture`);
- HTML parser adapters;
- parser/schema version metadata;
- cache/freshness policy (`FreshnessPolicy`, `CacheStatus`);
- provenance and as-of timestamps;
- explicit stale/auth-expired/failure states (`AcquisitionFetchState`);
- snapshot/diff support (`SnapshotRecord`, `SnapshotDiff`);
- API/CLI surfaces for debugging individual connectors
  (`/v1/private/connectors*`, `yowayowa private ...`).

### P1B — Broker read-side first

**Status: CODE-COMPLETE / REAL-SESSION BLOCKED (read-side).**

First target: Rakuten Securities Web — implemented read-only:
- `operator_bridge/rakuten_web.py` versioned resource catalog
  (account/positions/open_orders/order_history/executions × jp/us),
  GET-only host-checked transport, Japanese amount parsers, normalizers
  into generic `broker_models`;
- `services/broker_read_service.py` composes the acquisition toolkit
  (TTL cache, auth detection, snapshots, provenance);
- `/v1/broker-read/*` API + `yowayowa broker-read` CLI;
- reconciliation of order status against the audit trail
  (`services/order_inquiry_service.py`, P2C1).

**Blocked on the first real authenticated session:** catalog URLs/selectors
are unverified initial assumptions (`verified=False`); account/position/
order reads for JP and US must be confirmed live per
`docs/RAKUTEN_WEB_SESSION.md` (see `docs/BROKER_ACCEPTANCE_MATRIX.md`).

Still to ingest once reads are verified: fees detail, margin/collateral
state, average acquisition prices, unrealized P/L where the catalog does
not yet cover them.

### P1C — Company IR acquisition

**Status: DONE (verified end-to-end against Nitori Holdings, 2026-09-23).**

General IR monitoring pipeline for companies whose valuable information is
not fully represented by SEC/EDINET/Yahoo.

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

**Status: DONE (verified end-to-end against the operator's real mailbox, 2026-09-23).**

For sources the operator/family legitimately subscribes to or can access, personal-use authenticated connectors are allowed when useful.

Keep source-specific credentials/session state local whenever practical. Do not turn private access into redistribution.

Implementation status (2026-09-23): implemented and verified end-to-end against the
operator's real mailbox; see the P1D checkpoint in
`docs/AUTONOMOUS_AGENT_HANDOFF.md` and `docs/P1D_EVIDENCE.md`. New modules:
`acquisition/mailbox.py` (read-only `gog` CLI reader),
`acquisition/alerts.py` (earnings-calendar extraction),
`services/private_source_service.py`, `api/private_source_routes.py`,
`private_source_cli.py`; P1A/B/C modules unchanged.

### Exit criteria

- At least one authenticated broker read connector is useful in daily operation.
- At least one non-API IR workflow automatically detects and ingests new material.
- Private acquisition has debugger/API surfaces, not just hidden browser automation.
- Provenance and snapshot history survive normalization.

---

## P2 — Real Linux broker execution plane

**Status (2026-09-26 re-baseline): code path substantially DONE and
fail-closed — every real-session acceptance row is BLOCKED on the
operator (see `docs/BROKER_ACCEPTANCE_MATRIX.md`).**
Implemented: order domain + interlocks (P2A), audited submission
transport behind the frozen master gate (P2B), audit-matched order-status
inquiry (P2C1), cancellation design frozen pending `cancels_enabled` +
real-session proof (P2C2, NOT IMPLEMENTED by design).

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

#### P2A checkpoint — order domain + interlock layer (2026-09-23)

Implemented at HEAD `ba827f2` (no credentials, no transport, no submit path):

- `src/yowayowa/broker/execution/` — new order-domain package:
  `OrderProposal` (research→proposal traceability, motivation required),
  proposal hash over immutable economic fields only (idempotency key that
  survives restarts), order preview (no network, notional None stays None),
  fail-closed interlocks (settings gate + explicit runtime arming +
  notional-estimation gate + duplicate-submit mismatch), hash-chained
  append-only JSONL audit trail (fsync, tamper detection), restart-safe
  audit replay (duplicate registry + JST-day submit counts).
- Surfaces: `POST /v1/broker-execution/proposals`,
  `POST /v1/broker-execution/proposals/{id}/evaluate`, `GET .../audit`;
  CLI `yowayowa broker-exec` (audit / audit-verify / proposals-create /
  proposals-evaluate). All evaluate-only, never submit.
- `config.py`: one additive field `broker_execution_audit_dir`.
- Tests: 29 new (`tests/test_broker_execution_domain.py`), including JST
  day boundaries, UTC/JST divergence, tamper detection, restart replay.
- Verified: ruff/ruff-format/mypy clean, **671 passed** (642 baseline + 29),
  CI run 35850617952 green (verify + real-browser + prod deploy), production
  `/openapi.json` serves the three broker-execution paths, and the hosted
  environment correctly fails closed (`Private acquisition is disabled`).
- Next: P2B — Rakuten web submission connector inside the authenticated
  session (the only place `REQUEST_STAGE_SUBMIT` audit entries are written).

#### P2B checkpoint — Rakuten web submission transport (2026-09-23)

Implemented at HEAD `ce1cd6b` on top of the F1/F2/F3-hardened audit
layer (9888344; 20-probe auditor re-check passed → COO unfroze the
submission path):

- `RakutenWebSubmissionTransport` (`broker/execution/transport.py`):
  the only component writing `stage=submit` request audit entries.
  Ships fail-closed behind the COO master gate `submissions_enabled`
  (default False) — while shut, any submission records
  `state(submit-frozen)` and returns REJECTED before session access.
  Pipeline: frozen gate → audited proposal → intent/hash match →
  idempotent replay (never auto-resubmit) → interlocks → GET-only auth
  probe → `stage=submit` write (only at the submit instant; daily JST
  counter) → DOM submit → `record_response` → receipt. `accepted=True`
  only with a broker order number from the confirmation page; DOM
  errors are audited (`submit-failed`) and return UNKNOWN.
- CLI `yowayowa broker-exec submit` (`--submissions-enabled`,
  `--armed`, `--json`); no HTTP submission route by design.
- M4 constraint recorded: single-writer audit directory only.
- Order-form selectors are UNVERIFIED initial assumptions
  (`verified=False`) pending the first real operator session.
- Verified: 714 passed + 2 skipped (30 new transport tests + 2
  live-probe), make verify clean, CI run 35865575151 green, real-data
  fail-closed demos (frozen-gate rejection with zero session traffic;
  unauthenticated block before any submit write) in isolated audit dirs.
- Next: supervised unfreeze run — real login/MFA, one minimum-notional
  JP equity order, audit JSONL snapshot + broker order-id + portfolio
  reflection as the operational evidence.

#### P2C1 checkpoint — order status inquiry, audit-matched (2026-09-24)

Implemented on the P2B audit layer (base `ccc7133`), read-only:

- `src/yowayowa/services/order_inquiry_service.py` — `OrderInquiryService`
  (+ `OrderInquiryReport` / `OrderInquiryItem`): fetches `open_orders`
  and `order_history` through the existing P1B `BrokerReadService` (TTL
  cache, auth detection, snapshots, provenance; no new network code),
  merges them (dedupe by broker_order_id, order_history wins as the
  newer state, open_orders order preserved), and classifies every row
  against the P2A audit trail: `audit_matched` (client_order_id +
  proposal_hash filled from the latest `stage=submit` response entry),
  `unmatched_web` (manual/other-channel order), or `audit_only`
  (audited submission invisible in the web query; order restored from
  the audited proposal, status from the recorded response, and a note
  telling the operator to verify on the broker's order status page).
  Read-only by construction: the inquiry never appends to the audit
  trail; `intact_audit=false` when `verify_audit()` reports problems.
  Non-OK fetch states surface the outcome (notes/fetch states) without
  inventing rows (missing data is not zero).
- Surfaces: `GET /v1/broker-execution/orders` and
  `GET /v1/broker-execution/orders/{client_order_id}` (404 for an id
  that was never proposed; operation ids `broker_execution_list_orders`
  / `broker_execution_order_status`); CLI `yowayowa broker-exec orders`
  and `order-status` (API-backed, `--market jp|us`, `--json`).
  `RakutenWebSubmissionTransport.list_orders()` still fails closed and
  now points at the new GET endpoint. No cancel endpoint exists by
  design (see the cancellation design section in docs/OPERATOR_MODE.md).
- Tests: `tests/test_order_inquiry.py` (merge/dedupe/order, all three
  match kinds, order_status hit + unknown id, fail-closed fetch state,
  audit tamper → intact_audit=false, API 200/404/401-403, CLI, and the
  transport error message).
- Verified: `make verify` in a detached worktree (ruff, ruff-format,
  mypy, pytest) — 794 baseline plus the new tests, all green.
- Next: P2C2 — cancel implementation in the web-session transport,
  only after the `cancels_enabled` gate lands and a real operator
  session is verified (see docs/OPERATOR_MODE.md).

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

### Status (2026-09-26 re-baseline): **aggregate calibration DONE in code — walk-forward/out-of-sample and portfolio-aware sizing NOT STARTED.**

Implemented in `services/strategy_calibration.py` + AI tool
`get_strategy_calibration` (verified by `tests/test_strategy_calibration.py`):
- per-(strategy, scoring_version, horizon) aggregate calibration buckets;
- sample total/available/pending/unavailable counts with a
  minimum-sample warning (30) and decile minimum (10);
- score-decile and factor-decile outcome summaries;
- median/mean total return;
- median/mean benchmark excess return;
- positive-excess hit rate;
- Spearman rank IC with explicit `ic_insufficient` state
  (never a fabricated coefficient);
- outcome provenance propagated unchanged;
- AI access to calibration evidence via the agent tool catalog.

Remaining (NOT STARTED):
- walk-forward / out-of-sample evaluation (the rank IC currently treats
  overlapping windows as independent — flagged in bucket notes);
- bootstrap/confidence intervals where useful;
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

**Status: weekly credit-margin scraping DONE (parser/persistence/API/CLI +
`yowayowa-alpaca`-style weekly cron); the NEW daily all-issue feed is NOT
STARTED until the live production format exists (from 2026-09-28).**

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

**Status (2026-09-26): IN PROGRESS — P5-A (AI-led research surfaces) is
shipped; the workflow-dead-end audit itself is NOT STARTED.**

Shipped under P5-A:
- cited research Q&A (`research_ask`): deterministic evidence packet +
  one grounded agent round; facts/calculations/inferences/
  invalidation_conditions/missing_inputs separation (CG-20260925-006);
- morning research brief with Telegram delivery;
- OHLCV (stock + crypto) and macro evidence joined into the packet with
  provenance; `get_ohlcv` / `get_macro_series` agent tools;
- machine screening candidates as research starting points (P4-D);
- API/agent parity pinned in CI (CG-20260925-003).

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
