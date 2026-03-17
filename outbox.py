"""
Outbox 队列（供影刀发送端消费）

设计目标：
1. 支持多人发送：target_contacts
2. 支持多媒体：image_paths + file_paths
3. 支持预占、成功回写、失败重试、死信
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _normalize_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        val = (item or "").strip()
        if not val:
            continue
        if val in seen:
            continue
        seen.add(val)
        result.append(val)
    return result


@dataclass
class OutboxTask:
    task_id: str
    idempotency_key: str
    target_contacts: list[str]
    review_text: str
    image_paths: list[str]
    file_paths: list[str]
    status: str
    retry_count: int
    max_retry: int
    last_error: str | None
    next_retry_at: datetime | None
    reserved_by: str | None
    reserved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_attempt_at: datetime | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OutboxTask":
        return cls(
            task_id=row["task_id"],
            idempotency_key=row["idempotency_key"],
            target_contacts=json.loads(row["target_contacts_json"] or "[]"),
            review_text=row["review_text"],
            image_paths=json.loads(row["image_paths_json"] or "[]"),
            file_paths=json.loads(row["file_paths_json"] or "[]"),
            status=row["status"],
            retry_count=row["retry_count"],
            max_retry=row["max_retry"],
            last_error=row["last_error"],
            next_retry_at=_from_iso(row["next_retry_at"]),
            reserved_by=row["reserved_by"],
            reserved_at=_from_iso(row["reserved_at"]),
            created_at=_from_iso(row["created_at"]) or _utc_now(),
            updated_at=_from_iso(row["updated_at"]) or _utc_now(),
            last_attempt_at=_from_iso(row["last_attempt_at"]),
        )


def ensure_outbox_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox_tasks (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                target_contacts_json TEXT NOT NULL,
                review_text TEXT NOT NULL,
                image_paths_json TEXT NOT NULL,
                file_paths_json TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retry INTEGER NOT NULL DEFAULT 4,
                last_error TEXT,
                next_retry_at TEXT,
                reserved_by TEXT,
                reserved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_attempt_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_status_next_retry ON outbox_tasks(status, next_retry_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outbox_created_at ON outbox_tasks(created_at)"
        )
        _migrate_legacy_schema(conn)


def _migrate_legacy_schema(conn: sqlite3.Connection) -> None:
    columns = _get_columns(conn)
    # 兼容旧版本（单联系人 target_contact）
    if "target_contacts_json" not in columns:
        conn.execute("ALTER TABLE outbox_tasks ADD COLUMN target_contacts_json TEXT")
        if "target_contact" in columns:
            legacy_rows = conn.execute(
                "SELECT task_id, target_contact FROM outbox_tasks"
            ).fetchall()
            for task_id, target_contact in legacy_rows:
                contacts = _normalize_list([target_contact])
                conn.execute(
                    "UPDATE outbox_tasks SET target_contacts_json = ? WHERE task_id = ?",
                    (json.dumps(contacts, ensure_ascii=False), task_id),
                )
        else:
            conn.execute(
                """
                UPDATE outbox_tasks
                SET target_contacts_json = '[]'
                WHERE target_contacts_json IS NULL OR target_contacts_json = ''
                """
            )

    if "file_paths_json" not in columns:
        conn.execute("ALTER TABLE outbox_tasks ADD COLUMN file_paths_json TEXT")
        conn.execute(
            """
            UPDATE outbox_tasks
            SET file_paths_json = '[]'
            WHERE file_paths_json IS NULL OR file_paths_json = ''
            """
        )


def _get_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(outbox_tasks)").fetchall()
    return {row[1] for row in rows}


def _make_idempotency_key(
    target_contacts: list[str],
    review_text: str,
    image_paths: list[str],
    file_paths: list[str],
) -> str:
    payload = {
        "target_contacts": sorted(_normalize_list(target_contacts)),
        "review_text": (review_text or "").strip(),
        "image_paths": sorted(_normalize_list(image_paths)),
        "file_paths": sorted(_normalize_list(file_paths)),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _make_task_id(idempotency_key: str) -> str:
    return f"task_{idempotency_key[:24]}"


def enqueue_task(
    db_path: str,
    target_contacts: list[str],
    review_text: str,
    image_paths: list[str] | None = None,
    file_paths: list[str] | None = None,
    max_retry: int = 4,
) -> OutboxTask:
    contacts = _normalize_list(target_contacts)
    if not contacts:
        raise ValueError("target_contacts 不能为空")

    image_paths = _normalize_list(image_paths)
    file_paths = _normalize_list(file_paths)
    now = _utc_now()

    idem = _make_idempotency_key(contacts, review_text, image_paths, file_paths)
    task_id = _make_task_id(idem)

    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            columns = _get_columns(conn)

            existing = conn.execute(
                "SELECT * FROM outbox_tasks WHERE idempotency_key = ?",
                (idem,),
            ).fetchone()
            if existing:
                return OutboxTask.from_row(existing)

            if "target_contact" in columns:
                conn.execute(
                    """
                    INSERT INTO outbox_tasks (
                        task_id, idempotency_key, target_contact, target_contacts_json,
                        review_text, image_paths_json, file_paths_json,
                        status, retry_count, max_retry, last_error, next_retry_at, reserved_by, reserved_at,
                        created_at, updated_at, last_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?, NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        task_id,
                        idem,
                        contacts[0],
                        json.dumps(contacts, ensure_ascii=False),
                        review_text or "",
                        json.dumps(image_paths, ensure_ascii=False),
                        json.dumps(file_paths, ensure_ascii=False),
                        max_retry,
                        _to_iso(now),
                        _to_iso(now),
                        _to_iso(now),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO outbox_tasks (
                        task_id, idempotency_key, target_contacts_json, review_text, image_paths_json, file_paths_json,
                        status, retry_count, max_retry, last_error, next_retry_at, reserved_by, reserved_at,
                        created_at, updated_at, last_attempt_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, ?, NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        task_id,
                        idem,
                        json.dumps(contacts, ensure_ascii=False),
                        review_text or "",
                        json.dumps(image_paths, ensure_ascii=False),
                        json.dumps(file_paths, ensure_ascii=False),
                        max_retry,
                        _to_iso(now),
                        _to_iso(now),
                        _to_iso(now),
                    ),
                )
            row = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                raise RuntimeError("入队失败")
            return OutboxTask.from_row(row)


