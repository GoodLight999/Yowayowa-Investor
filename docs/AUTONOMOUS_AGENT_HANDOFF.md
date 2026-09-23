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

### P1B checkpoint — Rakuten Web read-side connector (2026-09-23)

P1B is implemented and `make verify` green on `agent/commercial-foundation` (not yet committed/deployed at writing time; commit by CTO):

- `operator_bridge/rakuten_web.py` — read-only Rakuten web domain layer: resource catalog (account/positions/open_orders/executions × jp/us), GET-only host-checked transport, Japanese amount parsers, normalizers to generic broker models, detail extraction (fees/margin/symbol names).
- `services/broker_read_service.py` — `BrokerReadService` composing `PrivateAcquisitionService` (json + hidden html-tables definitions), `BrokerReadOutcome` model.
- `api/broker_read_routes.py` + `api/deps.py:get_broker_read_service` — `/v1/broker-read/*` API (personal-mode guarded, 404 unknown connector).
- `broker_read_cli.py` + `cli_entry.py` wiring — `yowayowa broker-read list|auth-check|fetch|snapshots|diff`.
- config: `broker_rakuten_web_profile_dir` (default `./data/broker-profiles/rakuten`).
- tests: `tests/test_rakuten_web_read.py` (30), `tests/test_broker_read_service.py` (20), `tests/test_broker_read_api.py` (7), `tests/test_broker_read_cli.py` (8), `tests/test_broker_read_browser_wiring.py` (12) — all fixture/mock based, no network, no playwright.
- browser-wiring fix: the production path handed transport-resolved ABSOLUTE URLs to `PersistentBrokerWebSession` (relative-path only), which raised `BrokerWebSessionError`; that is not a `PrivateAcquisitionError`, so it escaped the acquisition service and turned `/v1/broker-read/.../fetch` into HTTP 500. `api/deps.py` now verifies the origin, converts the URL to path+query, and maps any non-acquisition session exception to an explicit `PrivateAcquisitionError(FAILED)`. `tests/test_broker_read_browser_wiring.py` drives the real factory with a session fake as strict as the real one; 11 of its 12 tests fail against the pre-fix code.
- docs: `docs/RAKUTEN_WEB_SESSION.md` (real-session DoD), OPERATOR_MODE.md "Broker read-side (P1B)" section.

**Blocked on operator (not on code):** real-session verification. All catalog URLs are unverified initial assumptions (`verified=False`); first real session must confirm actual XHR/HTML URLs via devtools, update `RAKUTEN_WEB_RESOURCE_CATALOG`, flip `verified=True`, and run the reconciliation checklist in `docs/RAKUTEN_WEB_SESSION.md`. Read-only by construction (GET-only transport); order submission/cancellation remains P2.

---

### P1C checkpoint — Company IR acquisition (2026-09-23)

P1C is implemented on `agent/commercial-foundation`, verified end-to-end against a
live non-API IR source (Nitori Holdings, `nitorihd.co.jp`).

Commit: `64cd3dc` (pushed, CI run `35811046451` green: verify 510 passed,
browser E2E 69 passed, deploy-production success).
Production verified: `/internal/debug/runtime` reports
`source_revision=64cd3dc2b99bbd566a479a18dc27d5948ed74e8e`, deployment
`dpl_BLodR564pztojtq96kcgzb1RVG4q`, and the five `/v1/ir/*` paths are present in
the production OpenAPI document (they answer 403 outside personal mode by design).

Pipeline: `IrSourceDefinition` → discovery → new/revised/unchanged/verified
classification → acquisition → structured extraction → previous-version KPI diff →
provenance → instrument timeline → REST/CLI.

New modules (all generic; no company-specific parsers):
- `acquisition/documents.py` — HTML tables (reuses `TableHtmlParser`), XLSX (stdlib OOXML),
  PDF text layer + coordinate-based table reconstruction (pdfminer, lazy import).
- `acquisition/ir.py` — canonical KPI labels (JP/EN), number/unit normalization
  (compound yen chains such as `892億74百万円`, `(Millions of yen)` captions,
  per-table unit hints), `IrTimelineStore`, fingerprints.
- `acquisition/discovery.py` — IR link discovery, document classification,
  `FingerprintStore` (URL-only records keep a URL from re-classifying as new).
