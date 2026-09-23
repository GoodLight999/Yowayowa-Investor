from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import SplitResult, urlsplit

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from yowayowa.acquisition.cache import AcquisitionCache
from yowayowa.acquisition.models import AcquisitionFetchState
from yowayowa.acquisition.registry import ConnectorRegistry
from yowayowa.acquisition.service import PrivateAcquisitionService
from yowayowa.acquisition.snapshots import SnapshotStore
from yowayowa.acquisition.transport import (
    PrivateAcquisitionError,
    SessionTransport,
    TransportResponse,
    provenance_url,
)
from yowayowa.config import Settings, get_settings
from yowayowa.db import get_session

if TYPE_CHECKING:
    from yowayowa.acquisition.registry import ConnectorDefinition
    from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession
    from yowayowa.services.broker_read_service import BrokerReadService
    from yowayowa.services.ir_monitor_service import IrMonitorService

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

DataSourceCredential = Literal["edinet", "estat", "fred", "bea", "bls"]
_DATA_SOURCE_CREDENTIALS: dict[DataSourceCredential, tuple[str, str]] = {
    "edinet": ("x-yowayowa-edinet-key", "edinet_api_key"),
    "estat": ("x-yowayowa-estat-key", "estat_app_id"),
    "fred": ("x-yowayowa-fred-key", "fred_api_key"),
    "bea": ("x-yowayowa-bea-key", "bea_api_key"),
    "bls": ("x-yowayowa-bls-key", "bls_api_key"),
}


def db_session() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def request_data_source_settings(
    request: Request,
    settings: Settings,
    source: DataSourceCredential,
) -> Settings:
    """Apply a browser BYOK credential to one request without persisting it."""

    header_name, field_name = _DATA_SOURCE_CREDENTIALS[source]
    value = request.headers.get(header_name, "").strip()
    if not value:
        return settings
    return settings.model_copy(update={field_name: value})


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


def require_private_connectors(settings: Settings = Depends(get_settings)) -> None:
    """Fail closed unless the private acquisition toolkit is explicitly enabled."""
    if settings.mode != "personal" or not settings.private_connectors_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private acquisition is disabled",
        )


@lru_cache(maxsize=1)
def get_private_acquisition_service() -> PrivateAcquisitionService:
    settings = get_settings()
    return PrivateAcquisitionService.build_default(Path(settings.private_acquisition_data_dir))


_URL_QUERY_PATTERN = re.compile(r"(https?://[^\s\"'<>]*)\?[^\s\"'<>]*")


def _scrub_url_queries(text: str) -> str:
    """Strip query strings from URLs inside a message (provenance-safe text)."""
    return _URL_QUERY_PATTERN.sub(r"\1", text)


def _origin_parts(parts: SplitResult) -> tuple[str, str, int | None]:
    """(scheme, host, port) of a parsed URL; never includes userinfo."""
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port)


def _origin_label(origin: tuple[str, str, int | None]) -> str:
    scheme, host, port = origin
    return f"{scheme}://{host}:{port}" if port is not None else f"{scheme}://{host}"


def _rakuten_browser_transport_factory(
    definition: ConnectorDefinition,
) -> SessionTransport:
    """Fail-closed Rakuten web transport factory (operator browser session).

    playwright and the persistent browser session are imported lazily inside
    this factory only; the acquisition service never touches them at import
    time. Without the operator-browser extra every fetch returns an explicit
    FAILED outcome instead of crashing the request path.
    """
    from yowayowa.config import get_settings as _get_settings
    from yowayowa.operator_bridge.rakuten_web import (
        RAKUTEN_WEB_BASE_URL,
        RakutenWebFetchTransport,
    )

    settings = _get_settings()
    base_origin = _origin_parts(urlsplit(RAKUTEN_WEB_BASE_URL))

    def _fetch_url(
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        **_: object,
    ) -> TransportResponse:
        try:
            from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession
        except ImportError as exc:  # playwright not installed
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                "operator browser session not configured "
                "(install yowayowa-investor[operator-browser])",
            ) from exc

        # The transport resolves the catalog resource to an absolute URL, but
        # PersistentBrokerWebSession only accepts paths relative to its own
        # configured origin. Verify the origin, then reduce the URL to
        # path + query (params stay a separate argument) before handing it over.
        parts = urlsplit(url)
        target = url
        if parts.scheme or parts.netloc:
            origin = _origin_parts(parts)
            if origin != base_origin:
                raise PrivateAcquisitionError(
                    AcquisitionFetchState.FAILED,
                    f"browser session origin mismatch: {_origin_label(origin)}",
                )
            target = parts.path or "/"
            if parts.query:
                target = f"{target}?{parts.query}"

        try:
            session = _RAKUTEN_BROWSER_SESSION.get("session")
            if session is None:
                new_session: PersistentBrokerWebSession = PersistentBrokerWebSession(
                    base_url=RAKUTEN_WEB_BASE_URL,
                    profile_dir=settings.broker_rakuten_web_profile_dir,
                )
                new_session.start()
                _RAKUTEN_BROWSER_SESSION["session"] = new_session
                session = new_session
            response = session.request(method, target, params=dict(params) if params else None)
            status_code = response.status
            response_url = response.url
            content_type = response.headers.get("content-type")
            text = response.text()
            content = response.body()
        except PrivateAcquisitionError:
            raise
        except Exception as exc:
            # BrokerWebSessionError, playwright errors, OSError, ...: never let a
            # transport-level failure escape to the API layer as an HTTP 500.
            raise PrivateAcquisitionError(
                AcquisitionFetchState.FAILED,
                f"browser session error: {type(exc).__name__}: {_scrub_url_queries(str(exc))}",
            ) from exc
        return TransportResponse(
            status_code=status_code,
            url=provenance_url(response_url),
            content_type=content_type,
            text=text,
            content=content,
            elapsed_ms=0.0,
        )

    return RakutenWebFetchTransport(fetch=_fetch_url)


@lru_cache(maxsize=1)
def get_ir_monitor_service() -> IrMonitorService:
    """IR monitor wired to the operator's local http transport.

    Default sources are empty: the operator registers company IR pages at
    runtime (CLI/API), so no source list is hardcoded into the product.
    """
    from yowayowa.services.ir_monitor_service import (
        IrMonitorService,
        _default_http_transport,
    )

    settings = get_settings()
    return IrMonitorService(
        data_dir=Path(settings.private_acquisition_data_dir),
        transport_factory=_default_http_transport,
    )


_RAKUTEN_BROWSER_SESSION: dict[str, PersistentBrokerWebSession] = {}


@lru_cache(maxsize=1)
def get_broker_read_service() -> BrokerReadService:
    from yowayowa.config import get_settings
    from yowayowa.services.broker_read_service import BrokerReadService

    settings = get_settings()
    acquisition = PrivateAcquisitionService(
        registry=ConnectorRegistry(),
        cache=AcquisitionCache(),
        snapshot_store=SnapshotStore(Path(settings.private_acquisition_data_dir)),
        data_dir=Path(settings.private_acquisition_data_dir),
        transport_factory=_rakuten_browser_transport_factory,
    )
    return BrokerReadService(acquisition=acquisition)
