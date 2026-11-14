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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .paths import (
    DEFAULT_HERMES_ROOT,
    DEFAULT_SECRETS_ROOT,
    hermes_root,
    read_env_lines,
    secrets_root,
)

logger = logging.getLogger("paper_c2.alpaca")

LIVE_API_PATTERN = re.compile(r"^https://api\.alpaca\.markets")
DEFAULT_PAPER_BASE_URL = "https://paper-api.alpaca.markets/v2"
DEFAULT_DATA_BASE_URL = "https://data.alpaca.markets/v2"
# 既定は現行の絶対パス。根だけ YOWAYOWA_SECRETS_ROOT / YOWAYOWA_HERMES_ROOT で
# 差し替えられる（:func:`secrets_env_path` / :func:`hermes_env_path`）。
SECRET_ENV_PATH = Path(DEFAULT_SECRETS_ROOT) / "alpaca_paper.env"
HERMES_ENV_PATH = Path(DEFAULT_HERMES_ROOT) / ".env"
PHASE_START_DATE = date(2026, 11, 3)


def secrets_env_path() -> Path:
    """Alpaca paper secret file (default ``/root/.secrets/alpaca_paper.env``)."""
    return secrets_root() / SECRET_ENV_PATH.name


def hermes_env_path() -> Path:
    """Hermes ``.env`` fallback (default ``/root/.hermes/.env``)."""
    return hermes_root() / HERMES_ENV_PATH.name