- `services/ir_monitor_service.py` — `IrMonitorService`, `IrKpiHistoryStore`,
  `IrSourceRegistryStore` (sources persist across restarts), `_default_http_transport`
  (unauthenticated public GET; no session, no credentials).
- `api/ir_routes.py` + `api/deps.py:get_ir_monitor_service` — `/v1/ir/*`
  (sources list/register/get, monitor, instrument timeline, document KPI history),
  guarded by the same private-connectors switch as the rest of the private surfaces.
- `ir_cli.py` + `cli_entry.py` wiring — `yowayowa ir sources|add-source|monitor|timeline|kpi-history`.
- `pyproject.toml` extra `operator-ir` (`pdfminer.six`), installed by `make install`,
  both CI jobs, and the Dockerfile.

Real-data evidence (Nitori, 106 discovered documents, fetch budget 6):
- new-document detection is idempotent: run 1 `new=106`, run 2 `new=0`,
  6 content-verified (`unchanged`) and 100 URL-only (`seen`) — these are
  reported separately (`unchanged_count` vs `seen_count`), never merged.
  `timeline_entries` counts actual on-disk timeline writes (6), not statuses.
- KPI extraction matches published figures, e.g. FY2026.3 4Q tanshin revenue
  `912,248 million yen` → `912248000000.0`, 3Q `688,503 million yen`, and the
  prose figure `当期利益892億74百万円` → `89,274,000,000.0`.
- previous-version diff on a real file replacement behind a stable URL:
  `status=revised` with `revenue` change `decrease`, `delta=-223745000000.0`
  and both versions retained in the KPI history.
- provenance (provider / source_url / license_class / retrieved_at) present on
  every timeline entry; image-only PDFs fail closed with an explicit
  `parse_note` instead of reporting an empty document as parsed.

**Operator note (not a code blocker):** `nitorihd.co.jp`'s WAF silently stalls
requests whose `User-Agent` embeds a `+https://...` reference URL (connection held
until timeout). `_default_http_transport` therefore sends
`Yowayowa-Investor/0.1 (personal research)` and documents the reason inline.

IR test counts (post F1/F3 rework, 2026-09-23):
`tests/test_ir_acquisition.py` 42, `tests/test_ir_surfaces.py` 6 — **48 passed**
(repo total at P1C: 510; commit `64cd3dc`'s message overstated these
counts (33(35)+7=42); `docs/P1C_EVIDENCE.md` section 4 carries the single
final reconciled set). CSV documents are now parsed and KPI-extracted;
XLSX/CSV label-column × period-column layouts, unit rows, and
current-period preference are covered by regression tests.

---

### P1D checkpoint — Authorized private mailbox source (2026-09-23)

P1D is implemented on `agent/commercial-foundation`, verified end-to-end against
the operator's real mailbox (`nakanagundam@gmail.com`, read-only via the `gog` CLI).

Commit: `eee6def` (CI run `35824917780` green: verify 634 passed, browser E2E
passed, deploy-production success). Production OpenAPI carries the five
`private_source_*` operations; they answer 403 outside personal mode by design
(private-source data stays local by construction).

First authorized private source: Rakuten Securities 銘柄情報通知サービス
earnings-calendar mail — a holdings/watch-list-scoped announcement-date stream no
public source provides.

New modules:
- `acquisition/mailbox.py` — `GogMailboxReader`: only ever invokes
  `gog -j --readonly -a <account> gmail messages search <query> --max N --include-body`;
  keyring password enters only via env/file, never logged/stored/returned
  (redacted from error reasons); keyring/TTY/token failures fail closed as
  `AUTH_EXPIRED` instead of an empty success.
- `acquisition/alerts.py` — generic `extract_earnings_calendar`: JP codes
  (`464A`→`464A.T`) and US tickers, section-header date fallback
  (`■決算発表日（…）1営業日前銘柄`), NFKC normalization for full-width mail text;
  dateless/unrecognized lines are dropped into `notes`, never guessed into events.
- `services/private_source_service.py` — source registry (JSON), sha256 message
  fingerprints, idempotent timeline entries via the P1C `IrTimelineStore` /
  `timeline_entry`, 900s in-memory cache with `force_refresh`.
- `api/private_source_routes.py` + `api/deps.py:get_private_source_service` —
  `/v1/private-sources/sources[...]`, `/fetch`, `/events` (personal-mode guarded).
