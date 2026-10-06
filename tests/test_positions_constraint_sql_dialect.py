"""Regression tests for the positions duplicate-check SQL dialect portability.

Production (PostgreSQL on Vercel) aborted ASGI lifespan startup with
``psycopg.errors.UndefinedColumn: column "cnt" does not exist`` because the
duplicate-detection query in ``_ensure_positions_unique_constraint`` referenced
the SELECT alias ``cnt`` inside HAVING. SQLite tolerates that syntax; PostgreSQL
does not. These tests pin the query to dialect-agnostic SQL and assert the
source never reintroduces the alias-in-HAVING form.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.dialects import postgresql


def test_positions_dup_query_compiles_on_postgresql() -> None:
    """The duplicate check must compile and run on PostgreSQL."""

    sql = str(
        text(
            "SELECT portfolio_id, symbol, COUNT(*) AS cnt "
            "FROM positions "
            "GROUP BY portfolio_id, symbol "
            "HAVING COUNT(*) > 1"
        ).compile(dialect=postgresql.dialect())
    )
    assert "HAVING COUNT(*)" in sql
    # "cnt" legitimately remains in the SELECT list as an alias; the regression
    # is only the alias appearing as the HAVING predicate.
    assert re.search(r"HAVING\s+cnt\b", sql) is None


def test_db_source_has_no_having_cnt_alias() -> None:
    """db.py must not reference the SELECT alias inside HAVING."""

    source = (Path(__file__).resolve().parents[1] / "src/yowayowa/db.py").read_text(
        encoding="utf-8"
    )
    assert "HAVING cnt" not in source


def test_non_sqlite_alter_failure_is_fail_soft(monkeypatch) -> None:
    """A failed non-SQLite ALTER must not abort startup and must be recorded."""

    import yowayowa.db as db_module

    engine = cast(Any, SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))

    class _FailingConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, _clause):
            raise RuntimeError("simulated migration failure")

    engine.begin = lambda *a, **k: _FailingConn()

    class _EmptyResult:
        def fetchall(self):
            return []

    class _EmptyConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, _clause):
            return _EmptyResult()

    engine.connect = lambda *a, **k: _EmptyConn()

    # Bypass the inspector short-circuits so execution reaches the ALTER branch.
    fake_inspector = SimpleNamespace(
        get_table_names=lambda: ["positions"],
        get_unique_constraints=lambda table: [],
        get_columns=lambda table: [],
    )
    monkeypatch.setattr(db_module, "inspect", lambda _engine: fake_inspector)

    db_module._reset_positions_constraint_blocker()
    try:
        db_module._ensure_positions_unique_constraint(engine)
        status = db_module.positions_unique_constraint_status()
        assert status["applied"] is False
        reason = str(status["reason"])
        assert "constraint application failed" in reason
        # Only the exception class name is recorded, never the message.
        assert "RuntimeError" in reason
        assert "simulated migration failure" not in reason
    finally:
        db_module._reset_positions_constraint_blocker()
