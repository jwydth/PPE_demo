import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.models.schemas import PersonResult, ZoneViolation
from app.services.spatial import is_point_in_polygon
from app.services.violation_store import list_zones, save_zone_violation

COORD_SCALE = 1000


class ZoneViolationRecord:
    """Tracks zone violation data for a person"""

    def __init__(self, zone_id: int, zone_name: str, zone_type: str, poly: list, threshold: float):
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.zone_type = zone_type
        self.poly = poly
        self.threshold = threshold

    def point_in_zone(self, test_point: tuple) -> bool:
        """Check if a point is inside this zone's polygon"""
        return is_point_in_polygon(test_point, self.poly)


def load_zones(video_name: str) -> list[ZoneViolationRecord]:
    """Load all active zones for a video with normalized coordinates"""
    active_zones = [z for z in list_zones(video_name) if z.is_active]
    zones: list[ZoneViolationRecord] = []

    for zone in active_zones:
        try:
            raw = json.loads(zone.flattened_coordinates)
            if not raw:
                continue
            coords = [(p["x"] * COORD_SCALE, p["y"] * COORD_SCALE) for p in raw]
            zones.append(
                ZoneViolationRecord(
                    zone_id=zone.id,
                    zone_name=zone.zone_name,
                    zone_type=zone.zone_type,
                    poly=coords,
                    threshold=zone.dwell_threshold_seconds,
                )
            )
        except Exception:
            pass

    return zones


def get_person_foot_point(person: PersonResult, frame_width: int, frame_height: int) -> tuple:
    """Calculate normalized foot point for a person's bounding box"""
    foot_x = (person.bbox.x1 + person.bbox.x2) / 2 / frame_width
    foot_y = person.bbox.y2 / frame_height
    return (foot_x * COORD_SCALE, foot_y * COORD_SCALE)


def check_zone_incursion(
    zones: list[ZoneViolationRecord],
    test_point: tuple,
) -> list[ZoneViolationRecord]:
    """Check which zones a point is inside"""
    incursion_zones = []
    for zone in zones:
        if zone.point_in_zone(test_point):
            incursion_zones.append(zone)
    return incursion_zones


def record_zone_violation(
    worker_state,
    zone: ZoneViolationRecord,
    frame: np.ndarray,
    person: PersonResult,
    video_name: str,
    frame_index: int,
    save_snapshot_fn,
) -> ZoneViolation:
    """Record a zone violation for a person"""
    timestamp = datetime.now(timezone.utc).isoformat()
    if zone.zone_type == "WALKWAY":
        label = f"Left Walkway: {zone.zone_name}"
    else:
        label = f"Entered Zone: {zone.zone_name}"

    snapshot_filename = save_snapshot_fn(
        frame=frame,
        person=person,
        missing=[label],
        video_stem=Path(video_name).stem,
        frame_index=frame_index,
    )

    violation = ZoneViolation(
        zone_id=zone.zone_id,
        zone_name=zone.zone_name,
        zone_type=zone.zone_type,
        track_id=person.track_id or 0,
        timestamp=timestamp,
        video_name=video_name,
        frame_index=frame_index,
        snapshot_path=snapshot_filename,
    )

    saved = save_zone_violation(violation)
    worker_state.reported_zones.add(zone.zone_id)
    return saved

