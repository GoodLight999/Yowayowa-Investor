"""Alpaca Paper API client for C2 forward paper execution.

Strict constraints:
- REAL MONEY = 0. LIVE BROKER ACCESS = 0.
- Base URL MUST NEVER be live trading API (https://api.alpaca.markets).
  Only https://paper-api.alpaca.markets and https://data.alpaca.markets are allowed.
- Secrets are loaded from /root/.secrets/alpaca_paper.env (mode 600).
- Keys are never logged in plaintext; account ID is masked.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("paper_c2.alpaca")

LIVE_API_PATTERN = re.compile(r"^https://api\.alpaca\.markets")
DEFAULT_PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
DEFAULT_DATA_BASE_URL = "https://data.alpaca.markets/v2"
SECRET_ENV_PATH = Path("/root/.secrets/alpaca_paper.env")
HERMES_ENV_PATH = Path("/root/.hermes/.env")


@dataclass(frozen=True)
class AlpacaAccountSummary:
    account_id: str
    masked_id: str
    status: str
    currency: str
    equity: float
    cash: float
    buying_power: float
    raw: dict[str, Any]


class AlpacaPaperClient:
    """Safeguarded client for Alpaca Paper trading & data APIs."""

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        data_url: str | None = None,
    ) -> None:
        self.key_id = key_id or self._load_secret("ALPACA_KEY_ID")
        self.secret_key = secret_key or self._load_secret("ALPACA_SECRET_KEY")
        self.base_url = (
            base_url or self._load_secret("ALPACA_BASE_URL") or DEFAULT_PAPER_BASE_URL
        ).rstrip("/")
        self.data_url = (
            data_url or self._load_secret("ALPACA_DATA_URL") or DEFAULT_DATA_BASE_URL
        ).rstrip("/")

        # Enforce safety guard: LIVE API IS FORBIDDEN
        self._enforce_safety_invariants()

    def _enforce_safety_invariants(self) -> None:
        if LIVE_API_PATTERN.match(self.base_url):
            raise AssertionError(
                f"SECURITY VIOLATION: Refusing connection to live trading endpoint: {self.base_url}"
            )
        if "paper-api.alpaca.markets" not in self.base_url:
            raise AssertionError(
                f"INVALID ENDPOINT: C2 paper runner must use paper API, got: {self.base_url}"
            )

    @staticmethod
    def _load_secret(key_name: str) -> str:
        # 1. Environment variable
        if os.environ.get(key_name):
            return os.environ[key_name].strip()
        # 2. /root/.secrets/alpaca_paper.env
        if SECRET_ENV_PATH.exists():
            for line in SECRET_ENV_PATH.read_text().splitlines():
                if line.startswith(f"{key_name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        # 3. /root/.hermes/.env
        if HERMES_ENV_PATH.exists():
            for line in HERMES_ENV_PATH.read_text().splitlines():
                if line.startswith(f"{key_name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
            "User-Agent": "Yowayowa-C2-PaperRunner/1.0",
        }

    def _request(
        self,
        url: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        self._enforce_safety_invariants()
        encoded = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=encoded, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.warning("Alpaca API HTTP %d for %s %s: %s", exc.code, method, url, body)
            try:
                err_data = json.loads(body)
            except Exception:
                err_data = {"raw_error": body}
            err_data["http_code"] = exc.code
            return err_data
        except Exception as exc:
            logger.error("Alpaca API connection failure for %s %s: %s", method, url, exc)
            return {"error": str(exc), "status": "connection_error"}

    def get_account(self) -> AlpacaAccountSummary:
        res = self._request(f"{self.base_url}/account")
        if res.get("http_code") or res.get("error"):
            raise RuntimeError(f"Failed to fetch Alpaca account: {res}")
        acct_id = res.get("id", "")
        masked = acct_id[:4] + "..." + acct_id[-4:] if len(acct_id) > 8 else "***"
        return AlpacaAccountSummary(
            account_id=acct_id,
            masked_id=masked,
            status=res.get("status", "UNKNOWN"),
            currency=res.get("currency", "USD"),
            equity=float(res.get("equity", 0.0)),
            cash=float(res.get("cash", 0.0)),
            buying_power=float(res.get("buying_power", 0.0)),
            raw=res,
        )

    def get_clock(self) -> dict[str, Any]:
        return self._request(f"{self.base_url}/clock")

    def get_positions(self) -> list[dict[str, Any]]:
        res = self._request(f"{self.base_url}/positions")
        return res if isinstance(res, list) else []

    def get_daily_bars(
        self,
        symbols: list[str],
        start: str,
        end: str,
        feed: str = "iex",
        limit: int = 1000,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch daily bars via Alpaca Market Data API (data.alpaca.markets)."""
        sym_str = ",".join(symbols)
        params = urllib.parse.urlencode(
            {
                "symbols": sym_str,
                "timeframe": "1Day",
                "start": start,
                "end": end,
                "feed": feed,
                "limit": str(limit),
            }
        )
        url = f"https://data.alpaca.markets/v2/stocks/bars?{params}"
        res = self._request(url)
        bars_dict = res.get("bars", {})
        if not isinstance(bars_dict, dict):
            return {s: [] for s in symbols}
        return {s: bars_dict.get(s, []) for s in symbols}

    def cancel_all_orders(self) -> list[dict[str, Any]]:
        """Cancel all open orders in the paper account."""
        self._enforce_safety_invariants()
        res = self._request(f"{self.base_url}/orders", method="DELETE")
        return res if isinstance(res, list) else []

    def close_all_positions(self, cancel_orders: bool = True) -> list[dict[str, Any]]:
        """Liquidate all open positions (Kill switch emergency liquidation)."""
        self._enforce_safety_invariants()
        params = "?cancel_orders=true" if cancel_orders else ""
        res = self._request(f"{self.base_url}/positions{params}", method="DELETE")
        logger.warning("Emergency close_all_positions executed: %s", res)
        return res if isinstance(res, list) else []

    def submit_paper_order(
        self,
        symbol: str,
        qty: int | float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: float | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit an order to Alpaca Paper Trading API.

        If paper account rejects new orders (e.g. 40310000 'new orders rejected by user request'),
        gracefully record the rejection and return fallback theoretical execution dict.
        """
        self._enforce_safety_invariants()
        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(round(qty, 4)),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force.lower(),
        }
        if limit_price is not None and order_type.lower() == "limit":
            payload["limit_price"] = str(round(limit_price, 2))
        if client_order_id:
            payload["client_order_id"] = client_order_id

        res = self._request(f"{self.base_url}/orders", method="POST", data=payload)
        if res.get("http_code") == 403 and res.get("code") == 40310000:
            logger.info(
                "Alpaca Paper orders rejected by paper account configuration (40310000). "
                "Engaging theoretical paper execution tracking."
            )
            return {
                "status": "theoretical_recorded",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "rejection_reason": res.get("message"),
                "paper_api_code": 40310000,
                "timestamp_utc": datetime.now(UTC).isoformat(),
            }
        return res
