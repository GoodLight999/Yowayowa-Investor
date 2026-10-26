"""US stock daily-OHLCV acquisition: fetch orchestration and JSONL persistence (P4-F).

Follows the acquisition layer's timeline-store pattern
(``crypto_acquisition.CryptoOhlcvStore``): an append-only JSONL file per
symbol under ``{data_dir}/stock-ohlcv/{SYMBOL}/ohlcv.jsonl`` (CTO-fixed
path), one line per record with full provenance on every line. Rows are kept
separate by ``provider`` — values are never averaged or merged. Missing days
from an API stay absent: no zero-fill, no forward-fill.

The store lives beside (not inside) the crypto store so the two asset classes
can never be conflated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from yowayowa.acquisition.snapshots import jsonl_read_diagnostics, read_jsonl_rows
from yowayowa.stock_models import StockOhlcvRecord, normalize_stock_symbol
from yowayowa.trading_sessions import session_lag


class StockOhlcvProvider(Protocol):
    """The slice a stock provider implements."""

    def ohlcv(self, symbol: str, *, days: int = 30) -> list[StockOhlcvRecord]: ...


def _key_ts(value: object) -> str:
    """Canonical as-of key: parses both 'Z' and '+00:00' ISO forms."""

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return parsed.isoformat()


_TIMELINE_MAX_READ = 10_000


class StockOhlcvStore:
    """Append-only per-symbol JSONL store of daily OHLCV rows."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, symbol: str) -> Path:
        directory = self.root / symbol.upper()
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "ohlcv.jsonl"

    def list_symbols(self) -> list[str]:
        """List symbols with persisted OHLCV without creating or modifying files."""
        if not self.root.exists():
            return []
        return sorted(
            path.parent.name.upper() for path in self.root.glob("*/ohlcv.jsonl") if path.is_file()
        )

    def _existing_keys(
        self,
        symbol: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> set[tuple[str, str, str]]:
        path = self.root / symbol.upper() / "ohlcv.jsonl"
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(jsonl_read_diagnostics(path))
        keys: set[tuple[str, str, str]] = set()
        for _row_number, entry in read_jsonl_rows(path, diagnostics=diagnostics):
            keys.add(
                (
                    str(entry.get("provider")),
                    str(entry.get("currency")),
                    _key_ts(entry.get("as_of")),
                )
            )
        return keys

    def append(
        self,
        symbol: str,
        records: list[StockOhlcvRecord],
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> int:
        """Append records; idempotent per (provider, currency, as_of). Returns lines written.

        A corrupt row already on disk is still skipped (never fatal), but its
        duplicate-exclusion key is now *counted* into ``diagnostics``: that is
        the observable reason a re-run can append a row it already had.
        """

        if not records:
            return 0
        normalized = normalize_stock_symbol(symbol)
        path = self._path(normalized)
        seen = self._existing_keys(normalized, diagnostics)
        written = 0
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                key = (record.provider, record.currency, _key_ts(record.as_of.isoformat()))
                if key in seen:
                    continue  # re-runs never duplicate a (source, day) row
                seen.add(key)
                handle.write(
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False, default=str)
                    + "\n"
                )
                written += 1
        return written

    def read(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        """Newest-first persisted rows for a symbol, optionally provider-filtered."""

        normalized = normalize_stock_symbol(symbol)
        path = self.root / normalized / "ohlcv.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-_TIMELINE_MAX_READ:]
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except ValueError:
                continue  # a corrupt line is skipped, never repaired silently
            if provider is not None and entry.get("provider") != provider:
                continue
            rows.append(entry)
        return rows[: max(0, limit)]


def fetch_stock_ohlcv(
    providers: dict[str, StockOhlcvProvider],
    store: StockOhlcvStore,
    symbols: list[str],
    *,
    days: int = 30,
    diagnostics: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Fetch daily OHLCV from every configured provider and persist per source.

    Source errors are isolated per provider AND per symbol: one failing
    symbol/provider never blocks the others. Rows are persisted raw and
    separated by ``provider`` — never averaged or mixed. When a ``diagnostics``
    dict is passed it receives one entry per symbol describing corrupt rows
    already on disk (see
    :func:`yowayowa.acquisition.snapshots.jsonl_read_diagnostics`); the
    returned summary keeps its existing keys.
    """

    summary: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_symbol in symbols:
        normalized = normalize_stock_symbol(raw_symbol)
        summary[normalized] = {}
        symbol_diagnostics: dict[str, Any] = {}
        for provider_name, provider in providers.items():
            try:
                records = provider.ohlcv(normalized, days=days)
            except LookupError as exc:
                summary[normalized][provider_name] = {"persisted": 0, "error": str(exc)}
                continue
            except Exception as exc:  # transport/other failures are per-source
                summary[normalized][provider_name] = {
                    "persisted": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                continue
            persisted = store.append(normalized, records, diagnostics=symbol_diagnostics)
            summary[normalized][provider_name] = {"persisted": persisted, "error": None}
        if diagnostics is not None:
            diagnostics[normalized] = symbol_diagnostics
    return summary


def default_store(data_dir: str | Path = "./data") -> StockOhlcvStore:
    """The CTO-fixed JSONL store root: ``{data_dir}/stock-ohlcv``."""

    return StockOhlcvStore(Path(data_dir) / "stock-ohlcv")


def persisted_freshness(
    store: StockOhlcvStore,
    symbol: str,
    *,
    now: datetime,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """Newest persisted bar for ``symbol`` plus its session lag against ``now``.

    ``session_lag == 0`` means the newest persisted bar is the most recent
    finalized NYSE session; ``1`` is the "one business day behind" case that
    the demo-loop mtm is priced on (``price_bar_date``). Read-only: nothing is
    repaired or synthesized here.
    """

    rows = store.read(symbol, provider=provider, limit=1)
    if not rows:
        return None
    newest = rows[0]
    as_of = datetime.fromisoformat(str(newest["as_of"]).replace("Z", "+00:00"))
    bar_date = as_of.astimezone(UTC).date()
    return {
        "as_of": as_of,
        "bar_date": bar_date,
        "retrieved_at": newest.get("retrieved_at"),
        "session_lag": session_lag(bar_date, now=now),
    }


def utc_now() -> datetime:
    return datetime.now(UTC)