def reserve_next_task(db_path: str, worker_id: str) -> OutboxTask | None:
    now = _utc_now()
    lease_timeout_sec = int(os.getenv("OUTBOX_PROCESSING_TIMEOUT_SEC", "120") or "120")
    stale_deadline = now - timedelta(seconds=max(1, lease_timeout_sec))
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM outbox_tasks
                WHERE (
                    (
                        status IN ('pending', 'retry')
                        AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    )
                    OR (
                        status = 'processing'
                        AND reserved_at IS NOT NULL
                        AND reserved_at <= ?
                    )
                )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (_to_iso(now), _to_iso(stale_deadline)),
            ).fetchone()
            if not row:
                return None

            conn.execute(
                """
                UPDATE outbox_tasks
                SET status = 'processing', reserved_by = ?, reserved_at = ?, last_attempt_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (worker_id, _to_iso(now), _to_iso(now), _to_iso(now), row["task_id"]),
            )
            reserved = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (row["task_id"],),
            ).fetchone()
            return OutboxTask.from_row(reserved) if reserved else None


def peek_next_due_task(db_path: str) -> OutboxTask | None:
    """
    只查看下一条可处理任务，不改变任务状态。
    """
    now = _utc_now()
    lease_timeout_sec = int(os.getenv("OUTBOX_PROCESSING_TIMEOUT_SEC", "120") or "120")
    stale_deadline = now - timedelta(seconds=max(1, lease_timeout_sec))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM outbox_tasks
            WHERE (
                (
                    status IN ('pending', 'retry')
                    AND (next_retry_at IS NULL OR next_retry_at <= ?)
                )
                OR (
                    status = 'processing'
                    AND reserved_at IS NOT NULL
                    AND reserved_at <= ?
                )
            )
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (_to_iso(now), _to_iso(stale_deadline)),
        ).fetchone()
        return OutboxTask.from_row(row) if row else None


def ack_success(db_path: str, task_id: str, worker_id: str = "") -> OutboxTask | None:
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")

            # 先读取当前状态用于日志和诊断
            before = conn.execute(
                "SELECT task_id, status, reserved_by FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not before:
                conn.commit()
                return None

            # 仅允许处理中的任务被确认成功；worker_id 传入时会尝试匹配
            if worker_id:
                cur = conn.execute(
                    """
                    UPDATE outbox_tasks
                    SET status = 'completed',
                        last_error = NULL,
                        next_retry_at = NULL,
                        reserved_by = NULL,
                        reserved_at = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                      AND status = 'processing'
                      AND (reserved_by IS NULL OR reserved_by = ?)
                    """,
                    (_to_iso(now), task_id, worker_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE outbox_tasks
                    SET status = 'completed',
                        last_error = NULL,
                        next_retry_at = NULL,
                        reserved_by = NULL,
                        reserved_at = NULL,
                        updated_at = ?
                    WHERE task_id = ?
                      AND status = 'processing'
                    """,
                    (_to_iso(now), task_id),
                )

            affected = cur.rowcount
            # 如果未更新，尝试兼容旧状态 success/completed 的幂等确认
            if affected == 0:
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM outbox_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                return OutboxTask.from_row(row) if row else None

            conn.commit()
            row = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return OutboxTask.from_row(row) if row else None


