import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR, settings
from app.models.schemas import ViolationReport


def _resolve_backend_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


DB_PATH = _resolve_backend_path(settings.VIOLATION_DB_PATH)
SNAPSHOT_DIR = _resolve_backend_path(settings.SNAPSHOT_DIR)


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                violation_type TEXT NOT NULL,
                details TEXT NOT NULL,
                snapshot_path TEXT,
                video_name TEXT,
                frame_index INTEGER,
                track_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_violations_timestamp
            ON violations(timestamp)
            """
        )
        conn.commit()


def save_violation(
    *,
    timestamp: str,
    violation_type: str,
    details: str,
    snapshot_filename: str | None,
    video_name: str | None,
    frame_index: int | None,
    track_id: int | None,
) -> ViolationReport:
    init_db()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO violations (
                timestamp,
                violation_type,
                details,
                snapshot_path,
                video_name,
                frame_index,
                track_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                violation_type,
                details,
                snapshot_filename,
                video_name,
                frame_index,
                track_id,
            ),
        )
        conn.commit()
        row_id = int(cursor.lastrowid)

    return ViolationReport(
        id=row_id,
        timestamp=timestamp,
        violation_type=violation_type,
        details=details,
        snapshot_url=_snapshot_url(snapshot_filename),
        video_name=video_name,
        frame_index=frame_index,
        track_id=track_id,
    )


def update_violation(
    *,
    report_id: int,
    violation_type: str,
    details: str,
    frame_index: int | None,
    track_id: int | None,
) -> ViolationReport:
    init_db()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            UPDATE violations
            SET violation_type = ?,
                details = ?,
                frame_index = ?,
                track_id = ?
            WHERE id = ?
            RETURNING *
            """,
            (violation_type, details, frame_index, track_id, report_id),
        ).fetchone()
        conn.commit()

    if row is None:
        raise ValueError(f"Violation report {report_id} does not exist.")

    return _row_to_report(dict(row))


def list_violations(limit: int = 100) -> list[ViolationReport]:
    init_db()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM violations
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [_row_to_report(dict(row)) for row in rows]


def _row_to_report(row: dict[str, Any]) -> ViolationReport:
    return ViolationReport(
        id=row["id"],
        timestamp=row["timestamp"],
        violation_type=row["violation_type"],
        details=row["details"],
        snapshot_url=_snapshot_url(row["snapshot_path"]),
        video_name=row["video_name"],
        frame_index=row["frame_index"],
        track_id=row["track_id"],
    )


def _snapshot_url(snapshot_filename: str | None) -> str | None:
    if not snapshot_filename:
        return None
    return f"/snapshots/{snapshot_filename}"
