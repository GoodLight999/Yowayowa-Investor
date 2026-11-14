from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import enforce_source_policy

# Alpha Vantage resets its free-tier window on the US/Eastern calendar day.
# Counting against the ET date keeps the client conservative under either
# documented reset interpretation (ET midnight or UTC midnight): the counter
# can only block early, never let a request through after the API-side reset.
_BUDGET_TZ = ZoneInfo("America/New_York")


class AlphaVantageQuotaExhausted(RuntimeError):
    """Daily request budget is spent; fail closed instead of burning quota."""


class _DailyBudget:
    """Persisted per-day request counter (fail-closed at the free-tier limit).

    The counter is stored as a small JSON file so separate processes (cron
    runs, shells) share one budget. Every attempt counts, including failed
    ones: a timeout or 5xx may still have consumed quota server-side, so
    undercounting is the one failure mode this guard must never allow.
    """

    def __init__(self, path: Path, limit: int) -> None:
        self.path = path
        self.limit = limit

    @staticmethod
    def _today() -> date:
        return datetime.now(_BUDGET_TZ).date()

    def _load(self) -> tuple[date, int]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._today(), 0
        except (OSError, json.JSONDecodeError):
            # Unreadable/corrupt state: fail closed for the rest of the day.
            return self._today(), self.limit
        if not isinstance(raw, dict):
            return self._today(), self.limit
        used = raw.get("used")
        if not isinstance(used, int) or used < 0:
            return self._today(), self.limit
        day = raw.get("date")
        if not isinstance(day, str):
            return self._today(), 0 if used == 0 else self.limit
        try:
            stored = date.fromisoformat(day)
        except ValueError:
            return self._today(), self.limit
        if stored != self._today():
            return self._today(), 0
        return stored, used

    def reserve(self) -> int:
        """Count one request before it is sent. Raises when the day is spent."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        today, used = self._load()
        if used >= self.limit:
            raise AlphaVantageQuotaExhausted(
                f"Alpha Vantage free-tier budget spent for {today.isoformat()}: "
                f"{used}/{self.limit} requests used; refusing to burn quota"
            )
        used += 1
        payload = {"date": today.isoformat(), "used": used, "limit": self.limit}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)
        return used


class AlphaVantageClient:
    base_url = "https://www.alphavantage.co/query"
    daily_request_limit = 25

    def __init__(self, settings: Settings, *, usage_path: str | Path | None = None) -> None:
        self.settings = settings
        enforce_source_policy("alpha-vantage", mode=settings.mode)
        self.client = httpx.Client(timeout=settings.request_timeout_seconds)
        self.usage_path = (
            Path(usage_path)
            if usage_path is not None
            else Path(settings.private_acquisition_data_dir) / "alpha-vantage" / "usage.json"
        )

    def _key(self) -> str:
        if not self.settings.alpha_vantage_api_key:
            raise RuntimeError("Alpha Vantage requires YOWAYOWA_ALPHA_VANTAGE_API_KEY")
        return self.settings.alpha_vantage_api_key

    def _get(self, **params: str) -> dict[str, Any]:
        # Fail closed in this order: policy, key, budget — no network I/O and
        # no quota charge can happen before every guard has passed.
        query = {"apikey": self._key(), **params}
        _DailyBudget(self.usage_path, limit=self.daily_request_limit).reserve()
        response = self.client.get(self.base_url, params=query)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        # Alpha Vantage reports rate limits and errors as HTTP 200 JSON bodies.
        for flag in ("Error Message", "Information", "Note"):
            if flag in payload:
                raise RuntimeError(f"Alpha Vantage API message ({flag}): {payload[flag]}")
        return payload

    def global_quote(self, symbol: str) -> dict[str, Any]:
        payload = self._get(function="GLOBAL_QUOTE", symbol=symbol)
        quote = payload.get("Global Quote")
        now = datetime.now(_BUDGET_TZ)
        return {
            "symbol": symbol,
            "quote": quote if isinstance(quote, dict) else None,
            "provenance": self._provenance(now, symbol).model_dump(mode="json"),
        }

    @staticmethod
    def _provenance(now: datetime, symbol: str | None = None) -> Provenance:
        source_url = (
            f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}"
            if symbol
            else "https://www.alphavantage.co/documentation/"
        )
        return Provenance(
            provider="alpha-vantage",
            source="Alpha Vantage",
            source_url=source_url,
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=now,
            as_of=now,
            notes=[
                "Personal-mode registered-key source. The Alpha Vantage Terms of "
                "Service license the platform for personal, non-commercial use "
                "only; free-tier quotes reflect the previous trading session and "
                "the tier is limited to 25 requests/day (enforced fail-closed "
                "client-side).",
            ],
        )
