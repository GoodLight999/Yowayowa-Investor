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
| AI-led / interpretable operation | 20% | DONE-equivalent (80%) | Structured agent tools, operation plans, strategy triage/history/outcomes/calibration, BYOK, Codex bridge (real-device-auth step: NEED-HUMAN), tool traces, cited research Q&A + morning brief (P5-A); WD-D adds a sizing tool on `agent/wdd`, pending merge |
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
- JPX daily margin: live machine-readable daily file — format contract now
  verified (2026-10-23) and the ingest path proven on the official package; the
  remaining block is an **operator cost/contract decision** (paid TMI / J-Quants
  Pro feed vs. a reader for the free public daily PDF). See
  `docs/P1D_JPX_MARGIN_DAILY_EVIDENCE.md`.

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
- Agent tool catalog (research/strategies/portfolio/alerts/outcome history):
  DONE at the CI-pinned baseline; WD-D adds one sizing tool on `agent/wdd`,
  pending merge.
- OpenAPI contract protecting core machine-facing endpoints: DONE,
  pinned by CI (CG-20260925-003).

#### WD-E checkpoint (2026-09-29)

The AI catalog now includes `propose_broker_order`: it records an audited
proposal with research-link/retrieval-time provenance and a broker positions
snapshot. It does not submit or cancel orders. Quantity remains explicit
(the tool does not calculate sizing). The tool fails closed until the broker
positions catalog is verified in a real operator session; this remains
NEED-HUMAN under `docs/BROKER_ACCEPTANCE_MATRIX.md` R1-R4.

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

### Status (2026-09-29): **IN PROGRESS — aggregate calibration + purged walk-forward / out-of-sample DONE in code; portfolio-aware sizing proposals implemented and tested on `agent/wdd`, pending merge.**

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
- AI access to calibration evidence via the agent tool catalog;
- purged walk-forward / out-of-sample evaluation: deterministic split on
  `captured_at` (never on sample values), last `oos_min_sample` outcomes by
  decision time as OOS, windows with `exit_at > split_at` purged from both
  segments (`purged_count`; a window captured exactly at `split_at` is purged
  too), in-sample restricted to windows fully closed at or before the split
  (`exit_at <= split_at`), per-segment `is_*` / `oos_*` statistics and rank
  ICs with independent `*_ic_insufficient` states, `oos_split_at`,
  `exit_at_unknown_count`,
  and `oos_notes` stating that `is_*` are front-half time-series statistics,
  not a fitted-model in-sample fit;
- fixed-seed (20260928) percentile bootstrap confidence intervals for the OOS
  median total / excess returns (`None` kept for n<2 or zero variance);
- `oos_min_sample` override on `calibration_report()`, the
  `GET /v1/strategy-research/calibration` query (1..1000), and the
  `get_strategy_calibration` tool schema.
- append-only hypothesis and falsification-criteria records with creation-time
  and evidence-link provenance (`POST/GET /v1/hypotheses`); records are
  private decision history and do not execute or mutate broker/order state.
- additive `create_all` migration for the new hypothesis table; existing rows
  are covered by a dedicated preservation test (`tests/test_hypotheses.py`).
- Repeatable snapshot-generation entrypoint: `yowayowa preset snapshot-builtins`
  runs each implemented built-in preset through discovery + evaluation with
  `record=true`; operator-managed scheduling may invoke it, but this command
  does not install a cron/systemd job. Daily idempotency is per strategy,
  scoring version, region, symbol, and UTC capture date; unavailable candidates
  are not fabricated or backfilled.

Remaining:
- bootstrap/confidence intervals where useful (prototype on `agent/p5-audit`, not landed);
- install/enable the committed systemd units on the operator host (see
  `deploy/systemd/yowayowa-api.service`, `yowayowa-snapshot.service`, and
  `yowayowa-snapshot.timer`; procedure and verification commands are in
  `docs/OPERATOR_MODE.md`). The timer runs the snapshot CLI daily against the
  persistent operator database.

