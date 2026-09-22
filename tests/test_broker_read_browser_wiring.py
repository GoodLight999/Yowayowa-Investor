"""Regression tests for the production Rakuten browser-session wiring (P1B).

These tests deliberately do NOT inject a fake transport into
``PrivateAcquisitionService``. They inject a fake *browser session* into the
module-level ``_RAKUTEN_BROWSER_SESSION`` cache and then drive the real
production factory (``deps._rakuten_browser_transport_factory``) and the real
``RakutenWebFetchTransport`` / ``BrokerReadService`` code paths.

The fake session mirrors the contract of ``PersistentBrokerWebSession``
strictly: it accepts ONLY relative paths and raises ``BrokerWebSessionError``
for anything carrying a scheme or netloc. A laxer fake would make these tests
meaningless — the two bugs they cover were:

1. deps.py handed the transport-resolved ABSOLUTE url to ``session.request``,
   so every fetch failed with ``BrokerWebSessionError: broker web-session
   paths must be relative`` against a real session;
2. that ``BrokerWebSessionError`` is not a ``PrivateAcquisitionError``, so it
   slipped through the acquisition service's ``except`` clause, escaped to the
   caller, and turned the API response into HTTP 500 instead of a fail-closed
   ``fetch_state=failed`` outcome.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from starlette.testclient import TestClient

from yowayowa.acquisition.cache import AcquisitionCache
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.acquisition.registry import ConnectorDefinition, ConnectorRegistry
from yowayowa.acquisition.service import PrivateAcquisitionService
from yowayowa.acquisition.snapshots import SnapshotStore
from yowayowa.acquisition.transport import PrivateAcquisitionError
from yowayowa.api import deps
from yowayowa.config import get_settings
from yowayowa.operator_bridge.rakuten_web import (
    RAKUTEN_WEB_BASE_URL,
    RAKUTEN_WEB_RESOURCE_CATALOG,
    RakutenWebFetchTransport,
)
from yowayowa.operator_bridge.web_session import BrokerWebSessionError
from yowayowa.private_connectors import PrivateAcquisitionMethod
from yowayowa.services.broker_read_service import BrokerReadService

if TYPE_CHECKING:
    from yowayowa.operator_bridge.web_session import PersistentBrokerWebSession

_ABSOLUTE_POSITIONS_URL = "https://trade.rakuten-sec.co.jp/web/positions/jp"
_OFF_ORIGIN_POSITIONS_URL = "https://www.rakuten-sec.co.jp/web/positions/jp"

_POSITIONS_BODY = json.dumps(
    {
        "positions": [
            {
                "symbol": "7203",
                "quantity": "100株",
                "average_cost": "2,500円",
                "unrealized_pnl": "▲15,000円",
            }
        ]
    },
    ensure_ascii=False,
).encode("utf-8")


class _FakeApiResponse:
    """Minimal stand-in for playwright's ``APIResponse``."""

    def __init__(
        self,
        *,
        status: int = 200,
        url: str = _ABSOLUTE_POSITIONS_URL,
        body: bytes = _POSITIONS_BODY,
        content_type: str = "application/json",
    ) -> None:
        self.status = status
        self.url = url
        self.headers = {"content-type": content_type}
        self._body = body

    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def body(self) -> bytes:
        return self._body


class _FakeBrokerWebSession:
    """Strict fake of ``PersistentBrokerWebSession`` (relative paths only)."""

    def __init__(
        self,
        *,
        response: _FakeApiResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.base_url = RAKUTEN_WEB_BASE_URL
        self._base_origin = ("https", "trade.rakuten-sec.co.jp", None)
        self._response = response or _FakeApiResponse()
        # Raised before path handling: simulates a crashed / not-started
        # browser, independent of the path shape.
        self._error = error
        self.paths: list[str] = []
        self.calls: list[dict[str, Any]] = []

    # -- real contract: identical validation to PersistentBrokerWebSession ---
    def _relative_url(self, path: str) -> str:
        from urllib.parse import urljoin, urlsplit

        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            raise BrokerWebSessionError("broker web-session paths must be relative")
        return urljoin(self.base_url, path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str | float | bool] | None = None,
        data: object | None = None,
        form: dict[str, str | float | bool] | None = None,
        timeout_ms: float = 30_000,
    ) -> _FakeApiResponse:
        if self._error is not None:
            raise self._error
        self._relative_url(path)  # rejects absolute URLs exactly like the real one
        self.paths.append(path)
        self.calls.append({"method": method, "path": path, "params": params})
        return self._response


