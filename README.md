# Yowayowa-Investor

Yowayowa-Investor is a private-operator-first investment research and execution workspace. Its primary Full / Operator profile is built for aggressive personal automation; the general-public profile is intentionally safer and more limited.

The canonical product specification lives in Notion. This repository implements it as a provider-agnostic modular monolith: one domain model powers the browser UI, REST API, CLI, automated tests, and future agent operations.

## What is implemented

- SEC EDGAR ticker search and normalized US-GAAP company facts with source provenance.
- Market-history adapter with SMA / EMA / RSI through `pandas-ta-classic`.
- Cross-asset Markets workspace and FX-aware Portfolio valuation.
- TradingView Lightweight Charts 5.2 browser charting with required attribution enabled.
- Multi-condition financial screening and multi-symbol comparison with shared financial definitions.
- Persistent watchlists and portfolios through SQLAlchemy; SQLite locally and PostgreSQL for durable hosted use.
- Public-safe official macro research through BLS, BEA, Japan e-Stat, and U.S. Treasury sources.
- EDINET v2 Japanese filing search, persistent company filing history, and normalized financial facts.
- Generic FRED BYOK access in personal mode only because underlying series rights vary by source.
- Natural-language operation planning: deterministic commands first, optional OpenAI-compatible BYOK fallback, and transparent operation plans.
- Strict market-symbol and currency normalization at persistence boundaries.
- Full / Operator private connectors: personal-only data, authenticated scraping, private-protocol integrations, and local broker-control bridges; public mode fails closed.
- Broker-control foundation with explicit live-order interlocks and a Rakuten Securities MARKET SPEED II RSS transport model.
- Responsive dark UI, generated OpenAPI document, Typer CLI, Docker image, and GitHub Actions verification.

## Data policy

Every data-bearing response carries provenance: provider, original source, license class, retrieval time, as-of time, and source URL when available. `YahooMarketProvider` is deliberately classified as `personal_only`; it is rejected when `YOWAYOWA_MODE=public`. Public deployment therefore requires a redistributable/licensed market-data provider rather than silently reusing personal-use data.

SEC data is obtained from official `data.sec.gov` endpoints. Configure a real contact address in `YOWAYOWA_SEC_USER_AGENT` before using the service beyond local evaluation.

Japanese government statistics use e-Stat's official REST API. The normal path is discovery -> metadata/dimensions -> bounded fact retrieval, rather than hard-coding statistics-table IDs that may change during revisions. See [`docs/ESTAT.md`](docs/ESTAT.md).

See [`docs/DATA_POLICY.md`](docs/DATA_POLICY.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and [`docs/OPERATOR_MODE.md`](docs/OPERATOR_MODE.md).

## Run

```bash
cp .env.example .env
# Edit YOWAYOWA_SEC_USER_AGENT to include a real contact address.
uv sync --extra dev
uv run uvicorn yowayowa.api.app:app --reload
```

Open `http://127.0.0.1:8000`.

Docker:

```bash
docker compose up --build
```

## Vercel

The root `app.py` exports the same FastAPI application for Vercel; there is no separate hosted implementation.

For a personal-use deployment:

- keep Vercel Authentication enabled while using the personal-only Yahoo/yfinance provider;
- set `DATABASE_URL` to a pooled PostgreSQL connection for durable Watchlists and Portfolios;
- when `DATABASE_URL` is absent on Vercel, the application uses `/tmp` SQLite only as an ephemeral preview/smoke-test fallback;
- never commit database credentials or provider keys to the repository.

PostgreSQL URLs are normalized to SQLAlchemy's psycopg 3 driver and non-SQLite connections use serverless-safe pool health checks.

## Verify

```bash
make verify
```

This runs formatting/lint checks, strict type checking, tests with coverage, and OpenAPI generation. GitHub Actions additionally starts the real Uvicorn application and runs Playwright/Chromium E2E, including 390px mobile overflow checks and browser research workflows.

## CLI examples

```bash
yowayowa search rocket
yowayowa fundamentals RKLB
yowayowa history RKLB --period 1y --indicators sma20,rsi14
yowayowa watchlist add 1 RKLB ASTS
yowayowa screen RKLB ASTS SOFI HOOD --filter operating_margin:gt:0
yowayowa macro estat-search "消費者物価指数"
yowayowa plan 'RKLB、ASTS、SOFI、HOODをウォッチリストに入れて'
```

## Architecture principles

- Financial correctness and traceability outrank convenience.
- Missing data is never silently coerced to zero.
- Business logic lives below HTTP/UI layers; GUI-only business rules are prohibited.
- External providers are replaceable policy boundaries.
- Reuse maintained libraries and official APIs instead of rebuilding commodity infrastructure.
- OpenBB's provider abstraction is a useful reference, but its AGPL code is not copied or linked into this commercial codebase.

See [`AGENTS.md`](AGENTS.md) for implementation invariants.


## Full / Operator mode

`YOWAYOWA_MODE=personal` is the primary profile. It may use private/authenticated scraping, non-public application protocols, local AI/CLI tools, and local broker bridges. `public` is the constrained safe profile and forcibly disables scraping/private connectors/broker control.

For Windows broker execution experiments:

```bash
uv sync --extra dev --extra operator-windows
```

Live order submission is off by default and requires explicit notional and daily-order limits. See `docs/OPERATOR_MODE.md`.
