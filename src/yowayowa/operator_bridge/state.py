from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from yowayowa.services.broker_execution import BrokerExecutionBlocked

_MAX_RSS_ORDER_ID = 2_147_483_647


class BrokerDispatchBlocked(BrokerExecutionBlocked):
    """Pre-transport admission refusal, distinct from unknown broker outcome."""


class SQLiteOperatorState:
    """Restart-safe local state for broker execution.

    This database is intentionally separate from the hosted product database.
    It stores only execution identifiers and redacted/auditable order metadata,
    never broker passwords or authenticated session secrets.
    """

    def __init__(
        self,
        path: str | Path,
        max_orders_per_day: int | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.max_orders_per_day = max_orders_per_day
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
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
                    reservation_day TEXT,
                    dispatch_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_order_reservations_created_at
                    ON order_reservations(created_at);
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
            if "dispatch_at" not in cols:
                connection.execute("ALTER TABLE order_reservations ADD COLUMN dispatch_at TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_reservations_reservation_day "
                "ON order_reservations(reservation_day)"
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
        max_orders_per_day: int | None = None,
    ) -> None:
        if max_orders_per_day is not None:
            self.max_orders_per_day = max_orders_per_day
        limit = (
            max_orders_per_day
            if max_orders_per_day is not None
            else getattr(self, "max_orders_per_day", None)
        )
        serialized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_dt = datetime.now(UTC)
            created_at = now_dt.isoformat()
            attempt_day = created_at[:10]
            if event_type == "order_submit_attempt":
                if not client_order_id:
                    raise BrokerExecutionBlocked(
                        "client_order_id is required for order_submit_attempt"
                    )
                row = connection.execute(
                    """
                    SELECT status, reservation_day, dispatch_at
                    FROM order_reservations
                    WHERE client_order_id = ?
                    """,
                    (client_order_id,),
                ).fetchone()
                if row is None or row["status"] != "SUBMITTING" or row["dispatch_at"] is not None:
                    raise BrokerExecutionBlocked(
                        "Attempt requires an unused SUBMITTING reservation"
                    )
                res_day = row["reservation_day"]
                current_count = self._query_daily_count(connection, attempt_day)
                if res_day != attempt_day:
                    if limit is not None and current_count >= limit:
                        raise BrokerExecutionBlocked("Daily order count limit reached")
                    cursor = connection.execute(
                        """
                        UPDATE order_reservations
                        SET reservation_day = ?, updated_at = ?
                        WHERE client_order_id = ? AND status = 'SUBMITTING'
                        """,
                        (attempt_day, created_at, client_order_id),
                    )
                    if cursor.rowcount != 1:
                        raise BrokerExecutionBlocked("Reservation day migration failed")
                elif limit is not None and current_count > limit:
                    raise BrokerExecutionBlocked("Daily order count limit reached")
                prior_attempt = connection.execute(
                    "SELECT 1 FROM broker_audit WHERE event_type = 'order_submit_attempt' "
                    "AND client_order_id = ? AND substr(created_at, 1, 10) = ? LIMIT 1",
                    (client_order_id, attempt_day),
                ).fetchone()
                if prior_attempt and limit is not None and current_count >= limit:
                    raise BrokerExecutionBlocked("Daily order count limit reached")
                if self.current_day != attempt_day:
                    raise BrokerExecutionBlocked("Execution day changed during attempt admission")

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
            connection.commit()

    def import_legacy_submit_attempt(
        self,
        *,
        created_at: datetime,
        client_order_id: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Explicit offline backfill, never live submission authorization.

        Imported attempts are migrated to UNKNOWN reservations, so they cannot
        be used to dispatch or replay. Existing live reservation IDs are rejected.
        """
        if not client_order_id or created_at.tzinfo is None:
            raise ValueError("Legacy import requires an ID and timezone-aware timestamp")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM order_reservations WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone():
                raise BrokerExecutionBlocked("Legacy import cannot replace a live reservation")
            connection.execute(
                "INSERT INTO broker_audit(created_at, event_type, client_order_id, payload_json) "
                "VALUES (?, 'order_submit_attempt', ?, ?)",
                (
                    created_at.astimezone(UTC).isoformat(),
                    client_order_id,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._migrate_legacy_audit(connection)

    def dispatch_submission(
        self,
        client_order_id: str,
        invoke: Callable[[], object],
        *,
        max_orders_per_day: int | None = None,
    ) -> object:
        """Admit the prepared transport at the last local invocation boundary.

        No day migration is permitted after an attempt is committed. Claim a
        durable single-use handoff under the writer lock, release it before broker
        IO, then recheck the clock immediately before invocation. Broker timeout
        remains UNKNOWN; a committed attempt never rolls back on transport failure.
        """
        limit = max_orders_per_day if max_orders_per_day is not None else self.max_orders_per_day
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            today = self.current_day
            row = connection.execute(
                "SELECT status, reservation_day, dispatch_at FROM order_reservations "
                "WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is None or row["status"] != "SUBMITTING" or row["reservation_day"] != today:
                raise BrokerDispatchBlocked("No current-day SUBMITTING reservation at dispatch")
            if row["dispatch_at"] is not None:
                raise BrokerDispatchBlocked("Transport handoff already claimed")
            attempt = connection.execute(
                "SELECT created_at FROM broker_audit "
                "WHERE event_type = 'order_submit_attempt' AND client_order_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (client_order_id,),
            ).fetchone()
            if attempt is None or attempt["created_at"][:10] != today:
                raise BrokerDispatchBlocked("No current-day attempt at dispatch")
            if limit is not None and self._query_daily_count(connection, today) > limit:
                raise BrokerDispatchBlocked("Daily order count limit reached at dispatch")
            if self.current_day != today:
                raise BrokerDispatchBlocked("Execution day changed during dispatch admission")
            cursor = connection.execute(
                "UPDATE order_reservations SET dispatch_at = ? "
                "WHERE client_order_id = ? AND status = 'SUBMITTING' AND dispatch_at IS NULL",
                (datetime.now(UTC).isoformat(), client_order_id),
            )
            if cursor.rowcount != 1:
                raise BrokerDispatchBlocked("Transport handoff claim failed")
            connection.commit()
        if self.current_day != today:
            raise BrokerDispatchBlocked("Execution day changed before transport invocation")
        return invoke()

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
        if max_orders_per_day is not None:
            self.max_orders_per_day = max_orders_per_day
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC).isoformat()
            today = now[:10]
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
                        SET status = 'SUBMITTING', updated_at = ?,
                            reservation_day = ?, dispatch_at = NULL
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

    @property
    def current_day(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def count_submission_attempts_today(self) -> int:
        today = self.current_day
        with self._connect() as connection:
            return self._query_daily_count(connection, today)

    def has_today_reservation(self, client_order_id: str) -> bool:
        today = self.current_day
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM order_reservations
                WHERE client_order_id = ?
                  AND COALESCE(reservation_day, substr(created_at, 1, 10)) = ?
                """,
                (client_order_id, today),
            ).fetchone()
            return row is not None

    def update_reservation_day(
        self,
        client_order_id: str,
        day: str,
        max_orders_per_day: int | None = None,
    ) -> bool:
        if max_orders_per_day is not None:
            self.max_orders_per_day = max_orders_per_day
        limit = (
            max_orders_per_day
            if max_orders_per_day is not None
            else getattr(self, "max_orders_per_day", None)
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(UTC).isoformat()
            row = connection.execute(
                """
                SELECT status, reservation_day
                FROM order_reservations
                WHERE client_order_id = ?
                """,
                (client_order_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                raise BrokerExecutionBlocked(f"Order reservation not found: {client_order_id}")
            if row["status"] != "SUBMITTING":
                connection.commit()
                raise BrokerExecutionBlocked(
                    f"Order reservation status is not SUBMITTING: {row['status']}"
                )

            current_reservation_day = row["reservation_day"]
            if current_reservation_day == day:
                connection.commit()
                return True

            current_count = self._query_daily_count(connection, day)
            if limit is not None and current_count >= limit:
                connection.commit()
                raise BrokerExecutionBlocked("Daily order count limit reached")

            cursor = connection.execute(
                """
                UPDATE order_reservations
                SET reservation_day = ?, updated_at = ?
                WHERE client_order_id = ? AND status = 'SUBMITTING'
                """,
                (day, now, client_order_id),
            )
            if cursor.rowcount != 1:
                connection.commit()
                raise BrokerExecutionBlocked(
                    "Concurrent update conflict during reservation day migration"
                )
            connection.commit()
            return True

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
