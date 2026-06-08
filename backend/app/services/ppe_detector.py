import logging
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import (
    BoundingBox,
    Detection,
    DetectionResponse,
    EquipmentStatus,
    PersonResult,
    Summary,
    TrackingOverlay,
    TrackingOverlayFrame,
    VideoProcessingResponse,
    VideoSummary,
)
from app.schemas.violation import (
    ViolationReport,
    ZoneViolation,
)
from app.services.ppe_violation_service import open_ppe_violation_service
from app.services.zone_service import (
    COORD_SCALE,
    load_zones,
    get_person_foot_point,
    check_zone_incursion,
    record_zone_violation,
)
from app.storage.local_paths import SNAPSHOT_DIR

logger = logging.getLogger(__name__)

COMPLIANT_COLOR = "#22c55e"
VIOLATION_COLOR = "#ef4444"
PERSON_COLOR = "#f97316"


@dataclass
class ViolationCase:
    report: ViolationReport
    missing: set[str]
    track_ids: set[int]
    last_bbox: BoundingBox
    first_frame: int
    last_frame: int


@dataclass
class WorkerState:
    track_ids: set[int]
    first_frame: int
    last_frame: int
    last_bbox: BoundingBox
    recent_bboxes: list[BoundingBox]
    helmet_seen_frame: int | None = None
    vest_seen_frame: int | None = None
    missing_counts: dict[str, int] | None = None
    reported_missing: set[str] | None = None
    reported: bool = False
    status: str = "unknown"
    zone_dwell: dict[int, float] | None = None  # camera_zone_view_id -> seconds
    reported_zones: set[int] | None = None  # camera_zone_view_ids

    def __post_init__(self) -> None:
        if self.missing_counts is None:
            self.missing_counts = {"Helmet": 0, "Vest": 0}
        if self.reported_missing is None:
            self.reported_missing = set()
        if self.zone_dwell is None:
            self.zone_dwell = {}
        if self.reported_zones is None:
            self.reported_zones = set()


def _area(b: dict) -> float:
    return max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])


def _inter_area(a: dict, b: dict) -> float:
    x_a = max(a["x1"], b["x1"])
    y_a = max(a["y1"], b["y1"])
    x_b = min(a["x2"], b["x2"])
    y_b = min(a["y2"], b["y2"])
    return max(0.0, x_b - x_a) * max(0.0, y_b - y_a)


def _overlap_ratio(equipment: dict, person: dict) -> float:
    eq_area = _area(equipment)
    if eq_area == 0:
        return 0.0
    return _inter_area(equipment, person) / eq_area


