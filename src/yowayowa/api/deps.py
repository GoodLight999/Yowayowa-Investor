from __future__ import annotations

import re
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from yowayowa.config import Settings, get_settings
from yowayowa.db import get_session

_PUBLIC_READ_ONLY_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"/v1/instruments/search")),
    ("GET", re.compile(r"/v1/fundamentals/[^/]+")),
    ("POST", re.compile(r"/v1/screen")),
    ("GET", re.compile(r"/v1/compare/metrics")),
    ("POST", re.compile(r"/v1/compare")),
    ("GET", re.compile(r"/v1/macro/bls/catalog")),
    ("GET", re.compile(r"/v1/macro/bls/[^/]+")),
    ("GET", re.compile(r"/v1/macro/bea/nipa/catalog")),
    ("GET", re.compile(r"/v1/macro/bea/nipa/[^/]+")),
    ("GET", re.compile(r"/v1/macro/estat/tables")),
    ("GET", re.compile(r"/v1/macro/estat/[^/]+/meta")),
    ("GET", re.compile(r"/v1/macro/estat/[^/]+/data")),
    ("GET", re.compile(r"/v1/licensing/sources")),
    ("GET", re.compile(r"/v1/filings/edinet")),
    ("GET", re.compile(r"/v1/filings/edinet/documents")),
    ("GET", re.compile(r"/v1/filings/edinet/index/history")),
    ("GET", re.compile(r"/v1/filings/edinet/index/issuers")),
    ("GET", re.compile(r"/v1/filings/edinet/[A-Za-z0-9]{8}/financials")),
    ("GET", re.compile(r"/v1/filings/edinet/[A-Za-z0-9]{8}/facts")),
    ("GET", re.compile(r"/v1/rates/treasury/curve")),
    ("GET", re.compile(r"/v1/institutional/13f/[^/]+")),
)


def db_session() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _anonymous_public_research_allowed(request: Request) -> bool:
    return any(
        method == request.method and pattern.fullmatch(request.url.path)
        for method, pattern in _PUBLIC_READ_ONLY_ROUTES
    )


def require_api_token(
    request: Request,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.mode == "public" and _anonymous_public_research_allowed(request):
        return
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
