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
from yowayowa.fx_models import FxHistory, FxHistoryPoint, FxRateSnapshot
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy
from yowayowa.providers.fundamentals import is_non_us_exchange_listing
from yowayowa.quote_currency import (
    CURRENCY_REASON_INVALID,
    CURRENCY_REASON_LOOKUP_FAILED,
    CURRENCY_REASON_NOT_PROVIDED,
    CURRENCY_SOURCE_FAST_INFO,
)
from yowayowa.symbols import InputValidationError, normalize_currency
from yowayowa.technical import compute_indicators


@dataclass(frozen=True, slots=True)
class MarketInstrumentSpec:
    symbol: str
    label: str
    category: str
    unit: str


# --- Price-currency contract for quotes (P5 R4) ------------------------------
# ``currency_source`` records where a resolved code came from; ``currency_reason``
# is the machine-readable explanation carried when the provider supplied none.
# The vocabulary lives in ``yowayowa.quote_currency`` so every quote surface and
# the valuation response share one set of strings.


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
        # A listing's price currency is stable; keep resolved values long
        # enough that the per-symbol lookup is paid once per session, and never
        # cache a miss (a transient provider failure must be retried).
        self._quote_currency_cache: TTLCache[str, tuple[str, str]] = TTLCache[str, tuple[str, str]](
            maxsize=2048, ttl=max(ttl, 12 * 3600)
        )
        self._overview_cache: TTLCache[str, MarketOverview] = TTLCache(maxsize=4, ttl=ttl)
        self._fx_history_cache: TTLCache[tuple[str, str, str], FxHistory] = TTLCache(
            maxsize=128, ttl=ttl
        )
        self._fx_rate_cache: TTLCache[str, FxRateSnapshot] = TTLCache(maxsize=64, ttl=ttl)

    def history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
        indicators: list[str] | None = None,
    ) -> MarketHistory:
        indicators = indicators or []
        normalized_symbol = symbol.upper().strip()
        provider_symbol = self._provider_symbol(normalized_symbol)
        cache_key = (normalized_symbol, period, interval, tuple(indicators))
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        frame = yf.Ticker(provider_symbol).history(
            period=period, interval=interval, auto_adjust=False
        )
        if frame.empty:
            raise LookupError(f"No market history returned for {normalized_symbol}")
        frame = frame.rename(columns=str.lower)
        # Provider rows are not guaranteed to arrive in date order; ordering the
        # frame chronologically keeps bar order and the published as_of honest
        # (F3: a reversed response used to publish a stale as_of).
        ordered = frame.sort_index(kind="stable")
        bars = [
            PriceBar(
                timestamp=self._utc_timestamp(index),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if pd.notna(row.get("volume")) else None,
            )
            for index, row in ordered.iterrows()
            if pd.notna(row.get("close"))
        ]
        computed = compute_indicators(ordered, indicators)
        now = datetime.now(UTC)
        result = MarketHistory(
            symbol=normalized_symbol,
            interval=interval,
            bars=bars,
            indicators=computed,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance",
                source_url=f"https://finance.yahoo.com/quote/{provider_symbol}",
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
        provider_symbols = sorted({self._provider_symbol(symbol) for symbol in normalized})
        frame = yf.download(
            tickers=provider_symbols,
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
        result = self._with_price_currency(result)
        self._quote_cache[normalized] = result
        return result

    def _with_price_currency(self, batch: MarketQuoteBatch) -> MarketQuoteBatch:
        """Attach the provider-resolved price currency to each priced quote.

        Only listings that produced a price are looked up: an unavailable
        symbol has nothing to denominate. The lookup never breaks the quote
        itself — a provider failure becomes ``currency_reason`` instead.
        """

        if not batch.quotes:
            return batch
        quotes: dict[str, MarketQuote] = {}
        for symbol, quote in batch.quotes.items():
            currency, source, reason = self._quote_currency(self._provider_symbol(symbol))
            quotes[symbol] = quote.model_copy(
                update={
                    "currency": currency,
                    "currency_source": source,
                    "currency_reason": reason,
                }
            )
        return batch.model_copy(update={"quotes": quotes})

    def fx_quote(self, pair: str) -> FxRateSnapshot:
        """Latest FX rate for a normalized pair, from the same Yahoo source."""

        from yowayowa.fx_models import fx_pair_to_yahoo, normalize_fx_pair

        normalized = normalize_fx_pair(pair)
        provider_symbol = fx_pair_to_yahoo(normalized)
        cached = self._fx_rate_cache.get(normalized)
        if cached is not None:
            return cached
        frame = yf.Ticker(provider_symbol).history(period="5d", interval="1d", auto_adjust=False)
        close = self._close_series(frame.rename(columns=str.lower) if not frame.empty else frame)
        # A non-positive quote is not a rate: 0.0 (or a negative print) is a bad
        # row, not a zero-valued currency pair (F2).
        if close is not None:
            close = close[close > 0]
        if close is None or close.empty:
            raise LookupError(f"No FX rate returned for {normalized}")
        snapshot = FxRateSnapshot(
            pair=normalized,
            rate=float(close.iloc[-1]),
            previous_close=float(close.iloc[-2]) if len(close) > 1 else None,
            as_of=self._utc_timestamp(close.index[-1]),
            provenance=self._fx_provenance(provider_symbol, close.index[-1]),
        )
        self._fx_rate_cache[normalized] = snapshot
        return snapshot

    def fx_history(
        self,
        pair: str,
        *,
        interval: str = "1d",
        period: str = "1mo",
    ) -> FxHistory:
        """FX OHLC history for a normalized pair; missing fields stay None."""

        from yowayowa.fx_models import fx_pair_to_yahoo, normalize_fx_pair

        normalized = normalize_fx_pair(pair)
        provider_symbol = fx_pair_to_yahoo(normalized)
        cache_key = (normalized, period, interval)
        cached = self._fx_history_cache.get(cache_key)
        if cached is not None:
            return cached
        frame = yf.Ticker(provider_symbol).history(
            period=period, interval=interval, auto_adjust=False
        )
        points: list[FxHistoryPoint] = []
        if not frame.empty:
            frame = frame.rename(columns=str.lower)
            for index, row in frame.iterrows():
                # Only fully-absent rows are skipped; individual None fields
                # are preserved as None (never zero-filled).
                if all(pd.isna(row.get(field)) for field in ("open", "high", "low", "close")):
                    continue
                points.append(
                    FxHistoryPoint(
                        timestamp=self._utc_timestamp(index),
                        open=float(row["open"]) if pd.notna(row.get("open")) else None,
                        high=float(row["high"]) if pd.notna(row.get("high")) else None,
                        low=float(row["low"]) if pd.notna(row.get("low")) else None,
                        close=float(row["close"]) if pd.notna(row.get("close")) else None,
                        volume=float(row["volume"]) if pd.notna(row.get("volume")) else None,
                    )
                )
        if not points:
            raise LookupError(f"No FX history returned for {normalized}")
        result = FxHistory(
            pair=normalized,
            interval=interval,
            points=points,
            provenance=self._fx_provenance(provider_symbol, points[-1].timestamp),
        )
        self._fx_history_cache[cache_key] = result
        return result

    @staticmethod
    def _fx_provenance(provider_symbol: str, as_of: Any) -> Provenance:
        return Provenance(
            provider="yahoo/yfinance",
            source="Yahoo Finance",
            source_url=f"https://finance.yahoo.com/quote/{provider_symbol}",
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=datetime.now(UTC),
            as_of=YahooMarketProvider._utc_timestamp(as_of),
            notes=["Personal-use provider. Public redistribution is blocked by provider policy."],
        )

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
    def _provider_symbol(symbol: str) -> str:
        """Translate only US class-share dot notation to Yahoo's dash notation."""

        normalized = symbol.upper().strip()
        if is_non_us_exchange_listing(normalized):
            return normalized
        base, separator, class_code = normalized.rpartition(".")
        if separator and base and len(class_code) == 1 and class_code.isalpha():
            return f"{base}-{class_code}"
        return normalized

    def _quote_currency(self, provider_symbol: str) -> tuple[str | None, str | None, str | None]:
        """Price currency for one listing as ``(currency, source, reason)``.

        The value comes from the provider's own quote metadata; it is never
        inferred from a ticker suffix or an exchange table. When the provider
        cannot supply a usable code the currency stays None and ``reason``
        carries a machine-readable code, so callers can distinguish
        "provider says nothing" from "not looked up yet" (P5 R4).
        """

        cached = self._quote_currency_cache.get(provider_symbol)
        if cached is not None:
            currency, source = cached
            return currency, source, None
        try:
            fast_info = yf.Ticker(provider_symbol).fast_info
            raw = fast_info.get("currency") if hasattr(fast_info, "get") else None
        except Exception:
            return None, None, CURRENCY_REASON_LOOKUP_FAILED
        text = "" if raw is None else str(raw).strip().upper()
        if not text:
            return None, None, CURRENCY_REASON_NOT_PROVIDED
        try:
            code = normalize_currency(text)
        except InputValidationError:
            return None, None, CURRENCY_REASON_INVALID
        # Cache successes only: a transient failure must be retried, not frozen.
        self._quote_currency_cache[provider_symbol] = (code, CURRENCY_SOURCE_FAST_INFO)
        return code, CURRENCY_SOURCE_FAST_INFO, None

    @staticmethod
    def _quotes_from_frame(frame: pd.DataFrame, symbols: tuple[str, ...]) -> MarketQuoteBatch:
        quotes: dict[str, MarketQuote] = {}
        unavailable: list[str] = []
        # A flat (non-MultiIndex) frame carries no symbol identity: yfinance only
        # produces one for a single-ticker download. Attribute it to the first
        # requested symbol and leave the rest unavailable, so one listing's
        # series is never stamped onto every symbol (F1).
        flat_frame = not frame.empty and not isinstance(frame.columns, pd.MultiIndex)
        for position, symbol in enumerate(symbols):
            provider_symbol = YahooMarketProvider._provider_symbol(symbol)
            symbol_frame = (
                None
                if flat_frame and position > 0
                else YahooMarketProvider._symbol_frame(frame, provider_symbol)
            )
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
                    "Price currency is read from the provider's quote metadata; when the "
                    "provider supplies none, currency is null and currency_reason states why.",
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
        # Same rule as ``_quotes_from_frame``: a flat frame is only attributable
        # to the first spec, never duplicated onto every requested instrument (F1).
        flat_frame = not frame.empty and not isinstance(frame.columns, pd.MultiIndex)
        for position, spec in enumerate(specs):
            symbol_frame = (
                None
                if flat_frame and position > 0
                else YahooMarketProvider._symbol_frame(frame, spec.symbol)
            )
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
