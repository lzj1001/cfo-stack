from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .errors import DuplicateOperation, NotFoundError, ValidationError


@dataclass(frozen=True)
class OperationRow:
    operation_key: str
    message_id: str
    operation_index: int
    status: str
    target_id: str | None
    receipt_id: str | None
    error_code: str | None
    retry_count: int
    audit_status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class QueueRow:
    queue_id: str
    operation_key: str
    action: str
    status: str
    available_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReminderJobRow:
    reminder_id: str
    task_id: str
    user_id: str
    status: str
    scheduled_at: str
    next_action_at: str
    follow_up_count: int
    max_follow_ups: int
    last_sent_at: str | None
    lease_until: str | None
    updated_at: str


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=5000")

    def migrate(self) -> None:
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
              version INTEGER NOT NULL
            );
            INSERT INTO schema_version(version)
            SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);

            CREATE TABLE IF NOT EXISTS operations (
              operation_key TEXT PRIMARY KEY,
              message_id TEXT NOT NULL,
              operation_index INTEGER NOT NULL,
              status TEXT NOT NULL,
              target_id TEXT,
              receipt_id TEXT,
              error_code TEXT,
              retry_count INTEGER NOT NULL DEFAULT 0,
              audit_status TEXT NOT NULL DEFAULT 'not_required',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(message_id, operation_index)
            );

            CREATE TABLE IF NOT EXISTS model_calls (
              call_id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              latency_ms INTEGER NOT NULL,
              input_tokens INTEGER,
              output_tokens INTEGER,
              result TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS queue (
              queue_id TEXT PRIMARY KEY,
              operation_key TEXT NOT NULL,
              action TEXT NOT NULL,
              status TEXT NOT NULL,
              available_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_cursors (
              name TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminder_jobs (
              reminder_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              status TEXT NOT NULL,
              scheduled_at TEXT NOT NULL,
              next_action_at TEXT NOT NULL,
              follow_up_count INTEGER NOT NULL,
              max_follow_ups INTEGER NOT NULL,
              last_sent_at TEXT,
              lease_until TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )

    def claim(self, key: str, message_id: str, operation_index: int) -> OperationRow:
        now = _now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                INSERT INTO operations(
                  operation_key, message_id, operation_index, status,
                  audit_status, created_at, updated_at
                ) VALUES (?, ?, ?, 'claimed', 'not_required', ?, ?)
                """,
                (key, message_id, operation_index, now, now),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self._connection.execute("ROLLBACK")
            raise DuplicateOperation(key) from error
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        return self.get_operation(key)

    def mark_business_succeeded(
        self, key: str, *, target_id: str, receipt_id: str
    ) -> OperationRow:
        cursor = self._connection.execute(
            """
            UPDATE operations
               SET status='business_succeeded', target_id=?, receipt_id=?,
                   error_code=NULL, audit_status='pending', updated_at=?
             WHERE operation_key=? AND status='claimed'
            """,
            (target_id, receipt_id, _now(), key),
        )
        if cursor.rowcount != 1:
            raise ValidationError("invalid transition to business_succeeded")
        return self.get_operation(key)

    def mark_failed(
        self,
        key: str,
        *,
        error_code: str,
        max_retries: int,
        target_id: str | None = None,
        receipt_id: str | None = None,
    ) -> OperationRow:
        current = self.get_operation(key)
        if current.status not in {"claimed", "failed"}:
            raise ValidationError("invalid transition to failed")
        if current.retry_count >= max_retries:
            raise ValidationError("retry limit reached")
        cursor = self._connection.execute(
            """
            UPDATE operations
               SET status='failed', error_code=?, retry_count=retry_count+1,
                   target_id=COALESCE(?, target_id),
                   receipt_id=COALESCE(?, receipt_id),
                   updated_at=?
             WHERE operation_key=? AND status IN ('claimed', 'failed')
               AND retry_count < ?
            """,
            (error_code, target_id, receipt_id, _now(), key, max_retries),
        )
        if cursor.rowcount != 1:
            raise ValidationError("invalid transition to failed")
        return self.get_operation(key)

    def recover_business_succeeded(
        self, key: str, *, target_id: str, receipt_id: str
    ) -> OperationRow:
        cursor = self._connection.execute(
            """
            UPDATE operations
               SET status='business_succeeded', target_id=?, receipt_id=?,
                   error_code=NULL, audit_status='pending', updated_at=?
             WHERE operation_key=? AND status='failed'
            """,
            (target_id, receipt_id, _now(), key),
        )
        if cursor.rowcount != 1:
            raise ValidationError("invalid recovery to business_succeeded")
        return self.get_operation(key)

    def mark_audit_completed(self, key: str) -> OperationRow:
        cursor = self._connection.execute(
            """
            UPDATE operations
               SET status='completed', audit_status='completed', updated_at=?
             WHERE operation_key=? AND status='business_succeeded'
               AND audit_status='pending'
            """,
            (_now(), key),
        )
        if cursor.rowcount != 1:
            raise ValidationError("invalid transition to completed")
        return self.get_operation(key)

    def get_operation(self, key: str) -> OperationRow:
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_key=?", (key,)
        ).fetchone()
        if row is None:
            raise NotFoundError(key)
        return OperationRow(**dict(row))

    def record_model_call(
        self,
        *,
        call_id: str,
        provider: str,
        model: str,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        result: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO model_calls(
              call_id, provider, model, latency_ms, input_tokens,
              output_tokens, result, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                provider,
                model,
                latency_ms,
                input_tokens,
                output_tokens,
                result,
                _now(),
            ),
        )

    def enqueue(
        self,
        *,
        queue_id: str,
        operation_key: str,
        action: str,
        available_at: str,
    ) -> None:
        now = _now()
        self._connection.execute(
            """
            INSERT INTO queue(
              queue_id, operation_key, action, status,
              available_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (queue_id, operation_key, action, available_at, now, now),
        )

    def next_due(self, now: str) -> QueueRow | None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT * FROM queue
                 WHERE status='pending' AND available_at <= ?
                 ORDER BY available_at, queue_id
                 LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            self._connection.execute(
                "UPDATE queue SET status='running', updated_at=? WHERE queue_id=?",
                (_now(), row["queue_id"]),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        claimed = self._connection.execute(
            "SELECT * FROM queue WHERE queue_id=?", (row["queue_id"],)
        ).fetchone()
        return QueueRow(**dict(claimed))

    def complete_queue(self, queue_id: str) -> None:
        cursor = self._connection.execute(
            """
            UPDATE queue SET status='completed', updated_at=?
             WHERE queue_id=? AND status='running'
            """,
            (_now(), queue_id),
        )
        if cursor.rowcount != 1:
            raise ValidationError("invalid queue transition")

    def set_cursor(self, name: str, value: str) -> None:
        self._connection.execute(
            """
            INSERT INTO sync_cursors(name, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (name, value, _now()),
        )

    def get_cursor(self, name: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM sync_cursors WHERE name=?", (name,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def schedule_reminder_job(
        self,
        *,
        reminder_id: str,
        task_id: str,
        user_id: str,
        status: str,
        scheduled_at: str,
        next_action_at: str,
        follow_up_count: int,
        max_follow_ups: int,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO reminder_jobs(
              reminder_id, task_id, user_id, status, scheduled_at, next_action_at,
              follow_up_count, max_follow_ups, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reminder_id) DO UPDATE SET
              task_id=excluded.task_id, user_id=excluded.user_id, status=excluded.status,
              scheduled_at=excluded.scheduled_at, next_action_at=excluded.next_action_at,
              follow_up_count=excluded.follow_up_count,
              max_follow_ups=excluded.max_follow_ups, updated_at=excluded.updated_at
            """,
            (
                reminder_id,
                task_id,
                user_id,
                status,
                scheduled_at,
                next_action_at,
                follow_up_count,
                max_follow_ups,
                _now(),
            ),
        )

    def claim_due_reminders(
        self, now: str, *, lease_until: str, limit: int = 10
    ) -> list[ReminderJobRow]:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            rows = self._connection.execute(
                """
                SELECT * FROM reminder_jobs
                 WHERE status IN ('SCHEDULED', 'SNOOZED')
                   AND next_action_at <= ?
                   AND (lease_until IS NULL OR lease_until <= ?)
                 ORDER BY next_action_at, reminder_id
                 LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    "UPDATE reminder_jobs SET lease_until=?, updated_at=? WHERE reminder_id=?",
                    (lease_until, _now(), row["reminder_id"]),
                )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return [self.get_reminder_job(row["reminder_id"]) for row in rows]

    def update_reminder_job(
        self,
        reminder_id: str,
        *,
        status: str,
        next_action_at: str,
        follow_up_count: int,
        last_sent_at: str | None,
    ) -> ReminderJobRow:
        cursor = self._connection.execute(
            """
            UPDATE reminder_jobs
               SET status=?, next_action_at=?, follow_up_count=?, last_sent_at=?,
                   lease_until=NULL, updated_at=?
             WHERE reminder_id=?
            """,
            (status, next_action_at, follow_up_count, last_sent_at, _now(), reminder_id),
        )
        if cursor.rowcount != 1:
            raise NotFoundError(reminder_id)
        return self.get_reminder_job(reminder_id)

    def get_reminder_job(self, reminder_id: str) -> ReminderJobRow:
        row = self._connection.execute(
            "SELECT * FROM reminder_jobs WHERE reminder_id=?", (reminder_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(reminder_id)
        return ReminderJobRow(**dict(row))

    def close(self) -> None:
        self._connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
