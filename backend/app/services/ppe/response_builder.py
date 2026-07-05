"""Response/overlay assembly, frame encoding, and video metadata."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.config import settings
from app.schemas.detection import (
    BoundingBox,
    Detection,
    DetectionResponse,
    EquipmentStatus,
    PersonResult,
    Summary,
    TrackingOverlayFrame,
)

from app.services.ppe.constants import (
    CLEANING_COVERALL_LABEL,
    COMPLIANT_COLOR,
    HELMET_LABEL,
    PERSON_COLOR,
    ROLE_UNIFORM_LABEL,
    VEST_LABEL,
)
from app.services.ppe.geometry import _overlap_ratio


def _append_tracking_overlay_frame(
    *,
    include_ppe: bool = True,
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

    missing_equipment = []
    if include_ppe:
        missing_equipment = decision.get("missing_to_report") or [
            equipment.label
            for equipment in person.equipment
            if equipment.status == "violation"
        ]
    has_zone_violation = zone_type in {"RESTRICTED", "WALKWAY", "SLIPPERY"}
    worker = decision.get("worker")
    worker_status = getattr(worker, "status", "unknown")
    if missing_equipment or has_zone_violation:
        status = "violation"
    elif decision.get("unknown") or worker_status == "unknown":
        status = "unknown"
    elif person.compliant or not include_ppe:
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
            role=person.role,
            uniform_type=person.uniform_type,
            compliant=person.compliant and not has_zone_violation,
            missing_equipment=missing_equipment,
            status=status,
            zone_id=camera_zone_view_id,
            camera_zone_view_id=camera_zone_view_id,
            physical_zone_id=physical_zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
        )
    )


def _encode_frame_to_base64(frame: np.ndarray) -> str:
    import cv2
    import base64

    # Resize to 640px width while maintaining aspect ratio to reduce payload size
    h, w = frame.shape[:2]
    target_w = 640
    if w > target_w:
        target_h = int(h * (target_w / w))
        display_frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
    else:
        display_frame = frame

    # Lower JPEG quality (e.g., 60) to significantly reduce string size
    _, buffer = cv2.imencode(".jpg", display_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    return base64.b64encode(buffer).decode("utf-8")


def _box_track_id(box) -> int | None:
    box_id = getattr(box, "id", None)
    if box_id is None:
        return None
    try:
        return int(box_id[0])
    except Exception:
        return None


def _extract_result_boxes(result) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    persons: list[dict] = []
    helmets: list[dict] = []
    vests: list[dict] = []
    cleaning_coveralls: list[dict] = []

    if result.boxes is None:
        return persons, helmets, vests, cleaning_coveralls

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
        elif cls_id == 3:
            cleaning_coveralls.append(entry)

    return persons, helmets, vests, cleaning_coveralls


def _build_response(
    persons: list[dict],
    helmets: list[dict],
    vests: list[dict],
    cleaning_coveralls: list[dict],
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
    cleaning_coverall_assignments = best_match(cleaning_coveralls)

    person_helmets = _best_equipment_by_person(helmets, helmet_assignments)
    person_vests = _best_equipment_by_person(vests, vest_assignments)
    person_cleaning_coveralls = _best_equipment_by_person(
        cleaning_coveralls,
        cleaning_coverall_assignments,
    )

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

        role, uniform_type = _infer_role(
            vest=person_vests.get(i),
            cleaning_coverall=person_cleaning_coveralls.get(i),
        )
        equipment_statuses, det_id = _build_equipment_statuses_for_role(
            flat_detections,
            det_id,
            role=role,
            helmet=person_helmets.get(i),
            vest=person_vests.get(i),
            cleaning_coverall=person_cleaning_coveralls.get(i),
        )

        is_compliant = role is not None and all(
            eq.status == "compliant" for eq in equipment_statuses
        )
        person_results.append(
            PersonResult(
                person_id=i + 1,
                track_id=p.get("track_id"),
                bbox=p_bbox,
                confidence=round(p["conf"], 4),
                role=role,
                uniform_type=uniform_type,
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


def _infer_role(
    *,
    vest: dict | None,
    cleaning_coverall: dict | None,
) -> tuple[str | None, str | None]:
    if cleaning_coverall is not None:
        return "janitor", "cleaning_coverall"
    if vest is not None:
        return "worker", "vest"
    return None, None


def _build_equipment_statuses_for_role(
    detections: list[Detection],
    det_id: int,
    *,
    role: str | None,
    helmet: dict | None,
    vest: dict | None,
    cleaning_coverall: dict | None,
) -> tuple[list[EquipmentStatus], int]:
    statuses: list[EquipmentStatus] = []
    det_id = _append_equipment_status(
        statuses,
        detections,
        det_id,
        HELMET_LABEL,
        helmet,
    )
    if role == "janitor":
        det_id = _append_equipment_status(
            statuses,
            detections,
            det_id,
            CLEANING_COVERALL_LABEL,
            cleaning_coverall,
        )
    elif role == "worker":
        det_id = _append_equipment_status(
            statuses,
            detections,
            det_id,
            VEST_LABEL,
            vest,
        )
    else:
        statuses.append(EquipmentStatus(label=ROLE_UNIFORM_LABEL, status="violation"))
    return statuses, det_id


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


def _video_metadata(video_path: str | Path) -> tuple[float, int]:
    import cv2

    path_str = str(video_path)
    is_stream = path_str.startswith(("rtsp://", "rtmp://", "http://", "https://"))

    # Force TCP for RTSP to avoid UDP packet loss/hangs
    if is_stream and path_str.startswith("rtsp://"):
        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

    cap = cv2.VideoCapture(path_str)
    if not cap.isOpened():
        raise ValueError(f"Could not open video source: {path_str}")

    # For files, ensure we can read a frame. For streams, cap.isOpened() +
    # CAP_PROP_FPS is usually enough and faster.
    if not is_stream:
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            raise ValueError(
                f"Video source {path_str} opened but frames could not be read."
            )

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if is_stream and fps <= 0:
        fps = 30.0  # Default for streams if not detected

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if is_stream:
        total_frames = 0  # Streams don't have a fixed frame count

    cap.release()
    return fps, total_frames
