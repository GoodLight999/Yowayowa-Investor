from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import yfinance as yf

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Portfolio, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.symbols import normalize_currency, normalize_symbol


class HistoricalReturnData:
    def __init__(
        self,
        returns: pd.DataFrame,
        benchmark_returns: pd.Series,
        unavailable_symbols: list[str],
        provenance: Provenance,
    ) -> None:
        self.returns = returns
        self.benchmark_returns = benchmark_returns
        self.unavailable_symbols = unavailable_symbols
        self.provenance = provenance


class YahooRiskProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-risk",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="Yahoo Finance adjusted daily history for portfolio risk; personal use only.",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings

    def portfolio_returns(
        self,
        portfolio: Portfolio,
        benchmark: str = "^GSPC",
        period: str = "1y",
    ) -> HistoricalReturnData:
        benchmark = normalize_symbol(benchmark)
        base_currency = normalize_currency(portfolio.base_currency)
        symbols = [position.symbol for position in portfolio.positions]
        fx_by_currency = {
            position.currency: self._fx_symbol(position.currency, base_currency)
            for position in portfolio.positions
            if position.currency != base_currency
        }
        tickers = list(dict.fromkeys([*symbols, benchmark, *fx_by_currency.values()]))
        frame = yf.download(
            tickers=tickers,
            period=period,
            interval="1d",
            auto_adjust=True,
            actions=False,
            progress=False,
            group_by="column",
            threads=True,
            timeout=self.settings.request_timeout_seconds,
        )
        close = self._close_frame(frame, tickers)
        returns = close.pct_change(fill_method=None)
        result = pd.DataFrame(index=returns.index)
        unavailable: list[str] = []
        for position in portfolio.positions:
            local = returns.get(position.symbol)
            if local is None or local.dropna().empty:
                unavailable.append(position.symbol)
                continue
            combined = local.copy()
            if position.currency != base_currency:
                fx_symbol = fx_by_currency[position.currency]
                fx_return = returns.get(fx_symbol)
                if fx_return is None or fx_return.dropna().empty:
                    unavailable.append(position.symbol)
                    continue
                combined = (1 + local) * (1 + fx_return) - 1
            result[position.symbol] = combined
        benchmark_returns = returns.get(benchmark)
        if benchmark_returns is None:
            benchmark_returns = pd.Series(dtype=float, name=benchmark)
        result = result.dropna(how="all")
        now = datetime.now(UTC)
        provenance = Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance adjusted daily history",
            source_url="https://finance.yahoo.com/",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=now,
            as_of=self._as_of(close, now),
            notes=[
                "Risk returns use adjusted daily closing prices.",
                (
                    "Foreign-currency position returns include daily Yahoo FX movement "
                    "into the portfolio base currency."
                ),
                "Historical statistics describe the sampled period and are not forecasts.",
            ],
        )
        return HistoricalReturnData(
            returns=result,
            benchmark_returns=benchmark_returns,
            unavailable_symbols=unavailable,
            provenance=provenance,
        )

    @staticmethod
    def _fx_symbol(source: str, target: str) -> str:
        source = normalize_currency(source)
        target = normalize_currency(target)
        if source == target:
            raise ValueError("FX symbol requested for identical currencies")
        if source == "USD":
            return f"{target}=X"
        if target == "USD":
            return f"{source}USD=X"
        return f"{source}{target}=X"

    @staticmethod
    def _close_frame(frame: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=tickers, dtype=float)
        if isinstance(frame.columns, pd.MultiIndex):
            level0 = set(str(item) for item in frame.columns.get_level_values(0))
            level1 = set(str(item) for item in frame.columns.get_level_values(1))
            selected: pd.Series | pd.DataFrame
            if "Close" in level0:
                selected = frame["Close"]
            elif "Close" in level1:
                selected = frame.xs("Close", axis=1, level=1)
            else:
                return pd.DataFrame(columns=tickers, index=frame.index, dtype=float)
            close_frame = (
                selected.to_frame(name=tickers[0]) if isinstance(selected, pd.Series) else selected
            )
            return close_frame.reindex(columns=tickers)
        if "Close" in frame.columns:
            series = frame["Close"]
            if isinstance(series, pd.Series):
                name = tickers[0] if len(tickers) == 1 else str(series.name or tickers[0])
                return series.to_frame(name=name)
        return pd.DataFrame(columns=tickers, index=frame.index, dtype=float)

    @staticmethod
    def _as_of(frame: pd.DataFrame, fallback: datetime) -> datetime:
        if frame.empty:
            return fallback
        index = frame.dropna(how="all").index
        if len(index) == 0:
            return fallback
        timestamp = pd.Timestamp(index[-1])
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()
