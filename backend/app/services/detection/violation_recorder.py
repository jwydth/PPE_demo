import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.schemas.detection import BoundingBox, PersonResult
from app.schemas.violation import ViolationReport
from app.services.ppe_violation_service import open_ppe_violation_service
from app.services.zone_service import COORD_SCALE
from app.storage.local_paths import SNAPSHOT_DIR
from .bbox_utils import (
    _bbox_aspect_ratio,
    _bbox_height,
    _bbox_iou,
    _bbox_width,
    _center_distance_ratio,
)

logger = logging.getLogger(__name__)


@dataclass
class ViolationCase:
    report: ViolationReport
    missing: set[str]
    track_ids: set[int]
    last_bbox: BoundingBox
    first_frame: int
    last_frame: int


def _record_violation_case(
    *,
    cases: list[ViolationCase],
    frame,
    person: PersonResult,
    worker: Any,
    missing: list[str],
    video_name: str,
    frame_index: int,
    confirmed_aspect_ratios: list[float] | None = None,
) -> None:
    if worker.reported:
        return

    case, match_reason = _find_existing_case_match(cases, person, frame_index)
    missing_set = set(missing)
    violation_type = _violation_type(missing)
    timestamp = datetime.now(timezone.utc).isoformat()
    _log_confirmed_incident(
        timestamp=timestamp,
        video_name=video_name,
        frame_index=frame_index,
        person=person,
        worker=worker,
        case=case,
        violation_type=violation_type,
        action="create" if case is None else "match_existing_immutable",
        match_reason=match_reason,
    )

    if confirmed_aspect_ratios is not None:
        confirmed_aspect_ratios.append(_bbox_aspect_ratio(person.bbox))

    if case is None:
        snapshot_filename = _save_violation_snapshot(
            frame=frame,
            person=person,
            missing=missing,
            video_stem=Path(video_name).stem,
            frame_index=frame_index,
        )
        report = save_violation(
            timestamp=timestamp,
            violation_type=violation_type,
            details=_violation_details(person, missing, frame_index),
            snapshot_filename=snapshot_filename,
            video_name=video_name,
            frame_index=frame_index,
            track_id=person.track_id,
            person_index=person.person_id,
            missing_equipment=missing,
            bounding_box=person.bbox.model_dump(),
            confidence=person.confidence,
        )
        cases.append(
            ViolationCase(
                report=report,
                missing=missing_set,
                track_ids={person.track_id} if person.track_id is not None else set(),
                last_bbox=person.bbox,
                first_frame=frame_index,
                last_frame=frame_index,
            )
        )
    else:
        case.last_bbox = person.bbox
        case.last_frame = frame_index
        if person.track_id is not None:
            case.track_ids.add(person.track_id)
    worker.reported = True
    worker.status = "violation"


def _find_existing_case(
    cases: list[ViolationCase],
    person: PersonResult,
    frame_index: int,
) -> ViolationCase | None:
    case, _ = _find_existing_case_match(cases, person, frame_index)
    return case


def _find_existing_case_match(
    cases: list[ViolationCase],
    person: PersonResult,
    frame_index: int,
) -> tuple[ViolationCase | None, str]:
    if person.track_id is not None:
        for case in cases:
            if person.track_id in case.track_ids:
                return case, "same_track"

    fresh_cases = [
        case
        for case in cases
        if frame_index - case.last_frame <= settings.VIDEO_CASE_MAX_FRAME_GAP
    ]

    best_case: ViolationCase | None = None
    best_iou = 0.0
    for case in fresh_cases:
        iou = _bbox_iou(person.bbox, case.last_bbox)
        if iou > best_iou:
            best_iou = iou
            best_case = case

    if best_case is not None and best_iou >= settings.VIDEO_CASE_IOU_THRESHOLD:
        return best_case, "iou"

    for case in fresh_cases:
        if (
            _center_distance_ratio(person.bbox, case.last_bbox)
            <= settings.VIDEO_CASE_CENTER_DISTANCE_RATIO
        ):
            return case, "center"

    return None, "new"