def _install_session(session: _FakeBrokerWebSession) -> None:
    deps._RAKUTEN_BROWSER_SESSION["session"] = cast("PersistentBrokerWebSession", session)


@pytest.fixture(autouse=True)
def _clear_browser_session() -> Any:
    """Never let an injected session leak into another test."""
    deps._RAKUTEN_BROWSER_SESSION.pop("session", None)
    yield
    deps._RAKUTEN_BROWSER_SESSION.pop("session", None)


def _positions_definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        id="rakuten-web",
        provider="rakuten-securities",
        base_url=RAKUTEN_WEB_BASE_URL,
        method=PrivateAcquisitionMethod.AUTHENTICATED_WEB_SESSION,
        parser="json",
    )


def _production_transport() -> RakutenWebFetchTransport:
    """The unmodified production factory output (not a test double)."""
    factory = deps._rakuten_browser_transport_factory
    assert factory is not None
    return cast("RakutenWebFetchTransport", factory(_positions_definition()))


def _production_service(tmp_path: Path) -> BrokerReadService:
    """BrokerReadService wired exactly as deps.get_broker_read_service wires it."""
    acquisition = PrivateAcquisitionService(
        registry=ConnectorRegistry(),
        cache=AcquisitionCache(),
        snapshot_store=SnapshotStore(tmp_path),
        data_dir=tmp_path,
        transport_factory=deps._rakuten_browser_transport_factory,
    )
    return BrokerReadService(acquisition=acquisition)


# ---------------------------------------------------------------------------
# The fake session must be as strict as the real one (guard on the guard).
# ---------------------------------------------------------------------------


def test_fake_session_rejects_absolute_url_like_the_real_one() -> None:
    session = _FakeBrokerWebSession()
    with pytest.raises(BrokerWebSessionError, match="must be relative"):
        session.request("GET", _ABSOLUTE_POSITIONS_URL)
    assert session.paths == []


# ---------------------------------------------------------------------------
# Bug 1: absolute URL must be converted to a relative path before the session.
# ---------------------------------------------------------------------------


def test_production_wiring_passes_relative_path_to_session() -> None:
    session = _FakeBrokerWebSession()
    _install_session(session)

    response = _production_transport().fetch("GET", "web/positions/jp")

    assert session.paths == ["/web/positions/jp"], session.calls
    assert response.status_code == 200
    assert response.content == _POSITIONS_BODY


def test_production_wiring_keeps_query_and_params_separate() -> None:
    session = _FakeBrokerWebSession()
    _install_session(session)

    _production_transport().fetch("GET", "web/positions/jp", params={"page": "2"})

    assert session.paths == ["/web/positions/jp"]
    assert session.calls[0]["params"] == {"page": "2"}


def test_production_wiring_strips_query_from_provenance_url(tmp_path: Path) -> None:
    session = _FakeBrokerWebSession(
        response=_FakeApiResponse(url=f"{_ABSOLUTE_POSITIONS_URL}?session_ref=abc123")
    )
    _install_session(session)

    outcome = _production_service(tmp_path).fetch("positions", "jp")

    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.source_url == _ABSOLUTE_POSITIONS_URL
    assert all("?" not in exchange.url for exchange in outcome.network)


# ---------------------------------------------------------------------------
# Origin check: an allowed host on a different origin must fail closed.
# ---------------------------------------------------------------------------


def test_off_origin_url_fails_closed_without_reaching_session() -> None:
    session = _FakeBrokerWebSession()
    _install_session(session)

    with pytest.raises(PrivateAcquisitionError) as excinfo:
        _production_transport().fetch("GET", _OFF_ORIGIN_POSITIONS_URL)

    assert excinfo.value.state == AcquisitionFetchState.FAILED
    assert "origin mismatch" in excinfo.value.reason
    assert "www.rakuten-sec.co.jp" in excinfo.value.reason
    assert session.paths == []


