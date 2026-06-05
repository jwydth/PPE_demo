import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.models.schemas import (
    BoundingBox,
    Detection,
    DetectionResponse,
    EquipmentStatus,
    PersonResult,
    Summary,
    VideoProcessingResponse,
    VideoSummary,
    ViolationReport,
)
from app.services.violation_store import SNAPSHOT_DIR, save_violation, update_violation

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
    status: str = "unknown"

    def __post_init__(self) -> None:
        if self.missing_counts is None:
            self.missing_counts = {"Helmet": 0, "Vest": 0}
        if self.reported_missing is None:
            self.reported_missing = set()


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


class PPEDetector:
    def __init__(self) -> None:
        self.model = None
        self._load_model()

    def _load_model(self) -> None:
        model_path = Path(settings.MODEL_PATH).expanduser()
        if not model_path.is_absolute():
            model_path = BACKEND_DIR / model_path
        model_path = model_path.resolve()

        if not model_path.exists():
            logger.warning(
                "Model weights not found at '%s'. Running in mock mode.",
                model_path,
            )
            return

        try:
            from ultralytics import YOLO

            self.model = YOLO(str(model_path))
            logger.info("YOLO model loaded from '%s'", model_path)
        except Exception as exc:
            logger.error("Failed to load model from '%s': %s", model_path, exc)
            logger.warning("Falling back to mock mode.")

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return self._mock_predict(image)
        return self._real_predict(image)

    def process_video(self, video_path: Path, video_name: str) -> VideoProcessingResponse:
        if self.model is None:
            return self._mock_process_video(video_path, video_name)
        return self._real_process_video(video_path, video_name)

    def _real_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        results = self.model(image, conf=settings.CONFIDENCE_THRESHOLD, verbose=False)

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

    def _real_process_video(self, video_path: Path, video_name: str) -> VideoProcessingResponse:
        fps, total_frames = _video_metadata(video_path)
        start = time.perf_counter()
        stride = max(1, settings.VIDEO_FRAME_STRIDE)

        cases: list[ViolationCase] = []
        workers: list[WorkerState] = []
        candidate_violations = 0
        processed_frames = 0

        results = self.model.track(
            source=str(video_path),
            stream=True,
            persist=True,
            conf=settings.CONFIDENCE_THRESHOLD,
            tracker=settings.VIDEO_TRACKER,
            classes=[0, 1, 2],
            vid_stride=stride,
            verbose=False,
        )

        for processed_frames, result in enumerate(results, start=1):
            frame_index = (processed_frames - 1) * stride
            persons, helmets, vests = _extract_result_boxes(result)
            response = _build_response(persons, helmets, vests, 0.0)
            frame = result.orig_img.copy()
            frame_height, frame_width = frame.shape[:2]
            used_worker_ids: set[int] = set()

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
                candidate_violations += int(decision["candidate"])
                missing_to_report = decision["missing_to_report"]
                if not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
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

    def _mock_process_video(self, video_path: Path, video_name: str) -> VideoProcessingResponse:
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
        candidate_violations = 0

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
                missing_to_report = decision["missing_to_report"]
                if not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
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

    raw_missing = _missing_equipment(person)
    judgeable = _is_worker_judgeable(worker, frame_index, fps, frame_width, frame_height)
    reason = "" if judgeable else _unknown_reason(worker, frame_index, fps, frame_width, frame_height)

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
        worker.missing_counts[label] = worker.missing_counts.get(label, 0) + 1 if label in missing else 0

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
        return None

    fresh_workers = [
        worker
        for worker in workers
        if id(worker) not in used_worker_ids
        and frame_index - worker.last_frame <= settings.VIDEO_CASE_MAX_FRAME_GAP
    ]

    best_worker: WorkerState | None = None
    best_iou = 0.0
    for worker in fresh_workers:
        iou = _bbox_iou(person.bbox, worker.last_bbox)
        if iou > best_iou:
            best_iou = iou
            best_worker = worker

    if best_worker is not None and best_iou >= settings.VIDEO_CASE_IOU_THRESHOLD:
        return best_worker

    for worker in fresh_workers:
        if _center_distance_ratio(person.bbox, worker.last_bbox) <= settings.VIDEO_CASE_CENTER_DISTANCE_RATIO:
            return worker

    return None


def _merge_worker_observation(worker: WorkerState, person: PersonResult, frame_index: int) -> None:
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
    if _bbox_height_ratio(worker.last_bbox, frame_height) < settings.VIDEO_MIN_PERSON_HEIGHT_RATIO:
        return False
    if not _is_bbox_stable(worker.recent_bboxes):
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
    if _bbox_height_ratio(worker.last_bbox, frame_height) < settings.VIDEO_MIN_PERSON_HEIGHT_RATIO:
        return "person_too_small"
    if not _is_bbox_stable(worker.recent_bboxes):
        return "unstable_bbox"
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


