from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

import yowayowa.research_models as research_models
from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

FAIL_CLOSED_NOTE = "Quotes missing a filtered field are excluded (fail-closed)."

NUMERIC_FILTER_OPERATORS = frozenset({"eq", "gt", "lt", "gte", "lte", "btwn"})

SCREENER_FIELDS: dict[str, tuple[str, ...]] = {
    "identity": ("region", "exchange", "sector", "industry", "peer_group"),
    "price": (
        "intradayprice",
        "percentchange",
        "intradaymarketcap",
        "fiftytwowkpercentchange",
        "lastclose52weekhigh.lasttwelvemonths",
        "lastclose52weeklow.lasttwelvemonths",
    ),
    "trading": (
        "dayvolume",
        "avgdailyvol3m",
        "beta",
        "pctheldinsider",
        "pctheldinst",
    ),
    "short_interest": (
        "days_to_cover_short.value",
        "short_interest.value",
        "short_interest_percentage_change.value",
        "short_percentage_of_float.value",
        "short_percentage_of_shares_outstanding.value",
    ),
    "valuation": (
        "peratio.lasttwelvemonths",
        "lastclosepriceearnings.lasttwelvemonths",
        "pegratio_5y",
        "pricebookratio.quarterly",
        "lastclosemarketcaptotalrevenue.lasttwelvemonths",
        "lastclosetevtotalrevenue.lasttwelvemonths",
        "lastclosetevebit.lasttwelvemonths",
        "lastclosetevebitda.lasttwelvemonths",
    ),
    "profitability": (
        "returnonassets.lasttwelvemonths",
        "returnonequity.lasttwelvemonths",
        "returnontotalcapital.lasttwelvemonths",
        "forward_dividend_per_share",
        "forward_dividend_yield",
        "consecutive_years_of_dividend_growth_count",
    ),
    "growth_and_income": (
        "quarterlyrevenuegrowth.quarterly",
        "totalrevenues1yrgrowth.lasttwelvemonths",
        "epsgrowth.lasttwelvemonths",
        "dilutedeps1yrgrowth.lasttwelvemonths",
        "netincome1yrgrowth.lasttwelvemonths",
        "ebitda1yrgrowth.lasttwelvemonths",
        "cashfromoperations1yrgrowth.lasttwelvemonths",
        "leveredfreecashflow1yrgrowth.lasttwelvemonths",
        "grossprofitmargin.lasttwelvemonths",
        "ebitdamargin.lasttwelvemonths",
        "netincomemargin.lasttwelvemonths",
        "totalrevenues.lasttwelvemonths",
        "ebitda.lasttwelvemonths",
        "netincomeis.lasttwelvemonths",
    ),
    "balance_sheet": (
        "totalassets.lasttwelvemonths",
        "totalcashandshortterminvestments.lasttwelvemonths",
        "totalcurrentassets.lasttwelvemonths",
        "totalcurrentliabilities.lasttwelvemonths",
        "totaldebt.lasttwelvemonths",
        "totalequity.lasttwelvemonths",
        "totalsharesoutstanding",
        "currentratio.lasttwelvemonths",
        "quickratio.lasttwelvemonths",
        "totaldebtequity.lasttwelvemonths",
        "netdebtebitda.lasttwelvemonths",
    ),
    "cash_flow": (
        "cashfromoperations.lasttwelvemonths",
        "capitalexpenditure.lasttwelvemonths",
        "leveredfreecashflow.lasttwelvemonths",
        "unleveredfreecashflow.lasttwelvemonths",
    ),
    "esg": (
        "esg_score",
        "environmental_score",
        "social_score",
        "governance_score",
        "highest_controversy",
    ),
}

# Current EquityQuery region values documented by yfinance. Keep this explicit so the
# browser catalog is deterministic while the provider remains easy to audit against
# upstream changes.
REGIONS = (
    "ae",
    "ar",
    "at",
    "au",
    "be",
    "br",
    "ca",
    "ch",
    "cl",
    "cn",
    "co",
    "cz",
    "de",
    "dk",
    "ee",
    "eg",
    "es",
    "fi",
    "fr",
    "gb",
    "gr",
    "hk",
    "hu",
    "id",
    "ie",
    "il",
    "in",
    "is",
    "it",
    "jp",
    "kr",
    "kw",
    "lk",
    "lt",
    "lv",
    "mx",
    "my",
    "nl",
    "no",
    "nz",
    "pe",
    "ph",
    "pk",
    "pl",
    "pt",
    "qa",
    "ro",
    "ru",
    "sa",
    "se",
    "sg",
    "sr",
    "th",
    "tr",
    "tw",
    "us",
    "ve",
    "vn",
    "za",
)