def _select_inference_device(preferred_device: str) -> str:
    requested = (preferred_device or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"

    if requested not in {"auto", "cuda", "gpu"} and not (
        requested.startswith("cuda:") or requested.isdigit()
    ):
        return "cpu"

    try:
        import torch
    except Exception:
        return "cpu"

    if not torch.cuda.is_available():
        return "cpu"

    device_count = torch.cuda.device_count()
    if requested.isdigit():
        device_index = int(requested)
    elif requested.startswith("cuda:"):
        try:
            device_index = int(requested.split(":", 1)[1])
        except ValueError:
            device_index = 0
    else:
        device_index = 0

    if device_index >= device_count:
        return "cpu"

    device = f"cuda:{device_index}"
    return device


class PPEDetector:
    def __init__(self) -> None:
        self.model = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self._load_model()

    def _load_model(self) -> None:
        model_path = Path(settings.MODEL_PATH).expanduser()
        if not model_path.is_absolute():
            model_path = BACKEND_DIR / model_path
        model_path = model_path.resolve()

        if not model_path.exists():
            return

        try:
            from ultralytics import YOLO

            self.model = YOLO(str(model_path))
        except Exception:
            pass

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return self._mock_predict(image)
        return self._real_predict(image)

    def process_video(
        self, video_path: Path, video_name: str
    ) -> VideoProcessingResponse:
        if self.model is None:
            return self._mock_process_video(video_path, video_name)
        return self._real_process_video(video_path, video_name)

    def _real_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        results = self.model(
            image,
            conf=settings.CONFIDENCE_THRESHOLD,
            device=self.device,
            verbose=False,
        )

        persons: list[dict] = []
        helmets: list[dict] = []
        vests: list[dict] = []

        for result in results:
            frame_persons, frame_helmets, frame_vests = _extract_result_boxes(result)
            persons.extend(frame_persons)
            helmets.extend(frame_helmets)
            vests.extend(frame_vests)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)

    def _real_process_video(
        self, video_path: Path, video_name: str
    ) -> VideoProcessingResponse:
        fps, total_frames = _video_metadata(video_path)
        start = time.perf_counter()
        stride = max(1, settings.VIDEO_FRAME_STRIDE)

        cases: list[ViolationCase] = []
        workers: list[WorkerState] = []
        zone_violations_list: list[ZoneViolation] = []
        confirmed_aspect_ratios: list[float] = []
        candidate_violations = 0
        processed_frames = 0
        frame_width: int | None = None
        frame_height: int | None = None
        overlay_frames: list[TrackingOverlayFrame] = []

        # Load zones
        zones = load_zones(video_name)

        results = self.model.track(
            source=str(video_path),
            stream=True,
            persist=True,
            conf=settings.CONFIDENCE_THRESHOLD,
            tracker=settings.VIDEO_TRACKER,
            classes=[0, 1, 2],
            vid_stride=stride,
            device=self.device,
            verbose=False,
        )

        for processed_frames, result in enumerate(results, start=1):
            frame_index = (processed_frames - 1) * stride
            persons, helmets, vests = _extract_result_boxes(result)
            response = _build_response(persons, helmets, vests, 0.0)
            frame = result.orig_img.copy()
            frame_height, frame_width = frame.shape[:2]
            used_worker_ids: set[int] = set()
            overlay_person_ids: set[tuple[str, int]] = set()

            for person in response.persons:
                decision = _update_worker_status(
                    workers=workers,
                    person=person,
                    frame_index=frame_index,
                    fps=fps,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    used_worker_ids=used_worker_ids,
                )
                worker = decision["worker"]
                candidate_violations += int(decision["candidate"])

                # Check Zone Incursions
                track_camera_zone_view_id: int | None = None
                track_physical_zone_id: int | None = None
                track_zone_name: str | None = None
                track_zone_type: str | None = None

                if zones:
                    test_point = get_person_foot_point(
                        person, frame_width, frame_height
                    )
                    incursion_zones = check_zone_incursion(zones, test_point)
                    incursion_camera_zone_view_ids = {
                        z.camera_zone_view_id for z in incursion_zones
                    }

                    for zone in zones:
                        camera_zone_view_id = zone.camera_zone_view_id
                        in_zone = (
                            camera_zone_view_id in incursion_camera_zone_view_ids
                        )

                        if zone.zone_type == "WALKWAY":
                            if in_zone:
                                # Person is safely inside walkway; reset outside-dwell counter.
                                worker.zone_dwell[camera_zone_view_id] = 0
                            else:
                                # Person has left the walkway; accumulate violation dwell.
                                worker.zone_dwell[camera_zone_view_id] = (
                                    worker.zone_dwell.get(camera_zone_view_id, 0)
                                    + (stride / fps)
                                )
                                if (
                                    worker.zone_dwell[camera_zone_view_id]
                                    > zone.threshold
                                    and camera_zone_view_id
                                    not in worker.reported_zones
                                ):
                                    zv = record_zone_violation(
                                        worker_state=worker,
                                        zone=zone,
                                        frame=frame,
                                        person=person,
                                        video_name=video_name,
                                        frame_index=frame_index,
                                        save_snapshot_fn=_save_violation_snapshot,
                                    )
                                    if zv:
                                        zone_violations_list.append(zv)
                        else:
                            # RESTRICTED: violation when person is inside
                            if in_zone:
                                worker.zone_dwell[camera_zone_view_id] = (
                                    worker.zone_dwell.get(camera_zone_view_id, 0)
                                    + (stride / fps)
                                )
                                if (
                                    worker.zone_dwell[camera_zone_view_id]
                                    > zone.threshold
                                    and camera_zone_view_id
                                    not in worker.reported_zones
                                ):
                                    zv = record_zone_violation(
                                        worker_state=worker,
                                        zone=zone,
                                        frame=frame,
                                        person=person,
                                        video_name=video_name,
                                        frame_index=frame_index,
                                        save_snapshot_fn=_save_violation_snapshot,
                                    )
                                    if zv:
                                        zone_violations_list.append(zv)

                    # Determine the most critical zone status for tracking overlay display
                    for zone in incursion_zones:
                        if zone.zone_type == "RESTRICTED":
                            track_camera_zone_view_id = zone.camera_zone_view_id
                            track_physical_zone_id = zone.physical_zone_id
                            track_zone_name = zone.zone_name
                            track_zone_type = "RESTRICTED"
                            break
                    if track_zone_type is None:
                        walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                        if walkway_zones and not any(
                            z.camera_zone_view_id in incursion_camera_zone_view_ids
                            for z in walkway_zones
                        ):
                            wz = walkway_zones[0]
                            track_camera_zone_view_id = wz.camera_zone_view_id
                            track_physical_zone_id = wz.physical_zone_id
                            track_zone_name = wz.zone_name
                            track_zone_type = "WALKWAY"

                _append_tracking_overlay_frame(
                    overlay_frames=overlay_frames,
                    seen_person_ids=overlay_person_ids,
                    person=person,
                    decision=decision,
                    frame_index=frame_index,
                    fps=fps,
                    camera_zone_view_id=track_camera_zone_view_id,
                    physical_zone_id=track_physical_zone_id,
                    zone_name=track_zone_name,
                    zone_type=track_zone_type,
                )

                missing_to_report = decision["missing_to_report"]
                if not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    worker=worker,
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
                    confirmed_aspect_ratios=confirmed_aspect_ratios,
                )

        elapsed_ms = (time.perf_counter() - start) * 1000
        duration_seconds = total_frames / fps if fps > 0 else 0.0
        reports = [case.report for case in cases]

        return VideoProcessingResponse(
            summary=VideoSummary(
                video_name=video_name,
                total_frames=total_frames,
                processed_frames=processed_frames,
                fps=round(fps, 2),
                duration_seconds=round(duration_seconds, 2),
                unique_violations=len(cases),
                candidate_violations=candidate_violations,
                inference_ms=round(elapsed_ms, 2),
            ),
            reports=reports,
            zone_violations=zone_violations_list,
            tracking_overlay=TrackingOverlay(
                fps=round(fps, 2),
                stride=stride,
                frame_width=frame_width,
                frame_height=frame_height,
                frames=overlay_frames,
            ),
        )

    def _mock_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        time.sleep(0.06)

        w, h = image.size

        def px(rel_box: tuple[float, float, float, float]) -> dict:
            rx1, ry1, rx2, ry2 = rel_box
            return {"x1": rx1 * w, "y1": ry1 * h, "x2": rx2 * w, "y2": ry2 * h}

        persons = [
            {**px((0.05, 0.02, 0.40, 0.98)), "conf": 0.96},
            {**px((0.55, 0.04, 0.95, 0.96)), "conf": 0.91},
        ]
        helmets = [{**px((0.10, 0.03, 0.35, 0.20)), "conf": 0.94}]
        vests = [{**px((0.08, 0.22, 0.38, 0.68)), "conf": 0.89}]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)

    def _mock_process_video(
        self, video_path: Path, video_name: str
    ) -> VideoProcessingResponse:
        import cv2

        fps, total_frames = _video_metadata(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError("Could not decode the uploaded video.")

        start = time.perf_counter()
        stride = max(1, settings.VIDEO_FRAME_STRIDE)
        frame_index = 0
        processed_frames = 0
        cases: list[ViolationCase] = []
        workers: list[WorkerState] = []
        confirmed_aspect_ratios: list[float] = []
        candidate_violations = 0
        frame_width: int | None = None
        frame_height: int | None = None
        overlay_frames: list[TrackingOverlayFrame] = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % stride != 0:
                frame_index += 1
                continue

            processed_frames += 1
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            response = self._mock_predict(Image.fromarray(rgb))
            frame_height, frame_width = frame.shape[:2]
            used_worker_ids: set[int] = set()
            overlay_person_ids: set[tuple[str, int]] = set()

            for person in response.persons:
                track_id = person.person_id
                person.track_id = track_id
                decision = _update_worker_status(
                    workers=workers,
                    person=person,
                    frame_index=frame_index,
                    fps=fps,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    used_worker_ids=used_worker_ids,
                )
                candidate_violations += int(decision["candidate"])
                _append_tracking_overlay_frame(
                    overlay_frames=overlay_frames,
                    seen_person_ids=overlay_person_ids,
                    person=person,
                    decision=decision,
                    frame_index=frame_index,
                    fps=fps,
                )
                missing_to_report = decision["missing_to_report"]
                if not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    worker=decision["worker"],
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
                    confirmed_aspect_ratios=confirmed_aspect_ratios,
                )

            frame_index += 1

        cap.release()
        elapsed_ms = (time.perf_counter() - start) * 1000
        duration_seconds = total_frames / fps if fps > 0 else 0.0
        reports = [case.report for case in cases]

        return VideoProcessingResponse(
            summary=VideoSummary(
                video_name=video_name,
                total_frames=total_frames,
                processed_frames=processed_frames,
                fps=round(fps, 2),
                duration_seconds=round(duration_seconds, 2),
                unique_violations=len(cases),
                candidate_violations=candidate_violations,
                inference_ms=round(elapsed_ms, 2),
            ),
            reports=reports,
            tracking_overlay=TrackingOverlay(
                fps=round(fps, 2),
                stride=stride,
                frame_width=frame_width,
                frame_height=frame_height,
                frames=overlay_frames,
            ),
        )