def _suppress_recently_seen_ppe(
    worker: WorkerState,
    missing: list[str],
    frame_index: int,
    fps: float,
) -> list[str]:
    memory_frames = _seconds_to_frames(settings.VIDEO_RECENT_PPE_MEMORY_SECONDS, fps)
    filtered: list[str] = []
    for label in missing:
        seen_frame = worker.helmet_seen_frame if label == "Helmet" else worker.vest_seen_frame
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
    missing: list[str],
    video_name: str,
    frame_index: int,
) -> None:
    case = _find_existing_case(cases, person, frame_index)
    missing_set = set(missing)

    if case is None:
        snapshot_filename = _save_violation_snapshot(
            frame=frame,
            person=person,
            missing=missing,
            video_stem=Path(video_name).stem,
            frame_index=frame_index,
        )
        report = save_violation(
            timestamp=datetime.now(timezone.utc).isoformat(),
            violation_type=_violation_type(missing),
            details=_violation_details(person, missing, frame_index),
            snapshot_filename=snapshot_filename,
            video_name=video_name,
            frame_index=frame_index,
            track_id=person.track_id,
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
        return

    case.last_bbox = person.bbox
    case.last_frame = frame_index
    if person.track_id is not None:
        case.track_ids.add(person.track_id)

    merged_missing = case.missing | missing_set
    if merged_missing == case.missing:
        return

    case.missing = merged_missing
    merged = _ordered_missing(case.missing)
    case.report = update_violation(
        report_id=case.report.id,
        violation_type=_violation_type(merged),
        details=_case_violation_details(case, merged),
        frame_index=case.first_frame,
        track_id=_primary_track_id(case),
    )


def _find_existing_case(
    cases: list[ViolationCase],
    person: PersonResult,
    frame_index: int,
) -> ViolationCase | None:
    if person.track_id is not None:
        for case in cases:
            if person.track_id in case.track_ids:
                return case

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
        return best_case

    for case in fresh_cases:
        if _center_distance_ratio(person.bbox, case.last_bbox) <= settings.VIDEO_CASE_CENTER_DISTANCE_RATIO:
            return case

    return None


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


def _bbox_diagonal(box: BoundingBox) -> float:
    return ((box.x2 - box.x1) ** 2 + (box.y2 - box.y1) ** 2) ** 0.5


def _ordered_missing(missing: set[str]) -> list[str]:
    return [label for label in ("Helmet", "Vest") if label in missing]


def _primary_track_id(case: ViolationCase) -> int | None:
    if not case.track_ids:
        return None
    return sorted(case.track_ids)[0]


def _case_violation_details(case: ViolationCase, missing: list[str]) -> str:
    track_text = ", ".join(str(track_id) for track_id in sorted(case.track_ids))
    subject = f"tracks {track_text}" if track_text else "person"
    return (
        f"{subject} counted as one worker violation case; "
        f"missing {', '.join(missing)} first detected at frame {case.first_frame}"
    )


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
        label_suffix = f" T{p['track_id']}" if "track_id" in p else f" {i + 1}"
        flat_detections.append(
            Detection(
                id=det_id,
                label=f"Person{label_suffix}",
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

    det_id = _append_unmatched_equipment(flat_detections, det_id, helmets, helmet_assignments, "Helmet")
    _append_unmatched_equipment(flat_detections, det_id, vests, vest_assignments, "Vest")

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


def _best_equipment_by_person(equipment: list[dict], assignments: list[int | None]) -> dict[int, dict]:
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
    matched = {id(eq) for eq, person_idx in zip(equipment, assignments) if person_idx is not None}
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


def _violation_details(person: PersonResult, missing: list[str], frame_index: int) -> str:
    subject = f"track {person.track_id}" if person.track_id is not None else f"person {person.person_id}"
    return f"{subject} missing {', '.join(missing)} at frame {frame_index}"


def _save_violation_snapshot(
    *,
    frame,
    person: PersonResult,
    missing: list[str],
    video_stem: str,
    frame_index: int,
) -> str:
    import cv2

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in video_stem)
    track_label = person.track_id if person.track_id is not None else person.person_id
    filename = f"{safe_stem}_frame_{frame_index}_track_{track_label}_{int(time.time() * 1000)}.jpg"
    path = SNAPSHOT_DIR / filename

    x1 = int(max(0, person.bbox.x1))
    y1 = int(max(0, person.bbox.y1))
    x2 = int(max(0, person.bbox.x2))
    y2 = int(max(0, person.bbox.y2))
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)
    cv2.putText(
        frame,
        f"Violation: {', '.join(missing)}",
        (x1, max(24, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 0, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(path), frame)
    return filename


def _video_metadata(video_path: Path) -> tuple[float, int]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not decode the uploaded video.")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return fps, total_frames
