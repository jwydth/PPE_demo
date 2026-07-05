"""ViolationCase dedup/matching logic.

Note: `_record_violation_case`, `_save_violation_snapshot`, and
`save_violation` deliberately stay in `app/services/ppe_detector.py` rather
than moving here — see the Step 3 refactor summary for why (persistence
snapshot drawing needs `zone_service.COORD_SCALE`, and several tests
monkeypatch these as a tightly-coupled trio; splitting them across modules
would silently break those tests' patch semantics).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.detection import BoundingBox, PersonResult
from app.schemas.violation import ViolationReport

from app.services.ppe.constants import (
    CLEANING_COVERALL_LABEL,
    HELMET_LABEL,
    ROLE_UNIFORM_LABEL,
    VEST_LABEL,
)
from app.services.ppe.geometry import _bbox_iou, _center_distance_ratio
from app.core.config import settings


@dataclass
class ViolationCase:
    report: ViolationReport
    missing: set[str]
    track_ids: set[int]
    last_bbox: BoundingBox
    first_frame: int
    last_frame: int
    video_name: str | None = None
    violation_type: str | None = None
    role: str | None = None


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


def _find_duplicate_ppe_case(
    *,
    cases: list[ViolationCase],
    person: PersonResult,
    video_name: str,
    violation_type: str,
    missing: set[str],
    frame_index: int,
) -> tuple[ViolationCase | None, str, int | None, float | None]:
    best_case: ViolationCase | None = None
    best_gap: int | None = None
    best_center_ratio: float | None = None
    max_gap = settings.VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP
    max_center_ratio = settings.VIDEO_PPE_DUPLICATE_CENTER_DISTANCE_RATIO

    for case in cases:
        if case.video_name is not None and case.video_name != video_name:
            continue

        frame_gap = frame_index - case.last_frame
        if frame_gap < 0 or frame_gap > max_gap:
            continue

        if not _case_violation_matches(case, violation_type, missing):
            continue

        if not _case_role_matches(case.role, person.role):
            continue

        center_ratio = _center_distance_ratio(person.bbox, case.last_bbox)
        if center_ratio > max_center_ratio:
            continue

        if best_center_ratio is None or center_ratio < best_center_ratio:
            best_case = case
            best_gap = frame_gap
            best_center_ratio = center_ratio

    if best_case is None:
        return None, "no_duplicate", best_gap, best_center_ratio

    return best_case, "ppe_duplicate_spatial", best_gap, best_center_ratio


def _case_violation_matches(
    case: ViolationCase,
    violation_type: str,
    missing: set[str],
) -> bool:
    if case.violation_type is not None and case.violation_type == violation_type:
        return True
    return case.missing == missing


def _case_role_matches(case_role: str | None, person_role: str | None) -> bool:
    if case_role is None or person_role is None:
        return True
    return case_role == person_role


def _violation_type(missing: list[str]) -> str:
    missing_set = set(missing)
    if {HELMET_LABEL, VEST_LABEL}.issubset(missing_set):
        return "missing_helmet_and_vest"
    if {HELMET_LABEL, CLEANING_COVERALL_LABEL}.issubset(missing_set):
        return "missing_helmet_and_cleaning_coverall"
    if {HELMET_LABEL, ROLE_UNIFORM_LABEL}.issubset(missing_set):
        return "missing_helmet_and_role_uniform"
    if HELMET_LABEL in missing_set:
        return "missing_helmet"
    if VEST_LABEL in missing_set:
        return "missing_vest"
    if CLEANING_COVERALL_LABEL in missing_set:
        return "missing_cleaning_coverall"
    if ROLE_UNIFORM_LABEL in missing_set:
        return "missing_role_uniform"
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