def _save_violation_snapshot(
    *,
    frame,
    person: PersonResult,
    missing: list[str],
    video_stem: str,
    frame_index: int,
    polygon: list[tuple[float, float]] | None = None,
    zone_type: str | None = None,
) -> str:
    import cv2

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_stem
    )
    track_label = person.track_id if person.track_id is not None else person.person_id
    filename = f"{safe_stem}_frame_{frame_index}_track_{track_label}_{int(time.time() * 1000)}.jpg"
    path = SNAPSHOT_DIR / filename

    snapshot = frame.copy()
    frame_height, frame_width = snapshot.shape[:2]

    if polygon:
        color = (34, 197, 94) if zone_type == "WALKWAY" else (0, 0, 255)
        pts = np.array(
            [
                [
                    int(p[0] * frame_width / COORD_SCALE),
                    int(p[1] * frame_height / COORD_SCALE),
                ]
                for p in polygon
            ],
            np.int32,
        ).reshape((-1, 1, 2))

        overlay = snapshot.copy()
        cv2.fillPoly(overlay, [pts], color=color)
        cv2.addWeighted(overlay, 0.3, snapshot, 0.7, 0, snapshot)
        cv2.polylines(snapshot, [pts], isClosed=True, color=color, thickness=2)

    x1 = int(max(0, person.bbox.x1))
    y1 = int(max(0, person.bbox.y1))
    x2 = int(max(0, person.bbox.x2))
    y2 = int(max(0, person.bbox.y2))
    cv2.rectangle(snapshot, (x1, y1), (x2, y2), (0, 0, 255), 3)
    cv2.putText(
        snapshot,
        f"Violation: {', '.join(missing)}",
        (x1, max(24, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 0, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(path), snapshot)
    return filename


def _violation_type(missing: list[str]) -> str:
    normalized = [item.lower() for item in missing]
    if "helmet" in normalized and "vest" in normalized:
        return "missing_helmet_and_vest"
    if "helmet" in normalized:
        return "missing_helmet"
    if "vest" in normalized:
        return "missing_vest"
    return "ppe_violation"


def _violation_details(
    person: PersonResult, missing: list[str], frame_index: int
) -> str:
    subject = (
        f"track {person.track_id}"
        if person.track_id is not None
        else f"person {person.person_id}"
    )
    return f"{subject} missing {', '.join(missing)} at frame {frame_index}"


def save_violation(
    *,
    timestamp: str,
    violation_type: str,
    details: str,
    snapshot_filename: str,
    video_name: str | None,
    frame_index: int | None,
    track_id: int | None,
    person_index: int | None,
    missing_equipment: list[str],
    bounding_box: dict[str, float] | None,
    confidence: float | None,
) -> ViolationReport:
    local_snapshot_path = SNAPSHOT_DIR / snapshot_filename
    with open_ppe_violation_service() as service:
        return service.persist_violation(
            timestamp=timestamp,
            violation_type=violation_type,
            details=details,
            local_snapshot_path=str(local_snapshot_path),
            video_name=video_name,
            frame_index=frame_index,
            track_id=track_id,
            person_index=person_index,
            missing_equipment=missing_equipment,
            bounding_box=bounding_box,
            confidence=confidence,
        )


def _format_track_ids(track_ids: set[int]) -> str:
    if not track_ids:
        return "-"
    return ",".join(str(track_id) for track_id in sorted(track_ids))


def _log_confirmed_incident(
    *,
    timestamp: str,
    video_name: str,
    frame_index: int,
    person: PersonResult,
    worker: Any,
    case: ViolationCase | None,
    violation_type: str,
    action: str,
    match_reason: str,
) -> None:
    bbox = person.bbox
    logger.info(
        "ppe_incident_confirmed timestamp=%s video=%s frame_index=%s track_id=%s "
        "worker_id=%s worker_reported_before_record=%s violation_type=%s action=%s "
        "match_reason=%s case_track_ids=%s case_first_frame=%s case_last_frame=%s "
        "bbox=(%.1f,%.1f,%.1f,%.1f) bbox_width=%.1f bbox_height=%.1f "
        "aspect_ratio=%.3f",
        timestamp,
        video_name,
        frame_index,
        person.track_id,
        id(worker),
        worker.reported,
        violation_type,
        action,
        match_reason,
        _format_track_ids(case.track_ids if case is not None else set()),
        case.first_frame if case is not None else None,
        case.last_frame if case is not None else None,
        bbox.x1,
        bbox.y1,
        bbox.x2,
        bbox.y2,
        _bbox_width(bbox),
        _bbox_height(bbox),
        _bbox_aspect_ratio(bbox),
    )
