from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_MAX_RSS_ORDER_ID = 2_147_483_647


class SQLiteOperatorState:
    """Restart-safe local state for broker execution.

    This database is intentionally separate from the hosted product database.
    It stores only execution identifiers and redacted/auditable order metadata,
    never broker passwords or authenticated session secrets.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rss_order_ids (
                    client_order_id TEXT PRIMARY KEY,
                    rss_order_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broker_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    client_order_id TEXT,
                    broker_order_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_broker_audit_created_at
                    ON broker_audit(created_at);
                CREATE INDEX IF NOT EXISTS idx_broker_audit_event_type
                    ON broker_audit(event_type);
                """
            )

    def allocate_rss_order_id(self, client_order_id: str) -> int:
        if not client_order_id:
            raise ValueError("client_order_id is required")
        now = datetime.now(UTC).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT rss_order_id FROM rss_order_ids WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return int(existing["rss_order_id"])

            row = connection.execute(
                "SELECT COALESCE(MAX(rss_order_id), 0) AS max_id FROM rss_order_ids"
            ).fetchone()
            next_id = int(row["max_id"]) + 1
            if next_id > _MAX_RSS_ORDER_ID:
                raise RuntimeError("MARKET SPEED II RSS order ID space exhausted")
            connection.execute(
                """
                INSERT INTO rss_order_ids(client_order_id, rss_order_id, created_at)
                VALUES (?, ?, ?)
                """,
                (client_order_id, next_id, now),
            )
            connection.commit()
            return next_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def append_audit(
        self,
        event_type: str,
        *,
        client_order_id: str | None,
        broker_order_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO broker_audit(
                    created_at,
                    event_type,
                    client_order_id,
                    broker_order_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    event_type,
                    client_order_id,
                    broker_order_id,
                    serialized,
                ),
            )

    def count_submission_attempts_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM broker_audit
                WHERE event_type = 'order_submit_attempt'
                  AND substr(created_at, 1, 10) = ?
                """,
                (today,),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    def audit_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, event_type, client_order_id, broker_order_id, payload_json
                FROM broker_audit
                ORDER BY id ASC
                """
            ).fetchall()
        return [
            {
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "client_order_id": row["client_order_id"],
                "broker_order_id": row["broker_order_id"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
