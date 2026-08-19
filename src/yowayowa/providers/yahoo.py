from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import yfinance as yf
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import (
    LicenseClass,
    MarketHistory,
    MarketOverview,
    MarketOverviewItem,
    MarketQuote,
    MarketQuoteBatch,
    PriceBar,
    Provenance,
)
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.technical import compute_indicators


@dataclass(frozen=True, slots=True)
class MarketInstrumentSpec:
    symbol: str
    label: str
    category: str
    unit: str


DEFAULT_MARKET_UNIVERSE: tuple[MarketInstrumentSpec, ...] = (
    MarketInstrumentSpec("^GSPC", "S&P 500", "US equities", "points"),
    MarketInstrumentSpec("^IXIC", "Nasdaq Composite", "US equities", "points"),
    MarketInstrumentSpec("^DJI", "Dow Jones", "US equities", "points"),
    MarketInstrumentSpec("^N225", "Nikkei 225", "Japan equities", "points"),
    MarketInstrumentSpec("^VIX", "VIX", "Volatility", "index"),
    MarketInstrumentSpec("GC=F", "Gold", "Commodities", "USD/oz"),
    MarketInstrumentSpec("CL=F", "WTI Crude", "Commodities", "USD/bbl"),
    MarketInstrumentSpec("JPY=X", "USD / JPY", "FX", "JPY"),
    MarketInstrumentSpec("BTC-USD", "Bitcoin", "Crypto", "USD"),
)


