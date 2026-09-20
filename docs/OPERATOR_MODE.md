# Full / Operator mode

Yowayowa-Investor is private-operator-first.

The primary product is the `personal` mode, which should be read as **Full / Operator mode**. It is allowed to use capabilities that are unsuitable for a public multi-tenant SaaS: authenticated scraping, private/non-public provider protocols, local software bridges, personal-only data sources, local credentials, local AI/CLI tools, and broker control for the operator's own accounts.

The `public` mode remains as a deliberately limited safe profile. Public-mode constraints must never force the Full / Operator implementation to become weaker.

## Connector priority

For data acquisition and broker control, use the strongest reliable interface available in this order:

1. official programmable API or local programmable interface;
2. authenticated private HTTP/JSON/GraphQL/WebSocket protocol used by the operator's own account;
3. structured scraping of authenticated pages or downloadable files;
4. operating-system or application automation where the product exposes only a local GUI/programmatic bridge;
5. browser/UI click automation only as a last-resort fallback.

Do not describe browser automation as the default architecture for broker control.

Authentication, MFA, CAPTCHA, device approval, and similar access controls are not bypass targets. The operator completes the broker's legitimate authentication flow; Yowayowa may then reuse the resulting authorized session or supported local execution interface.

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

## Broker control

Broker control is also a required Full / Operator capability, including brokers without a conventional public API.

The generic broker boundary lives in `broker_models.py`. A broker connector should expose account state, positions/orders where available, order preview, submission, cancellation, and explicit capabilities.

Transport examples:
- official broker API;
- official desktop/local integration;
- reverse-engineered authenticated private protocol;
- authenticated account scraping plus a supported submission path.

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

For domestic equities and supported derivatives, the preferred initial transport is **MARKET SPEED II RSS**, not browser automation. Rakuten documents that the RSS add-in supports investment data, order/execution information, and order functions, including VBA-callable order functions.

For domestic cash equities, the official VBA function is:

`RssStockOrder_V(order_id, symbol, side, order_kind, sor, quantity, price_kind, price, execution_condition, expiration, account_type, stop_trigger_price, stop_trigger_condition, stop_price_kind, stop_price, set_order_kind, set_order_price, set_order_execution_condition, set_order_expiration)`

Yowayowa models this signature in `providers/rakuten_ms2_rss.py`.

The runtime architecture should be:
- MARKET SPEED II + RSS + Excel remain on the operator's Windows machine;
- a local Operator Bridge talks to the RSS add-in/Excel automation interface;
- Yowayowa communicates with that bridge over a local authenticated IPC/localhost channel;
- the bridge never exposes broker credentials to the hosted application;
- order IDs are unique and persisted for idempotency;
- order acceptance is not treated as fill; order/status inquiry remains authoritative.

For products not exposed by MARKET SPEED II RSS, or for brokers without an equivalent local programmable interface, implement private-protocol or scraping connectors under the same broker boundary.

## Deployment

Vercel remains useful for the public/safe application and remotely accessible research surfaces, but it is not the execution host for local broker control.

Full / Operator deployments may be:
- local desktop/service;
- private LAN/VPN service;
- a split architecture with hosted research plus a local Operator Bridge.

Secrets and authenticated sessions should be kept as close to the local operator as possible.