class YahooScreenerProvider:
    descriptor = ProviderDescriptor(
        name="yahoo-screener",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description="Yahoo Finance global equity screener via yfinance; personal use only.",
    )

    def __init__(self, settings: Settings) -> None:
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.settings = settings
        self.allowed_fields = {field for fields in SCREENER_FIELDS.values() for field in fields}

    def catalog(self) -> dict[str, Any]:
        return {
            "fields": {category: list(fields) for category, fields in SCREENER_FIELDS.items()},
            "regions": list(REGIONS),
            "predefined": sorted(yf.PREDEFINED_SCREENER_QUERIES.keys()),
            "operators": ["eq", "is-in", "btwn", "gt", "lt", "gte", "lte"],
            "max_results": 250,
        }

    def screen(
        self,
        request: research_models.MarketScreenRequest,
    ) -> research_models.MarketScreenResponse:
        if request.predefined:
            if request.predefined not in yf.PREDEFINED_SCREENER_QUERIES:
                raise ValueError(f"Unknown predefined screener: {request.predefined}")
            raw = yf.screen(
                request.predefined,
                offset=request.offset,
                count=request.size,
            )
            query_repr: dict[str, Any] = {"predefined": request.predefined}
        else:
            query = self._query(request.filters)
            raw = yf.screen(
                query,
                offset=request.offset,
                size=request.size,
                sortField=request.sort_field,
                sortAsc=request.sort_ascending,
            )
            query_repr = {
                "filters": [item.model_dump(mode="json") for item in request.filters],
                "sort_field": request.sort_field,
                "sort_ascending": request.sort_ascending,
            }
        payload = raw if isinstance(raw, dict) else {}
        quotes_raw = payload.get("quotes", [])
        quotes = [dict(item) for item in quotes_raw if isinstance(item, dict)]
        total_raw = payload.get("total")
        total = int(total_raw) if isinstance(total_raw, (int, float)) else None
        notes = [
            "Yahoo custom screener supports up to 250 rows per request.",
            "Field availability varies by market and security.",
        ]
        filtered_out = 0
        if not request.predefined:
            checks = self._numeric_checks(request.filters)
            if checks:
                kept: list[dict[str, Any]] = []
                for quote in quotes:
                    if self._passes_numeric_checks(quote, checks):
                        kept.append(quote)
                    else:
                        filtered_out += 1
                quotes = kept
                if filtered_out > 0:
                    notes.append(FAIL_CLOSED_NOTE)
        now = datetime.now(UTC)
        return research_models.MarketScreenResponse(
            quotes=quotes,
            total=total,
            offset=request.offset,
            size=request.size,
            filtered_out=filtered_out,
            query=query_repr,
            provenance=Provenance(
                provider="yahoo/yfinance",
                source="Yahoo Finance Equity Screener",
                source_url="https://finance.yahoo.com/research-hub/screener/",
                license_class=LicenseClass.PERSONAL_ONLY,
                retrieved_at=now,
                as_of=now,
                notes=notes,
            ),
        )

    def _numeric_checks(
        self,
        filters: list[research_models.MarketScreenFilter],
    ) -> list[tuple[str, str, tuple[float, ...]]]:
        """Extract locally re-checkable numeric filters from the request.

        Identity-style fields (region, exchange, ...) and ``is-in`` membership
        filters are excluded: they cannot be re-evaluated numerically and a
        missing identity value does not create the missing-number hazard this
        post-filter exists to close.
        """

        identity_fields = SCREENER_FIELDS["identity"]
        checks: list[tuple[str, str, tuple[float, ...]]] = []
        for item in filters:
            if item.operator not in NUMERIC_FILTER_OPERATORS:
                continue
            if item.field in identity_fields:
                continue
            if item.operator == "btwn":
                if not isinstance(item.value, list) or len(item.value) != 2:
                    continue
                try:
                    bounds: tuple[float, ...] = (float(item.value[0]), float(item.value[1]))
                except (TypeError, ValueError):
                    continue
            else:
                raw = item.value[0] if isinstance(item.value, list) else item.value
                try:
                    bounds = (float(raw),)
                except (TypeError, ValueError):
                    continue
            checks.append((item.field, item.operator, bounds))
        return checks

    @staticmethod
    def _passes_numeric_checks(
        quote: dict[str, Any],
        checks: list[tuple[str, str, tuple[float, ...]]],
    ) -> bool:
        """Re-evaluate numeric filters locally; missing data fails closed."""

        for field, operator, bounds in checks:
            raw = quote.get(field)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return False
            value = float(raw)
            if not math.isfinite(value):
                return False
            if operator == "btwn":
                low, high = bounds
                if not low <= value <= high:
                    return False
                continue
            (threshold,) = bounds
            if operator == "eq" and value != threshold:
                return False
            if operator == "gt" and not value > threshold:
                return False
            if operator == "lt" and not value < threshold:
                return False
            if operator == "gte" and not value >= threshold:
                return False
            if operator == "lte" and not value <= threshold:
                return False
        return True

    def _query(
        self,
        filters: list[research_models.MarketScreenFilter],
    ) -> yf.EquityQuery:
        if not filters:
            return yf.EquityQuery("is-in", ["region", *REGIONS])
        children = [self._filter(item) for item in filters]
        return children[0] if len(children) == 1 else yf.EquityQuery("and", children)

    def _filter(self, item: research_models.MarketScreenFilter) -> yf.EquityQuery:
        if item.field not in self.allowed_fields:
            raise ValueError(f"Unsupported Yahoo screener field: {item.field}")
        value = item.value
        if item.operator == "is-in":
            values = value if isinstance(value, list) else [value]
            return yf.EquityQuery("is-in", [item.field, *values])
        if item.operator == "btwn":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("btwn screener filters require exactly two values")
            return yf.EquityQuery("btwn", [item.field, value[0], value[1]])
        if isinstance(value, list):
            if len(value) != 1:
                raise ValueError(f"{item.operator} screener filters require one value")
            scalar: str | float | int = value[0]
        else:
            scalar = value
        return yf.EquityQuery(item.operator, [item.field, scalar])
