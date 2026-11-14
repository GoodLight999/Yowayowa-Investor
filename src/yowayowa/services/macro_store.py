"""Macro observation JSONL store (P5-A).

Reads the local macro observation timeline written by the operator's
acquisition batch: one JSONL file per source under
``data/macro-observations/{bls,fred,treasury}.jsonl`` (CTO-fixed path).

Rules inherited from the screening/crypto timeline stores:

- Per-source rows are never merged: ``source_kind`` (the file: bls / fred /
  treasury) stays on every observation, and the store never mixes rows from
  different files into one series.
- Missing data is never zero-filled: a row whose value cannot be parsed is
  skipped and counted, never replaced with 0 or an interpolated point.
- Every observation keeps its full provenance (provider, source_url,
  retrieved_at, license_class) exactly as captured on the JSONL line.
- The store is read-only. Acquisition (writing the JSONL files) happens in
  the operator's local batch, not on any serving surface.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

__all__ = [
    "MACRO_SOURCE_KINDS",
    "MacroObservationStore",
    "default_macro_store",
]

MACRO_SOURCE_KINDS: tuple[str, ...] = ("bls", "fred", "treasury")

_MONTHLY_PERIOD = re.compile(r"^M(\d{2})$")

_MAX_READ_LINES = 20_000


def _parse_date(value: Any) -> str | None:
    """Normalize an ISO date/datetime-ish value to its ``YYYY-MM-DD`` prefix."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    return None


def _parse_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


class MacroObservationStore:
    """Read-only per-source JSONL reader for macro observations."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, source_kind: str) -> Path:
        return self.root / f"{source_kind}.jsonl"

    def read(
        self,
        source_kind: str,
        *,
        series_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Normalized observations of one source file, in file order.

        ``series_id`` filters to one series. The coverage dict records the
        row/observation counts and every skip reason — a missing file yields
        an empty list plus ``file_missing`` coverage, never an error and
        never fabricated rows.
        """

        normalized = source_kind.strip().lower()
        if normalized not in MACRO_SOURCE_KINDS:
            raise ValueError(f"Unknown macro source kind: {source_kind}")
        path = self._path(normalized)
        coverage: dict[str, Any] = {
            "source_kind": normalized,
            "path": str(path),
            "row_count": 0,
            "observations": 0,
            "skipped_unparseable": 0,
        }
        if not path.is_file():
            coverage["reason"] = "file_missing"
            return [], coverage
        observations: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if line_no > _MAX_READ_LINES:
                    coverage["reason"] = "read_line_budget_exhausted"
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                coverage["row_count"] += 1
                try:
                    raw = json.loads(stripped)
                except ValueError:
                    coverage["skipped_unparseable"] += 1
                    continue
                if not isinstance(raw, dict):
                    coverage["skipped_unparseable"] += 1
                    continue
                for observation in self._normalize_row(normalized, raw):
                    if series_id is not None and observation["series_id"] != series_id:
                        continue
                    observations.append(observation)
                    coverage["observations"] += 1
        return observations, coverage

    def _normalize_row(self, source_kind: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Split one raw JSONL row into per-series observations.

        A combined-series row (the FRED multi-series CSV export stores one
        ``value_row`` per date) contributes one observation per series column.
        Columns without a parseable value are skipped — never zero-filled.
        """

        series_key = raw.get("series_id")
        series_ids: list[str]
        value_row: dict[str, Any] | None = None
        if isinstance(series_key, str) and "," in series_key:
            value_row_raw = raw.get("value_row")
            value_row = value_row_raw if isinstance(value_row_raw, dict) else None
            series_ids = [token.strip() for token in series_key.split(",") if token.strip()]
        elif isinstance(series_key, str) and series_key.strip():
            series_ids = [series_key.strip()]
        else:
            return []

        as_of = _parse_date(raw.get("date") or raw.get("record_date"))
        if as_of is None and isinstance(raw.get("year"), int):
            period = str(raw.get("period") or "")
            month = _MONTHLY_PERIOD.match(period)
            if month is not None:
                as_of = date(int(raw["year"]), int(month.group(1)), 1).isoformat()
        title = raw.get("title")
        base = {
            "source_kind": source_kind,
            "provider": raw.get("provider"),
            "retrieved_at": raw.get("retrieved_at"),
            "source_url": raw.get("source_url"),
            "license_class": raw.get("license_class"),
            "as_of": as_of,
            "period_label": raw.get("period_name") or raw.get("security_desc"),
        }
        observations: list[dict[str, Any]] = []
        for one_series in series_ids:
            if value_row is not None:
                if one_series not in value_row:
                    continue
                value = _parse_value(value_row.get(one_series))
            else:
                value = _parse_value(raw.get("value"))
            if value is None:
                continue  # a missing number stays missing; never zero-filled
            observations.append(
                {
                    **base,
                    "series_id": one_series,
                    "title": title if isinstance(title, str) and title else None,
                    "value": value,
                }
            )
        return observations

    def latest_by_series(self) -> list[dict[str, Any]]:
        """Latest parsed observation per (source_kind, series_id).

        Ties (same as_of) resolve to the later line in the file, so a
        re-fetch that appends a corrected row wins deterministically. Files
        that are missing simply contribute nothing.
        """

        latest: dict[tuple[str, str], tuple[tuple[str, int], dict[str, Any]]] = {}
        for source_kind in MACRO_SOURCE_KINDS:
            observations, _coverage = self.read(source_kind)
            for line_index, observation in enumerate(observations):
                key = (source_kind, str(observation["series_id"]))
                rank = (str(observation.get("as_of") or ""), line_index)
                current = latest.get(key)
                if current is None or rank >= current[0]:
                    latest[key] = (rank, observation)
        sorted_entries: list[tuple[tuple[str, str], tuple[tuple[str, int], dict[str, Any]]]] = (
            sorted(latest.items(), key=lambda item: item[0])
        )
        latest_rows: list[dict[str, Any]] = [entry[1] for _key, entry in sorted_entries]
        return latest_rows


def default_macro_store(data_dir: str | Path = "./data") -> MacroObservationStore:
    """The CTO-fixed macro observation root: ``{data_dir}/macro-observations``."""

    return MacroObservationStore(Path(data_dir) / "macro-observations")
