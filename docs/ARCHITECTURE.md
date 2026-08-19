# Architecture

## Shape

Yowayowa-Investor is a modular monolith: one deployable application, strict internal boundaries.

- **Domain models** are provider-neutral Pydantic objects.
- **Providers** acquire external data and attach provenance + licensing class.
- **Services** calculate normalized metrics and perform user operations.
- **FastAPI** is the canonical application boundary and OpenAPI contract.
- **CLI** calls the same HTTP API rather than owning separate business logic.
- **Web UI** is deliberately thin and consumes `/v1` endpoints.
- **SQLite** is the zero-cost personal default; SQLAlchemy keeps a PostgreSQL migration path open without splitting services.

This avoids premature microservices while preserving replaceable data sources.

## Provider policy

Every provider carries a `LicenseClass`. `personal_only` providers are rejected when the app runs in `public` mode. This is intentional: an open-source connector license and the upstream market-data redistribution rights are separate questions.

Current adapters:

| Adapter | Role | Policy |
|---|---|---|
| SEC EDGAR | US issuer search and standard XBRL facts | official public |
| Yahoo/yfinance | price history for personal instance | personal only |
| FRED | macro time series | BYOK / source terms vary |
| EDINET v2 | Japanese filing metadata/download entry point | BYOK |

## Provenance

Data-bearing responses include provider, source, source URL, retrieved time, effective/as-of time, license class and notes. Derived metrics are calculated only from normalized source facts; unavailable facts remain unavailable.

## AI operations

Natural-language requests compile into an `OperationPlan`, not a prose investment answer. A deterministic parser handles common high-confidence operations first. An optional OpenAI-compatible BYOK adapter is a fallback and its JSON output is validated against the same schema before the UI may apply it.

## Deliberate reuse

- TradingView Lightweight Charts for financial chart rendering.
- pandas-ta-classic for technical indicators.
- Arelle is an optional Apache-2.0 XBRL engine boundary for deeper EDINET/iXBRL processing.
- OpenBB is studied for its provider abstraction but is not linked into the application because its current platform is AGPL-3.0-only.