class YahooMarketProvider:
    descriptor = ProviderDescriptor(
        name="yahoo",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Yahoo Finance via yfinance; enabled for personal use, denied in public mode by "
            "default."
        ),
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        ttl = max(30, settings.cache_ttl_seconds)
        self._history_cache: TTLCache[tuple[str, str, str, tuple[str, ...]], MarketHistory] = (
            TTLCache(maxsize=256, ttl=ttl)
        )
        self._quote_cache: TTLCache[tuple[str, ...], MarketQuoteBatch] = TTLCache(
            maxsize=128, ttl=ttl
        )
        self._overview_cache: TTLCache[str, MarketOverview] = TTLCache(maxsize=4, ttl=ttl)

    def history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
        indicators: list[str] | None = None,
    ) -> MarketHistory:
        indicators = indicators or []
        cache_key = (symbol.upper(), period, interval, tuple(indicators))
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        frame = yf.Ticker(symbol.upper()).history(
            period=period, interval=interval, auto_adjust=False
        )
        if frame.empty:
            raise LookupError(f"No market history returned for {symbol.upper()}")
        frame = frame.rename(columns=str.lower)
        bars = [
            PriceBar(
                timestamp=self._utc_timestamp(index),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if pd.notna(row.get("volume")) else None,
            )
            for index, row in frame.iterrows()
            if pd.notna(row.get("close"))
        ]
        computed = compute_indicators(frame, indicators)
        now = datetime.now(UTC)
        result = MarketHistory(
            symbol=symbol.upper(),
            interval=interval,
            bars=bars,
            indicators=computed,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url=f"https://finance.yahoo.com/quote/{symbol.upper()}",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=bars[-1].timestamp if bars else now,
                notes=[
                    "Personal-use provider. Public redistribution is blocked by provider policy."
                ],
            ),
        )
        self._history_cache[cache_key] = result
        return result

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        normalized = tuple(sorted({symbol.upper().strip() for symbol in symbols if symbol.strip()}))
        cached = self._quote_cache.get(normalized)
        if cached is not None:
            return cached
        if not normalized:
            result = self._quotes_from_frame(pd.DataFrame(), normalized)
            self._quote_cache[normalized] = result
            return result
        frame = yf.download(
            tickers=list(normalized),
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            ignore_tz=True,
            multi_level_index=True,
        )
        result = self._quotes_from_frame(frame if frame is not None else pd.DataFrame(), normalized)
        self._quote_cache[normalized] = result
        return result

    def overview(self) -> MarketOverview:
        cached = self._overview_cache.get("default")
        if cached is not None:
            return cached
        symbols = [spec.symbol for spec in DEFAULT_MARKET_UNIVERSE]
        frame = yf.download(
            tickers=symbols,
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            ignore_tz=True,
            multi_level_index=True,
        )
        if frame is None or frame.empty:
            raise LookupError("No market overview data returned")
        result = self._overview_from_frame(frame, DEFAULT_MARKET_UNIVERSE)
        self._overview_cache["default"] = result
        return result

    @staticmethod
    def _quotes_from_frame(frame: pd.DataFrame, symbols: tuple[str, ...]) -> MarketQuoteBatch:
        quotes: dict[str, MarketQuote] = {}
        unavailable: list[str] = []
        for symbol in symbols:
            symbol_frame = YahooMarketProvider._symbol_frame(frame, symbol)
            close = YahooMarketProvider._close_series(symbol_frame)
            if close is None or close.empty:
                unavailable.append(symbol)
                continue
            latest = float(close.iloc[-1])
            quotes[symbol] = MarketQuote(
                symbol=symbol,
                price=latest,
                previous_close=float(close.iloc[-2]) if len(close) > 1 else None,
                as_of=YahooMarketProvider._utc_timestamp(close.index[-1]),
            )
        now = datetime.now(UTC)
        latest_as_of = max((quote.as_of for quote in quotes.values()), default=now)
        return MarketQuoteBatch(
            quotes=quotes,
            unavailable_symbols=unavailable,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url="https://finance.yahoo.com/markets/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=latest_as_of,
                notes=[
                    "Batched 5-day daily history used for latest and previous closes.",
                    "Personal-use provider. Public redistribution is blocked by provider policy.",
                ],
            ),
        )

    @staticmethod
    def _overview_from_frame(
        frame: pd.DataFrame,
        specs: tuple[MarketInstrumentSpec, ...] = DEFAULT_MARKET_UNIVERSE,
    ) -> MarketOverview:
        items: list[MarketOverviewItem] = []
        unavailable: list[str] = []
        for spec in specs:
            symbol_frame = YahooMarketProvider._symbol_frame(frame, spec.symbol)
            close = YahooMarketProvider._close_series(symbol_frame)
            if close is None or close.empty:
                unavailable.append(spec.symbol)
                continue
            close.index = pd.DatetimeIndex(
                [YahooMarketProvider._utc_timestamp(index) for index in close.index]
            )
            close = close.sort_index()
            latest = float(close.iloc[-1])
            as_of = YahooMarketProvider._utc_timestamp(close.index[-1])
            item = MarketOverviewItem(
                symbol=spec.symbol,
                label=spec.label,
                category=spec.category,
                unit=spec.unit,
                value=latest,
                change_1d=YahooMarketProvider._change(
                    latest,
                    close.iloc[-2] if len(close) > 1 else None,
                ),
                change_1m=YahooMarketProvider._period_change(close, months=1),
                change_3m=YahooMarketProvider._period_change(close, months=3),
                change_1y=YahooMarketProvider._change(
                    latest,
                    close.iloc[0] if len(close) > 1 else None,
                ),
                sparkline=[
                    (YahooMarketProvider._utc_timestamp(index), float(value))
                    for index, value in close.iloc[-30:].items()
                ],
                as_of=as_of,
            )
            items.append(item)

        now = datetime.now(UTC)
        latest_as_of = max((item.as_of for item in items), default=now)
        return MarketOverview(
            items=items,
            unavailable_symbols=unavailable,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url="https://finance.yahoo.com/markets/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=latest_as_of,
                notes=[
                    "Batched daily history via yfinance.",
                    "Personal-use provider. Public redistribution is blocked by provider policy.",
                ],
            ),
        )

    @staticmethod
    def _symbol_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
        if frame.empty:
            return None
        if isinstance(frame.columns, pd.MultiIndex):
            level_zero = {str(value) for value in frame.columns.get_level_values(0)}
            if symbol in level_zero:
                primary = frame[symbol]
                return primary if isinstance(primary, pd.DataFrame) else primary.to_frame()
            level_one = {str(value) for value in frame.columns.get_level_values(1)}
            if symbol in level_one:
                secondary = frame.xs(symbol, axis=1, level=1)
                return secondary if isinstance(secondary, pd.DataFrame) else secondary.to_frame()
            return None
        return frame

    @staticmethod
    def _close_series(frame: pd.DataFrame | None) -> pd.Series | None:
        if frame is None:
            return None
        close_column = next(
            (column for column in frame.columns if str(column).casefold() == "close"),
            None,
        )
        if close_column is None:
            return None
        raw_close = frame.loc[:, close_column]
        if isinstance(raw_close, pd.DataFrame):
            raw_close = raw_close.iloc[:, 0]
        close = pd.to_numeric(raw_close, errors="coerce").dropna()
        return close.sort_index()

    @staticmethod
    def _period_change(close: pd.Series, *, months: int) -> float | None:
        latest_index = close.index[-1]
        target = latest_index - pd.DateOffset(months=months)
        on_or_after = close.loc[close.index >= target]
        baseline = on_or_after.iloc[0] if not on_or_after.empty else close.iloc[0]
        return YahooMarketProvider._change(float(close.iloc[-1]), baseline)

    @staticmethod
    def _change(latest: float, baseline: Any | None) -> float | None:
        if baseline is None or bool(pd.isna(baseline)):
            return None
        base = float(baseline)
        if base == 0:
            return None
        return latest / base - 1

    @staticmethod
    def _utc_timestamp(value: Any) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()
