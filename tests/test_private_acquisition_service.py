from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yowayowa.acquisition.cache import AcquisitionCache
from yowayowa.acquisition.models import AcquisitionFetchState, AuthState
from yowayowa.acquisition.registry import (
    ConnectorDefinition,
    ConnectorRegistry,
)
from yowayowa.acquisition.service import PrivateAcquisitionService, TransportFactory
from yowayowa.acquisition.snapshots import SnapshotStore
from yowayowa.acquisition.transport import (
    PrivateAcquisitionError,
    SessionTransport,
    TransportResponse,
)

_JSON_OK = b'{"cash": 1000, "positions": []}'


class ScriptedTransport:
    def __init__(self, responses: list[TransportResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.headers_seen: Mapping[str, str] | None = None

    def fetch(
        self,
        method: str,
        resource: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: object | None = None,
    ) -> TransportResponse:
        self.calls.append(
            {"method": method, "resource": resource, "params": params, "headers": headers}
        )
        # A real credentialed transport (session cookies, bearer tokens) sees
        # these on the wire even when the service passes no explicit headers.
        self.headers_seen = {
            "authorization": "Bearer sekrit",
            "cookie": "session=abc",
        }
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _response(
    body: bytes = _JSON_OK,
    status: int = 200,
    url: str = "https://broker.example/api/account",
    content_type: str = "application/json",
) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        url=url,
        content_type=content_type,
        text=body.decode("utf-8", errors="replace"),
        content=body,
        elapsed_ms=12.5,
    )


def _definition(**overrides: Any) -> ConnectorDefinition:
    values: dict[str, Any] = {
        "id": "test-conn",
        "provider": "broker",
        "base_url": "https://broker.example/api/",
        "method": "private_http",
    }
    values.update(overrides)
    return ConnectorDefinition(**values)


def _build_service(
    transport: SessionTransport,
    *,
    data_dir: Path,
    now: Callable[[], datetime] | None = None,
) -> PrivateAcquisitionService:
    factory: TransportFactory = lambda definition: transport  # noqa: E731
    kwargs: dict[str, Any] = {
        "registry": ConnectorRegistry(),
        "cache": AcquisitionCache(),
        "snapshot_store": SnapshotStore(data_dir),
        "data_dir": data_dir,
        "transport_factory": factory,
    }
    if now is not None:
        kwargs["now"] = now
    return PrivateAcquisitionService(**kwargs)


def test_ok_json_fetch_provenance_and_first_snapshot_without_diff(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())

    outcome = service.fetch("test-conn", "account")

    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.auth_state == AuthState.AUTHENTICATED
    assert outcome.payload == {"cash": 1000, "positions": []}
    assert outcome.source_url == "https://broker.example/api/account"
    assert outcome.retrieved_at is not None
    assert outcome.parser_version == "json-v1"
    assert outcome.schema_version == "json-v1"
    assert outcome.snapshot is not None
    assert outcome.snapshot.snapshot_id == outcome.snapshot.payload_sha256
    assert outcome.diff is None  # first snapshot has nothing to diff against
    assert len(outcome.network) == 1
    assert outcome.network[0].url == "https://broker.example/api/account"
    assert "?" not in outcome.network[0].url


def test_second_fetch_same_payload_diff_not_changed(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(), _response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    service.fetch("test-conn", "account")
    second = service.fetch("test-conn", "account", force_refresh=True)
    assert second.diff is not None
    assert second.diff.changed is False


def test_mutated_payload_diff_changed_with_field_change(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(b'{"cash": 1000}'), _response(b'{"cash": 1200}')])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    service.fetch("test-conn", "account")
    second = service.fetch("test-conn", "account", force_refresh=True)
    assert second.diff is not None
    assert second.diff.changed is True
    assert second.diff.changes[0].path == "$.cash"
    assert second.diff.changes[0].previous == "1000"
    assert second.diff.changes[0].current == "1200"


def test_auth_expired_outcome_has_no_snapshot_and_invalidates_cache(tmp_path: Path) -> None:
    transport = ScriptedTransport(
        [
            _response(b'{"cash": 1}'),
            _response(b"<html>Login</html>", status=200, url="https://broker.example/login"),
        ]
    )
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    first = service.fetch("test-conn", "account")
    assert first.fetch_state == AcquisitionFetchState.OK

    expired = service.fetch("test-conn", "account", force_refresh=True)
    assert expired.fetch_state == AcquisitionFetchState.AUTH_EXPIRED
    assert expired.auth_state == AuthState.UNAUTHENTICED
    assert expired.payload is None
    assert expired.snapshot is None
    assert "reauthentication" in " ".join(expired.notes)

    # Cache must be invalidated: a subsequent fetch goes back to the network.
    followup_transport = ScriptedTransport([_response()])
    object.__setattr__(service, "_transport_factory", lambda definition: followup_transport)
    refetched = service.fetch("test-conn", "account")
    assert refetched.fetch_state == AcquisitionFetchState.OK
    assert len(followup_transport.calls) == 1


def test_failed_http_error_returns_failed_outcome(tmp_path: Path) -> None:
    transport = ScriptedTransport([PrivateAcquisitionError(AcquisitionFetchState.FAILED, "boom")])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    outcome = service.fetch("test-conn", "account")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert outcome.payload is None
    assert outcome.notes == ["boom"]


def test_stale_served_from_cache_with_reason(tmp_path: Path) -> None:
    clock = {"now": datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)}

    def now_fn() -> datetime:
        return clock["now"]

    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path, now=now_fn)
    service.register_connector(_definition(freshness={"ttl_seconds": 60, "max_stale_seconds": 600}))
    first = service.fetch("test-conn", "account")
    assert first.fetch_state == AcquisitionFetchState.OK

    clock["now"] = clock["now"].replace(minute=2)  # past ttl, within max-stale
    stale = service.fetch("test-conn", "account")
    assert stale.fetch_state == AcquisitionFetchState.STALE
    assert stale.payload == {"cash": 1000, "positions": []}
    assert stale.cache is not None
    assert stale.cache.state == AcquisitionFetchState.STALE
    assert stale.cache.reason is not None
    assert len(transport.calls) == 1  # served without a new network fetch


