# Full / Operator mode

Yowayowa-Investor is private-operator-first.

The primary product is the `personal` mode, which should be read as **Full / Operator mode**. It is allowed to use capabilities that are unsuitable for a public multi-tenant SaaS: authenticated scraping, private/non-public provider protocols, local software bridges, personal-only data sources, local credentials, local AI/CLI tools, and broker control for the operator's own accounts.

The `public` mode remains as a deliberately limited safe profile. Public-mode constraints must never force the Full / Operator implementation to become weaker.

## Connector priority

The primary operator environment is Linux and the product must support domestic and overseas securities. The universal broker-control surface is therefore an **authenticated browser session**, unless the broker offers a stronger cross-platform programmable API.

Use this order:

1. official cross-platform programmable API when available;
2. authenticated browser session owned by the operator;
3. inside that session, the broker's own XHR/JSON/GraphQL/WebSocket transport where practical;
4. DOM interaction for functions that are not safely reproducible at the network layer;
5. structured scraping/downloads for read paths;
6. platform-specific local interfaces as optional accelerators.

A browser session is not merely a click bot. It is the container for the broker's legitimate authenticated state. Network-level calls and DOM-level actions may coexist behind the same broker connector.

Authentication, MFA, CAPTCHA, device approval, and similar access controls are not bypass targets. The operator completes the broker's legitimate authentication flow; Yowayowa reuses only the resulting authorized session.

## Scraping

Scraping is a required Full / Operator capability.

Prefer structured sources exposed by the target application itself:
- JSON/XHR/fetch responses;
- GraphQL;
- WebSocket messages;
- downloadable CSV/JSON;
- embedded structured state;
- HTML parsing only when structured transport is unavailable.

Every scraper must declare:
- target/provider;
- authentication/session assumptions;
- source provenance;
- cache/freshness behavior;
- parser/schema version assumptions;
- failure semantics;
- whether redistribution is permitted.

A personal/private scraper is not automatically a public provider. Public mode must fail closed unless redistribution rights and security posture are appropriate.

## Private acquisition toolkit

The general authenticated acquisition framework lives in `src/yowayowa/acquisition/`. It turns "the operator can legitimately see this data" into a provenance-tracked, inspectable pipeline without asking each source for a public API.

Capabilities:

- **Connector registry** — declarative connector definitions (provider, origin, method, parser, freshness policy) registered at runtime through the API or CLI.
- **Session transports** — same-origin private HTTP (reusing the authenticated private HTTP client's origin/size/redirect guarantees) and a browser-session transport over the operator's persistent Chromium profile. Browser-session connectors need an injected transport; playwright is loaded lazily and never required for HTTP-only connectors.
- **Auth-state detection** — each fetch is classified `authenticated` / `unauthenticated` / `unknown` from status, URL, title, and body signals. An expired session returns an explicit `auth_expired` outcome with an operator-reauthentication note; nothing is fetched as if it were data.
- **Download capture** — CSV/JSON/XLSX downloads are captured with sha256, size, and format; CSV is parsed into capped rows, JSON lists into documents, XLSX is stored raw with an explicit "not parsed" note.
- **Parsers** — versioned table and text HTML parsers (stdlib only) so scraped HTML becomes structured, schema-versioned payloads.
- **Cache / freshness** — per-connector TTL with a bounded stale window; entries older than the max-stale bound are dropped rather than served.
- **Snapshots + diff** — every successful fetch is appended to a local JSONL history with payload hash; consecutive payloads are structurally diffed so "what changed at this source" is a first-class question.
- **Provenance-safe outcomes** — every fetch returns a debug outcome with network exchange records (method, query-free URL, status, content type, size, duration), source URL, retrieved/as-of timestamps, and parser/schema versions. Cookies, tokens, and Authorization headers never appear in any record.

Local data: snapshots are written under `YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR` (default `./data/private-acquisition`).

API surface: `/v1/private/connectors` (list/register/get), `.../fetch`, `.../auth-check`, `.../snapshots`, `.../diff`. CLI: `yowayowa private list|register|fetch|auth-check|snapshots|diff`. The routes fail closed with 403 outside personal mode or when private connectors are disabled.

Reauthentication flow: when a fetch reports `auth_expired`, complete the source's normal login/MFA in the operator browser session (or refresh the credential), then run `yowayowa private auth-check <connector>` until it reports authenticated; the next fetch re-acquires normally.

## Broker read-side (P1B)

The first concrete broker read connector is Rakuten Securities Web (`rakuten-web`), implemented read-only on top of the acquisition toolkit:

- **Domain layer** — `operator_bridge/rakuten_web.py`: versioned resource catalog (account / positions / open_orders / order_history / executions × jp / us), read-only host-checked transport wrapper (`RakutenWebFetchTransport`: GET only, `www.rakuten-sec.co.jp` only), Japanese statement amount parsers (▲ negatives, 円/米ドル/株/口, fullwidth digits), and pure normalizers into the generic `broker_models` (`BrokerAccountSnapshot` / `BrokerPosition` / `BrokerOrder`). Missing data stays `None` + notes; JPY and USD are never mixed or converted. Fees, symbol names, and margin/collateral state live in the outcome `detail` dict because the generic models do not carry them.
- **Service layer** — `services/broker_read_service.py`: `BrokerReadService` composes `PrivateAcquisitionService` (one `rakuten-web` JSON definition + one hidden `rakuten-web-html` tables definition; the catalog's `parser_kind` per resource decides which one serves a fetch). TTL 60s / max-stale 600s; the auth probe is the jp account resource; login detection uses `login/signin/sign-in` URL markers plus login text markers.
- **API surface** — `/v1/broker-read/connectors` (list/get), `.../auth-check`, `.../fetch` (body: resource, market jp|us, force_refresh), `.../snapshots`, `.../diff`. Same guards as `/v1/private`: 403 outside personal mode or with private connectors disabled; unknown connectors 404. Outcomes carry full acquisition provenance (source URL, retrieved/as-of, parser/schema versions, network exchanges, snapshot, diff, cache state).
- **CLI** — `yowayowa broker-read list|auth-check|fetch|snapshots|diff`; fetch prints a state/auth summary (cash, buying power, position count, order count, margin state) and `--json` prints the full outcome.
- **URL caveat** — catalog URLs are unverified initial assumptions (`verified=False` in every outcome's `detail`); confirm them during the first real session per `docs/RAKUTEN_WEB_SESSION.md` and update the catalog. No state-changing request path exists on this surface (GET-only transport; order submission/cancellation is P2).

Reauthentication uses the same flow as the acquisition toolkit: on `auth_expired`, complete Rakuten's normal login/MFA in the persistent browser profile (`YOWAYOWA_BROKER_RAKUTEN_WEB_PROFILE_DIR`, default `./data/broker-profiles/rakuten`), then `yowayowa broker-read auth-check rakuten-web` until authenticated.

Real-session verification procedure (operator DoD): `docs/RAKUTEN_WEB_SESSION.md`.

## Company IR acquisition (P1C)

A general IR monitoring pipeline for companies whose useful information is not
fully represented by SEC/EDINET/Yahoo. It is read-only and uses public,
unauthenticated GET transport: no session, no credentials, no cookies.

Pipeline: IR source discovery → new-document detection → HTML/PDF/XLSX/CSV
acquisition → structured extraction → previous-version diff → provenance →
instrument timeline.

- **Discovery** — `acquisition/discovery.py` enumerates document links on the
  source's listing page (same-origin, `.pdf/.xlsx?/.csv`; HTML navigation links
  are pages, not documents) and classifies each URL against the recorded
  fingerprints as `new` / `seen` / `verified` / `unchanged` / `revised`.
- **Extraction** — `acquisition/documents.py` handles HTML tables (the existing
  `TableHtmlParser`), XLSX (stdlib OOXML), CSV (stdlib `csv`; bytes decode as
  utf-8-sig then cp932, otherwise the document fails closed with an explicit
  note), and PDF (pdfminer text layer plus coordinate-based table
  reconstruction). CSV and XLSX rows become the same generic row-table shape,
  and multi-column KPI forms are extracted for both: a label column paired
  with period columns (current period preferred over the previous one),
  generic `項目`/`値` heads, title rows and `単位：百万円` unit rows ahead of
  the detected header. A table with no recognized unit declaration keeps
  multiplier 1.0 and `unit: None` — fail closed; values are never invented.
  An image-only PDF fails closed with an explicit parse note; it is never
  reported as a parsed document with no KPIs.
- **KPI normalization** — `acquisition/ir.py` maps JP/EN labels to canonical KPI
  names and normalizes values to yen, handling per-table unit declarations,
  document-level captions such as `(Millions of yen)`, and compound yen chains
  such as `当期利益892億74百万円`. A first observation of a document carries no
  diff: reporting one would fabricate a previous version.
- **Run counting semantics** — a monitoring run reports the discovered
  documents in disjoint buckets: `new` (URL + new content fetched this run),
  `revised` (content changed behind a known URL), `unchanged`
  (content-verified again), and `seen` (URL known but only classified by
  URL — beyond this run's fetch budget). `unchanged_count` never includes
  URL-only records: `unchanged {n} · seen {m}` in the CLI counts them
  separately, and `timeline_entries` equals the number of entries actually
  appended to the on-disk timeline, not the number of statuses.
- **Diff and history** — a revision is a changed content hash behind a stable
  URL, so `kpi_diff` compares against the last recorded observation of the same
  URL and reports `increase` / `decrease` / `revision` / `added` / `removed` with
  a signed delta. Both versions stay in the KPI history and the timeline.
- **Sources** — company IR sources are operator configuration and persist in the
  registry (`data/private-acquisition/ir-sources/sources.json`), so they survive
  a process restart.

API surface: `/v1/ir/sources` (list/register/get), `/v1/ir/sources/{id}/monitor`,
`/v1/ir/instruments/{symbol}/timeline`, `/v1/ir/documents/kpi-history`. CLI:
`yowayowa ir sources|add-source|monitor|timeline|kpi-history`. Same guards as
`/v1/private`: 403 outside personal mode or with private connectors disabled.

PDF extraction requires the `operator-ir` extra (`pdfminer.six`), installed by
`make install`, CI, and the Dockerfile. Without it, PDFs fail closed with a note
naming the extra; HTML/XLSX/CSV processing is unaffected.

Add a company with:

```
yowayowa ir add-source nitorihd-ir --symbol 9843.T --provider nitorihd.co.jp \
  --listing-url https://www.nitorihd.co.jp/ir/library/summary.html
yowayowa ir monitor nitorihd-ir
```

## Broker control

Broker control is also a required Full / Operator capability, including brokers without a conventional public API.

The generic broker boundary lives in `broker_models.py`. A broker connector should expose account state, positions/orders where available, order preview, submission, cancellation, and explicit capabilities.

Transport examples:
- official broker API;
- persistent Chromium/Chrome session on Linux;
- authenticated private HTTP/JSON/GraphQL/WebSocket calls made from the same session;
- DOM-based order entry/confirmation when the network contract is too fragile or opaque;
- official desktop/local integration as a broker/product-specific accelerator;
- authenticated account scraping for read-only data.

Broker credentials and trading passwords must remain local whenever practical. Do not persist plaintext credentials in Yowayowa's database or send them to Vercel.

### Live order interlock

Read-only broker control and live submission are separate capabilities.

Live order submission requires:
- `personal` mode;
- private connectors enabled;
- broker control enabled;
- the live-order arm switch enabled;
- an explicit single-order notional limit;
- an explicit daily order-count limit.

These are anti-accident interlocks, not product limitations. The operator can raise the limits deliberately.

Every live connector must additionally implement idempotency/multi-submit protection and preserve an audit trail of intent, request, broker response, and later order state.

## Rakuten Securities first path

Rakuten Securities is the first concrete broker target.

The primary path is a persistent authenticated web session because:
- the operator environment is Linux;
- Rakuten Web covers domestic equities and U.S. equities, including U.S. cash and margin trading;
- MARKET SPEED II RSS is Windows-only and does not support foreign equities.

The first Rakuten connector should therefore keep a persistent Chromium profile locally, allow the operator to complete normal login/MFA, and then use the authenticated session for:
- account/position/order reads;
- Japanese and U.S. equity order entry;
- order status and cancellation;
- structured data extraction;
- broker-internal HTTP/JSON calls when their contract is stable enough;
- DOM operations when the private network contract is not sufficiently stable.

The connector must not assume that every action should be implemented as DOM clicking. Prefer the most robust path within the same authenticated browser session on a per-operation basis.

MARKET SPEED II RSS remains valuable for domestic equities and supported Japanese derivatives. It is an optional Windows-side acceleration/execution transport, not the Full / Operator architecture. Yowayowa keeps the existing RSS models and bridge so a Windows machine can be attached later if useful.

The official VBA function for domestic cash equities is:

`RssStockOrder_V(order_id, symbol, side, order_kind, sor, quantity, price_kind, price, execution_condition, expiration, account_type, stop_trigger_price, stop_trigger_condition, stop_price_kind, stop_price, set_order_kind, set_order_price, set_order_execution_condition, set_order_expiration)`

Yowayowa models this signature in `providers/rakuten_ms2_rss.py`.

## Deployment

Vercel remains useful for the public/safe application and remotely accessible research surfaces, but it is not the execution host for local broker control.

Full / Operator deployments may be:
- local Linux desktop/service with a persistent browser profile;
- private LAN/VPN service;
- a split architecture with hosted research plus a local Operator Bridge;
- optional Windows sidecar for broker-specific interfaces such as MARKET SPEED II RSS.

Secrets and authenticated sessions should be kept as close to the local operator as possible.