# The only code path allowed to issue the blanket DELETE /v2/orders is the kill
# switch. That exact call silently canceled the whole 2026-11-02 batch 3h28m
# before the open, so it now requires an explicit reason token.
KILL_SWITCH_CANCEL_REASON = "kill_switch"


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
        # 3. /root/.hermes/.env
        # 既定は現行の絶対パスのまま（YOWAYOWA_SECRETS_ROOT / YOWAYOWA_HERMES_ROOT で
        # 根だけ差し替え可）。stat すら許されないホスト（例: CI ランナーの /root）でも
        # 落ちないよう、読めないファイルは「無い」ものとして扱う。
        for path in (secrets_env_path(), hermes_env_path()):
            for line in read_env_lines(path):
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
        """Read the position book, raising instead of returning ``[]`` on error.

        ``[]`` must mean "flat", never "the read failed". The order-lifecycle
        retry sizes its residual from this payload, so collapsing an HTTP 503
        into an empty list would be read as "no positions" and re-send the full
        target next to a position that had already filled (review finding
        2026-11-09). Callers that decide whether to send an order treat an
        exception as "do not submit".
        """
        res = self._request(f"{self.base_url}/positions")
        if isinstance(res, dict) and (res.get("http_code") or res.get("error")):
            raise RuntimeError(f"Failed to read Alpaca positions: {res}")
        if not isinstance(res, list):
            raise RuntimeError(f"Unexpected Alpaca positions payload: {type(res).__name__}")
        return res

    def get_positions_strict(self) -> list[dict[str, Any]]:
        """Alias of :meth:`get_positions` (same strict, raise-on-failure contract)."""
        return self.get_positions()

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

    def get_latest_trades(self, symbols: list[str], feed: str = "iex") -> dict[str, dict[str, Any]]:
        """Last trade per symbol from the Alpaca data API.

        Returns ``{symbol: {"price": float, "timestamp": str}}`` for symbols that
        answered. Used to re-base limit orders on the price actually quoted at
        submission time instead of the previous session's close.
        """
        params = urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": feed})
        res = self._request(f"{self.data_url}/stocks/trades/latest?{params}")
        trades = res.get("trades", {})
        if not isinstance(trades, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            row = trades.get(sym)
            if not isinstance(row, dict):
                continue
            try:
                price = float(row.get("p") or 0.0)
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[sym] = {"price": price, "timestamp": row.get("t")}
        return out

    def get_latest_quotes(self, symbols: list[str], feed: str = "iex") -> dict[str, dict[str, Any]]:
        """Latest NBBO-ish quote per symbol; price is the mid of bid/ask.

        Used only when no fresh trade print is available (pre-market). Falls back
        to whichever side exists when the other is zero/absent.
        """
        params = urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": feed})
        res = self._request(f"{self.data_url}/stocks/quotes/latest?{params}")
        quotes = res.get("quotes", {})
        if not isinstance(quotes, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            row = quotes.get(sym)
            if not isinstance(row, dict):
                continue
            try:
                bid = float(row.get("bp") or 0.0)
                ask = float(row.get("ap") or 0.0)
            except (TypeError, ValueError):
                continue
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2.0
            elif ask > 0:
                price = ask
            elif bid > 0:
                price = bid
            else:
                continue
            out[sym] = {"price": price, "timestamp": row.get("t"), "bid": bid, "ask": ask}
        return out

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a single order (DELETE /v2/orders/{id}).

        This is the only cancellation the execution model may use outside the
        kill switch: it is scoped to one order and cannot wipe the batch.
        """
        self._enforce_safety_invariants()
        if not order_id:
            raise ValueError("order_id is required")
        res = self._request(
            f"{self.base_url}/orders/{urllib.parse.quote(order_id, safe='')}", method="DELETE"
        )
        return res if isinstance(res, dict) else {}

    def cancel_all_orders(self, *, reason: str) -> list[dict[str, Any]]:
        """Cancel every open order in the paper account. KILL SWITCH ONLY.

        ``reason`` must be exactly ``KILL_SWITCH_CANCEL_REASON``. The blanket
        DELETE /v2/orders is what canceled all four 2026-11-02 orders 3h28m
        before the open, so every other path — rebalances, fallbacks, manual
        runs — must go through ``cancel_order`` instead.
        """
        if reason != KILL_SWITCH_CANCEL_REASON:
            raise PermissionError(
                "cancel_all_orders is reserved for the kill-switch path "
                f"(reason={KILL_SWITCH_CANCEL_REASON!r}); got reason={reason!r}. "
                "Use cancel_order(order_id) for a single order."
            )
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

    def get_orders(
        self,
        status: str = "open",
        limit: int | None = None,
        direction: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch orders with given status ('open', 'closed', 'all').

        ``limit``/``direction``/``after`` are optional; Alpaca defaults to 50 rows,
        which silently truncates the book for a long-running paper account.
        """
        self._enforce_safety_invariants()
        params: dict[str, str] = {"status": status}
        if limit is not None:
            params["limit"] = str(int(limit))
        if direction is not None:
            params["direction"] = direction
        if after is not None:
            params["after"] = after
        query = urllib.parse.urlencode(params)
        res = self._request(f"{self.base_url}/orders?{query}")
        return res if isinstance(res, list) else []

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch a single order by broker id or client_order_id."""
        self._enforce_safety_invariants()
        res = self._request(f"{self.base_url}/orders/{urllib.parse.quote(str(order_id))}")
        return res if isinstance(res, dict) else {}

    def get_orders_strict(
        self,
        status: str = "all",
        limit: int = 500,
        direction: str = "desc",
    ) -> list[dict[str, Any]]:
        """Read the order book, raising instead of returning an empty list on error.

        ``get_orders`` swallows API failures into ``[]``, which is indistinguishable
        from "no orders" -- unusable for idempotency checks, where an empty read
        would authorise a duplicate submission. Callers that decide whether to send
        an order must use this.
        """
        self._enforce_safety_invariants()
        params = {"status": status, "limit": str(int(limit)), "direction": direction}
        res = self._request(f"{self.base_url}/orders?{urllib.parse.urlencode(params)}")
        if isinstance(res, dict) and (res.get("http_code") or res.get("error")):
            raise RuntimeError(f"Failed to read Alpaca orders: {res}")
        if not isinstance(res, list):
            raise RuntimeError(f"Unexpected Alpaca orders payload: {type(res).__name__}")
        return res

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

        Enforces:
        - Date guard: Cannot submit orders prior to PHASE_START_DATE (2026-11-03).
        - If paper account rejects new orders (e.g. 40310000 'new orders rejected by user request'),
          gracefully record the rejection and return fallback theoretical execution dict.
        """
        self._enforce_safety_invariants()

        # Strict Date Guard: Pre-phase start order submission is rejected
        current_date = datetime.now(UTC).date()
        if current_date < PHASE_START_DATE:
            logger.warning(
                "ORDER REJECTED: Current UTC date %s is prior to forward phase start %s. "
                "No live broker orders may be submitted before phase start.",
                current_date,
                PHASE_START_DATE,
            )
            return {
                "status": "rejected_before_phase_start",
                "client_order_id": client_order_id,
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "current_date": current_date.isoformat(),
                "phase_start_date": PHASE_START_DATE.isoformat(),
                "timestamp_utc": datetime.now(UTC).isoformat(),
            }

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