def test_off_origin_catalog_url_fails_closed_through_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog entry pointing at another (even allow-listed) origin must
    produce a FAILED outcome, not an escaped exception or a silent fetch."""
    session = _FakeBrokerWebSession()
    _install_session(session)
    entry = RAKUTEN_WEB_RESOURCE_CATALOG[("positions", "jp")]
    monkeypatch.setitem(
        RAKUTEN_WEB_RESOURCE_CATALOG,
        ("positions", "jp"),
        replace(entry, url=_OFF_ORIGIN_POSITIONS_URL),
    )

    outcome = _production_service(tmp_path).fetch("positions", "jp")

    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "origin mismatch" in " ".join(outcome.notes)
    assert session.paths == []


# ---------------------------------------------------------------------------
# Bug 2: session-level exceptions must become FAILED outcomes, never escape.
# ---------------------------------------------------------------------------


def test_session_exception_becomes_failed_outcome_not_exception(tmp_path: Path) -> None:
    session = _FakeBrokerWebSession(
        error=BrokerWebSessionError("broker web session has not been started")
    )
    _install_session(session)

    outcome = _production_service(tmp_path).fetch("positions", "jp")

    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert outcome.positions == []
    assert outcome.account is None
    note = " ".join(outcome.notes)
    assert "browser session error" in note
    assert "BrokerWebSessionError" in note
    assert "broker web session has not been started" in note


def test_arbitrary_session_exception_becomes_failed_outcome(tmp_path: Path) -> None:
    session = _FakeBrokerWebSession(error=OSError("connection reset by peer"))
    _install_session(session)

    outcome = _production_service(tmp_path).fetch("positions", "jp")

    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "browser session error: OSError" in " ".join(outcome.notes)


def test_production_transport_converts_session_error_to_acquisition_error() -> None:
    """The transport boundary must raise PrivateAcquisitionError (which the
    acquisition service catches), never a bare BrokerWebSessionError."""
    session = _FakeBrokerWebSession(error=BrokerWebSessionError("broker web session down"))
    _install_session(session)

    transport = _production_transport()
    with pytest.raises(PrivateAcquisitionError) as excinfo:
        transport.fetch("GET", "web/positions/jp")

    assert excinfo.value.state == AcquisitionFetchState.FAILED
    assert "browser session error" in excinfo.value.reason
    assert isinstance(excinfo.value.__cause__, BrokerWebSessionError)


# ---------------------------------------------------------------------------
# Auth expiry detection through the production wiring.
# ---------------------------------------------------------------------------


def test_login_page_through_production_wiring_reports_auth_expired(tmp_path: Path) -> None:
    login_html = "<html><head><title>ログイン</title></head><body>ログイン</body></html>".encode()
    session = _FakeBrokerWebSession(
        response=_FakeApiResponse(
            url="https://trade.rakuten-sec.co.jp/login",
            body=login_html,
            content_type="text/html",
        )
    )
    _install_session(session)

    outcome = _production_service(tmp_path).fetch("positions", "jp")

    assert session.paths == ["/web/positions/jp"]
    assert outcome.fetch_state == AcquisitionFetchState.AUTH_EXPIRED
    assert outcome.auth_state == AuthState.UNAUTHENTICED
    assert outcome.positions == []


# ---------------------------------------------------------------------------
# API level: the wiring bug used to be an HTTP 500.
# ---------------------------------------------------------------------------


def _personal_api_env(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YOWAYOWA_BROKER_RAKUTEN_WEB_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    monkeypatch.delenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", raising=False)
    get_settings.cache_clear()
    deps.get_broker_read_service.cache_clear()


def test_api_fetch_uses_production_browser_session_wiring(monkeypatch: Any, tmp_path: Path) -> None:
    _personal_api_env(monkeypatch, tmp_path)
    session = _FakeBrokerWebSession()
    _install_session(session)
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/broker-read/connectors/rakuten-web/fetch",
            json={"resource": "positions", "market": "jp"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fetch_state"] == "ok"
    assert body["auth_state"] == "authenticated"
    assert [position["symbol"] for position in body["positions"]] == ["7203"]
    assert session.paths == ["/web/positions/jp"]


def test_api_fetch_session_failure_returns_failed_outcome_not_500(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _personal_api_env(monkeypatch, tmp_path)
    _install_session(
        _FakeBrokerWebSession(
            error=BrokerWebSessionError("broker web-session paths must be relative")
        )
    )
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/broker-read/connectors/rakuten-web/fetch",
            json={"resource": "positions", "market": "jp"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fetch_state"] == "failed"
    assert body["positions"] == []
    assert "browser session error" in " ".join(body["notes"])
    # No session material, cookies, or query strings in the client-facing note.
    assert "session_ref" not in " ".join(body["notes"])