#### CLI parity for the P3 learning loop (2026-09-29)

The learning-loop surfaces above existed on the API and the AI tool catalog but
not on the CLI, which broke invariant 1 (GUI/API/CLI/agent share one domain
logic). Three CLI surfaces close that gap; all of them are read-only or
append-only and none of them can execute or mutate an order:

- `yowayowa strategy-calibration` — same filters as
  `GET /v1/strategy-research/calibration` (`--strategy-id --region --symbol
  --horizons --benchmark --limit --oos-min-sample`). Prints sample counts and
  minimum-sample warnings, score/factor deciles, median/mean total and
  benchmark-excess returns, positive-excess hit rate, the Spearman rank IC, and
  the purged walk-forward segments under `Out-of-sample:` / `In-sample:` /
  `Split:` headings. Rates render as percentages; coefficients, counts and
  timestamps keep their raw value; `*_ic_insufficient` prints
  `insufficient (n=...)` instead of a coefficient.
- `yowayowa hypothesis add|list|show` — append-only hypothesis and
  falsification-criteria records (`POST/GET /v1/hypotheses`). `add` requires a
  non-blank thesis, at least one `--criteria` and at least one `--evidence-url`
  and fails closed without them; 403 outside personal mode is surfaced as the
  correct behaviour.
- `yowayowa portfolio sizing-proposals PORTFOLIO_ID` — `--idea` JSON candidates
  plus `--risk-budget-pct` / `--max-position-pct`; prints the non-executable
  proposal (quantity, notional, limiting constraints) with the explicit
  `proposal only — not executable` marker. Unparseable `--idea` input fails
  closed before any request.

#### Portfolio-aware sizing checkpoint (WD-D, 2026-09-29)

- State: **IN PROGRESS** on branch `agent/wdd`; not merged into the canonical branch.
- `POST /v1/portfolios/{portfolio_id}/sizing-proposals` and AI tool
  `propose_portfolio_sizing` call the same deterministic service.
- Quantity is the lower of the whole-lot risk-budget quantity and the remaining
  per-position concentration quantity. The total stop-loss budget is based on
  gross marked holdings and split equally across the submitted ideas.
- Candidate entry/stop prices, currency, lot size, and quote provenance/as-of
  are required caller inputs. Holdings require complete valuation and provenance;
  cross-currency candidates fail closed. Cash, liabilities, costs, slippage, and
  gap risk are not included. Output is explicitly non-executable.
- NEED-HUMAN remains for validating caller-supplied quote/stop/lot assumptions
  against the operator's intended market and for any real broker-session or
  order acceptance. No broker connection or order submission is part of WD-D.

Never silently overwrite historical scoring semantics. New calibrated rules become new scoring versions.

#### Profitability & Monetization Evaluation Checkpoint (Task t_af2b0af6, 2026-10-19)

- State: **EVALUATED & RETAINED IN PILOT / FORMAL LIVE ADOPTION PENDING MILESTONES**.
- Context & Evidence:
  - Dividend accounting fix (v6, x23-ledger-3) approved by audit-argus R6 (`t_c707cb4c`, TSUKUMO `results_v6`).
  - Demo forward loop v1.3 canonical flip verified and operational (`t_1c3cd673`, commit `3a48814`).
  - Full evidence and provenance ledger: `/root/.hermes/kanban/workspaces/t_af2b0af6/EVIDENCE_T_AF2B0AF6.md` and `monetization_summary_20261019.json`.
