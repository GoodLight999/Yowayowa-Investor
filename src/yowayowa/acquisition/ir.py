"""Generic KPI extraction, document fingerprints, and instrument timeline (P1C).

This module keeps the IR pipeline company-agnostic:

- ``extract_kpis`` pulls generic label/number pairs out of extracted tables
  and PDF text using shared Japanese/English financial label normalization.
  It never invents values; rows without a parsable numeric value are skipped
  and reported via notes.
- ``DocumentFingerprint`` identifies a document by (resolved URL, sha256,
  label) so new-document detection can distinguish "new link", "same link
  new content" (revision), and "already seen".
- ``IrTimelineStore`` is an append-only JSONL timeline per instrument that
  records discovered documents and extracted KPI observations with full
  provenance, and serves the timeline back newest-first.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yowayowa.acquisition.snapshots import _safe_component

# --------------------------------------------------------------------- KPI

# Normalized label -> canonical KPI name (shared across JP/EN documents).
_CANONICAL_LABELS: dict[str, str] = {
    "売上収益": "revenue",
    "売上高": "revenue",
    "営業収益": "revenue",
    "営業利益": "operating_profit",
    "経常利益": "ordinary_profit",
    "税引前利益": "profit_before_tax",
    "当期利益": "profit",
    "親会社の所有者に帰属する当期利益": "profit_attributable_to_owners",
    "当期純利益": "profit",
    "受注残": "order_backlog",
    "受注高": "order_bookings",
    "契約高": "contract_value",
    "店舗数": "store_count",
    "既存店売上": "same_store_sales",
    "arr": "arr",
    "利用者数": "subscribers",
    "契約数": "contracts",
    "稼働率": "utilization",
    "出荷量": "shipments",
    "配当": "dividend",
    "revenue": "revenue",
    "net sales": "revenue",
    "operating profit": "operating_profit",
    "operating income": "operating_profit",
    "ordinary profit": "ordinary_profit",
    "profit before tax": "profit_before_tax",
    "profit": "profit",
    "net income": "profit",
    "order backlog": "order_backlog",
    "backlog": "order_backlog",
    "bookings": "order_bookings",
    "remaining performance obligations": "remaining_performance_obligations",
    "store count": "store_count",
    "number of stores": "store_count",
    "stores": "store_count",
    "same-store sales": "same_store_sales",
    "subscribers": "subscribers",
    "contracts": "contracts",
    "utilization": "utilization",
    "shipments": "shipments",
    "dividend": "dividend",
    "eps": "eps",
    "基本的1株当たり当期利益": "eps",
    "基本的１株当たり当期利益": "eps",
}

# Label keywords that indicate a row carries a period/column header rather
# than a KPI value ("2026年3月期", "Fiscal year ended March 31, 2026", ...).
_PERIOD_HINT_RE = re.compile(
    r"(期$|fiscal year|quarter|前期|当期|四半期|累計|通期|第[0-9一二三四])"
)

# Numeric value with optional thousand separators and minus (incl. \uff0b full-width
# variants handled in parse_number: MINUS SIGN U+2212, FULLWIDTH COMMA U+FF0C,
# WHITE UP-POINTING TRIANGLE U+25B3 used by Japanese tanshin for negatives).
_NUMBER_RE = re.compile(r"[-\u2212\u25b3]?[0-9][0-9,\uff0c]*\.?[0-9]*")
_TRAILING_ZEROS_RE = re.compile(r"\.0+$")

# Units recognized next to numbers in text/table cells.
_UNIT_MULTIPLIERS: dict[str, float] = {
    "百万円": 1_000_000.0,
    "十億円": 1_000_000_000.0,
    "億円": 100_000_000.0,
    "千円": 1_000.0,
    "円": 1.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "billion yen": 1_000_000_000.0,
    "million yen": 1_000_000.0,
    "millions of yen": 1_000_000.0,
    "thousands of yen": 1_000.0,
    "million usd": 1_000_000.0,
}


def normalize_label(raw: str) -> str:
    """Collapse whitespace/full-width and lowercase for label matching."""

    text = raw.replace("\u3000", " ").strip()
    return " ".join(text.split()).lower()


def canonical_kpi_name(raw: str) -> str | None:
    """Canonical KPI name for a raw label, or None when not recognized."""

    label = normalize_label(raw)
    if not label:
        return None
    return _CANONICAL_LABELS.get(label)


def parse_number(raw: str) -> float | None:
    """Parse a numeric string with separators/minus variants; None if not numeric."""

    text = raw.strip().replace(" ", "").replace(",", "").replace("\uff0c", "")
    text = text.replace("\u2212", "-").replace("\u25b3", "-")
    if not text or not _NUMBER_RE.fullmatch(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _canonical_number(value: float) -> float:
    """Render 125000.0 as 125000.0 and 125.50 as 125.5 (stable for diffs)."""

    text = _TRAILING_ZEROS_RE.sub("", f"{value:.6f}")
    return float(text)


def _unit_multiplier(text: str) -> tuple[float, str | None]:
    """Largest recognized unit in text -> (multiplier, unit string).

    Ties on multiplier prefer the longest token so "millions of yen" wins over
    the bare "million" substring of the same text.
    """

    lowered = text.lower()
    best: tuple[float, str] | None = None
    for unit, multiplier in _UNIT_MULTIPLIERS.items():
        if unit not in lowered:
            continue
        if (
            best is None
            or multiplier > best[0]
            or (multiplier == best[0] and len(unit) > len(best[1]))
        ):
            best = (multiplier, unit)
    if best is None:
        return 1.0, None
    return best[0], best[1]


# Compound yen chains as written in Japanese prose and tables:
# "892億74百万円" = 892e8 + 74e6 yen. A single-number regex reads only "892"
# and silently produces a value four orders of magnitude too small, so the
# whole chain must be consumed.
_YEN_SCALES: dict[str, float] = {
    "\u5341\u5104": 1_000_000_000.0,  # 十億
    "\u5104": 100_000_000.0,  # 億
    "\u767e\u4e07": 1_000_000.0,  # 百万
    "\u4e07": 10_000.0,  # 万
    "\u5343": 1_000.0,  # 千
    "\u767e": 100.0,  # 百
}
_YEN_CHAIN_PART_RE = re.compile(
    r"([0-9][0-9,\uff0c]*)\s*(\u5341\u5104|\u5104|\u767e\u4e07|\u4e07|\u5343|\u767e)"
)
_YEN_CHAIN_RE = re.compile(
    r"^\s*((?:[0-9][0-9,\uff0c]*\s*(?:\u5341\u5104|\u5104|\u767e\u4e07|\u4e07|\u5343|\u767e))+)\s*\u5186"
)


def parse_yen_chain(text: str) -> tuple[float, str] | None:
    """Value of a compound yen chain at the start of ``text``.

    ``"892\u510474\u767e\u4e07\u5186"`` -> ``(892e8 + 74e6, "892\u510474\u767e\u4e07\u5186")``.
    A single-part chain returns the bare unit token (``"125,526\u767e\u4e07\u5186"``
    -> ``(1.25526e11, "\u767e\u4e07\u5186")``) so the observation's unit stays
    canonical. Returns None for plain "100\u5186" and for non-chain text, so
    callers fall back to the single-number path.
    """

    match = _YEN_CHAIN_RE.match(text)
    if not match:
        return None
    parts = _YEN_CHAIN_PART_RE.findall(match.group(1))
    if not parts:
        return None
    total = 0.0
    for number_text, scale_text in parts:
        number = parse_number(number_text)
        if number is None:
            return None
        total += number * _YEN_SCALES[scale_text]
    if len(parts) == 1:
        return total, f"{parts[0][1]}\u5186"
    return total, match.group(0).strip()


def document_unit_hint(text: str) -> str | None:
    """Document-level unit declaration, only when unambiguous.

    IR documents declare their unit once ("(Millions of yen)",
    "(unit declaration in Japanese)"). Returning a hint only when every
    recognized unit token maps to the SAME multiplier avoids mis-scaling
    documents that mix thousand/million-yen contexts; ambiguity fails closed
    to None.
    """

    lowered = text.lower()
    matched: list[str] = []
    for token in sorted(_UNIT_MULTIPLIERS, key=len, reverse=True):
        if token in lowered and not any(token in other for other in matched):
            matched.append(token)
    if not matched:
        return None
    multipliers = {_UNIT_MULTIPLIERS[token] for token in matched}
    if len(multipliers) != 1:
        return None
    return matched[0]


class KpiObservation(dict[str, Any]):
    """Typed dict alias for a single extracted KPI value."""


# Unit declarations in table chrome. Full-width colon U+FF1A appears in
# Japanese IR documents by design.
_UNIT_HINT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\(\s*単位\s*[\uff1a:]\s*(\u767e\u4e07\u5186|\u5341\u5104\u5186|\u5104\u5186|\u5343\u5186)\s*\)"
    ),
    re.compile(
        r"\((\u767e\u4e07\u5186|\u5341\u5104\u5186|\u5104\u5186|\u5343\u5186|million|billion)\)"
    ),
    re.compile(
        r"(単位\s*[\uff1a:]\s*(\u767e\u4e07\u5186|\u5341\u5104\u5186|\u5104\u5186|\u5343\u5186))"
    ),
)


def table_unit_hint(table: dict[str, Any]) -> str | None:
    """Unit declared in a table's header/first rows (単位 = million yen etc.)."""

    candidates: list[str] = []
    headers = table.get("headers")
    if isinstance(headers, list):
        candidates.extend(str(header) for header in headers[:6])
    cells = table.get("cells")
    if isinstance(cells, list):
        for row in cells[:6]:
            if isinstance(row, list):
                candidates.extend(str(cell) for cell in row[:6])
    for text in candidates:
        for pattern in _UNIT_HINT_RES:
            match = pattern.search(text)
            if match:
                return match.group(1)
    return None


