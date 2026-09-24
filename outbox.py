"""
审核记录持久化（SQLite）

用于批量生成后的图片人工确认：待审核、通过、驳回重生成。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

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


def ensure_outbox_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        _ensure_delivery_approvals_table(conn)


@dataclass
class DeliveryApproval:
    approval_id: str
    status: str
    source: str
    product_title: str
    target_contacts: list[str]
    review_text: str
    image_paths: list[str]
    file_paths: list[str]
    max_retry: int
    extra: dict
    note: str
    queued_task_id: str | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DeliveryApproval":
        return cls(
            approval_id=row["approval_id"],
            status=row["status"],
            source=row["source"],
            product_title=row["product_title"],
            target_contacts=json.loads(row["target_contacts_json"] or "[]"),
            review_text=row["review_text"],
            image_paths=json.loads(row["image_paths_json"] or "[]"),
            file_paths=json.loads(row["file_paths_json"] or "[]"),
            max_retry=row["max_retry"],
            extra=json.loads(row["extra_json"] or "{}"),
            note=row["note"] or "",
            queued_task_id=row["queued_task_id"],
            created_at=_from_iso(row["created_at"]) or _utc_now(),
            updated_at=_from_iso(row["updated_at"]) or _utc_now(),
            approved_at=_from_iso(row["approved_at"]),
            rejected_at=_from_iso(row["rejected_at"]),
        )

    def to_dict(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "source": self.source,
            "product_title": self.product_title,
            "target_contacts": self.target_contacts,
            "review_text": self.review_text,
            "image_paths": self.image_paths,
            "file_paths": self.file_paths,
            "max_retry": self.max_retry,
            "extra": self.extra,
            "note": self.note,
            "queued_task_id": self.queued_task_id,
            "created_at": _to_iso(self.created_at),
            "updated_at": _to_iso(self.updated_at),
            "approved_at": _to_iso(self.approved_at),
            "rejected_at": _to_iso(self.rejected_at),
        }


def _ensure_delivery_approvals_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_approvals (
            approval_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            source TEXT NOT NULL DEFAULT '',
            product_title TEXT NOT NULL DEFAULT '',
            target_contacts_json TEXT NOT NULL DEFAULT '[]',
            review_text TEXT NOT NULL DEFAULT '',
            image_paths_json TEXT NOT NULL DEFAULT '[]',
            file_paths_json TEXT NOT NULL DEFAULT '[]',
            max_retry INTEGER NOT NULL DEFAULT 4,
            extra_json TEXT NOT NULL DEFAULT '{}',
            note TEXT NOT NULL DEFAULT '',
            queued_task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            approved_at TEXT,
            rejected_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_status ON delivery_approvals(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_approval_created ON delivery_approvals(created_at)"
    )


def create_delivery_approval(
    db_path: str,
    *,
    source: str,
    product_title: str,
    target_contacts: list[str],
    review_text: str,
    image_paths: list[str] | None = None,
    file_paths: list[str] | None = None,
    max_retry: int = 4,
    extra: dict | None = None,
) -> DeliveryApproval:

    approval_id = f"apr_{uuid4().hex[:12]}"
    now = _utc_now()

    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_delivery_approvals_table(conn)
            conn.execute(
                """
                INSERT INTO delivery_approvals (
                    approval_id, status, source, product_title,
                    target_contacts_json, review_text, image_paths_json, file_paths_json,
                    max_retry, extra_json, note, queued_task_id,
                    created_at, updated_at, approved_at, rejected_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?, NULL, NULL)
                """,
                (
                    approval_id,
                    source or "",
                    product_title or "",
                    json.dumps(_normalize_list(target_contacts), ensure_ascii=False),
                    review_text or "",
                    json.dumps(_normalize_list(image_paths), ensure_ascii=False),
                    json.dumps(_normalize_list(file_paths), ensure_ascii=False),
                    max_retry,
                    json.dumps(extra or {}, ensure_ascii=False),
                    _to_iso(now),
                    _to_iso(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM delivery_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            return DeliveryApproval.from_row(row)


def get_delivery_approval(db_path: str, approval_id: str) -> DeliveryApproval | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_delivery_approvals_table(conn)
        row = conn.execute(
            "SELECT * FROM delivery_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return DeliveryApproval.from_row(row) if row else None


def list_delivery_approvals(
    db_path: str,
    status: str | None = "pending",
    limit: int = 200,
) -> list[DeliveryApproval]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_delivery_approvals_table(conn)
        if status and status != "all":
            rows = conn.execute(
                """
                SELECT * FROM delivery_approvals
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM delivery_approvals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [DeliveryApproval.from_row(r) for r in rows]


def confirm_delivery_approval(
    db_path: str,
    approval_id: str,
    note: str = "",
) -> DeliveryApproval | None:
    """将待确认记录更新为 confirmed。"""
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_delivery_approvals_table(conn)
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT * FROM delivery_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if not row:
                return None

            item = DeliveryApproval.from_row(row)
            if item.status == "rejected":
                return item
            if item.status == "confirmed":
                return item

            conn.execute(
                """
                UPDATE delivery_approvals
                SET status = 'confirmed', queued_task_id = ?,
                    approved_at = ?, updated_at = ?, note = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                ("__confirmed__", _to_iso(now), _to_iso(now), note or item.note, approval_id),
            )

    return get_delivery_approval(db_path, approval_id)


def reject_delivery_approval(
    db_path: str,
    approval_id: str,
    note: str = "",
) -> DeliveryApproval | None:
    """驳回审核。"""
    now = _utc_now()
    with _LOCK:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_delivery_approvals_table(conn)
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT * FROM delivery_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if not row:
                return None

            item = DeliveryApproval.from_row(row)
            if item.status == "confirmed":
                return item

            conn.execute(
                """
                UPDATE delivery_approvals
                SET status = 'rejected', rejected_at = ?, updated_at = ?, note = ?
                WHERE approval_id = ? AND status = 'pending'
                """,
                (_to_iso(now), _to_iso(now), note or item.note, approval_id),
            )

    return get_delivery_approval(db_path, approval_id)
