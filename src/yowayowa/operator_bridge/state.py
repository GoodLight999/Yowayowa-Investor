from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

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
                CREATE TABLE IF NOT EXISTS order_reservations (
                    client_order_id TEXT PRIMARY KEY,
                    intent_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    receipt_json TEXT,
                    reservation_day TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_order_reservations_created_at
                    ON order_reservations(created_at);
                CREATE INDEX IF NOT EXISTS idx_order_reservations_reservation_day
                    ON order_reservations(reservation_day);
                CREATE INDEX IF NOT EXISTS idx_broker_audit_created_at
                    ON broker_audit(created_at);
                CREATE INDEX IF NOT EXISTS idx_broker_audit_event_type
                    ON broker_audit(event_type);
                """
            )
            cols = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(order_reservations)").fetchall()
            }
            if "reservation_day" not in cols:
                connection.execute("ALTER TABLE order_reservations ADD COLUMN reservation_day TEXT")
                connection.execute(
                    "UPDATE order_reservations SET reservation_day = substr(created_at, 1, 10) "
                    "WHERE reservation_day IS NULL"
                )
            self._migrate_legacy_audit(connection)

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
        payload: dict[str, object] | None = None,
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

    def latest_order_result(self, client_order_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM broker_audit
                WHERE event_type IN ('order_submit_result', 'order_cancel_result')
                  AND client_order_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (client_order_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None

    def _migrate_legacy_audit(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT client_order_id, created_at, event_type, broker_order_id, payload_json
            FROM broker_audit
            WHERE client_order_id IS NOT NULL
            ORDER BY id ASC
            """
        ).fetchall()
        if not rows:
            return

        grouped: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            cid = r["client_order_id"]
            if cid:
                grouped.setdefault(cid, []).append(r)

        existing_cids = {
            row["client_order_id"]
            for row in connection.execute(
                "SELECT client_order_id FROM order_reservations"
            ).fetchall()
        }

        now = datetime.now(UTC).isoformat()
        for cid, events in grouped.items():
            if cid in existing_cids:
                continue
            attempt = next((e for e in events if e["event_type"] == "order_submit_attempt"), None)
            result = next(
                (
                    e
                    for e in reversed(events)
                    if e["event_type"] in ("order_submit_result", "order_cancel_result")
                ),
                None,
            )

            intent_hash = "LEGACY_UNKNOWN_HASH"
            created_at = events[0]["created_at"] or now
            updated_at = events[-1]["created_at"] or now
            receipt_json = None

            if attempt is not None:
                try:
                    payload = json.loads(attempt["payload_json"])
                    if (
                        isinstance(payload, dict)
                        and "intent" in payload
                        and isinstance(payload["intent"], dict)
                    ):
                        intent_data = dict(payload["intent"])
                        intent_data.pop("reason", None)
                        serialized = json.dumps(
                            intent_data, sort_keys=True, ensure_ascii=False
                        ).encode("utf-8")
                        intent_hash = hashlib.sha256(serialized).hexdigest()
                except Exception:
                    pass

            if result is not None:
                status = "COMPLETED"
                receipt_json = result["payload_json"]
            else:
                status = "UNKNOWN"

            res_day = created_at[:10] if created_at else now[:10]
            connection.execute(
                """
                INSERT OR IGNORE INTO order_reservations(
                    client_order_id, intent_hash, status, created_at,
                    updated_at, receipt_json, reservation_day
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (cid, intent_hash, status, created_at, updated_at, receipt_json, res_day),
            )

    @staticmethod
    def _query_daily_count(connection: sqlite3.Connection, today: str) -> int:
        count_row = connection.execute(
            """
            SELECT (
                (SELECT COUNT(*) FROM broker_audit
                 WHERE event_type = 'order_submit_attempt'
                   AND substr(created_at, 1, 10) = ?)
                +
                (SELECT COUNT(*) FROM order_reservations
                 WHERE COALESCE(reservation_day, substr(created_at, 1, 10)) = ?
                   AND status IN ('SUBMITTING', 'COMPLETED', 'UNKNOWN')
                   AND client_order_id NOT IN (
                       SELECT client_order_id FROM broker_audit
                       WHERE event_type = 'order_submit_attempt'
                         AND substr(created_at, 1, 10) = ?
                         AND client_order_id IS NOT NULL
                   ))
            ) AS count
            """,
            (today, today, today),
        ).fetchone()
        return int(count_row["count"]) if count_row else 0

    def reserve_order_submission(
        self,
        client_order_id: str,
        intent_hash: str,
        max_orders_per_day: int | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        if not client_order_id:
            raise ValueError("client_order_id is required")
        now = datetime.now(UTC).isoformat()
        today = datetime.now(UTC).date().isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT intent_hash, status, receipt_json
                FROM order_reservations
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            if row is not None:
                saved_hash = str(row["intent_hash"])
                saved_status = str(row["status"])
                if saved_hash != intent_hash:
                    connection.commit()
                    return ("MISMATCH", None)
                if saved_status == "COMPLETED" and row["receipt_json"]:
                    connection.commit()
                    payload = json.loads(row["receipt_json"])
                    return ("ALREADY_COMPLETED", payload if isinstance(payload, dict) else None)
                if saved_status in ("RESERVED", "SUBMITTING", "UNKNOWN"):
                    connection.commit()
                    return ("UNCERTAIN_OR_IN_FLIGHT", None)
                if saved_status == "REJECTED":
                    current_count = self._query_daily_count(connection, today)
                    if max_orders_per_day is not None and current_count >= max_orders_per_day:
                        connection.commit()
                        return ("DAILY_LIMIT_EXCEEDED", None)
                    cursor = connection.execute(
                        """
                        UPDATE order_reservations
                        SET status = 'SUBMITTING', updated_at = ?, reservation_day = ?
                        WHERE client_order_id = ? AND status = 'REJECTED'
                        """,
                        (now, today, client_order_id),
                    )
                    if cursor.rowcount != 1:
                        connection.commit()
                        return ("UNCERTAIN_OR_IN_FLIGHT", None)
                    connection.commit()
                    return ("RESERVED", None)
                connection.commit()
                return ("UNCERTAIN_OR_IN_FLIGHT", None)

            current_count = self._query_daily_count(connection, today)
            if max_orders_per_day is not None and current_count >= max_orders_per_day:
                connection.commit()
                return ("DAILY_LIMIT_EXCEEDED", None)

            connection.execute(
                """
                INSERT INTO order_reservations(
                    client_order_id, intent_hash, status, created_at, updated_at, reservation_day
                )
                VALUES (?, ?, 'SUBMITTING', ?, ?, ?)
                """,
                (client_order_id, intent_hash, now, now, today),
            )
            connection.commit()
            return ("RESERVED", None)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_submission_success(self, client_order_id: str, receipt: dict[str, object]) -> None:
        now = datetime.now(UTC).isoformat()
        serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE order_reservations
                SET status = 'COMPLETED', receipt_json = ?, updated_at = ?
                WHERE client_order_id = ?
                """,
                (serialized, now, client_order_id),
            )

    def record_submission_failure(self, client_order_id: str, status: str = "UNKNOWN") -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE order_reservations
                SET status = ?, updated_at = ?
                WHERE client_order_id = ?
                """,
                (status, now, client_order_id),
            )

    def count_submission_attempts_today(self) -> int:
        today = datetime.now(UTC).date().isoformat()
        with self._connect() as connection:
            return self._query_daily_count(connection, today)

    def audit_events(self) -> list[dict[str, object]]:
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