def extract_kpis_from_tables(
    tables: list[dict[str, Any]], *, unit_hint: str | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract canonical KPI values from generic table rows.

    - label/value rows: some cell key normalizes to a canonical KPI label and
      the row carries a numeric value (``row[label] = "912,248"``);
    - header-cross rows (financial statements): the FIRST cell of the row is
      the KPI label and the column headers are periods — the first numeric
      cell of the row is the value for the most recent period;
    - coordinate rows (PDF reconstruction): the table carries ``cells`` lists
      from layout analysis; a row whose first cell is a canonical KPI label
      and whose remaining cells hold >=2 numbers yields the SECOND number
      (Japanese tanshin convention: prev-period, current-period, delta...).

    Returns (observations, notes). An observation carries:
    kpi (canonical name), label (raw label as seen), value (float),
    unit (recognized unit string or None), source_table (index).
    Rows whose label looks like a period header or whose value does not parse
    are skipped (skips reported in notes, capped).
    """

    observations: list[dict[str, Any]] = []
    notes: list[str] = []
    skipped = 0
    for table_index, table in enumerate(tables):
        # A table-level unit declaration (単位 header row) applies to all
        # numeric cells in it; per-cell units still win when present.
        table_unit = table_unit_hint(table)
        effective_hint = unit_hint or table_unit
        # Shape 3: coordinate rows keep raw cell lists — use them first since
        # the dict projection collapses multi-column financial layouts.
        row_cells: list[Any] = table.get("cells") or []
        table_rows: list[Any] = table.get("rows") or []
        for row in table_rows:
            if not isinstance(row, dict):
                continue
            keys = [str(key) for key in row if row[key] not in (None, "")]
            values = [str(row[key]) for key in row if row[key] not in (None, "")]
            if not keys or not values:
                continue
            # Shape 1: a cell KEY is a canonical KPI label ("営業利益": "125,526")
            matched_key = None
            for key in keys:
                canonical = canonical_kpi_name(key)
                if canonical is not None and not _PERIOD_HINT_RE.search(normalize_label(key)):
                    matched_key = (key, canonical)
                    break
            if matched_key is not None:
                label, canonical = matched_key
                raw_value = str(row[label] or "")
                number = parse_number(raw_value)
                if number is None:
                    skipped += 1
                    continue
                multiplier, unit = _unit_multiplier(f"{effective_hint or ''} {label} {raw_value}")
                observations.append(
                    {
                        "kpi": canonical,
                        "label": label,
                        "value": _canonical_number(number * multiplier),
                        "raw_value": raw_value,
                        "unit": unit,
                        "source": f"table[{table_index}]",
                    }
                )
                continue
            # Shape 2: first cell is the KPI label, columns are periods.
            first_key = keys[0]
            canonical = canonical_kpi_name(first_key)
            if canonical is None or _PERIOD_HINT_RE.search(normalize_label(first_key)):
                skipped += 1
                continue
            number = None
            raw_value = ""
            for candidate in values:
                parsed = parse_number(candidate)
                if parsed is not None:
                    number = parsed
                    raw_value = candidate
                    break
            if number is None:
                skipped += 1
                continue
            multiplier, unit = _unit_multiplier(f"{effective_hint or ''} {first_key} {raw_value}")
            observations.append(
                {
                    "kpi": canonical,
                    "label": first_key,
                    "value": _canonical_number(number * multiplier),
                    "raw_value": raw_value,
                    "unit": unit,
                    "source": f"table[{table_index}]",
                }
            )
        for cells in row_cells:
            if not isinstance(cells, list) or len(cells) < 3:
                continue
            cell_texts = [str(cell) for cell in cells]
            label = cell_texts[0]
            canonical = canonical_kpi_name(label)
            if canonical is None or _PERIOD_HINT_RE.search(normalize_label(label)):
                continue
            numbers = [(parse_number(cell), cell) for cell in cell_texts[1:]]
            numbers = [(n, c) for n, c in numbers if n is not None]
            if len(numbers) < 2:
                continue
            # tanshin layout: [prev, current, delta, delta%] — prefer the
            # second numeric cell (current period).
            picked_number, raw_value = numbers[1]
            if picked_number is None:  # pragma: no cover - filtered above
                continue
            number = picked_number
            multiplier, unit = _unit_multiplier(f"{effective_hint or ''} {label} {raw_value}")
            observations.append(
                {
                    "kpi": canonical,
                    "label": label,
                    "value": _canonical_number(number * multiplier),
                    "raw_value": raw_value,
                    "unit": unit,
                    "source": f"table[{table_index}].cells",
                }
            )
    if skipped:
        notes.append(f"kpi extraction skipped {skipped} unparsable/period rows")
    return observations, notes


# PDF-text KPI context: label (2-40 chars, no comma/period separators) then a
# number then an optional unit. Full-width punctuation appears by design in
# Japanese tanshin text (\uff0c comma, \u3001 comma, \uff1a colon, U+2212 minus,
# U+25B3 triangle, U+FF0C full-width comma).
_TEXT_KPI_CONTEXT_RE = re.compile(
    r"([^\n\uff0c\u3001]{2,40}?)\s*[:\uff1a]?\s*"
    r"([-\u2212\u25b3]?[0-9][0-9,\uff0c]*\.?[0-9]*)\s*"
    r"(\u767e\u4e07\u5186|\u5104\u5186|\u5343\u5186|\u5186|million|billion)?"
)


def merge_kpi_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate observations by KPI name.

    Unit-bearing observations win over unit-less ones (a table-scoped
    unit declaration such as 百万円 normalizes the value; a bare number
    from an un-hinted table is ambiguous). Within the same unit class, the
    first observation is kept.
    """

    best: dict[str, dict[str, Any]] = {}
    for observation in observations:
        kpi = observation["kpi"]
        current = best.get(kpi)
        if current is None:
            best[kpi] = observation
            continue
        if current.get("unit") is None and observation.get("unit") is not None:
            best[kpi] = observation
    return [best[kpi] for kpi in best]


def _match_canonical_label(raw: str) -> str | None:
    """Canonical KPI name for a label, tolerating period prefixes.

    ``"2026年3月期 売上収益"`` carries a period prefix before the KPI label,
    so the whole-label lookup fails; try the trailing tokens/segments, longest
    first, and return the first that canonicalizes.
    """

    candidates = [raw, *re.split(r"[\s\u3000,、]+", raw)]
    for candidate in candidates:
        canonical = canonical_kpi_name(candidate)
        if canonical is not None:
            return canonical
    return None


def extract_kpis_from_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract canonical KPIs from PDF text using label-near-number patterns.

    Only labels that normalize to a canonical KPI name are kept; the value is
    the first number on the label's line context. A compound yen chain
    ("892\u510474\u767e\u4e07\u5186") is consumed whole so prose values are not
    silently truncated to their leading number. A document-level unit
    declaration ("(Millions of yen)") is applied to plain numbers when the
    declaration is unambiguous.
    """

    observations: list[dict[str, Any]] = []
    notes: list[str] = []
    doc_unit = document_unit_hint(text)
    for line in text.splitlines()[:5_000]:
        for match in _TEXT_KPI_CONTEXT_RE.finditer(line):
            label = match.group(1).strip()
            canonical = _match_canonical_label(label)
            if canonical is None:
                continue
            raw_value = match.group(2)
            unit = match.group(3)
            number = parse_number(raw_value)
            if number is None:
                continue
            # Compound yen chain from the number onward ("892" + "億74百万円")
            chain = parse_yen_chain(line[match.start(2) :])
            if chain is not None:
                value, chain_unit = chain
                observations.append(
                    {
                        "kpi": canonical,
                        "label": label,
                        "value": _canonical_number(value),
                        "raw_value": chain_unit,
                        "unit": chain_unit,
                        "source": "text",
                    }
                )
                continue
            if unit:
                multiplier, unit_text = _unit_multiplier(unit)
            elif doc_unit:
                multiplier, unit_text = _unit_multiplier(doc_unit)
            else:
                multiplier, unit_text = 1.0, None
            observations.append(
                {
                    "kpi": canonical,
                    "label": label,
                    "value": _canonical_number(number * multiplier),
                    "raw_value": raw_value,
                    "unit": unit_text or unit or None,
                    "source": "text",
                }
            )
    if not observations:
        notes.append("no canonical KPI labels matched in text")
    return observations, notes


# ------------------------------------------------------------- fingerprints


def document_fingerprint(*, url: str, content: bytes, label: str | None = None) -> dict[str, str]:
    """Stable identity for a discovered document (url, sha256, label)."""

    return {
        "url": url,
        "sha256": hashlib.sha256(content).hexdigest(),
        "label": label or "",
    }


def fingerprint_key(fingerprint: dict[str, str]) -> str:
    """Join key for fingerprint sets: URL and content hash both matter."""

    return f"{fingerprint['url']}|{fingerprint['sha256']}"


# ----------------------------------------------------------------- timeline

_TIMELINE_MAX_READ = 10_000


class IrTimelineStore:
    """Append-only per-instrument JSONL timeline of IR events (P1C)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, symbol: str) -> Path:
        directory = self.root / _safe_component(symbol)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "timeline.jsonl"

    def append(self, symbol: str, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._path(symbol).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def entries(
        self, symbol: str, *, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        path = self.root / _safe_component(symbol) / "timeline.jsonl"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-_TIMELINE_MAX_READ:]
        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if kind is not None and entry.get("kind") != kind:
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break
        return entries


def timeline_entry(
    *,
    kind: str,
    symbol: str,
    provider: str,
    source_url: str,
    retrieved_at: datetime,
    as_of: datetime | None,
    license_class: str,
    payload: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Build a provenance-complete timeline entry; fails closed on missing
    provenance fields (missing license class or provider is an error)."""

    if not provider:
        raise ValueError("timeline entry requires provider provenance")
    if not license_class:
        raise ValueError("timeline entry requires license_class provenance")
    if not source_url:
        raise ValueError("timeline entry requires source_url provenance")
    return {
        "kind": kind,
        "symbol": symbol,
        "recorded_at": datetime.now(UTC).isoformat(),
        "provenance": {
            "provider": provider,
            "source_url": source_url,
            "license_class": license_class,
            "retrieved_at": retrieved_at.isoformat(),
            "as_of": as_of.isoformat() if as_of else None,
        },
        "payload": payload,
        "notes": notes or [],
    }
