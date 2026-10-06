"""IR source discovery and new-document detection (P1C).

Discovers IR document links on an investor-relations listing page and
classifies them against a fingerprint store so a monitoring run can tell:

- ``new``      — URL never seen before
- ``revised``  — URL known, but content hash changed (company replaced the
                 file or published a corrected version at the same address)
- ``unchanged``— URL known with identical content hash

Discovery is generic: it enumerates <a href> anchors whose link text or URL
contains an IR-document marker (pdf/xlsx/csv extension or IR keywords), with
the extension list configurable per source. It never follows pagination and
never leaves the listing page's origin.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from yowayowa.acquisition.snapshots import _safe_component

_IR_KEYWORD_RE = re.compile(
    r"(決算|説明会|資料|短信|FactBook|factbook|financial|results|presentation|"
    r"briefing|supplement|tanshin|summary|中期経営|中期計画|月次|operative|"
    r"earnings|KPI|kpi)",
    re.IGNORECASE,
)
# Documents are binary data files (pdf/xls/xlsx/csv). HTML pages are never
# "documents"; they can be monitored only via include_pages + IR keyword on
# the anchor text.
_DOCUMENT_EXTENSION_RE = re.compile(r"\.(pdf|xlsx?|csv)(\?|$)", re.IGNORECASE)
_MAX_LINKS = 500


@dataclass
class DiscoveredDocument:
    """One IR document link found on a listing page."""

    url: str
    label: str
    kind: str  # "document" (pdf/xls/csv) or "page" (html link w/ IR keyword)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self._href = value
                self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append({"href": self._href, "text": " ".join("".join(self._chunks).split())})
            self._href = None
            self._chunks = []


def same_origin(listing_url: str, target_url: str) -> bool:
    base = urlsplit(listing_url)
    target = urlsplit(target_url)
    return (base.scheme, base.hostname) == (target.scheme, target.hostname)


def discover_ir_documents(
    listing_html: str,
    *,
    listing_url: str,
    include_pages: bool = False,
) -> tuple[list[DiscoveredDocument], list[str]]:
    """Enumerate IR document links on one listing page.

    Returns (documents, notes). Only same-origin links are kept; document
    links are pdf/xls/xlsx/csv; html links are kept only when
    ``include_pages`` is set AND the anchor text carries an IR keyword.
    """

    collector = _LinkCollector()
    collector.feed(listing_html)
    collector.close()
    documents: list[DiscoveredDocument] = []
    seen_urls: set[str] = set()
    skipped_offorigin = 0
    for link in collector.links[: _MAX_LINKS * 4]:
        href = link["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(listing_url, href)
        if not same_origin(listing_url, absolute):
            skipped_offorigin += 1
            continue
        normalized = absolute.split("#", 1)[0]
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        text = link["text"]
        is_document = bool(_DOCUMENT_EXTENSION_RE.search(normalized))
        if is_document:
            documents.append(DiscoveredDocument(url=normalized, label=text, kind="document"))
        elif include_pages and _IR_KEYWORD_RE.search(f"{text} {normalized}"):
            documents.append(DiscoveredDocument(url=normalized, label=text, kind="page"))
        if len(documents) >= _MAX_LINKS:
            break
    notes: list[str] = []
    if skipped_offorigin:
        notes.append(f"skipped {skipped_offorigin} off-origin links")
    if not documents:
        notes.append("no IR document links found on listing page")
    return documents, notes


# ------------------------------------------------------- fingerprint store


def classify_documents(
    discovered: list[DiscoveredDocument],
    fetched: dict[str, dict[str, str]],
    known: set[str],
    known_content_urls: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Classify discovered documents against known fingerprint keys.

    ``fetched`` maps document URL -> fingerprint dict (url/sha256/label) for
    documents whose bytes were fetched this run. Statuses:

    - ``new``: URL never recorded before.
    - ``seen``: URL recorded before (URL-only), bytes not fetched this run.
    - ``verified``: URL recorded before only URL-only, bytes fetched for the
      first time now — a first content observation, not a revision.
    - ``unchanged``: content hash matches a previously recorded content hash.
    - ``revised``: URL had a recorded content hash before and it changed.

    ``known_content_urls`` (urls with a prior non-empty sha) distinguishes
    ``verified`` from ``revised``; when omitted it is derived from ``known``.

    ``known`` keys are ``url|sha`` (empty sha for URL-only records).
    """

    if known_content_urls is None:
        known_content_urls = {key.split("|", 1)[0] for key in known if key.split("|", 1)[1]}
    results: list[dict[str, Any]] = []
    known_urls = {key.split("|", 1)[0] for key in known}
    for document in discovered:
        fingerprint = fetched.get(document.url)
        if fingerprint is not None:
            key = f"{fingerprint['url']}|{fingerprint['sha256']}"
            if key in known:
                status = "unchanged"
            elif document.url in known_content_urls:
                status = "revised"
            elif document.url in known_urls:
                status = "verified"
            else:
                status = "new"
            results.append(
                {
                    "url": document.url,
                    "label": document.label,
                    "kind": document.kind,
                    "status": status,
                    "sha256": fingerprint["sha256"],
                }
            )
        else:
            status = "seen" if document.url in known_urls else "new"
            results.append(
                {
                    "url": document.url,
                    "label": document.label,
                    "kind": document.kind,
                    "status": status,
                    "sha256": None,
                }
            )
    return results


@dataclass
class FingerprintStore:
    """Append-only JSONL store of document fingerprints per IR source."""

    root: Path

    def _path(self, source_id: str) -> Path:
        directory = self.root / _safe_component(source_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "fingerprints.jsonl"

    def record(self, source_id: str, fingerprints: list[dict[str, str]]) -> None:
        lines = [json.dumps(item, ensure_ascii=False) for item in fingerprints]
        with self._path(source_id).open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")

    def known_keys(self, source_id: str) -> set[str]:
        """All recorded (url, sha) keys; URL-only records key as ``url|``."""

        path = self.root / _safe_component(source_id) / "fingerprints.jsonl"
        if not path.exists():
            return set()
        keys: set[str] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                url = item.get("url")
                if url:
                    keys.add(f"{url}|{item.get('sha256') or ''}")
        return keys

    def known_content_urls(self, source_id: str) -> set[str]:
        """URLs with at least one recorded non-empty content hash."""

        path = self.root / _safe_component(source_id) / "fingerprints.jsonl"
        if not path.exists():
            return set()
        urls: set[str] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                url = item.get("url")
                sha = item.get("sha256")
                if url and sha:
                    urls.add(url)
        return urls

    def known_urls(self, source_id: str) -> set[str]:
        path = self.root / _safe_component(source_id) / "fingerprints.jsonl"
        if not path.exists():
            return set()
        urls: set[str] = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                url = item.get("url")
                if url:
                    urls.add(url)
        return urls
