"""P1C IR surfaces: API routes, CLI parity, and fail-closed gating.

Follows the existing P1A/P1B surface test conventions: the shared module-level
app, the private-connectors env switch, and dependency-override-free stubbing
via the service factory's cache.
"""

from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.api.deps import get_ir_monitor_service
from yowayowa.cli_entry import app as cli_app
from yowayowa.config import get_settings
from yowayowa.services.ir_monitor_service import (
    IrMonitorService,
    IrSourceDefinition,
)

runner = CliRunner()


class _StubService(IrMonitorService):
    """Real service with pre-registered sources; monitor() never uses the net."""

    def __init__(self, tmp_path: Any) -> None:
        def _unused_factory(source: IrSourceDefinition) -> Any:
            raise AssertionError("tests must not perform network fetches")

        super().__init__(
            data_dir=tmp_path,
            transport_factory=_unused_factory,
            sources=[
                IrSourceDefinition(
                    source_id="example-ir",
                    symbol="1234.T",
                    provider="ir.example.co.jp",
                    listing_url="https://ir.example.co.jp/library/briefing.html",
                    license_class="official_public",
                )
            ],
        )

    def monitor(self, source_id: str):  # type: ignore[override]
        from yowayowa.acquisition.models import AcquisitionFetchState
        from yowayowa.services.ir_monitor_service import IrMonitorOutcome

        if self.get_source(source_id) is None:
            return super().monitor(source_id)
        return IrMonitorOutcome(
            source_id=source_id,
            symbol="1234.T",
            fetch_state=AcquisitionFetchState.OK,
            listing_url="https://ir.example.co.jp/library/briefing.html",
            new_count=1,
            notes=["stub"],
        )


def _personal_env(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_ir_monitor_service.cache_clear()


def test_ir_cli_help_exits_zero() -> None:
    result = runner.invoke(cli_app, ["ir", "--help"])
    assert result.exit_code == 0, result.output
    for command in ["sources", "add-source", "monitor", "timeline", "kpi-history"]:
        result = runner.invoke(cli_app, ["ir", command, "--help"])
        assert result.exit_code == 0, result.output
        assert result.exception is None


def test_api_lists_and_registers_sources(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_ir_monitor_service] = lambda: _StubService(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.get("/v1/ir/sources")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert [source["source_id"] for source in payload["sources"]] == ["example-ir"]

            new_source = {
                "source_id": "second-ir",
                "symbol": "5678.T",
                "provider": "ir.example.co.jp",
                "listing_url": "https://ir.example.co.jp/library/summary.html",
            }
            created = client.post("/v1/ir/sources", json=new_source)
            assert created.status_code == 201, created.text
            assert created.json()["source_id"] == "second-ir"
            assert len(client.get("/v1/ir/sources").json()["sources"]) == 2
    finally:
        app.dependency_overrides.pop(get_ir_monitor_service, None)


def test_api_unknown_source_is_404(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_ir_monitor_service] = lambda: _StubService(tmp_path)
    try:
        with TestClient(app) as client:
            assert client.get("/v1/ir/sources/nope").status_code == 404
            assert client.post("/v1/ir/sources/nope/monitor").status_code == 404
    finally:
        app.dependency_overrides.pop(get_ir_monitor_service, None)


def test_api_monitor_returns_outcome(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_ir_monitor_service] = lambda: _StubService(tmp_path)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/ir/sources/example-ir/monitor")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["source_id"] == "example-ir"
            assert body["new_count"] == 1
    finally:
        app.dependency_overrides.pop(get_ir_monitor_service, None)


def test_api_timeline_and_kpi_history_empty_are_ok(monkeypatch: Any, tmp_path: Any) -> None:
    _personal_env(monkeypatch, tmp_path)
    from yowayowa.api.app import app

    app.dependency_overrides[get_ir_monitor_service] = lambda: _StubService(tmp_path)
    try:
        with TestClient(app) as client:
            timeline = client.get("/v1/ir/instruments/1234.T/timeline")
            assert timeline.status_code == 200
            assert timeline.json() == {"symbol": "1234.T", "entries": []}
            history = client.get("/v1/ir/documents/kpi-history", params={"url": "https://a/1.pdf"})
            assert history.status_code == 200
            assert history.json()["entries"] == []
    finally:
        app.dependency_overrides.pop(get_ir_monitor_service, None)


def test_ir_surface_fails_closed_in_public_mode(monkeypatch: Any, tmp_path: Any) -> None:
    """Public mode must not expose the private IR acquisition surface."""
    monkeypatch.setenv("YOWAYOWA_MODE", "public")
    monkeypatch.setenv("YOWAYOWA_API_TOKEN", "token")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_CONNECTORS_ENABLED", "false")
    monkeypatch.setenv("YOWAYOWA_PRIVATE_ACQUISITION_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    get_ir_monitor_service.cache_clear()
    from yowayowa.api.app import app

    with TestClient(app) as client:
        response = client.get("/v1/ir/sources", headers={"Authorization": "Bearer token"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Private acquisition is disabled"
