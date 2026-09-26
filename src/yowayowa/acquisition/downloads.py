from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Literal

from yowayowa.acquisition.models import AcquisitionFetchState, DownloadCapture
from yowayowa.acquisition.transport import PrivateAcquisitionError

DownloadFormat = Literal["csv", "json", "xlsx", "other"]

_MAX_ROWS = 10_000
_MAX_COLUMNS = 256
_SHEET_XML_MARKER = b"sheet"
_CONTENT_TYPES_MARKER = b"[Content_Types].xml"


def sniff_format(content: bytes, content_type: str | None, filename: str | None) -> DownloadFormat:
    """Best-effort format classification from magic bytes, headers, and name."""
    name = (filename or "").lower()
    header = content[:4096]
    if content.startswith(b"PK\x03\x04") and (
        "xlsx" in name
        or _CONTENT_TYPES_MARKER in header
        or _SHEET_XML_MARKER in header
        or (content_type is not None and "spreadsheetml" in content_type.lower())
    ):
        return "xlsx"
    if _parse_json(content) is not None:
        return "json"
    if content_type is not None and content_type.lower().startswith("text/csv"):
        return "csv"
    if _looks_like_csv(content):
        return "csv"
    return "other"


def _parse_json(content: bytes) -> object | None:
    try:
        parsed: object = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed


def _looks_like_csv(content: bytes) -> bool:
    if not content.strip():
        return False
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    comma_counts = {line.count(",") for line in lines}
    return len(comma_counts) == 1 and next(iter(comma_counts)) >= 1


def capture_download(
    *,
    content: bytes,
    content_type: str | None,
    filename: str | None,
    max_bytes: int = 10_000_000,
) -> DownloadCapture:
    if len(content) > max_bytes:
        raise PrivateAcquisitionError(AcquisitionFetchState.FAILED, "download too large")
    digest = hashlib.sha256(content).hexdigest()
    fmt = sniff_format(content, content_type, filename)
    if fmt == "csv":
        rows = _parse_csv(content)
        return DownloadCapture(
            filename=filename,
            content_type=content_type,
            sha256=digest,
            size_bytes=len(content),
            format=fmt,
            parsed=True,
            parse_note=None if len(rows) <= _MAX_ROWS else f"rows capped at {_MAX_ROWS}",
            rows=rows[:_MAX_ROWS],
        )
    if fmt == "json":
        payload = _parse_json(content)
        if isinstance(payload, list):
            documents = [item for item in payload if isinstance(item, dict)]
            note = (
                None
                if len(documents) == len(payload)
                else f"{len(payload) - len(documents)} non-object items dropped"
            )
            return DownloadCapture(
                filename=filename,
                content_type=content_type,
                sha256=digest,
                size_bytes=len(content),
                format=fmt,
                parsed=True,
                parse_note=note,
                documents=documents,
            )
        return DownloadCapture(
            filename=filename,
            content_type=content_type,
            sha256=digest,
            size_bytes=len(content),
            format=fmt,
            parsed=False,
            parse_note="json payload was not a top-level list",
        )
    if fmt == "xlsx":
        return DownloadCapture(
            filename=filename,
            content_type=content_type,
            sha256=digest,
            size_bytes=len(content),
            format=fmt,
            parsed=False,
            parse_note="xlsx captured raw; parsing not available",
        )
    return DownloadCapture(
        filename=filename,
        content_type=content_type,
        sha256=digest,
        size_bytes=len(content),
        format=fmt,
        parsed=False,
        parse_note="unrecognized format",
    )


def _parse_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {
            str(key): ("" if value is None else str(value))
            for key, value in list(raw.items())[:_MAX_COLUMNS]
            if key is not None
        }
        rows.append(row)
        if len(rows) > _MAX_ROWS:
            break
    return rows