def test_unknown_connector_returns_failed_outcome(tmp_path: Path) -> None:
    service = PrivateAcquisitionService.build_default(data_dir=tmp_path)
    outcome = service.fetch("ghost", "res")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "unknown connector" in outcome.notes[0]


def test_no_secrets_in_outcome_json(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(), _response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    # The transport layer legitimately sees credential-bearing headers on the
    # wire for an authenticated source; the acquisition outcome must not leak
    # any trace of them.
    service.fetch("test-conn", "account", force_refresh=True)
    assert transport.headers_seen is not None
    rendered = service.fetch("test-conn", "account", force_refresh=True).model_dump_json()
    lowered = rendered.lower()
    assert "authorization" not in lowered
    assert "cookie" not in lowered
    assert "sekrit" not in lowered
    assert "session=abc" not in lowered


def test_cache_hit_returns_cached_payload_with_reason(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    first = service.fetch("test-conn", "account")
    assert first.fetch_state == AcquisitionFetchState.OK
    hit = service.fetch("test-conn", "account")
    assert hit.fetch_state == AcquisitionFetchState.OK
    assert hit.notes == ["cache-hit"]
    assert hit.payload == {"cash": 1000, "positions": []}
    assert len(transport.calls) == 1


def test_as_of_extracted_from_payload(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(b'{"cash": 1, "as_of": "2026-09-01T00:00:00+00:00"}')])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    outcome = service.fetch("test-conn", "account")
    assert outcome.as_of is not None
    assert outcome.as_of.year == 2026 and outcome.as_of.month == 9


def test_json_array_payload_fails_with_expected_object(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(b"[1,2,3]")])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    outcome = service.fetch("test-conn", "account")
    assert outcome.fetch_state == AcquisitionFetchState.FAILED
    assert "expected object" in outcome.notes[0]


def test_tables_parser_connector_flow(tmp_path: Path) -> None:
    html = b"<table><tr><th>Cash</th></tr><tr><td>100</td></tr></table>"
    transport = ScriptedTransport([_response(html, content_type="text/html")])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition(parser="tables", method="html_scrape"))
    outcome = service.fetch("test-conn", "account")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.payload == {"tables": [{"headers": ["Cash"], "rows": [{"Cash": "100"}]}]}
    assert outcome.parser_version == "table-v1"


def test_download_parser_connector_flow(tmp_path: Path) -> None:
    csv_bytes = b"symbol,qty\n7203,100\n"
    transport = ScriptedTransport(
        [_response(csv_bytes, content_type="text/csv", url="https://broker.example/api/export.csv")]
    )
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition(parser="download"))
    outcome = service.fetch("test-conn", "export.csv")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.downloads, "download capture missing"
    capture = outcome.downloads[0]
    assert capture.format == "csv"
    assert capture.rows == [{"symbol": "7203", "qty": "100"}]
    assert outcome.parser_version == "raw-v1"


def test_auth_check_reports_authenticated(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response()])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition(auth_recheck_resource="session"))
    outcome = service.auth_check("test-conn")
    assert outcome.fetch_state == AcquisitionFetchState.OK
    assert outcome.auth_state == AuthState.AUTHENTICATED
    assert outcome.snapshot is None
    assert transport.calls[0]["resource"] == "session"


def test_snapshots_listing_and_diff_via_service(tmp_path: Path) -> None:
    transport = ScriptedTransport([_response(b'{"v": 1}'), _response(b'{"v": 2}')])
    service = _build_service(transport, data_dir=tmp_path)
    service.register_connector(_definition())
    service.fetch("test-conn", "account")
    service.fetch("test-conn", "account", force_refresh=True)
    history = service.snapshots("test-conn", "account")
    assert len(history) == 2
    diff = service.diff("test-conn", "account")
    assert diff is not None
    assert diff.changed is True
    assert diff.changes[0].path == "$.v"
