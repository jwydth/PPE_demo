"""WorkerState and worker matching/merging/judgeability logic."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from app.core.config import settings
from app.schemas.detection import BoundingBox, EquipmentStatus, PersonResult

from app.services.ppe.constants import (
    CLEANING_COVERALL_LABEL,
    HELMET_LABEL,
    MISSING_LABEL_ORDER,
    VEST_LABEL,
)
from app.services.ppe.geometry import (
    _bbox_aspect_ratio,
    _bbox_height,
    _bbox_height_ratio,
    _bbox_iou,
    _center_distance_ratio,
)


@dataclass
class WorkerState:
    track_ids: set[int]
    first_frame: int
    last_frame: int
    last_bbox: BoundingBox
    recent_bboxes: list[BoundingBox]
    role: str | None = None
    uniform_type: str | None = None
    helmet_seen_frame: int | None = None
    vest_seen_frame: int | None = None
    cleaning_coverall_seen_frame: int | None = None
    missing_counts: dict[str, int] | None = None
    reported_missing: set[str] | None = None
    reported: bool = False
    status: str = "unknown"
    zone_dwell: dict[int, float] | None = None  # camera_zone_view_id -> seconds
    reported_zones: set[int] | None = None  # camera_zone_view_ids
    zone_last_in: dict[int, bool] | None = None  # camera_zone_view_id -> was inside zone at last detection

    def __post_init__(self) -> None:
        if self.missing_counts is None:
            self.missing_counts = _empty_missing_counts()
        if self.reported_missing is None:
            self.reported_missing = set()
        if self.zone_dwell is None:
            self.zone_dwell = {}
        if self.reported_zones is None:
            self.reported_zones = set()
        if self.zone_last_in is None:
            self.zone_last_in = {}


def _update_worker_status(
    *,
    workers: list[WorkerState],
    person: PersonResult,
    frame_index: int,
    fps: float,
    frame_width: int,
    frame_height: int,
    used_worker_ids: set[int],
) -> dict:
    worker, worker_match_reason = _find_or_create_worker(
        workers,
        person,
        frame_index,
        used_worker_ids,
    )
    _merge_worker_observation(worker, person, frame_index, fps)
    _update_worker_role(worker, person)
    _apply_worker_role_to_person(worker, person)

    present = _present_equipment(person)
    if HELMET_LABEL in present:
        worker.helmet_seen_frame = frame_index
    if VEST_LABEL in present:
        worker.vest_seen_frame = frame_index
    if CLEANING_COVERALL_LABEL in present:
        worker.cleaning_coverall_seen_frame = frame_index

    if worker.reported:
        worker.status = "violation"
        _reset_missing_counts(worker)
        return {
            "unknown": False,
            "candidate": False,
            "reason": "already_reported",
            "missing": [],
            "worker": worker,
            "worker_match_reason": worker_match_reason,
            "missing_to_report": _ordered_missing(worker.reported_missing)
            if worker.reported_missing
            else [],
        }

    raw_missing = _missing_equipment(person)
    relevant_labels = _relevant_missing_labels(person)
    judgeable = _is_worker_judgeable(
        worker, frame_index, fps, frame_width, frame_height
    )
    reason = (
        ""
        if judgeable
        else _unknown_reason(worker, frame_index, fps, frame_width, frame_height)
    )

    if not judgeable:
        if worker.status != "violation":
            worker.status = "unknown"
            # Removed _reset_missing_counts here to persist evidence
        return {
            "unknown": worker.status != "violation",
            "candidate": False,
            "reason": reason,
            "missing": raw_missing,
            "worker": worker,
            "worker_match_reason": worker_match_reason,
            "missing_to_report": [],
        }

    # If we reached here, they ARE judgeable
    missing = _suppress_recently_seen_ppe(worker, raw_missing, frame_index, fps)
    if worker.reported_missing:
        missing = [label for label in missing if label not in worker.reported_missing]

    confirm_frames = _seconds_to_frames(settings.VIDEO_VIOLATION_CONFIRM_SECONDS, fps)

    if not missing:
        if worker.status != "violation":
            worker.status = "compliant"
        _reset_missing_counts(worker)
        return {
            "unknown": False,
            "candidate": False,
            "reason": "",
            "missing": [],
            "worker": worker,
            "worker_match_reason": worker_match_reason,
            "missing_to_report": [],
        }

    # They are missing PPE
    if worker.missing_counts is None:
        worker.missing_counts = _empty_missing_counts()
    for label in MISSING_LABEL_ORDER:
        if label not in worker.missing_counts:
            worker.missing_counts[label] = 0
        worker.missing_counts[label] = (
            worker.missing_counts.get(label, 0) + 1 if label in missing else 0
        )

    confirmed_missing = [
        label
        for label in relevant_labels
        if (
            worker.missing_counts
            and worker.missing_counts.get(label, 0) >= confirm_frames
            and (worker.reported_missing is None or label not in worker.reported_missing)
        )
    ]

    if not confirmed_missing:
        if worker.status != "violation":
            worker.status = "unknown"
        return {
            "unknown": False,
            "candidate": True,
            "reason": "",
            "missing": missing,
            "worker": worker,
            "worker_match_reason": worker_match_reason,
            "missing_to_report": [],
        }

    worker.status = "violation"
    if worker.reported_missing is None:
        worker.reported_missing = set()
    worker.reported_missing.update(confirmed_missing)
    return {
        "unknown": False,
        "candidate": True,
        "reason": "",
        "missing": _ordered_missing(worker.reported_missing),
        "worker": worker,
        "worker_match_reason": worker_match_reason,
        "missing_to_report": _ordered_missing(worker.reported_missing),
    }


def _find_or_create_worker(
    workers: list[WorkerState],
    person: PersonResult,
    frame_index: int,
    used_worker_ids: set[int],
) -> tuple[WorkerState, str]:
    worker, match_reason = _find_existing_worker(
        workers,
        person,
        frame_index,
        used_worker_ids,
    )
    if worker is not None:
        used_worker_ids.add(id(worker))
        return worker, match_reason

    worker = WorkerState(
        track_ids={person.track_id} if person.track_id is not None else set(),
        first_frame=frame_index,
        last_frame=frame_index,
        last_bbox=person.bbox,
        recent_bboxes=[],
    )
    workers.append(worker)
    used_worker_ids.add(id(worker))
    return worker, "new_worker"


def _find_existing_worker(
    workers: list[WorkerState],
    person: PersonResult,
    frame_index: int,
    used_worker_ids: set[int],
) -> tuple[WorkerState | None, str]:
    if person.track_id is not None:
        for worker in workers:
            if id(worker) in used_worker_ids:
                continue
            if person.track_id in worker.track_ids:
                return worker, "same_track"

    fresh_workers = [
        worker
        for worker in workers
        if id(worker) not in used_worker_ids
        and frame_index - worker.last_frame <= settings.VIDEO_CASE_MAX_FRAME_GAP
    ]

    reported_candidates = [worker for worker in fresh_workers if worker.reported]
    reported_worker, reported_reason = _find_spatial_worker_match(
        reported_candidates,
        person,
    )
    if reported_worker is not None:
        return reported_worker, f"reported_{reported_reason}"

    unreported_candidates = [worker for worker in fresh_workers if not worker.reported]
    unreported_worker, unreported_reason = _find_spatial_worker_match(
        unreported_candidates,
        person,
    )
    if unreported_worker is not None:
        return unreported_worker, f"unreported_{unreported_reason}"

    return None, "new_worker"


def _find_spatial_worker_match(
    workers: list[WorkerState], person: PersonResult
) -> tuple[WorkerState | None, str]:
    best_worker: WorkerState | None = None
    best_iou = 0.0
    for worker in workers:
        iou = _bbox_iou(person.bbox, worker.last_bbox)
        if iou > best_iou:
            best_iou = iou
            best_worker = worker

    if best_worker is not None and best_iou >= settings.VIDEO_CASE_IOU_THRESHOLD:
        return best_worker, "iou"

    for worker in workers:
        if (
            _center_distance_ratio(person.bbox, worker.last_bbox)
            <= settings.VIDEO_CASE_CENTER_DISTANCE_RATIO
        ):
            return worker, "center"

    return None, "no_spatial_match"


def _merge_worker_observation(
    worker: WorkerState, person: PersonResult, frame_index: int, fps: float
) -> None:
    effective_fps = fps if fps > 0 else 30.0
    gap_seconds = (frame_index - worker.last_frame) / effective_fps
    if gap_seconds >= settings.VIDEO_ZONE_REENTRY_GAP_SECONDS:
        # Only reset zones where the person was last seen OUTSIDE the zone.
        # If they were inside when the tracker dropped (e.g. occluded by a sign),
        # keep the zone state so we don't fire a duplicate violation on reappearance.
        for cv_id in list(worker.reported_zones):
            if not worker.zone_last_in.get(cv_id, False):
                worker.reported_zones.discard(cv_id)
                worker.zone_dwell[cv_id] = 0

    if person.track_id is not None:
        worker.track_ids.add(person.track_id)
    worker.last_frame = frame_index
    worker.last_bbox = person.bbox
    worker.recent_bboxes.append(person.bbox)
    max_window = max(2, settings.VIDEO_STABILITY_WINDOW_FRAMES)
    if len(worker.recent_bboxes) > max_window:
        worker.recent_bboxes = worker.recent_bboxes[-max_window:]


def _is_worker_judgeable(
    worker: WorkerState,
    frame_index: int,
    fps: float,
    frame_width: int,
    frame_height: int,
) -> bool:
    grace_frames = _seconds_to_frames(settings.VIDEO_NEW_TRACK_GRACE_SECONDS, fps)
    if frame_index - worker.first_frame < grace_frames:
        return False

    # Edge margin: use the configured ratio so corner-entry workers aren't judged too early
    margin_x = frame_width * settings.VIDEO_EDGE_MARGIN_RATIO
    margin_y = frame_height * settings.VIDEO_EDGE_MARGIN_RATIO
    if (worker.last_bbox.x1 <= margin_x or worker.last_bbox.y1 <= margin_y or
        worker.last_bbox.x2 >= frame_width - margin_x or worker.last_bbox.y2 >= frame_height - margin_y):
        return False

    # Height check: tiny person allowed (2%)
    if _bbox_height_ratio(worker.last_bbox, frame_height) < 0.02:
        return False

    # Stability check: only need 2 frames
    if len(worker.recent_bboxes) < 2:
        return False
    if _has_unclear_posture(worker):
        return False

    return True

def _unknown_reason(
    worker: WorkerState,
    frame_index: int,
    fps: float,
    frame_width: int,
    frame_height: int,
) -> str:
    grace_frames = _seconds_to_frames(settings.VIDEO_NEW_TRACK_GRACE_SECONDS, fps)
    if frame_index - worker.first_frame < grace_frames:
        return f"grace_period_failed({frame_index - worker.first_frame}<{grace_frames})"

    margin_x = frame_width * settings.VIDEO_EDGE_MARGIN_RATIO
    margin_y = frame_height * settings.VIDEO_EDGE_MARGIN_RATIO
    if (worker.last_bbox.x1 <= margin_x or worker.last_bbox.y1 <= margin_y or
        worker.last_bbox.x2 >= frame_width - margin_x or worker.last_bbox.y2 >= frame_height - margin_y):
        return "near_edge"

    if _bbox_height_ratio(worker.last_bbox, frame_height) < 0.02:
        return "too_small"

    if len(worker.recent_bboxes) < 2:
        return "need_more_frames"
    if _has_unclear_posture(worker):
        return "unclear_posture"

    return "unknown"


def _has_unclear_posture(worker: WorkerState) -> bool:
    current_bbox = worker.last_bbox
    if _bbox_aspect_ratio(current_bbox) < settings.VIDEO_MIN_CLEAR_PERSON_ASPECT_RATIO:
        return True

    recent_heights = [_bbox_height(box) for box in worker.recent_bboxes[:-1]]
    history_frames = max(0, settings.VIDEO_POSTURE_HISTORY_MIN_FRAMES)
    if len(recent_heights) >= history_frames and history_frames > 0:
        median_height = statistics.median(recent_heights)
        if (
            _bbox_height(current_bbox)
            < median_height * settings.VIDEO_POSTURE_HEIGHT_DROP_RATIO
        ):
            return True

    return False


def _suppress_recently_seen_ppe(
    worker: WorkerState,
    missing: list[str],
    frame_index: int,
    fps: float,
) -> list[str]:
    memory_frames = _seconds_to_frames(settings.VIDEO_RECENT_PPE_MEMORY_SECONDS, fps)
    filtered: list[str] = []
    for label in missing:
        seen_frame = _last_seen_frame(worker, label)
        if seen_frame is not None and frame_index - seen_frame <= memory_frames:
            continue
        filtered.append(label)
    return filtered


def _present_equipment(person: PersonResult) -> set[str]:
    return {eq.label for eq in person.equipment if eq.status == "compliant"}


def _update_worker_role(worker: WorkerState, person: PersonResult) -> None:
    if person.role is None:
        return
    worker.role = person.role
    worker.uniform_type = person.uniform_type


def _apply_worker_role_to_person(worker: WorkerState, person: PersonResult) -> None:
    if person.role is not None or worker.role is None:
        return

    helmet_status = _equipment_status_for_label(person, HELMET_LABEL)
    if worker.role == "janitor":
        uniform_status = _equipment_status_for_label(person, CLEANING_COVERALL_LABEL)
        person.role = "janitor"
        person.uniform_type = "cleaning_coverall"
    else:
        uniform_status = _equipment_status_for_label(person, VEST_LABEL)
        person.role = "worker"
        person.uniform_type = "vest"

    person.equipment = [helmet_status, uniform_status]
    person.compliant = all(eq.status == "compliant" for eq in person.equipment)


def _equipment_status_for_label(person: PersonResult, label: str) -> EquipmentStatus:
    for equipment in person.equipment:
        if equipment.label == label and equipment.status == "compliant":
            return equipment
    return EquipmentStatus(label=label, status="violation")


def _relevant_missing_labels(person: PersonResult) -> tuple[str, ...]:
    return tuple(equipment.label for equipment in person.equipment)


def _last_seen_frame(worker: WorkerState, label: str) -> int | None:
    if label == HELMET_LABEL:
        return worker.helmet_seen_frame
    if label == VEST_LABEL:
        return worker.vest_seen_frame
    if label == CLEANING_COVERALL_LABEL:
        return worker.cleaning_coverall_seen_frame
    return None


def _empty_missing_counts() -> dict[str, int]:
    return {label: 0 for label in MISSING_LABEL_ORDER}


def _reset_missing_counts(worker: WorkerState) -> None:
    worker.missing_counts = _empty_missing_counts()


def _seconds_to_frames(seconds: float, fps: float) -> int:
    effective_fps = fps if fps > 0 else 30.0
    return max(1, math.ceil(seconds * effective_fps))


def _ordered_missing(missing: set[str]) -> list[str]:
    return [label for label in MISSING_LABEL_ORDER if label in missing]


def _format_track_ids(track_ids: set[int]) -> str:
    if not track_ids:
        return "-"
    return ",".join(str(track_id) for track_id in sorted(track_ids))


def _missing_equipment(person: PersonResult) -> list[str]:
    return [eq.label for eq in person.equipment if eq.status == "violation"]
