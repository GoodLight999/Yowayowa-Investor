from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from yowayowa.config import Settings, get_settings
from yowayowa.db import Base, WatchlistRecord, dispose_database, init_database


def _configure_personal_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("YOWAYOWA_MODE", "personal")
    monkeypatch.setenv("YOWAYOWA_DATABASE_URL", f"sqlite:///{tmp_path / 'hypotheses.db'}")
    monkeypatch.delenv("YOWAYOWA_API_TOKEN", raising=False)
    get_settings.cache_clear()


def test_hypothesis_api_persists_criteria_and_creation_provenance(tmp_path, monkeypatch) -> None:
    _configure_personal_api(tmp_path, monkeypatch)
    from yowayowa.api.app import app

    payload = {
        "hypothesis": "Company X can sustain its margin because recurring revenue is growing.",
        "symbol": "XXXX",
        "falsification_criteria": [
            "If the next annual filing reports recurring revenue below the prior year, "
            "reject this thesis."
        ],
        "evidence_links": [
            {
                "source_url": "https://example.com/filings/2026-annual",
                "provider": "example-filing-source",
                "source": "2026 annual filing",
                "retrieved_at": "2026-09-28T11:30:00Z",
                "as_of": "2026-03-31",
            }
        ],
    }

    with TestClient(app) as client:
        created = client.post("/v1/hypotheses", json=payload)
        assert created.status_code == 201
        record = created.json()
        assert record["hypothesis"] == payload["hypothesis"]
        assert record["falsification_criteria"] == payload["falsification_criteria"]
        assert record["symbol"] == "XXXX"
        assert record["provenance"]["evidence_links"] == payload["evidence_links"]
        created_at_value = record["provenance"]["created_at"].replace("Z", "+00:00")
        created_at = datetime.fromisoformat(created_at_value)
        assert created_at.tzinfo is not None
        assert created_at.utcoffset() == UTC.utcoffset(created_at)

        listed = client.get("/v1/hypotheses", params={"symbol": "XXXX"})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [record["id"]]

        fetched = client.get(f"/v1/hypotheses/{record['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == record
        assert client.get("/v1/hypotheses/99999").status_code == 404
        assert client.put(f"/v1/hypotheses/{record['id']}", json=payload).status_code == 405
        assert client.delete(f"/v1/hypotheses/{record['id']}").status_code == 405


def test_hypothesis_api_rejects_missing_thesis_criteria_or_evidence(tmp_path, monkeypatch) -> None:
    _configure_personal_api(tmp_path, monkeypatch)
    from yowayowa.api.app import app

    complete = {
        "hypothesis": "A testable statement.",
        "falsification_criteria": ["A measurable condition occurs."],
        "evidence_links": [{"source_url": "https://example.com/source"}],
    }
    invalid_payloads = [
        {key: value for key, value in complete.items() if key != "hypothesis"},
        {key: value for key, value in complete.items() if key != "falsification_criteria"},
        {key: value for key, value in complete.items() if key != "evidence_links"},
        {**complete, "hypothesis": "   "},
        {**complete, "falsification_criteria": ["  "]},
        {**complete, "evidence_links": []},
    ]

    with TestClient(app) as client:
        for payload in invalid_payloads:
            assert client.post("/v1/hypotheses", json=payload).status_code == 422
        assert client.get("/v1/hypotheses").json() == []


def test_additive_hypothesis_migration_preserves_existing_watchlist_data(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path}"
    legacy_engine = create_engine(database_url)
    try:
        # Model the pre-migration installation: existing tables only.
        Base.metadata.create_all(legacy_engine, tables=[WatchlistRecord.__table__])
        with Session(legacy_engine) as session:
            session.add(
                WatchlistRecord(
                    name="legacy watchlist",
                    created_at=datetime(2026, 9, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 9, 2, tzinfo=UTC),
                )
            )
            session.commit()
    finally:
        legacy_engine.dispose()

    init_database(Settings(database_url=database_url))
    migrated_engine = create_engine(database_url)
    try:
        assert "investment_hypotheses" in inspect(migrated_engine).get_table_names()
        with Session(migrated_engine) as session:
            rows = session.query(WatchlistRecord).all()
        assert len(rows) == 1
        assert rows[0].name == "legacy watchlist"
        assert rows[0].created_at.isoformat() == "2026-09-01T00:00:00"
        assert rows[0].updated_at.isoformat() == "2026-09-02T00:00:00"
    finally:
        migrated_engine.dispose()
        dispose_database()