- `private_source_cli.py` + `cli_entry.py` wiring —
  `yowayowa private-sources sources|add-source|fetch|events`.
- config: `mailbox_command` (default `gog`), `mailbox_keyring_password_file`.

Real-data evidence (2026-09-23, 40-message scan):
- 10 fetched mails → 11 events incl. `COUR`/`INTC` 2026-10-21, `464A.T` 2026-10-14,
  and `6871.T` via the section-date fallback.
- run1: `messages_scanned=40`, 70 timeline entries appended; run2 (cache) wrote
  nothing; `force_refresh` and a fresh service over the same data dir both
  reported `new_events=0` with zero duplicate entries (idempotent).
- provenance on every entry: `provider=rakuten-sec-alerts`,
  `source_url=mailbox://<account>?q=<urlencoded query>`,
  `license_class=personal_only`, `retrieved_at`, source message id/subject/sent_at.
- live fail-closed check: without keyring access gog exits with
  `no TTY available for keyring file backend password prompt` → `AUTH_EXPIRED`,
  `events=[]`, note `operator reauthentication required: ...`.

Tests: 123 new (mailbox 42, extraction 26, service 38, surfaces 17); repo total
634 passed (510 at P1C). P1A/P1B/P1C modules unchanged (frozen diff empty).
Evidence: `docs/P1D_EVIDENCE.md`.

### P2A checkpoint — order domain + interlock layer (2026-09-23)

HEAD `ba827f2` (commit + docs checkpoint). Credentials still not required.
New `src/yowayowa/broker/execution/`: `OrderProposal` (motivation/research
link/provenance required by design), immutable-economic-field proposal hash
(restart-safe idempotency key), network-free preview (missing notional stays
None), fail-closed interlocks (existing settings gate + explicit runtime
arming + notional gate + duplicate-submit mismatch), hash-chained append-only
JSONL audit (fsync, `verify()` tamper detection), restart-safe audit replay
providing the duplicate registry and JST-day order-count basis.
Surfaces: `/v1/broker-execution/proposals` (+ `/evaluate`), `GET /audit`,
CLI `yowayowa broker-exec`. There is deliberately no submit path anywhere;
`REQUEST_STAGE_SUBMIT` audit entries are reserved for the P2B Rakuten
submission connector.
config: `broker_execution_audit_dir` (additive, default
`./data/broker-execution/audit`).
Tests: 29 new (`tests/test_broker_execution_domain.py`); repo total
**671 passed** (642 at P1C-rework). Verified locally (ruff/format/mypy/
openapi) and via CI run 35850617952 (verify + real-browser E2E + prod
deploy green); production `/openapi.json` now serves the three new paths
and fails closed on the hosted environment as designed.

### P2A remedy checkpoint — audit F1/F2/F3 hardening (2026-09-23)

HEAD `9888344`. Remediation of the three findings from the independent
P2A audit (verdict APPROVE WITH FINDINGS), required before P2B starts
writing `stage=submit` entries into the same audit file.

- **F1 (tail truncation)**: every append now atomically persists an
  `audit.state.json` sidecar `{count, last_entry_hash}` (tmp write +
  fsync + `os.replace`). `verify()` cross-checks it: deleting the tail,
  the whole file, or reforging the last entry is detected. A missing
  sidecar self-heals (upgrade path for pre-existing audit dirs); an
  unparsable sidecar is reported as a problem, never raised.
- **F2 (shadow overwrite)**: `propose()` / `propose_model()` raise
  `DuplicateProposalError` on a second propose of the same
  `client_order_id` — strict first-wins, even for identical economic
  content. The replay registry absorb path is first-wins too, so the
  trail holds exactly one `intent` entry per id. API POST
  `/v1/broker-execution/proposals` returns **409** on duplicates (was
  silent 200); CLI `proposals-create` exits nonzero.
- **F3 (torn line kills /audit)**: unparsable JSONL lines never raise.
  `verify()` reports them as problems with line numbers, `entries()`
  skips them, `append()` chains off the last clean entry, and the
  service constructor replays successfully. `GET /audit` returns 200
  with `verify_problems` non-empty (degraded but diagnosable) instead
  of a 500 exactly when the trail is broken.
- **M1** (minor, same files): removed the dead
  `OrderExecutionPreview.model_post_init` cleanup.