- Strategy Adoption & PnL Status (after 15 bps commission + slippage, 1M JPY base):
  - **S-01 (crypto_trend_dual_ma, SMA200 daily)**: Backtest (2023-2026, 3.75y) CAGR +21.85% (+1,097,400 JPY / 1M JPY base), MaxDD -39.75% (vs Buy&Hold -60.15%, +20.40pp shallower). OOS CAGR +36.33% / MaxDD -5.64%. Deployed as Book A in forward demo loop v1.3: 10 calendar days (14 decision sessions, 40 decisions, 38 fills), cumulative return -2.52% (-25,240 JPY / 1M base) vs Benchmark -2.70% (-27,020 JPY), outperforming by +0.18pp (+1,780 JPY). Drawdown -3.31% vs Benchmark -3.49%. CUSUM edge-lifetime monitor S_w = 0.1308 < 4.8. Retained in demo loop; live adoption deferred until 4w / 12w / 26w design milestones (`SHORT_TERM_STRATEGY_DESIGN.md` §4) are reached (today's decision count does not prove forward profitability).
  - **S-02 (vol_target_15% weekly, US Equity + Cash)**: Backtest (2021-2026, 5.71y, results_v6) CAGR +8.94% (+631,196 JPY / 1M base), Sharpe 0.839, MaxDD -14.68% (vs EW -18.76%). OOS CAGR +14.75%, OOS Sharpe 1.325, OOS MaxDD -10.28% (vs EW -13.15%). Passed all 4 backtest adoption criteria. Awaiting deployment into demo loop as Book C (paper trading parallel run).
  - **S-03 (garp_momentum_hybrid)**: REJECTED (Full MaxDD worsened to -36.17% vs baseline -31.00%, failing condition c).
  - **S-05 (defensive_rotation)**: REJECTED (Full MaxDD -26.30% vs same-universe momentum -24.88%, failing condition b).
  - **P3 Equity Baselines (Momentum 12-1 etc.)**: REJECTED as standalone (Momentum collapsed in OOS to -6.91%; bootstrap CIs cross zero).
- Concrete Next Step for Live Operational Decision:
  - Complete S-01 demo loop milestone 1 (4 weeks: 2026-11-07) with zero HALT incidents and verified cost ratios.
  - Activate Book C (S-02 equity vol-target) in the forward demo loop for paper parallel tracking.

#### Book C (S-02 US Equity Vol-Target 15% Defensive) Demo-Loop Paper Parallel Deployment Checkpoint (Task t_feb3aa55, 2026-10-19)

- State: **DEPLOYED IN PAPER PARALLEL (DEMO-LOOP v1.3)**.
- Preconditions Verification (`SHORT_TERM_STRATEGY_DESIGN.md` §4):
  - **Deployment Timing**: Monday open deployment (2026-10-19 JST / UTC morning), aligned with weekly rebalance cadence.
  - **Execution & Cost Assumed**: 15 bps one-way fee model (`COST_RATE_BPS = 15.0`) identical to backtest baseline. Zero live broker orders / zero paid APIs.
  - **Capital Base**: Initialized with virtual 1.0 capital (`cash: 1.0`, `equity: 1.0`, `peak_equity: 1.0`, `drawdown: 0.0`).
  - **Preregistration**: Preregistered in `prereg.jsonl` with author `worker-agy:t_feb3aa55`, hypothesis `"S-02 (frozen US equity vol target 15% weekly) controls drawdown vs EW"`, judgment rule `"design §4 milestones at 4/12/26 weeks"`, and spec hash `10ba65cac72a7d5d6ed763b0268f6d3f46e97e6339dcf3fff0688733aedbf434`.
  - **Errata E1-E4 Documentation & Scope Clarity**:
    - **E1 (Weekend Data Freshness Gap)**: Handled equity market close calendar (`max_fresh = 3` on Mondays to accommodate Friday close -> Monday open).
    - **E2 (Asset Universe)**: Fixed `EQUITY_UNIVERSE = ("SPY", "IWM", "JNJ", "PG")` per frozen S-02 specification.
    - **E3 (Strategy & Signal Engine)**: Evaluated through frozen backtest engine `vol_target_defensive` (lookback=21, target_vol=0.15, weekly cadence) with counterfactual raw weights and guard checks.
    - **E4 (Milestone Schedule Independence)**: S-01 (Book A) 4-week milestone remains fixed at 2026-11-07; Book C paper milestones are independently tracked from deployment date (2026-10-19) at 4 weeks (2026-11-16), 12 weeks (2027-01-11), and 26 weeks (2027-04-19).
- Live Demo-Loop Ledger Execution:
  - Run ID: `20261019T045750-602560` (Seq 175-182 in `data/demo-loop/journal.jsonl`).
  - Seq 176: `halt_check` for Book C recorded (`action: none`, `halted_before: false`, `halted_after: false`, `margin_min: -4.467`).
  - Seq 177-180: Initial decisions recorded for SPY (target 0.25, RISK_ON), IWM (target 0.25, RISK_OFF), JNJ (target 0.25, RISK_ON), PG (target 0.25, RISK_ON).
  - Seq 181: Initial `mtm` recorded for Book C (`cash: 1.0`, `equity: 1.0`, `peak: 1.0`, `drawdown: 0.0`, `price_bar_date: 2026-10-16`).
  - Seq 182: `run` end recorded (`rows_written: 4`, `ok: true`).
- Verification & Test Results:
  - `paperloop_verify` exited with code 0 (`chain_ok: true`, `anchors_ok: true`, rows: 182, failed_checks: 0, L1-L14 all green).
  - All 31 paperloop unit and integration tests passing (`tests/test_paperloop*.py`).
  - Weekly report (`loop_cli report`) extended to include Book C in `eval_by_book` alongside Book A and CRYPTO_1N.

#### Book C Deployment Review Round 1 Remediation & Clarifications (Task t_feb3aa55, Round 2)

- State: **REMEDIATED (D1/D2 DEFECTS RESOLVED & ERRATA CLARIFIED)**.
- Remediation of Blocking Defects (Review Round 1 by `audit-argus`):
  - **D1 (Equity Data Feed Pipeline Integration)**: Integrated `stock-fetch SPY IWM JNJ PG --days 5` into `ops/demo-loop/daily.sh` preceding `mark` and `decide`. Daily cron `9b64eaec1cb9` (UTC 00:20 / JST 09:20) now continuously refreshes equity bars, preventing Tuesday lag from exceeding the 72h staleness threshold and averting false HALT trips.
  - **D2 (Book C Weekly Cadence Gate)**: Gated Book C decision and rebalancing execution in `paperloop.py::run_daily_decide` to Mondays (`weekday() == 0`). On non-rebalance days (Tuesday through Sunday), Book C generates zero new trade decisions, preserving the frozen backtest weekly rebalancing design and eliminating 15 bps daily turnover drift. Daily mark-to-market (`run_daily_mtm`) remains active every calendar day.
  - **Automated Regression Tests**: Added `test_stock_fetch_pipeline_advances_bars` (D1) and `test_book_c_weekly_cadence_gate` (D2) to `tests/test_paperloop.py`, with all 33 paperloop tests passing.
- Errata N1 Clarification (Parent Task `t_af2b0af6` vs Deployment Clarifications):
  - The four items previously labeled E1–E4 above were four implementation design points (D-E1 weekend freshness, D-E2 equity universe, D-E3 signal engine, D-E4 milestone schedule).
  - The actual parent task `t_af2b0af6` review errata are clarified as follows (informational, non-blocking):
    - **Parent E1**: Fill attribution in demo-loop v1.3 is Book A = 20, CRYPTO_1N = 18 (total 38 fills).
    - **Parent E2**: Metric key in `aggregate_monetization_metrics.py` is `sharpe_ratio` (value 0.72497 rounded to 0.725; fixed key reference).
    - **Parent E3**: Cumulative `fill_rejected` count across journal is 6 (2 for Book A, 4 for CRYPTO_1N).
    - **Parent E4**: `weekly_summary.jsonl` last anchor sequence reference is seq 167 (not 168).
- Provenance & Author: Round 2 changes committed by `worker-agy` (remediating N2).

#### Book C Comparison Wiring — EQUITY_EW Benchmark Enabled (Task t_dddae39e, 2026-10-23)

- State: **COMPARISON BASE WIRED (no strategy/parameter change, no orders).**
- Defect closed: `EQUITY_EW` (4-asset equal-weight buy-and-hold) was preregistered
  (`BOOK_SPEC_HASHES`), had weights and equity branches, but was missing from
  `BOOKS_ENABLED` — so Book C's frozen hypothesis ("controls drawdown vs EW") had no
  comparison book and `vs_benchmark_pp_cum` / `dd_vs_benchmark_ratio` stayed null.
- Changes (4 points):
  - `BOOKS_ENABLED` now includes `BENCH_EQUITY_BOOK`; it buys 1/N once and holds (D7).
  - The Book C Monday cadence gate now covers `EQUITY_EW` as well (same strategy, so
    the comparison line is not a different regime).
  - `EQUITY_EW`'s prereg row is attributed to `worker-agy:t_feb3aa55` (the Book C
    deployment), not to the v1.1 crypto task.
  - `weekly_summary` adds per-strategy comparison fields (`benchmark_book`, `cum_pnl`,
    `max_drawdown`, `benchmark_cum_pnl`, `benchmark_max_drawdown`,
    `vs_benchmark_pp_cum`, `dd_vs_benchmark_ratio`); missing data stays null.
