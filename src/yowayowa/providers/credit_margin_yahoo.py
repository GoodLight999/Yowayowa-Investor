"""Yahoo!ファイナンス 信用残 (weekly credit margin) provider (P4-C).

Fetches ``https://finance.yahoo.co.jp/quote/{code}.T/margin`` and parses the
server-rendered weekly history table (latest 20 weeks) into
:class:`yowayowa.credit_margin_models.CreditMarginWeekly` rows.

Live format verified 2026-09-24 (docs/CREDIT_MARGIN.md):

- the history table is rendered in the HTML; row shape is
  ``<th ...>2026/9/11</th><td ...>299,300</td><td ...>7,581,100</td>...`` with
  the numeric cell value nested in ``_StyledNumber__value_`` spans;
- columns: 日付・売残・買残・売残増減・買残増減・信用倍率. Only 売残 (short)
  and 買残 (long) are persisted here; 増減 columns are redundant derived
  data (recomputed read-time from persisted weeks) and 信用倍率 likewise;
- unit is shares (株 int), comma-grouped; negative change cells carry a
  leading ``-``;
- the same-day snapshot inside the page's RSC payload is deliberately NOT
  parsed (highly volatile, duplicates the last history week).

Fail-closed: any HTTP error, missing table, unexpected column count, or
unparseable number raises :class:`CreditMarginYahooError` — the page is never
partially interpreted, and no silent fallback exists.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Final
from urllib.parse import quote

import httpx

from yowayowa.credit_margin_models import CreditMarginWeekly, yahoo_credit_margin_symbol
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

__all__ = [
    "CREDIT_MARGIN_YAHOO_URL_TEMPLATE",
    "CreditMarginYahooError",
    "credit_margin_yahoo_descriptor",
    "credit_margin_yahoo_provenance",
    "credit_margin_yahoo_url",
    "enforce_credit_margin_yahoo_policy",
    "fetch_credit_margin_yahoo",
    "parse_credit_margin_yahoo_html",
]

CREDIT_MARGIN_YAHOO_URL_TEMPLATE: Final[str] = "https://finance.yahoo.co.jp/quote/{symbol}/margin"

# Browser UA string: the quote pages are built for browsers and served
# identically to scripts; no authentication, cookies, or storage are used.
_CREDIT_MARGIN_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# One history row: date th, exactly five tds (売残・買残・売残増減・買残増減・
# 信用倍率). The row body between the date th and </tr> must contain exactly
# five "</td>" terminators — a broken/renamed row fails the parse. Every
# td's inner content is captured; the short/long cells must each contain
# exactly one numeric token (un-nested via _YAHOO_CELL_VALUE_RE), the
# trailing 増減/倍率 cells are ignored (derived data is recomputed read-time).
_YAHOO_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"<th[^>]*>(\d{4})/(\d{1,2})/(\d{1,2})</th>\s*"
    r"((?:\s*<td[^>]*>.*?</td>){5})\s*</tr>",
    re.S,
)

_YAHOO_TD_RE: Final[re.Pattern[str]] = re.compile(r"<td[^>]*>(.*?)</td>", re.S)

# The un-nested numeric value inside a styled-number cell: the number itself
# lives in <span class="_StyledNumber__value_...">299,300</span>.
_YAHOO_STYLED_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"_StyledNumber__value_[^\"]*\">([^<]*)</span>"
)

# The five expected column headers; a renamed/missing column is format drift.
_YAHOO_REQUIRED_HEADERS: Final[tuple[str, ...]] = (
    ">日付</th>",
    ">売残</th>",
    ">買残</th>",
    ">売残増減</th>",
    ">買残増減</th>",
    ">信用倍率</th>",
)


class CreditMarginYahooError(RuntimeError):
    """Raised when the Yahoo credit margin page violates the observed format."""


def credit_margin_yahoo_url(code: str) -> str:
    """Quote-page URL for one code (``7203`` -> ``.../quote/7203.T/margin``)."""

    # quote path segment: codes are digits (validated), so quoting is a no-op
    # kept explicit to make the "no query injection" property visible.
    return CREDIT_MARGIN_YAHOO_URL_TEMPLATE.format(symbol=quote(yahoo_credit_margin_symbol(code)))


def _cell_value(cell_html: str) -> str:
    """The single numeric value inside a styled-number cell.

    The cell nests the number inside ``_StyledNumber__value_`` spans; a cell
    that does not contain exactly one such value (e.g. ``abc``, empty, or
    extra text) is format drift and fails the parse.
    """

    matches = _YAHOO_STYLED_VALUE_RE.findall(cell_html)
    if len(matches) != 1:
        raise CreditMarginYahooError(
            f"Yahoo credit margin cell {cell_html[:80]!r} does not contain exactly one "
            "styled numeric value: format drift"
        )
    value = matches[0].strip()
    if not re.fullmatch(r"-?[\d,]+", value):
        raise CreditMarginYahooError(
            f"Yahoo credit margin cell value {value!r} is not an integer amount: format drift"
        )
    return str(value)


def _parse_int(raw: str, *, field: str, code: str, as_of: str) -> int:
    text = raw.strip().replace(",", "")
    try:
        return int(text)
    except ValueError as exc:
        raise CreditMarginYahooError(
            f"Unparseable {field} value {raw!r} for {code} week {as_of}: refusing to coerce"
        ) from exc


def parse_credit_margin_yahoo_html(
    html: str,
    *,
    code: str,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[CreditMarginWeekly]:
    """Parse the rendered weekly history table into weekly rows, newest first.

    ``retrieved_at`` defaults to now (UTC). Every row carries full provenance
    (provider, source URL, license class, retrieved-at, as-of week).
    """

    missing_headers = [header for header in _YAHOO_REQUIRED_HEADERS if header not in html]
    if missing_headers:
        raise CreditMarginYahooError(
            "Yahoo credit margin page does not contain the expected history table "
            f"headers for {code} (missing: {missing_headers}): format drift, "
            "refusing to guess"
        )
    parsed_at = retrieved_at or datetime.now(UTC)
    rows: list[CreditMarginWeekly] = []
    seen: set[date] = set()
    for match in _YAHOO_ROW_RE.finditer(html):
        year, month, day, cells_html = match.groups()
        cells = _YAHOO_TD_RE.findall(cells_html)
        if len(cells) != 5:
            raise CreditMarginYahooError(
                f"Yahoo credit margin row for {code} week {year}/{month}/{day} has "
                f"{len(cells)} columns, expected 5: format drift"
            )
        short_cell, long_cell = cells[0], cells[1]
        try:
            as_of_date = date(int(year), int(month), int(day))
        except ValueError as exc:
            raise CreditMarginYahooError(
                f"Invalid week date {year}/{month}/{day} for {code}: {exc}"
            ) from exc
        if as_of_date in seen:
            raise CreditMarginYahooError(
                f"Duplicate week {as_of_date.isoformat()} in Yahoo credit margin history for {code}"
            )
        seen.add(as_of_date)
        rows.append(
            CreditMarginWeekly(
                as_of_date=as_of_date,
                code=code,
                short_total=_parse_int(
                    _cell_value(short_cell),
                    field="short_total",
                    code=code,
                    as_of=as_of_date.isoformat(),
                ),
                long_total=_parse_int(
                    _cell_value(long_cell),
                    field="long_total",
                    code=code,
                    as_of=as_of_date.isoformat(),
                ),
                provenance=credit_margin_yahoo_provenance(
                    source_url=source_url,
                    retrieved_at=parsed_at,
                    as_of=as_of_date,
                ),
            )
        )
    if not rows:
        raise CreditMarginYahooError(
            f"Yahoo credit margin page for {code} contains no history rows: format drift"
        )
    return rows


def fetch_credit_margin_yahoo(
    code: str,
    *,
    client: httpx.Client | None = None,
    retrieved_at: datetime | None = None,
) -> list[CreditMarginWeekly]:
    """Fetch and parse one Yahoo credit margin page (1 URL = 1 request).

    Pass ``client`` to reuse a transport in tests; the default builds a
    one-shot browser-UA client. Failures raise :class:`CreditMarginYahooError`
    (fail-closed), never return partial data.
    """

    url = credit_margin_yahoo_url(code)
    if client is None:
        owned_client = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": _CREDIT_MARGIN_USER_AGENT},
            timeout=30.0,
        )
    else:
        owned_client = client
    try:
        response = owned_client.get(url)
        if response.status_code != 200:
            raise CreditMarginYahooError(
                f"Yahoo credit margin page for {code} returned HTTP {response.status_code}"
            )
        return parse_credit_margin_yahoo_html(
            response.text,
            code=code,
            source_url=url,
            retrieved_at=retrieved_at,
        )
    except httpx.HTTPError as exc:
        raise CreditMarginYahooError(f"Yahoo credit margin fetch failed for {code}: {exc}") from exc
    finally:
        if client is None:
            owned_client.close()


def credit_margin_yahoo_descriptor() -> ProviderDescriptor:
    """Scraped personal-use data; not redistributable (docs/CREDIT_MARGIN.md)."""

    return ProviderDescriptor(
        name="yahoo_finance_margin",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "Yahoo!ファイナンス 信用残 weekly history (scraped quote page, "
            "personal operator use only)."
        ),
    )


def enforce_credit_margin_yahoo_policy(*, mode: str) -> None:
    """Fail closed outside personal mode (single policy enforcement point)."""

    enforce_provider_policy(credit_margin_yahoo_descriptor(), mode=mode)


def credit_margin_yahoo_provenance(
    *,
    source_url: str,
    retrieved_at: datetime,
    as_of: date,
) -> Provenance:
    return Provenance(
        provider="yahoo_finance_margin",
        source="Yahoo!ファイナンス 信用残 weekly history (quote page)",
        source_url=source_url,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=retrieved_at,
        as_of=as_of,
        notes=[
            "Personal-only scraped data; not redistributable (docs/CREDIT_MARGIN.md).",
            "Public HTML page, no authenticated/XHR access; one URL = one request.",
            f"as-of week: {as_of.isoformat()}",
        ],
    )
