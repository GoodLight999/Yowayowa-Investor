"""Frankfurter API provider: ECB reference exchange rates (analysis surface).

Frankfurter is an open, key-less mirror of the European Central Bank's official
reference exchange rates. It is the first FX source classified OFFICIAL_PUBLIC
in this product, so it is the first FX provider allowed in public mode.

Rate limits are deliberate and explicit:
- daily data only (ECB fixes rates once per business day at 16:00 CET); every
  other interval is refused with ``ValueError`` instead of being silently
  degraded;
- only ECB-covered currencies are served. Pairs containing a non-ECB currency
  or a crypto asset raise ``LookupError`` — they are never silently fallen back
  to a personal-only aggregator;
- missing data (HTTP 404 from the source) raises ``LookupError`` (HTTP 404
  upstream); an unreachable source raises ``FrankfurterTransportError`` (HTTP
  502 upstream). Missing data is never zero-filled.

Cross rates are computed by Frankfurter from the ECB EUR-base series; that
derivation is recorded in the provenance notes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
from cachetools import TTLCache

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.fx_models import FxHistory, FxHistoryPoint, FxRateSnapshot, normalize_fx_pair
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

# Currencies covered by the ECB reference-rate feed (verified against the live
# Frankfurter API on 2026-09-24). Deliberately explicit: a non-ECB currency is
# refused before any network call instead of relying on a remote API error.
ECB_REFERENCE_CURRENCIES: frozenset[str] = frozenset(
    {
        "AUD",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "CZK",
        "DKK",
        "EUR",
        "GBP",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "ISK",
        "JPY",
        "KRW",
        "MXN",
        "MYR",
        "NOK",
        "NZD",
        "PHP",
        "PLN",
        "RON",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "USD",
        "ZAR",
    }
)

# Primary host is the current api.frankfurter.dev/v1 service; the legacy
# api.frankfurter.app host remains as an explicit fallback constant.
FRANKFURTER_BASE_URL = "https://api.frankfurter.dev/v1"
_FRANKFURTER_LEGACY_BASE_URL = "https://api.frankfurter.app"

# Window for the latest-fix lookup: a range request returns both the most
# recent fix and the previous business day's fix in a single call.
_QUOTE_WINDOW_DAYS = 10

_SUPPORTED_INTERVALS: frozenset[str] = frozenset({"1d"})
_PERIOD_DAYS: dict[str, int] = {
    "1d": 3,
    "5d": 7,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 365,
    "2y": 730,
    "5y": 1826,
    "max": 3650,
}

_PROVIDED_NOTE = (
    "ECB reference exchange rates via Frankfurter; daily only (16:00 CET fix). "
    "Weekend/holiday queries resolve to the previous business day's rate."
)
_CROSS_NOTE = (
    "Cross rates (non-EUR pairs) are computed by Frankfurter from the ECB "
    "EUR-base reference series."
)


class FrankfurterTransportError(RuntimeError):
    """Frankfurter is unreachable on all known hosts (maps to HTTP 502)."""


def _is_ecb_pair(pair: str) -> bool:
    base, quote = pair[:3], pair[3:]
    return base in ECB_REFERENCE_CURRENCIES and quote in ECB_REFERENCE_CURRENCIES


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


class FrankfurterFxProvider:
    """ECB reference-rate FX provider for the /v1/fx analysis surface."""

    descriptor = ProviderDescriptor(
        name="frankfurter-ecb",
        license_class=LicenseClass.OFFICIAL_PUBLIC,
        redistributable=True,
        description=(
            "Frankfurter API serving European Central Bank reference exchange "
            "rates; official public source, key-less and free."
        ),
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.client = httpx.Client(
            timeout=settings.request_timeout_seconds,
            headers={"User-Agent": "Yowayowa-Investor/1.0 (+personal investment research)"},
        )
        ttl = max(300, settings.cache_ttl_seconds)
        self._rate_cache: TTLCache[str, FxRateSnapshot] = TTLCache(maxsize=64, ttl=ttl)
        self._history_cache: TTLCache[tuple[str, str, str], FxHistory] = TTLCache(
            maxsize=128, ttl=ttl
        )

    # ------------------------------------------------------------------ rate

    def fx_quote(self, pair: str) -> FxRateSnapshot:
        """Latest ECB reference rate for a normalized pair.

        One range request supplies the most recent fix and the previous
        business day's fix. ``previous_close`` stays ``None`` when the window
        holds a single observation; retrieval failure raises
        ``LookupError``/``FrankfurterTransportError`` — never a zero rate.
        """

        normalized = normalize_fx_pair(pair)
        self._require_ecb_pair(normalized)
        cached = self._rate_cache.get(normalized)
        if cached is not None:
            return cached
        series = self._recent_series(normalized, window_days=_QUOTE_WINDOW_DAYS)
        rate_date, rate = series[-1]
        previous_close = series[-2][1] if len(series) > 1 else None
        snapshot = FxRateSnapshot(
            pair=normalized,
            rate=rate,
            previous_close=previous_close,
            as_of=_utc_midnight(rate_date),
            provenance=self._fx_provenance(rate_date),
        )
        self._rate_cache[normalized] = snapshot
        return snapshot

    # ---------------------------------------------------------------- history

    def fx_history(self, pair: str, *, interval: str = "1d", period: str = "1mo") -> FxHistory:
        """Daily ECB reference-rate history for a normalized pair.

        ECB reference rates exist only as a daily series: any other interval is
        refused with ``ValueError`` (HTTP 422) rather than silently resampled.
        Non-trading days are absent from the source response and stay absent
        here (no forward-fill, no zero-fill).
        """

        normalized = normalize_fx_pair(pair)
        self._require_ecb_pair(normalized)
        if interval not in _SUPPORTED_INTERVALS:
            raise ValueError(
                f"Unsupported FX history interval {interval!r}: ECB reference rates are "
                "daily only; use interval=1d"
            )
        days = _PERIOD_DAYS.get(period)
        if days is None:
            raise ValueError(f"Unsupported FX history period {period!r}")
        cache_key = (normalized, period, interval)
        cached = self._history_cache.get(cache_key)
        if cached is not None:
            return cached
        series = self._recent_series(normalized, window_days=days)
        points = [
            FxHistoryPoint(timestamp=_utc_midnight(point_date), close=rate)
            for point_date, rate in series
        ]
        result = FxHistory(
            pair=normalized,
            interval=interval,
            points=points,
            provenance=self._fx_provenance(series[-1][0]),
        )
        self._history_cache[cache_key] = result
        return result

    # ----------------------------------------------------------------- shared

    def _recent_series(self, normalized: str, *, window_days: int) -> list[tuple[date, float]]:
        end = datetime.now(UTC).date()
        start = end - timedelta(days=window_days)
        payload = self._get_json(
            f"/{start.isoformat()}..{end.isoformat()}",
            params={"base": normalized[:3], "symbols": normalized[3:]},
        )
        return self._sorted_series(normalized, payload)

    def _require_ecb_pair(self, normalized: str) -> None:
        if not _is_ecb_pair(normalized):
            raise LookupError(
                f"Pair {normalized!r} is not covered by ECB reference rates; this provider "
                "does not fall back to personal-only aggregators"
            )

    def _get_json(self, path: str, *, params: dict[str, str]) -> dict[str, object]:
        failures: list[str] = []
        for base_url in (FRANKFURTER_BASE_URL, _FRANKFURTER_LEGACY_BASE_URL):
            try:
                response = self.client.get(f"{base_url}{path}", params=params)
            except httpx.HTTPError as exc:
                failures.append(f"{base_url}: {type(exc).__name__}")
                continue
            if response.status_code == 404:
                # Current API behavior (verified 2026-09-24): unknown symbol or
                # unresolvable date range returns HTTP 404 {"message": "not found"}.
                raise LookupError(
                    "Frankfurter has no rate data for the requested pair or range "
                    f"({self._error_message(response)})"
                )
            if response.is_error:
                failures.append(f"{base_url}: HTTP {response.status_code}")
                continue
            try:
                payload = response.json()
            except ValueError:
                failures.append(f"{base_url}: invalid JSON")
                continue
            if not isinstance(payload, dict):
                failures.append(f"{base_url}: unexpected payload shape")
                continue
            # Defensive: the documented-but-legacy behavior of reporting
            # unknown symbols as a 200 body is also treated as missing data.
            if payload.get("message") == "not found":
                raise LookupError(
                    "Frankfurter has no rate data for the requested pair or range (not found)"
                )
            return payload
        raise FrankfurterTransportError(
            "Frankfurter is unreachable (primary and fallback hosts failed): " + "; ".join(failures)
        )

    def _sorted_series(
        self, normalized: str, payload: dict[str, object]
    ) -> list[tuple[date, float]]:
        rates = payload.get("rates")
        if not isinstance(rates, dict) or not rates:
            raise LookupError(f"Frankfurter returned no rates for {normalized}")
        quote = normalized[3:]
        series: list[tuple[date, float]] = []
        for raw_date, day_rates in sorted(rates.items()):
            if not isinstance(day_rates, dict):
                continue
            raw_rate = day_rates.get(quote)
            if raw_rate is None:
                continue
            series.append((self._parse_date(raw_date), float(raw_rate)))
        if not series:
            raise LookupError(f"Frankfurter returned no rates for {normalized}")
        return series

    def _fx_provenance(self, as_of: date) -> Provenance:
        return Provenance(
            provider="frankfurter-ecb",
            source="European Central Bank reference exchange rates (via Frankfurter)",
            source_url="https://www.ecb.europa.eu/stats/policy_and_exchange_rates/"
            "euro_reference_exchange_rates/html/index.en.html",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=datetime.now(UTC),
            as_of=as_of,
            notes=[_PROVIDED_NOTE, _CROSS_NOTE],
        )

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        message = payload.get("message") if isinstance(payload, dict) else None
        return str(message) if message else f"HTTP {response.status_code}"

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise LookupError(f"Frankfurter returned an invalid date: {value!r}") from exc