P2B gates carried over from the audit (still binding): single-writer
audit directory only; `record_request(stage=submit)` written only at
the actual submit instant; replay-flagged ALLOWED verdicts must never
be auto-resubmitted.

Tests: 13 new regression tests (`test_30`..`test_42` in
`tests/test_broker_execution_domain.py`); repo total **684 passed**
(671 before). `make verify` clean (ruff / format / mypy / openapi).
CI run 35858245940 green on `9888344`. The auditor's three probes
(truncation, shadow overwrite, torn-line 500) were re-executed on this
commit and now detect/reject/survive as designed; evidence handed to
audit-argus for independent confirmation (20-probe re-check passed —
audit closed, COO unfroze the P2B submission path).

### P2B checkpoint — Rakuten web submission transport (2026-09-23)

HEAD `ce1cd6b`. The first real submission path, layered on the P2A
domain + interlock layer. **Ships fail-closed**: a COO-ruling master
gate `submissions_enabled` (default False) is evaluated FIRST inside
`submit_order` — while shut, every path (including a fully-armed happy
path) records `state(stage=submit-frozen)` and returns REJECTED before
proposal lookup, session access, DOM, or any `stage=submit` write.
Unfreezing requires the explicit CLI flag `--submissions-enabled` AND
all P2A interlocks (settings arm + runtime `--armed` + notional limits
+ authenticated session).

- `src/yowayowa/broker/execution/transport.py` (new):
  `RakutenWebSubmissionTransport` satisfies the `BrokerConnector`
  protocol. Sequential pipeline: frozen gate → audited-proposal lookup
  → intent/hash match → idempotent replay (NEVER auto-resubmits; the
  receipt is restored from the prior audited response, else UNKNOWN +
  order-inquiry guidance) → interlock evaluate → GET-only auth probe →
  `record_request(stage=submit)` (the ONLY write site, at the submit
  instant; daily JST counter counts this line only) → DOM fill/submit →
  `record_response` → receipt. `accepted=True` requires a broker order
  number read from the confirmation page; DOM exceptions are audited as
  `state(submit-failed)` and return UNKNOWN with check-order-before-
  resend guidance. Read/cancel protocol methods raise
  `BrokerConnectorFeatureError` (read path remains the P1B connector).
  `RAKUTEN_WEB_ORDER_FORM` selectors are UNVERIFIED initial assumptions
  (`verified=False`, URL-provenance notice like `rakuten_web.py`) and
  must be confirmed against the real session before first live use.
- CLI: `yowayowa broker-exec submit <id>` (`--armed/--no-armed`,
  `--submissions-enabled`, `--json`). No HTTP route is exposed for
  submission by design (minimal surface; CLI + shared domain service
  satisfy API-first).
- **M4 (single-writer audit directory)**: the audit log is
  read-modify-write; only ONE writer process may open a given audit
  directory (single uvicorn worker, one CLI run at a time). Concurrent
  writers would corrupt seq/prev_hash chaining. This constraint binds
  every P2B submit run.
- Tests: 30 new (`tests/test_broker_execution_transport.py`) + 2
  live-probe tests (`tests/test_broker_execution_transport_live.py`,
  skipped unless `YOWAYOWA_P2B_LIVE_PROBE=1`); repo total **714 passed,
  2 skipped**. `make verify` clean. Real-data fail-closed demos in
  isolated temp audit dirs: frozen gate rejects an armed submission
  with zero session traffic; gate-open + unauthenticated profile blocks
  before any `stage=submit` write. CI run 35865575151 green on
  `ce1cd6b` (verify + browser + deploy).
- Next: operator-side unfreeze flow — real Rakuten login/MFA in the
  persistent profile, `--submissions-enabled` + `--armed`, and ONE
  supervised minimum-notional JP equity order with its audit JSONL
  snapshot as the operational evidence (separate supervised run, not
  part of this checkpoint).

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
- `acquisition/` toolkit (P1A): connector registry, session transports
  (private HTTP + browser session), heuristic auth-state detection, download
  capture (CSV/JSON/XLSX), versioned table/text HTML parsers, TTL cache with
  bounded staleness, JSONL snapshot history with structural payload diffs, and
  the fail-closed `/v1/private` API + `yowayowa private` CLI surface.

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
