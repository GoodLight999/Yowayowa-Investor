"""株探 (kabutan) 信用取引残高 (weekly credit margin) provider (P4-C).

Fetches ``https://kabutan.jp/stock/?code={code}`` and parses the
``<h2 class="mgt6">信用取引&nbsp;(単位:千株)</h2>`` section table (latest 4
weeks) into :class:`yowayowa.credit_margin_models.CreditMarginWeekly` rows,
converting 千株 decimals into shares (株 int) by x1000 rounding.

Live format verified 2026-09-24 (docs/CREDIT_MARGIN.md):

- row shape: ``<tr><th scope='row'><time datetime="2026-09-11">09/11</time></th>
  <td>2,625.4</td><td>17,239.0</td><td>6.57</td></tr>`` — columns 日付・売り残・
  買い残・倍率; only 売り残/買い残 are persisted (倍率 is derived read-time);
- unit is 千株 with one decimal; x1000 keeps the value within ±100 shares of
  the exact share count (cross-validated against Yahoo at the same as_of, see
  docs/CREDIT_MARGIN.md).

Fail-closed: HTTP error, missing 信用取引 section, unexpected columns, or
unparseable numbers raise :class:`CreditMarginKabutanError`. Decorative cells
(SVG icons etc.) cannot be confused with data because the row regex requires
the exact ``<th scope='row'><time ...>`` date shape followed by three
plain-number cells.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from urllib.parse import quote

import httpx

from yowayowa.credit_margin_models import CreditMarginWeekly, normalize_credit_margin_code
from yowayowa.domain import LicenseClass, Provenance
from yowayowa.providers.base import ProviderDescriptor, enforce_provider_policy

__all__ = [
    "CREDIT_MARGIN_KABUTAN_URL_TEMPLATE",
    "CreditMarginKabutanError",
    "credit_margin_kabutan_descriptor",
    "credit_margin_kabutan_provenance",
    "credit_margin_kabutan_url",
    "enforce_credit_margin_kabutan_policy",
    "fetch_credit_margin_kabutan",
    "parse_credit_margin_kabutan_html",
]

CREDIT_MARGIN_KABUTAN_URL_TEMPLATE: Final[str] = "https://kabutan.jp/stock/?code={code}"

_CREDIT_MARGIN_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# The 信用取引 section header is the only anchor for its table; without it the
# page format has drifted and the parse must fail.
_KABUTAN_SECTION_RE: Final[re.Pattern[str]] = re.compile(r"<h2[^>]*>信用取引[^<]*</h2>")

# One data row inside that section: date th + three numeric tds (売り残・
# 買い残・倍率, 千株-decimal / unitless). Decorative/image cells cannot match.
_KABUTAN_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"<tr>\s*<th scope='row'><time datetime=\"(\d{4})-(\d{2})-(\d{2})\">[^<]*</time></th>\s*"
    r"<td>([\d,.]+)</td>\s*"
    r"<td>([\d,.]+)</td>\s*"
    r"<td>([\d,.]+)</td>"
)

_KABUTAN_DECIMALS: Final[Decimal] = Decimal("1000")


class CreditMarginKabutanError(RuntimeError):
    """Raised when the kabutan stock page violates the observed credit format."""


def credit_margin_kabutan_url(code: str) -> str:
    """Stock-top URL for one code (``7203`` -> ``.../stock/?code=7203``)."""

    return CREDIT_MARGIN_KABUTAN_URL_TEMPLATE.format(code=quote(normalize_credit_margin_code(code)))


def _parse_thousands(value: str, *, field: str, code: str, as_of: str) -> int:
    """千株 decimal cell -> shares int (x1000, exact decimal rounding)."""

    text = value.strip().replace(",", "")
    try:
        return int((Decimal(text) * _KABUTAN_DECIMALS).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise CreditMarginKabutanError(
            f"Unparseable {field} value {value!r} for {code} week {as_of}: "
            "refusing to coerce (unit is 千株, x1000 to shares)"
        ) from exc


def parse_credit_margin_kabutan_html(
    html: str,
    *,
    code: str,
    source_url: str,
    retrieved_at: datetime | None = None,
) -> list[CreditMarginWeekly]:
    """Parse the 信用取引 section table into weekly rows, newest first."""

    section = _KABUTAN_SECTION_RE.search(html)
    if section is None:
        raise CreditMarginKabutanError(
            f"kabutan stock page for {code} has no 信用取引 section: format drift, "
            "refusing to guess"
        )
    table_html = _table_following_section(html, section.end())
    parsed_at = retrieved_at or datetime.now(UTC)
    rows: list[CreditMarginWeekly] = []
    seen: set[date] = set()
    for match in _KABUTAN_ROW_RE.finditer(table_html):
        year, month, day, short_raw, long_raw, _ratio = match.groups()
        try:
            as_of_date = date(int(year), int(month), int(day))
        except ValueError as exc:
            raise CreditMarginKabutanError(
                f"Invalid week date {year}-{month}-{day} for {code}: {exc}"
            ) from exc
        if as_of_date in seen:
            raise CreditMarginKabutanError(
                f"Duplicate week {as_of_date.isoformat()} in kabutan credit history for {code}"
            )
        seen.add(as_of_date)
        rows.append(
            CreditMarginWeekly(
                as_of_date=as_of_date,
                code=code,
                short_total=_parse_thousands(
                    short_raw, field="short_total", code=code, as_of=as_of_date.isoformat()
                ),
                long_total=_parse_thousands(
                    long_raw, field="long_total", code=code, as_of=as_of_date.isoformat()
                ),
                provenance=credit_margin_kabutan_provenance(
                    source_url=source_url,
                    retrieved_at=parsed_at,
                    as_of=as_of_date,
                ),
            )
        )
    if not rows:
        raise CreditMarginKabutanError(
            f"kabutan 信用取引 table for {code} contains no data rows: format drift"
        )
    return rows


def _table_following_section(html: str, search_from: int) -> str:
    """The first ``</table>``-terminated region after the 信用取引 heading."""

    table_start = html.find("<table", search_from)
    if table_start == -1:
        raise CreditMarginKabutanError(
            "kabutan 信用取引 section is not followed by a table: format drift"
        )
    table_end = html.find("</table>", table_start)
    if table_end == -1:
        raise CreditMarginKabutanError("kabutan 信用取引 table is unterminated: format drift")
    return html[table_start : table_end + len("</table>")]


def fetch_credit_margin_kabutan(
    code: str,
    *,
    client: httpx.Client | None = None,
    retrieved_at: datetime | None = None,
) -> list[CreditMarginWeekly]:
    """Fetch and parse one kabutan stock page (1 URL = 1 request)."""

    url = credit_margin_kabutan_url(code)
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
            raise CreditMarginKabutanError(
                f"kabutan stock page for {code} returned HTTP {response.status_code}"
            )
        return parse_credit_margin_kabutan_html(
            response.text,
            code=code,
            source_url=url,
            retrieved_at=retrieved_at,
        )
    except httpx.HTTPError as exc:
        raise CreditMarginKabutanError(
            f"kabutan credit margin fetch failed for {code}: {exc}"
        ) from exc
    finally:
        if client is None:
            owned_client.close()


def credit_margin_kabutan_descriptor() -> ProviderDescriptor:
    """Scraped personal-use data; not redistributable (docs/CREDIT_MARGIN.md)."""

    return ProviderDescriptor(
        name="kabutan_margin",
        license_class=LicenseClass.PERSONAL_ONLY,
        redistributable=False,
        description=(
            "株探 信用取引残高 weekly table (scraped stock top page, personal operator use only)."
        ),
    )


def enforce_credit_margin_kabutan_policy(*, mode: str) -> None:
    """Fail closed outside personal mode (single policy enforcement point)."""

    enforce_provider_policy(credit_margin_kabutan_descriptor(), mode=mode)


def credit_margin_kabutan_provenance(
    *,
    source_url: str,
    retrieved_at: datetime,
    as_of: date,
) -> Provenance:
    return Provenance(
        provider="kabutan_margin",
        source="株探 信用取引残高 weekly table (stock top page)",
        source_url=source_url,
        license_class=LicenseClass.PERSONAL_ONLY,
        retrieved_at=retrieved_at,
        as_of=as_of,
        notes=[
            "Personal-only scraped data; not redistributable (docs/CREDIT_MARGIN.md).",
            "Public HTML page, no authenticated/XHR access; one URL = one request.",
            "Source unit is 千株; persisted as shares (x1000, ±100 share rounding).",
            f"as-of week: {as_of.isoformat()}",
        ],
    )
