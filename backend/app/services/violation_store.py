import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR, settings
from app.models.schemas import CameraCalibration, ViolationReport, Zone, ZoneViolation


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
            CREATE TABLE IF NOT EXISTS zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_name TEXT NOT NULL,
                zone_name TEXT NOT NULL,
                zone_type TEXT NOT NULL,
                dwell_threshold_seconds INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                ui_shape_data TEXT NOT NULL,
                flattened_coordinates TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS zone_violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                video_name TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                snapshot_path TEXT,
                FOREIGN KEY (zone_id) REFERENCES zones (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_calibrations (
                video_name TEXT PRIMARY KEY,
                source_points TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_zones_video_name ON zones(video_name)"
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


def save_zone(zone: Zone) -> Zone:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO zones (
                video_name, zone_name, zone_type, dwell_threshold_seconds,
                is_active, ui_shape_data, flattened_coordinates
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone.video_name,
                zone.zone_name,
                zone.zone_type,
                zone.dwell_threshold_seconds,
                1 if zone.is_active else 0,
                zone.ui_shape_data,
                zone.flattened_coordinates,
            ),
        )
        conn.commit()
        zone.id = cursor.lastrowid
    return zone


def list_zones(video_name: str) -> list[Zone]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM zones WHERE video_name = ?", (video_name,)
        ).fetchall()
    return [
        Zone(
            id=row["id"],
            video_name=row["video_name"],
            zone_name=row["zone_name"],
            zone_type=row["zone_type"],
            dwell_threshold_seconds=row["dwell_threshold_seconds"],
            is_active=bool(row["is_active"]),
            ui_shape_data=row["ui_shape_data"],
            flattened_coordinates=row["flattened_coordinates"],
        )
        for row in rows
    ]


def delete_zone(zone_id: int) -> bool:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_zones_by_video(video_name: str) -> int:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM zones WHERE video_name = ?", (video_name,))
        conn.commit()
        return cursor.rowcount


def delete_all_violations() -> int:
    """Delete all PPE violations from the database."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM violations")
        conn.commit()
        return cursor.rowcount


def delete_all_zone_violations() -> int:
    """Delete all zone violations from the database."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM zone_violations")
        conn.commit()
        return cursor.rowcount

def delete_violation(violation_id: int) -> bool:
    """Delete a PPE violation by ID."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM violations WHERE id = ?", (violation_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_zone_violation(zone_violation_id: int) -> bool:
    """Delete a zone violation by ID."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM zone_violations WHERE id = ?", (zone_violation_id,))
        conn.commit()
        return cursor.rowcount > 0

def update_zone(zone: Zone) -> Zone:
    if zone.id is None:
        raise ValueError("Zone ID is required for update")
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE zones
            SET zone_name = ?,
                zone_type = ?,
                dwell_threshold_seconds = ?,
                is_active = ?,
                ui_shape_data = ?,
                flattened_coordinates = ?
            WHERE id = ?
            """,
            (
                zone.zone_name,
                zone.zone_type,
                zone.dwell_threshold_seconds,
                1 if zone.is_active else 0,
                zone.ui_shape_data,
                zone.flattened_coordinates,
                zone.id,
            ),
        )
        conn.commit()
    return zone


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


def save_calibration(calibration: CameraCalibration) -> CameraCalibration:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO camera_calibrations (video_name, source_points)
            VALUES (?, ?)
            ON CONFLICT(video_name) DO UPDATE SET source_points = excluded.source_points
            """,
            (calibration.video_name, calibration.source_points),
        )
        conn.commit()
    return calibration


def get_calibration(video_name: str) -> CameraCalibration | None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM camera_calibrations WHERE video_name = ?", (video_name,)
        ).fetchone()
        if row:
            return CameraCalibration(
                video_name=row["video_name"], source_points=row["source_points"]
            )
    return None


def save_zone_violation(violation: ZoneViolation) -> ZoneViolation:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO zone_violations (
                zone_id, track_id, timestamp, video_name, frame_index, snapshot_path
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                violation.zone_id,
                violation.track_id,
                violation.timestamp,
                violation.video_name,
                violation.frame_index,
                violation.snapshot_path,
            ),
        )
        conn.commit()
        violation.id = cursor.lastrowid
    return violation


def list_zone_violations(limit: int = 100) -> list[ZoneViolation]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM zone_violations ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            ZoneViolation(
                id=row["id"],
                zone_id=row["zone_id"],
                track_id=row["track_id"],
                timestamp=row["timestamp"],
                video_name=row["video_name"],
                frame_index=row["frame_index"],
                snapshot_path=_snapshot_url(row["snapshot_path"]),
            )
            for row in rows
        ]