def ack_failure(db_path: str, task_id: str, error_message: str) -> OutboxTask | None:
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                return None

            task = OutboxTask.from_row(row)
            next_retry_count = task.retry_count + 1
            if next_retry_count >= task.max_retry:
                next_status = "dead_letter"
                next_retry_at = None
            else:
                next_status = "retry"
                backoff = [5, 15, 60, 300][min(next_retry_count - 1, 3)]
                next_retry_at = now + timedelta(seconds=backoff)

            conn.execute(
                """
                UPDATE outbox_tasks
                SET status = ?, retry_count = ?, last_error = ?, next_retry_at = ?,
                    reserved_by = NULL, reserved_at = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    next_status,
                    next_retry_count,
                    (error_message or "")[:2000],
                    _to_iso(next_retry_at),
                    _to_iso(now),
                    task_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return OutboxTask.from_row(updated) if updated else None


def requeue_dead_letter(db_path: str, task_id: str) -> OutboxTask | None:
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            task = OutboxTask.from_row(row)
            if task.status != "dead_letter":
                return task

            conn.execute(
                """
                UPDATE outbox_tasks
                SET status = 'retry', next_retry_at = ?, last_error = NULL, reserved_by = NULL, reserved_at = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (_to_iso(now), _to_iso(now), task_id),
            )
            updated = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return OutboxTask.from_row(updated) if updated else None


def recover_processing_task(db_path: str, task_id: str, note: str = "") -> OutboxTask | None:
    """
    手动恢复卡在 processing 的任务到 retry。
    """
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            task = OutboxTask.from_row(row)
            if task.status != "processing":
                return task

            detail = f"[manual_recover] {note}".strip()
            conn.execute(
                """
                UPDATE outbox_tasks
                SET status = 'retry', next_retry_at = ?, reserved_by = NULL, reserved_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (_to_iso(now), detail[:2000], _to_iso(now), task_id),
            )
            updated = conn.execute(
                "SELECT * FROM outbox_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return OutboxTask.from_row(updated) if updated else None


def list_tasks(db_path: str, limit: int = 50, status: str | None = None) -> list[OutboxTask]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute(
                """
                SELECT * FROM outbox_tasks
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM outbox_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [OutboxTask.from_row(row) for row in rows]

