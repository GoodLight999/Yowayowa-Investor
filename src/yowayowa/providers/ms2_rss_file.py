"""Rakuten MARKET SPEED II RSS quote-file provider (Windows Node ingest).

The MARKET SPEED II RSS bridge only runs on the operator's Windows machine
(Excel + `RssMarket_*` formulas), so quote capture happens as a Windows-side
Python batch (``scripts/windows/ms2_rss_export.py``) that writes one JSON line
per instrument into ``ms2_quotes_YYYYMMDD.jsonl``. The file is copied to this
VPS over SCP into ``data/ms2/quotes/``; this provider is the receiving end:

- it globs ``data/ms2/quotes/*.jsonl`` and loads every parsable record;
- it dedupes per symbol keeping the record with the newest ``as_of`` (ties on
  ``as_of`` are broken by ``retrieved_at``, then file order);
- four-price gaps (``bid``/``ask``/``high``/``low``/``last``/``volume``) stay
  ``None`` — missing data is never zero-filled;
- it is **PERSONAL_ONLY**: quotes originate from the operator's own
  Rakuten Securities account feed and must never leave personal mode (see
  ``services/licensing.py``, key ``rakuten-ms2-rss``).

The provider has no scheduler and makes no network calls — it is a receiver
only. Windows-side scheduling and the SCP copy are operator-environment work
documented in ``docs/MS2_RSS.md``.

This module is deliberately separate from ``providers/rakuten_ms2_rss.py``,
which is the existing order-bridge (RssStockOrder_V etc.) surface and is not
modified here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yowayowa.config import Settings
from yowayowa.domain import LicenseClass, MarketQuote, MarketQuoteBatch, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

MS2_QUOTES_DIR = "data/ms2/quotes"
MS2_SOURCE_NAME = "Rakuten MARKET SPEED II RSS (operator Windows Node)"
MS2_SOURCE_URL = "ms2-file://data/ms2/quotes"

# The exact four-price set the Windows exporter emits. Extra keys in a record
# are ignored; missing ones stay None.
_QUOTE_FIELDS = ("bid", "ask", "high", "low", "last", "volume")


class Ms2RssFileError(RuntimeError):
    """The MS2 JSONL store is unreadable or malformed (maps to HTTP 502)."""


class Ms2RssLookupError(LookupError):
    """No usable record for the requested symbol (maps to HTTP 404)."""


class Ms2RssFileProvider:
    """Read-only receiver for Windows-side MARKET SPEED II RSS quote exports."""

    descriptor = ProviderDescriptor(
        name="rakuten-ms2-rss",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "MARKET SPEED II RSS quotes batch-exported on the operator's "
            "Windows Node and ingested as JSONL; personal account feed, "
            "personal-mode only."
        ),
    )

    def __init__(self, settings: Settings, *, quotes_dir: str | Path | None = None) -> None:
        self.settings = settings
        enforce_provider_policy(
            self.descriptor,
            mode=settings.mode,
            allow_personal_in_public=settings.allow_personal_provider_in_public,
        )
        self.quotes_dir = Path(quotes_dir) if quotes_dir is not None else Path(MS2_QUOTES_DIR)

    # ------------------------------------------------------------------ ingest

    def load_records(self) -> list[dict[str, Any]]:
        """Load and dedupe JSONL records; newest ``as_of`` wins per symbol.

        Malformed lines and unparsable files are skipped (a partially written
        transfer must not take the whole store down); an empty store returns
        ``[]``. A file that exists but cannot be opened at all raises
        ``Ms2RssFileError`` — unreadable data is an operator-actionable fault,
        not a silent gap.
        """

        records: list[dict[str, Any]] = []
        paths = sorted(self.quotes_dir.glob("*.jsonl"))
        for path in paths:
            records.extend(self._load_file(path))
        return self._dedupe(records)

    def _load_file(self, path: Path) -> list[dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise Ms2RssFileError(f"cannot read MS2 quotes file {path}: {exc}") from exc
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except ValueError:
                continue  # partially written / truncated line: skip it
            if isinstance(record, dict) and self._valid_record(record):
                records.append(record)
            # Non-dict or schema-invalid lines are skipped the same way.
        return records

    @staticmethod
    def _valid_record(record: dict[str, Any]) -> bool:
        symbol = record.get("symbol")
        quotes = record.get("quotes")
        return (
            isinstance(symbol, str)
            and bool(symbol.strip())
            and isinstance(quotes, dict)
            and _parse_ts(record.get("as_of")) is not None
        )

    @staticmethod
    def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for record in records:
            symbol = str(record["symbol"]).strip()
            current = best.get(symbol)
            if current is None or _record_sort_key(record) > _record_sort_key(current):
                best[symbol] = record
        return [best[symbol] for symbol in sorted(best)]

    # ------------------------------------------------------------------ quotes

    def quotes(self, symbols: list[str]) -> MarketQuoteBatch:
        """Latest snap per requested symbol; missing symbols are unavailable."""

        records_by_symbol = {str(r["symbol"]).strip(): r for r in self.load_records()}
        quotes: dict[str, MarketQuote] = {}
        unavailable: list[str] = []
        for symbol in symbols:
            normalized = symbol.strip()
            record = records_by_symbol.get(normalized)
            if record is None:
                unavailable.append(normalized)
                continue
            quotes[normalized] = self._record_to_quote(record)
        return MarketQuoteBatch(
            quotes=quotes,
            unavailable_symbols=unavailable,
            provenance=self._provenance(),
        )

    def quote(self, symbol: str) -> MarketQuote:
        """Latest snap for a single symbol; raises ``Ms2RssLookupError`` on gap."""

        normalized = symbol.strip()
        batch = self.quotes([normalized])
        if normalized in batch.unavailable_symbols:
            raise Ms2RssLookupError(f"no MS2 RSS record for {normalized!r}")
        return batch.quotes[normalized]

    # ------------------------------------------------------------------ helpers

    def _record_to_quote(self, record: dict[str, Any]) -> MarketQuote:
        quotes = record.get("quotes") or {}
        price = _optional_float(quotes.get("last") or quotes.get("ask") or quotes.get("bid"))
        if price is None:
            raise Ms2RssLookupError(f"MS2 RSS record for {record['symbol']!r} has no usable price")
        return MarketQuote(
            symbol=str(record["symbol"]).strip(),
            price=price,
            previous_close=None,
            as_of=_parse_ts(record["as_of"]) or datetime.now(UTC),
        )

    def _provenance(self) -> Provenance:
        return Provenance(
            provider="rakuten-ms2-rss",
            source=MS2_SOURCE_NAME,
            source_url=MS2_SOURCE_URL,
            license_class=LicenseClass.PERSONAL_ONLY,
            retrieved_at=datetime.now(UTC),
            as_of=None,
            notes=[
                "Reads Windows Node MARKET SPEED II RSS batch exports "
                "(data/ms2/quotes/*.jsonl); personal account feed, never "
                "redistributed.",
            ],
        )


def _record_sort_key(record: dict[str, Any]) -> tuple[datetime, datetime]:
    as_of = _parse_ts(record.get("as_of")) or datetime.min.replace(tzinfo=UTC)
    retrieved_at = _parse_ts(record.get("retrieved_at")) or datetime.min.replace(tzinfo=UTC)
    return (as_of, retrieved_at)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def quote_field_names() -> tuple[str, ...]:
    """Field names the Windows exporter must fill (or null) per record."""

    return _QUOTE_FIELDS
