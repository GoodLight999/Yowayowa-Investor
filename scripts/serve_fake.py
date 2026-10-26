"""Start the real Yowayowa FastAPI app with a fixture risk provider.

Reproduces the four P5 R10 benchmark scenarios deterministically without any
external (yfinance) network call and without a new data source:

  ^GSPC   — benchmark retrieved, correlation computed (success)
  ^TOPX   — benchmark ticker not found by the provider
  ^ERR    — provider raised an exception during download
  ^INSUF  — benchmark retrieved but too few aligned observations

Usage:
    .venv/bin/python scripts/serve_fake.py --port 8123

Then in another terminal:
    .venv/bin/yowayowa portfolio create Demo --base-currency USD --base-url http://127.0.0.1:8123
    .venv/bin/yowayowa portfolio position 1 AAA 10 --average-cost 50 --currency USD --base-url http://127.0.0.1:8123
    .venv/bin/yowayowa portfolio risk 1 --benchmark ^GSPC --base-url http://127.0.0.1:8123
"""

from __future__ import annotations

import os
import sys

# --- environment must be set BEFORE any yowayowa import (Settings is cached) ---
_DB_PATH = os.path.join(os.environ.get("HERMES_SCRATCH", "/tmp"), "serve_fake_r10.db")
os.environ.setdefault("YOWAYOWA_MODE", "personal")
os.environ.setdefault("YOWAYOWA_DATABASE_URL", f"sqlite:///{_DB_PATH}")
os.environ.setdefault("YOWAYOWA_API_TOKEN", "")  # no auth in personal mode

# --- fixture data ----------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

import pandas as pd  # noqa: E402

from yowayowa.domain import (  # noqa: E402
    LicenseClass,
    MarketQuote,
    MarketQuoteBatch,
    Provenance,
)
from yowayowa.providers.yahoo_risk import HistoricalReturnData  # noqa: E402

_INDEX = pd.date_range("2026-08-03", periods=5, tz="UTC")
_PORTFOLIO_RETURNS = pd.DataFrame(
    {"AAA": [0.02, -0.01, 0.03, -0.02, 0.01]},
    index=_INDEX,
)
_BENCH_RETURNS_SUCCESS = pd.Series(
    [0.01, -0.005, 0.015, -0.01, 0.005],
    index=_INDEX,
    name="^GSPC",
)

_PROVENANCE = Provenance(
    provider="fixture",
    source="P5 R10 serve_fake fixture data",
    license_class=LicenseClass.PERSONAL_ONLY,
    retrieved_at=datetime.now(UTC),
    as_of=datetime.now(UTC),
)


class _FakeMarketProvider:
    """Returns a fixed quote for AAA (USD) so portfolio_analytics runs offline."""

    descriptor = type(
        "desc",
        (),
        {"license_class": LicenseClass.PERSONAL_ONLY},
    )()

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        now = datetime.now(UTC)
        batch_quotes: dict[str, MarketQuote] = {}
        for sym in symbols:
            if sym == "AAA":
                batch_quotes[sym] = MarketQuote(
                    symbol="AAA",
                    price=51.0,
                    previous_close=50.0,
                    currency="USD",
                    currency_source="fixture",
                    as_of=now,
                )
        return MarketQuoteBatch(
            quotes=batch_quotes,
            unavailable_symbols=[s for s in symbols if s not in batch_quotes],
            provenance=_PROVENANCE,
        )


class _FakeRiskProvider:
    """Returns HistoricalReturnData based on the benchmark symbol."""

    def portfolio_returns(
        self,
        portfolio,  # Portfolio
        benchmark: str,
        period: str,
    ) -> HistoricalReturnData:
        bn = benchmark.upper()
        if bn == "^ERR":
            return HistoricalReturnData(
                returns=pd.DataFrame(),
                benchmark_returns=pd.Series(dtype=float, name=benchmark),
                unavailable_symbols=[p.symbol for p in portfolio.positions],
                provenance=_PROVENANCE,
                benchmark_status="provider_error",
            )
        if bn == "^TOPX":
            return HistoricalReturnData(
                returns=_PORTFOLIO_RETURNS.copy(),
                benchmark_returns=pd.Series(dtype=float, name=benchmark),
                unavailable_symbols=[],
                provenance=_PROVENANCE,
                benchmark_status="symbol_not_found",
            )
        if bn == "^INSUF":
            # Benchmark has 1 observation that aligns — too few for correlation.
            bench = pd.Series(
                [0.01],
                index=[_INDEX[0]],
                name=benchmark,
            )
            return HistoricalReturnData(
                returns=_PORTFOLIO_RETURNS.copy(),
                benchmark_returns=bench,
                unavailable_symbols=[],
                provenance=_PROVENANCE,
                benchmark_status="ok",
            )
        # ^GSPC and anything else: success scenario.
        return HistoricalReturnData(
            returns=_PORTFOLIO_RETURNS.copy(),
            benchmark_returns=_BENCH_RETURNS_SUCCESS.copy(),
            unavailable_symbols=[],
            provenance=_PROVENANCE,
            benchmark_status="ok",
        )


# --- wire the fakes into the risk route ------------------------------------
# Must happen AFTER the app module is imported (which imports risk_routes).
from yowayowa.api import risk_routes  # noqa: E402
from yowayowa.api.app import app  # noqa: E402

risk_routes.yahoo_market_provider = lambda: _FakeMarketProvider()  # type: ignore[assignment]
risk_routes.yahoo_risk_provider = lambda: _FakeRiskProvider()  # type: ignore[assignment]


if __name__ == "__main__":
    import uvicorn

    port = 8123
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"serve_fake: DB={_DB_PATH}  port={port}  scenarios: ^GSPC ^TOPX ^ERR ^INSUF")
    # Clean any stale DB so portfolio IDs are deterministic.
    if os.path.exists(_DB_PATH):
        os.remove(_DB_PATH)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