- Measured: old-vs-new `weekly_summary` on the same journal copy is byte-identical for
  every pre-existing field of A / CRYPTO_1N / C (only the `EQUITY_EW` entry and the
  additive comparison fields differ). `paperloop_verify` 247 rows, L1–L14 clean,
  R1 30/30. Paperloop tests pass individually (23 + 19; the full file exceeds 400 s on
  this host, so tests were run one by one and judged by exit code).
- Not established: Book C's benchmark difference cannot exist before `EQUITY_EW`'s
  first decision (2026-10-26) and first fill (2026-10-27); equity mtm marks are one
  session stale (newest SPY bar 2026-10-20 at the 2026-10-22 run) — recorded, not fixed.
- Evidence: `docs/MONETIZATION_READING_20261023.md`.

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

**Status (2026-10-23): weekly credit-margin scraping DONE
(parser/persistence/API/CLI + `yowayowa-alpaca`-style weekly cron); the daily
all-issue ingestion code (parser/persistence/API/CLI) is DONE and verified
against the current official specification and the unchanged official sample
package; the live machine-readable feed is contract-gated (TMI / J-Quants Pro,
401 without a token, no credential configured), so no real business day has been
ingested yet. Evidence: `docs/P1D_JPX_MARGIN_DAILY_EVIDENCE.md`.**

Two ways forward, both requiring an operator decision:
1. contract the paid feed (from 100,000円/month 税抜 for 総合取引参加者向け,
   250,000円 for その他法人向け) and keep the existing ingest path;
2. read the free public daily PDF (all issues, ~1 week of dates, 修正版は `_01`)
   — same business data, needs its own parser/fixtures/tests.

Already done:
- actual format and semantics inspected (spec §3.2.3 verified, real-value
  identity confirmed on the free public daily table: 4,249 issues, 2026-10-21);
- ingest path (parse → persist → CLI → API) verified end-to-end on the official
  distribution bytes with provenance and per-date replacement semantics.

Still open:
- ingest real daily history (needs 1 or 2 above);
- persist sell/buy balances and changes (implemented, waiting for real data);
- capture negotiable/general vs standardized/system-margin breakdowns
  (implemented, waiting for real data);
- capture balance values and listed-share ratios where provided (values
  implemented; listed-share ratios are not in this dataset's issue file);
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