def _append_tracking_overlay_frame(
    *,
    overlay_frames: list[TrackingOverlayFrame],
    seen_person_ids: set[tuple[str, int]],
    person: PersonResult,
    decision: dict,
    frame_index: int,
    fps: float,
    camera_zone_view_id: int | None = None,
    physical_zone_id: int | None = None,
    zone_name: str | None = None,
    zone_type: str | None = None,
) -> None:
    person_key = (
        ("track", person.track_id)
        if person.track_id is not None
        else ("person", person.person_id)
    )
    if person_key in seen_person_ids:
        return
    seen_person_ids.add(person_key)

    missing_equipment = [
        equipment.label
        for equipment in person.equipment
        if equipment.status == "violation"
    ]
    worker = decision.get("worker")
    worker_status = getattr(worker, "status", "unknown")
    if decision.get("unknown") or worker_status == "unknown":
        status = "unknown"
    elif missing_equipment:
        status = "violation"
    elif person.compliant:
        status = "compliant"
    else:
        status = "unknown"

    overlay_frames.append(
        TrackingOverlayFrame(
            frame_index=frame_index,
            time_seconds=frame_index / fps if fps > 0 else 0.0,
            track_id=person.track_id,
            person_id=person.person_id,
            bbox=person.bbox,
            confidence=person.confidence,
            compliant=person.compliant,
            missing_equipment=missing_equipment,
            status=status,
            zone_id=camera_zone_view_id,
            camera_zone_view_id=camera_zone_view_id,
            physical_zone_id=physical_zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
        )
    )


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
    worker = _find_or_create_worker(workers, person, frame_index, used_worker_ids)
    _merge_worker_observation(worker, person, frame_index)

    present = _present_equipment(person)
    if "Helmet" in present:
        worker.helmet_seen_frame = frame_index
    if "Vest" in present:
        worker.vest_seen_frame = frame_index

    if worker.reported:
        worker.status = "violation"
        _reset_missing_counts(worker)
        return {
            "unknown": False,
            "candidate": False,
            "reason": "already_reported",
            "missing": [],
            "worker": worker,
            "missing_to_report": [],
        }

    raw_missing = _missing_equipment(person)
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
            _reset_missing_counts(worker)
        return {
            "unknown": worker.status != "violation",
            "candidate": False,
            "reason": reason,
            "missing": raw_missing,
            "worker": worker,
            "missing_to_report": [],
        }

    missing = _suppress_recently_seen_ppe(worker, raw_missing, frame_index, fps)
    if worker.reported_missing:
        missing = [label for label in missing if label not in worker.reported_missing]
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
            "missing_to_report": [],
        }

    confirm_frames = _seconds_to_frames(settings.VIDEO_VIOLATION_CONFIRM_SECONDS, fps)
    for label in ("Helmet", "Vest"):
        if worker.missing_counts is None:
            worker.missing_counts = {"Helmet": 0, "Vest": 0}
        worker.missing_counts[label] = (
            worker.missing_counts.get(label, 0) + 1 if label in missing else 0
        )

    confirmed_missing = [
        label
        for label in ("Helmet", "Vest")
        if (
            worker.missing_counts
            and worker.missing_counts.get(label, 0) >= confirm_frames
            and worker.reported_missing is not None
            and label not in worker.reported_missing
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
        "missing_to_report": _ordered_missing(worker.reported_missing),
    }


def _find_or_create_worker(
    workers: list[WorkerState],
    person: PersonResult,
    frame_index: int,
    used_worker_ids: set[int],
) -> WorkerState:
    worker = _find_existing_worker(workers, person, frame_index, used_worker_ids)
    if worker is not None:
        used_worker_ids.add(id(worker))
        return worker

    worker = WorkerState(
        track_ids={person.track_id} if person.track_id is not None else set(),
        first_frame=frame_index,
        last_frame=frame_index,
        last_bbox=person.bbox,
        recent_bboxes=[],
    )
    workers.append(worker)
    used_worker_ids.add(id(worker))
    return worker


def _find_existing_worker(
    workers: list[WorkerState],
    person: PersonResult,
    frame_index: int,
    used_worker_ids: set[int],
) -> WorkerState | None:
    if person.track_id is not None:
        for worker in workers:
            if id(worker) in used_worker_ids:
                continue
            if person.track_id in worker.track_ids:
                return worker

    fresh_workers = [
        worker
        for worker in workers
        if id(worker) not in used_worker_ids
        and frame_index - worker.last_frame <= settings.VIDEO_CASE_MAX_FRAME_GAP
    ]

    reported_candidates = [worker for worker in fresh_workers if worker.reported]
    reported_worker = _find_spatial_worker_match(
        reported_candidates,
        person,
    )
    if reported_worker is not None:
        return reported_worker

    unreported_candidates = [worker for worker in fresh_workers if not worker.reported]
    unreported_worker = _find_spatial_worker_match(unreported_candidates, person)
    if unreported_worker is not None:
        return unreported_worker

    return None


def _find_spatial_worker_match(
    workers: list[WorkerState], person: PersonResult
) -> WorkerState | None:
    best_worker: WorkerState | None = None
    best_iou = 0.0
    for worker in workers:
        iou = _bbox_iou(person.bbox, worker.last_bbox)
        if iou > best_iou:
            best_iou = iou
            best_worker = worker

    if best_worker is not None and best_iou >= settings.VIDEO_CASE_IOU_THRESHOLD:
        return best_worker

    for worker in workers:
        if (
            _center_distance_ratio(person.bbox, worker.last_bbox)
            <= settings.VIDEO_CASE_CENTER_DISTANCE_RATIO
        ):
            return worker

    return None


def _merge_worker_observation(
    worker: WorkerState, person: PersonResult, frame_index: int
) -> None:
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
    if _is_near_frame_edge(worker.last_bbox, frame_width, frame_height):
        return False
    if (
        _bbox_height_ratio(worker.last_bbox, frame_height)
        < settings.VIDEO_MIN_PERSON_HEIGHT_RATIO
    ):
        return False
    if not _is_bbox_stable(worker.recent_bboxes):
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
        return "new_track_grace"
    if _is_near_frame_edge(worker.last_bbox, frame_width, frame_height):
        return "near_frame_edge"
    if (
        _bbox_height_ratio(worker.last_bbox, frame_height)
        < settings.VIDEO_MIN_PERSON_HEIGHT_RATIO
    ):
        return "person_too_small"
    if not _is_bbox_stable(worker.recent_bboxes):
        return "unstable_bbox"
    if _has_unclear_posture(worker):
        return "unclear_posture"
    return "not_judgeable"


def _is_near_frame_edge(box: BoundingBox, frame_width: int, frame_height: int) -> bool:
    margin_x = frame_width * settings.VIDEO_EDGE_MARGIN_RATIO
    margin_y = frame_height * settings.VIDEO_EDGE_MARGIN_RATIO
    return (
        box.x1 <= margin_x
        or box.y1 <= margin_y
        or box.x2 >= frame_width - margin_x
        or box.y2 >= frame_height - margin_y
    )


def _bbox_height_ratio(box: BoundingBox, frame_height: int) -> float:
    if frame_height <= 0:
        return 0.0
    return max(0.0, box.y2 - box.y1) / frame_height


def _is_bbox_stable(boxes: list[BoundingBox]) -> bool:
    window = max(2, settings.VIDEO_STABILITY_WINDOW_FRAMES)
    if len(boxes) < window:
        return False

    first = boxes[0]
    last = boxes[-1]
    if _center_distance_ratio(first, last) > settings.VIDEO_MAX_CENTER_SHIFT_RATIO:
        return False

    first_area = max(_area(first.model_dump()), 1.0)
    last_area = max(_area(last.model_dump()), 1.0)
    size_change = abs(last_area - first_area) / first_area
    return size_change <= settings.VIDEO_MAX_SIZE_CHANGE_RATIO


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
        seen_frame = (
            worker.helmet_seen_frame if label == "Helmet" else worker.vest_seen_frame
        )
        if seen_frame is not None and frame_index - seen_frame <= memory_frames:
            continue
        filtered.append(label)
    return filtered


def _present_equipment(person: PersonResult) -> set[str]:
    return {eq.label for eq in person.equipment if eq.status == "compliant"}


def _reset_missing_counts(worker: WorkerState) -> None:
    worker.missing_counts = {"Helmet": 0, "Vest": 0}


def _seconds_to_frames(seconds: float, fps: float) -> int:
    effective_fps = fps if fps > 0 else 30.0
    return max(1, math.ceil(seconds * effective_fps))


def _record_violation_case(
    *,
    cases: list[ViolationCase],
    frame,
    person: PersonResult,
    worker: WorkerState,
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


def _bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    a_dict = a.model_dump()
    b_dict = b.model_dump()
    union = _area(a_dict) + _area(b_dict) - _inter_area(a_dict, b_dict)
    if union <= 0:
        return 0.0
    return _inter_area(a_dict, b_dict) / union


def _center_distance_ratio(a: BoundingBox, b: BoundingBox) -> float:
    ax = (a.x1 + a.x2) / 2
    ay = (a.y1 + a.y2) / 2
    bx = (b.x1 + b.x2) / 2
    by = (b.y1 + b.y2) / 2
    distance = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
    diagonal = max(_bbox_diagonal(a), _bbox_diagonal(b), 1.0)
    return distance / diagonal


def _bbox_width(box: BoundingBox) -> float:
    return max(0.0, box.x2 - box.x1)


def _bbox_height(box: BoundingBox) -> float:
    return max(0.0, box.y2 - box.y1)


def _bbox_aspect_ratio(box: BoundingBox) -> float:
    width = max(_bbox_width(box), 1.0)
    return _bbox_height(box) / width


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
    worker: WorkerState,
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


def _bbox_diagonal(box: BoundingBox) -> float:
    return ((box.x2 - box.x1) ** 2 + (box.y2 - box.y1) ** 2) ** 0.5


def _ordered_missing(missing: set[str]) -> list[str]:
    return [label for label in ("Helmet", "Vest") if label in missing]


def _extract_result_boxes(result) -> tuple[list[dict], list[dict], list[dict]]:
    persons: list[dict] = []
    helmets: list[dict] = []
    vests: list[dict] = []

    if result.boxes is None:
        return persons, helmets, vests

    for box in result.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        entry = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": conf}

        if cls_id == 0:
            track_id = _box_track_id(box)
            if track_id is not None:
                entry["track_id"] = track_id
            persons.append(entry)
        elif cls_id == 1:
            helmets.append(entry)
        elif cls_id == 2:
            vests.append(entry)

    return persons, helmets, vests


def _box_track_id(box) -> int | None:
    box_id = getattr(box, "id", None)
    if box_id is None:
        return None
    try:
        return int(box_id[0])
    except Exception:
        return None


def _build_response(
    persons: list[dict],
    helmets: list[dict],
    vests: list[dict],
    elapsed_ms: float,
) -> DetectionResponse:
    threshold = settings.PPE_OVERLAP_THRESHOLD

    def best_match(equip_list: list[dict]) -> list[int | None]:
        assignments: list[int | None] = []
        for eq in equip_list:
            best_idx: int | None = None
            best_ratio = threshold
            for i, p in enumerate(persons):
                ratio = _overlap_ratio(eq, p)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = i
            assignments.append(best_idx)
        return assignments

    helmet_assignments = best_match(helmets)
    vest_assignments = best_match(vests)

    person_helmets = _best_equipment_by_person(helmets, helmet_assignments)
    person_vests = _best_equipment_by_person(vests, vest_assignments)

    person_results: list[PersonResult] = []
    flat_detections: list[Detection] = []
    det_id = 0

    for i, p in enumerate(persons):
        p_bbox = BoundingBox(x1=p["x1"], y1=p["y1"], x2=p["x2"], y2=p["y2"])
        label_suffix = f"T{p['track_id']}" if "track_id" in p else f"{i + 1}"
        flat_detections.append(
            Detection(
                id=det_id,
                label=f"P{label_suffix}",
                category="compliant",
                confidence=round(p["conf"], 4),
                bbox=p_bbox,
                color=PERSON_COLOR,
            )
        )
        det_id += 1

        equipment_statuses: list[EquipmentStatus] = []
        det_id = _append_equipment_status(
            equipment_statuses,
            flat_detections,
            det_id,
            "Helmet",
            person_helmets.get(i),
        )
        det_id = _append_equipment_status(
            equipment_statuses,
            flat_detections,
            det_id,
            "Vest",
            person_vests.get(i),
        )

        is_compliant = all(eq.status == "compliant" for eq in equipment_statuses)
        person_results.append(
            PersonResult(
                person_id=i + 1,
                track_id=p.get("track_id"),
                bbox=p_bbox,
                confidence=round(p["conf"], 4),
                equipment=equipment_statuses,
                compliant=is_compliant,
            )
        )

    compliant_count = sum(1 for pr in person_results if pr.compliant)
    violation_count = len(person_results) - compliant_count

    return DetectionResponse(
        detections=flat_detections,
        persons=person_results,
        summary=Summary(
            total_persons=len(person_results),
            compliant=compliant_count,
            violations=violation_count,
            inference_ms=round(elapsed_ms, 2),
        ),
    )


def _best_equipment_by_person(
    equipment: list[dict], assignments: list[int | None]
) -> dict[int, dict]:
    best: dict[int, dict] = {}
    for eq, person_idx in zip(equipment, assignments):
        if person_idx is None:
            continue
        if person_idx not in best or best[person_idx]["conf"] < eq["conf"]:
            best[person_idx] = eq
    return best


def _append_equipment_status(
    statuses: list[EquipmentStatus],
    detections: list[Detection],
    det_id: int,
    label: str,
    equipment: dict | None,
) -> int:
    if equipment is None:
        statuses.append(EquipmentStatus(label=label, status="violation"))
        return det_id

    bbox = BoundingBox(
        x1=equipment["x1"],
        y1=equipment["y1"],
        x2=equipment["x2"],
        y2=equipment["y2"],
    )
    detections.append(
        Detection(
            id=det_id,
            label=label,
            category="compliant",
            confidence=round(equipment["conf"], 4),
            bbox=bbox,
            color=COMPLIANT_COLOR,
        )
    )
    statuses.append(
        EquipmentStatus(
            label=label,
            status="compliant",
            confidence=round(equipment["conf"], 4),
            bbox=bbox,
        )
    )
    return det_id + 1


def _append_unmatched_equipment(
    detections: list[Detection],
    det_id: int,
    equipment: list[dict],
    assignments: list[int | None],
    label: str,
) -> int:
    matched = {
        id(eq)
        for eq, person_idx in zip(equipment, assignments)
        if person_idx is not None
    }
    for eq in equipment:
        if id(eq) in matched:
            continue
        detections.append(
            Detection(
                id=det_id,
                label=f"{label} (unassigned)",
                category="compliant",
                confidence=round(eq["conf"], 4),
                bbox=BoundingBox(x1=eq["x1"], y1=eq["y1"], x2=eq["x2"], y2=eq["y2"]),
                color=COMPLIANT_COLOR,
            )
        )
        det_id += 1
    return det_id


def _missing_equipment(person: PersonResult) -> list[str]:
    return [eq.label for eq in person.equipment if eq.status == "violation"]


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


def _video_metadata(video_path: Path) -> tuple[float, int]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not decode the uploaded video.")

    # Try to read the first frame to ensure the codec is supported
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise ValueError(
            "Video file opened but frames could not be read. The codec might be unsupported by the server."
        )

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return fps, total_frames
