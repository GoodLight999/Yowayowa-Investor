"""P5 R3: a portfolio created by mistake must be deletable, but fail closed.

Covered boundaries (real status codes, real row counts):

* unknown id -> 404 / ``LookupError``;
* holdings still present -> 409 by default, and the portfolio survives;
* explicit ``force`` -> holdings, snapshots and the broker-application ledger
  are all gone, and the delete reports how many rows of each kind went away;
* the second delete of the same id -> 404 (idempotent-safe, not silent);
* another portfolio (different base currency) is untouched.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from typer.testing import CliRunner

from yowayowa.cli import app as cli_app
from yowayowa.config import get_settings
from yowayowa.db import (
    Base,
    BrokerExecutionApplicationRecord,
    PortfolioRecord,
    PortfolioSnapshotRecord,
    PositionRecord,
    get_session,
)
from yowayowa.domain import PositionBulkUpsert, PositionUpsert
from yowayowa.services.portfolios import (
    PortfolioNotEmptyError,
    bulk_upsert_positions,
    create_portfolio,
    delete_portfolio,
    record_portfolio_snapshot,
)

_NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# service level (in-memory engine)
# ---------------------------------------------------------------------------
def _session() -> tuple[Any, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine, expire_on_commit=False)


def _add_snapshot(session: Session, portfolio_id: int) -> None:
    session.add(
        PortfolioSnapshotRecord(
            portfolio_id=portfolio_id,
            net_market_value=Decimal("1000"),
            gross_market_value=Decimal("1000"),
            unrealized_pnl=Decimal("0"),
            day_pnl=Decimal("0"),
            captured_at=_NOW,
        )
    )
    session.commit()


def _add_applied_execution(session: Session, portfolio_id: int) -> None:
    session.add(
        BrokerExecutionApplicationRecord(
            portfolio_id=portfolio_id,
            market="US",
            execution_id=f"exec-{portfolio_id}",
            execution_fingerprint="f" * 64,
            preview_id="preview-1",
            applied_at=_NOW,
        )
    )
    session.commit()


def _count(session: Session, model: Any, portfolio_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(model.portfolio_id == portfolio_id)
        )
        or 0
    )


def test_delete_unknown_portfolio_raises_lookup_error() -> None:
    engine, session = _session()
    try:
        with pytest.raises(LookupError):
            delete_portfolio(session, 424242)
    finally:
        session.close()
        engine.dispose()


def test_delete_refuses_while_holdings_remain() -> None:
    engine, session = _session()
    try:
        portfolio = create_portfolio(session, "Mistake", "JPY")
        bulk_upsert_positions(
            session,
            portfolio.id,
            PositionBulkUpsert(positions=[PositionUpsert(symbol="7203", quantity="100")]),
        )
        with pytest.raises(PortfolioNotEmptyError) as excinfo:
            delete_portfolio(session, portfolio.id)
        assert excinfo.value.counts == {
            "positions": 1,
            "snapshots": 0,
            "applied_executions": 0,
        }
        assert "positions=1" in str(excinfo.value)
        # Nothing was removed: a refused delete must not be a partial delete.
        assert _count(session, PositionRecord, portfolio.id) == 1
        assert session.get(PortfolioRecord, portfolio.id) is not None
    finally:
        session.close()
        engine.dispose()


def test_delete_refuses_when_only_snapshots_remain() -> None:
    """Historical snapshots are financial history too, so they also block."""
    engine, session = _session()
    try:
        portfolio = create_portfolio(session, "Snapshot Only", "USD")
        _add_snapshot(session, portfolio.id)
        with pytest.raises(PortfolioNotEmptyError) as excinfo:
            delete_portfolio(session, portfolio.id)
        assert excinfo.value.counts["snapshots"] == 1
        assert session.get(PortfolioRecord, portfolio.id) is not None
    finally:
        session.close()
        engine.dispose()


def test_force_delete_cascades_positions_snapshots_and_ledger() -> None:
    engine, session = _session()
    try:
        portfolio = create_portfolio(session, "Mistake", "USD")
        bulk_upsert_positions(
            session,
            portfolio.id,
            PositionBulkUpsert(
                positions=[
                    PositionUpsert(symbol="RKLB", quantity="10", currency="USD"),
                    PositionUpsert(symbol="7203", quantity="100", currency="JPY"),
                ]
            ),
        )
        _add_snapshot(session, portfolio.id)
        _add_applied_execution(session, portfolio.id)

        deletion = delete_portfolio(session, portfolio.id, force=True)

        assert deletion.portfolio_id == portfolio.id
        assert deletion.name == "Mistake"
        assert deletion.force is True
        assert deletion.deleted_positions == 2
        assert deletion.deleted_snapshots == 1
        assert deletion.deleted_applied_executions == 1
        assert session.get(PortfolioRecord, portfolio.id) is None
        assert _count(session, PositionRecord, portfolio.id) == 0
        assert _count(session, PortfolioSnapshotRecord, portfolio.id) == 0
        assert _count(session, BrokerExecutionApplicationRecord, portfolio.id) == 0

        with pytest.raises(LookupError):
            delete_portfolio(session, portfolio.id, force=True)
    finally:
        session.close()
        engine.dispose()


def test_delete_without_dependents_needs_no_force_and_spares_other_portfolios() -> None:
    engine, session = _session()
    try:
        doomed = create_portfolio(session, "Doomed", "JPY")
        keeper = create_portfolio(session, "Keeper", "USD")
        bulk_upsert_positions(
            session,
            keeper.id,
            PositionBulkUpsert(positions=[PositionUpsert(symbol="AAPL", quantity="5")]),
        )
        _add_snapshot(session, keeper.id)
        _add_applied_execution(session, keeper.id)

        deletion = delete_portfolio(session, doomed.id)

        assert deletion.force is False
        assert deletion.deleted_positions == 0
        assert session.get(PortfolioRecord, doomed.id) is None
        kept = session.get(PortfolioRecord, keeper.id)
        assert kept is not None
        assert kept.base_currency == "USD"
        assert _count(session, PositionRecord, keeper.id) == 1
        assert _count(session, PortfolioSnapshotRecord, keeper.id) == 1
        assert _count(session, BrokerExecutionApplicationRecord, keeper.id) == 1
    finally:
        session.close()
        engine.dispose()


def test_snapshot_rows_are_removed_with_the_portfolio_not_orphaned() -> None:
    engine, session = _session()
    try:
        portfolio = create_portfolio(session, "With History", "USD")
        record_portfolio_snapshot(
            session,
            _analytics_for(portfolio.id),
        )
        assert _count(session, PortfolioSnapshotRecord, portfolio.id) == 1
        delete_portfolio(session, portfolio.id, force=True)
        assert session.scalar(select(func.count()).select_from(PortfolioSnapshotRecord)) == 0
    finally:
        session.close()
        engine.dispose()


def _analytics_for(portfolio_id: int) -> Any:
    from yowayowa.domain import LicenseClass, PortfolioAnalytics, Provenance

    return PortfolioAnalytics(
        portfolio_id=portfolio_id,
        name="With History",
        base_currency="USD",
        net_market_value=1000.0,
        gross_market_value=1000.0,
        known_cost_basis=900.0,
        known_cost_market_value=1000.0,
        unrealized_pnl=100.0,
        unrealized_pnl_pct=0.111,
        day_pnl=0.0,
        day_change_pct=0.0,
        largest_position_weight=1.0,
        concentration_hhi=1.0,
        positions=[],
        currency_exposure=[],
        unavailable_symbols=[],
        provenance=Provenance(
            provider="fixture",
            source="fixture://portfolio-delete",
            license_class=LicenseClass.OFFICIAL_PUBLIC,
            retrieved_at=_NOW,
            as_of=_NOW,
        ),
        evaluated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# API level (real HTTP status codes and real DB rows)
# ---------------------------------------------------------------------------
def _personal_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()


def test_api_delete_is_404_then_409_then_cascades_with_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _personal_mode(monkeypatch)

    from yowayowa.api.app import app

    with TestClient(app) as client:
        doomed = client.post("/v1/portfolios", json={"name": "Mistake", "base_currency": "JPY"})
        assert doomed.status_code == 201
        doomed_id = doomed.json()["id"]
        keeper = client.post("/v1/portfolios", json={"name": "Core", "base_currency": "USD"})
        keeper_id = keeper.json()["id"]

        post = client.put(
            f"/v1/portfolios/{doomed_id}/positions",
            json={"symbol": "7203", "quantity": "100", "currency": "JPY"},
        )
        assert post.status_code == 200
        client.put(
            f"/v1/portfolios/{keeper_id}/positions",
            json={"symbol": "AAPL", "quantity": "5", "currency": "USD"},
        )

        with get_session() as session:
            _add_snapshot(session, doomed_id)
            _add_applied_execution(session, doomed_id)
            _add_snapshot(session, keeper_id)
            _add_applied_execution(session, keeper_id)

        missing = client.delete("/v1/portfolios/999999")
        assert missing.status_code == 404
        assert "not found" in missing.json()["detail"]

        conflict = client.delete(f"/v1/portfolios/{doomed_id}")
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()["detail"]
        assert "positions=1" in detail and "snapshots=1" in detail and "force=true" in detail
        # A 409 changed nothing.
        assert client.get(f"/v1/portfolios/{doomed_id}").status_code == 200

        deleted = client.delete(f"/v1/portfolios/{doomed_id}", params={"force": "true"})
        assert deleted.status_code == 200, deleted.text
        payload = deleted.json()
        assert payload == {
            "portfolio_id": doomed_id,
            "name": "Mistake",
            "force": True,
            "deleted_positions": 1,
            "deleted_snapshots": 1,
            "deleted_applied_executions": 1,
        }
        assert client.get(f"/v1/portfolios/{doomed_id}").status_code == 404
        assert client.get(f"/v1/portfolios/{doomed_id}/snapshots").status_code == 404

        again = client.delete(f"/v1/portfolios/{doomed_id}", params={"force": "true"})
        assert again.status_code == 404

        with get_session() as session:
            for model in (
                PositionRecord,
                PortfolioSnapshotRecord,
                BrokerExecutionApplicationRecord,
            ):
                assert _count(session, model, doomed_id) == 0
            assert _count(session, PositionRecord, keeper_id) == 1
            assert _count(session, PortfolioSnapshotRecord, keeper_id) == 1
            assert _count(session, BrokerExecutionApplicationRecord, keeper_id) == 1
            keeper_row = session.get(PortfolioRecord, keeper_id)
            assert keeper_row is not None and keeper_row.base_currency == "USD"

        kept = client.get(f"/v1/portfolios/{keeper_id}")
        assert kept.status_code == 200
        assert [item["symbol"] for item in kept.json()["positions"]] == ["AAPL"]


def test_api_delete_allows_an_empty_mistaken_portfolio_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _personal_mode(monkeypatch)

    from yowayowa.api.app import app

    with TestClient(app) as client:
        created = client.post("/v1/portfolios", json={"name": "Typo", "base_currency": "USD"})
        portfolio_id = created.json()["id"]
        deleted = client.delete(f"/v1/portfolios/{portfolio_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["force"] is False
        assert client.get("/v1/portfolios").json() == []


# ---------------------------------------------------------------------------
# CLI (`portfolio delete <id> [--force] [--yes]`)
# ---------------------------------------------------------------------------
runner = CliRunner()


class _StubResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)


def _stub_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int = 200,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class _Client:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def delete(self, path: str, params: dict[str, Any] | None = None) -> _StubResponse:
            calls.append({"method": "DELETE", "path": path, "params": params})
            if status_code == 200:
                return _StubResponse(
                    {
                        "portfolio_id": 7,
                        "name": "Mistake",
                        "force": bool(params and params["force"]),
                        "deleted_positions": 2,
                        "deleted_snapshots": 1,
                        "deleted_applied_executions": 0,
                    }
                )
            return _StubResponse(
                {"detail": "Portfolio 7 still owns dependent rows (positions=2)"},
                status_code=status_code,
            )

    import yowayowa.cli as cli_module

    monkeypatch.setattr(cli_module, "_client", lambda base_url, token: _Client())
    return calls


def test_cli_delete_without_yes_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch)

    result = runner.invoke(cli_app, ["portfolio", "delete", "7"])

    assert result.exit_code == 1
    assert calls == []
    assert "without --yes" in result.output


def test_cli_delete_with_yes_calls_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch)

    result = runner.invoke(cli_app, ["portfolio", "delete", "7", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == [{"method": "DELETE", "path": "/v1/portfolios/7", "params": {"force": False}}]
    assert "Deleted portfolio 7" in result.output
    assert "positions 2" in result.output
    assert "snapshots 1" in result.output


def test_cli_delete_force_flag_and_409_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_client(monkeypatch)
    ok = runner.invoke(cli_app, ["portfolio", "delete", "7", "--yes", "--force"])
    assert ok.exit_code == 0, ok.output
    assert calls[0]["params"] == {"force": True}
    assert "force True" in ok.output

    calls = _stub_client(monkeypatch, status_code=409)
    refused = runner.invoke(cli_app, ["portfolio", "delete", "7", "--yes"])
    assert refused.exit_code == 1
    assert "Delete failed (409)" in refused.output
    assert "still owns dependent rows" in refused.output


def test_cli_portfolio_delete_help_exits_zero() -> None:
    # 幅を明示する: Rich の help は端末幅依存で、狭い幅ではオプション名が
    # ellipsis に畳まれて "--force" が消える (CI の verify で実際 1 件落ちた)。
    # Rich は COLUMNS を読むので、端末幅の無いランナーでもこれで固定できる。
    result = runner.invoke(
        cli_app, ["portfolio", "delete", "--help"], env={"COLUMNS": "200", "TERM": "dumb"}
    )
    assert result.exit_code == 0, result.output
    assert "--force" in result.output
    assert "--yes" in result.output
