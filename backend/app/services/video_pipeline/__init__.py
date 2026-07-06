"""Orchestration boundary between PPE detection and zone monitoring.

This is the **only** module in the codebase allowed to import from both
`app.services.ppe` and `app.services.zone_service`. It contains the
per-frame logic that interleaves PPE compliance checking with zone-incursion
checking (and auto-zone/sign-suggestion generation), previously inlined in
`PPEDetector._real_video_pipeline` / `_mock_process_video` /
`_mock_stream_video`.

`PPEDetector` (in `app.services.ppe.detector`) calls into the functions here
via thin delegating methods so its public surface (`_real_video_pipeline`,
`_mock_process_video`, `_mock_stream_video` as callables on a detector
instance) is unchanged for existing callers and tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.config import settings
from app.schemas.detection import (
    PersonResult,
    TrackingOverlay,
    TrackingOverlayFrame,
    VideoProcessingResponse,
    VideoSummary,
)
from app.schemas.streaming import StreamEvent
from app.schemas.violation import ViolationReport
from app.services.auto_zone import SignPPERegistry, SignZoneRegistry, extract_signs
from app.services.fall_detector import FallDetector, FallModelUnavailable
from app.services.ppe_violation_service import open_ppe_violation_service
from app.services.zone_service import (
    COORD_SCALE,
    ZoneViolationRecord,
    load_zones,
    get_person_foot_point,
    check_zone_incursion,
    record_zone_violation,
)
from app.storage.local_paths import SNAPSHOT_DIR

from app.services.ppe.device import _resolve_video_tracker
from app.services.ppe.geometry import _bbox_aspect_ratio
from app.services.ppe.response_builder import (
    _append_tracking_overlay_frame,
    _build_response,
    _encode_frame_to_base64,
    _extract_result_boxes,
    _video_metadata,
)
from app.services.ppe.violation_matching import (
    ViolationCase,
    _find_duplicate_ppe_case,
    _find_existing_case_match,
    _violation_details,
    _violation_type,
)
from app.services.ppe.worker_tracking import WorkerState, _update_worker_status

logger = logging.getLogger(__name__)
_fall_detector = FallDetector()

# Sentinel camera_zone_view_id used for the virtual "no walkway defined" zone.
# Uses a negative value so it can never collide with real database IDs.
_NO_WALKWAY_SENTINEL_ID = -1


def _record_violation_case(
    *,
    cases: list[ViolationCase],
    frame,
    person: PersonResult,
    worker: WorkerState,
    missing: list[str],
    video_name: str,
    frame_index: int,
    worker_match_reason: str = "unknown",
    confirmed_aspect_ratios: list[float] | None = None,
) -> None:
    if worker.reported:
        logger.info(
            "[PPE] Suppressed persistence for already-reported worker: "
            "frame=%s track_id=%s missing=%s worker_match=%s",
            frame_index,
            person.track_id,
            missing,
            worker_match_reason,
        )
        return

    case, match_reason = _find_existing_case_match(cases, person, frame_index)
    missing_set = set(missing)
    violation_type = _violation_type(missing)
    timestamp = datetime.now(timezone.utc).isoformat()
    duplicate_case, duplicate_reason, duplicate_gap, duplicate_center = (
        _find_duplicate_ppe_case(
            cases=cases,
            person=person,
            video_name=video_name,
            violation_type=violation_type,
            missing=missing_set,
            frame_index=frame_index,
        )
    )
    duplicate_suppressed = case is None and duplicate_case is not None
    action = (
        "suppress_duplicate"
        if duplicate_suppressed
        else "match_existing_immutable"
        if case is not None
        else "create"
    )
    aspect_ratio = _bbox_aspect_ratio(person.bbox)
    logger.info(
        "[PPE] Persistence decision: frame=%s track_id=%s violation_type=%s "
        "missing=%s role=%s worker_match=%s case_match=%s action=%s duplicate=%s "
        "duplicate_reason=%s frame_gap=%s center_distance_ratio=%s aspect_ratio=%.3f",
        frame_index,
        person.track_id,
        violation_type,
        sorted(missing_set),
        person.role,
        worker_match_reason,
        match_reason,
        action,
        duplicate_suppressed,
        duplicate_reason,
        duplicate_gap if duplicate_gap is not None else "-",
        f"{duplicate_center:.3f}" if duplicate_center is not None else "-",
        aspect_ratio,
    )

    if confirmed_aspect_ratios is not None:
        confirmed_aspect_ratios.append(aspect_ratio)

    if duplicate_suppressed and duplicate_case is not None:
        duplicate_case.last_bbox = person.bbox
        duplicate_case.last_frame = frame_index
        if person.track_id is not None:
            duplicate_case.track_ids.add(person.track_id)
        worker.reported = True
        worker.status = "violation"
        return

    if case is None:
        try:
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
                    video_name=video_name,
                    violation_type=violation_type,
                    role=person.role,
                )
            )
        except Exception as e:
            logger.error(f"Failed to save violation to DB: {e}", exc_info=True)
            return
    else:
        case.last_bbox = person.bbox
        case.last_frame = frame_index
        if person.track_id is not None:
            case.track_ids.add(person.track_id)
    worker.reported = True
    worker.status = "violation"


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


async def real_video_pipeline(
    detector,
    video_path: str | Path,
    video_name: str,
    stride: int,
    *,
    enable_ppe: bool = True,
    enable_zone: bool = True,
    enable_fall: bool = False,
    settings_state: dict | None = None,
):
    """Unified internal generator for video processing (real model path)."""
    fps, total_frames = _video_metadata(video_path)
    is_stream = total_frames <= 0
    start_wall_time = time.perf_counter()
    cases: list[ViolationCase] = []
    workers: list[WorkerState] = []
    confirmed_aspect_ratios: list[float] = []
    candidate_violations = 0
    processed_frames = 0
    frame_width: int | None = None
    frame_height: int | None = None
    prev_zone_enabled = enable_zone  # track zone toggle to trigger retroactive check
    fall_live_session = None
    fall_unavailable_message: str | None = None
    last_fall_payload: dict | None = None

    # Rolling window of foot-point history: track_id → [(frame_index, foot_point)]
    # Used to retroactively check newly-accepted zones against recent worker positions.
    _FOOT_HISTORY_FRAMES = int(fps * 30) if fps > 0 else 900  # last 30 s
    foot_history: dict[int, list[tuple[int, tuple]]] = {}

    # Helper to get current flags
    def get_flags():
        if settings_state:
            return (
                settings_state.get("enable_ppe", enable_ppe),
                settings_state.get("enable_zone", enable_zone),
                settings_state.get("enable_fall", enable_fall),
            )
        return enable_ppe, enable_zone, enable_fall

    def _retroactive_zone_check(new_zones, current_frame_index):
        """Check foot-point history against zones and emit any missed violations.

        Uses max-continuous-dwell so the result matches real-time behaviour:
        a person who dips in briefly twice doesn't accumulate dwell across
        the two separate visits.
        """
        violations = []
        frame_duration = stride / fps if fps > 0 else 1 / 30
        for zone in new_zones:
            cv_id = zone.camera_zone_view_id
            for track_id, history in foot_history.items():
                worker = next((w for w in workers if track_id in w.track_ids), None)
                if worker is None or cv_id in worker.reported_zones:
                    continue
                # Janitors are immune to slippery zone violations
                if zone.zone_type == "SLIPPERY" and worker.role == "janitor":
                    continue
                # Compute the longest unbroken streak of in-zone frames
                max_dwell = 0.0
                current_dwell = 0.0
                for _, fp in history:
                    if zone.point_in_zone(fp):
                        current_dwell += frame_duration
                        max_dwell = max(max_dwell, current_dwell)
                    else:
                        current_dwell = 0.0
                if max_dwell > zone.threshold:
                    logger.info(
                        f"[ZONE] Retroactive violation: worker {track_id} "
                        f"was in '{zone.zone_name}' for {max_dwell:.2f}s (threshold={zone.threshold}s)"
                    )
                    violations.append((worker, zone, track_id))
        return violations

    zones = load_zones(video_name)
    has_walkway = any(z.zone_type == "WALKWAY" for z in zones)
    logger.info(f"[ZONE] Loaded {len(zones)} zone(s) for '{video_name}': {[(z.zone_name, z.zone_type, z.camera_zone_view_id) for z in zones]} has_walkway={has_walkway}")
    # Virtual zone used when zone monitoring is on but no WALKWAY zone exists.
    _no_walkway_zone = ZoneViolationRecord(
        camera_zone_view_id=_NO_WALKWAY_SENTINEL_ID,
        physical_zone_id=_NO_WALKWAY_SENTINEL_ID,
        zone_name="No Walkway Defined",
        zone_type="WALKWAY",
        poly=[],  # not used for point-in-polygon; every worker is "outside"
        threshold=settings.NO_WALKWAY_DWELL_SECONDS,
    )
    sign_registry = SignZoneRegistry()
    ppe_sign_registry = SignPPERegistry()
    sign_classes = list({*settings.SIGN_CLASS_ZONE_MAP, *settings.SIGN_CLASS_PPE_TRIGGER})

    yield StreamEvent(
        event="start",
        data={"video_name": video_name, "fps": round(fps, 2), "total_frames": total_frames},
    )

    # Use TCP for RTSP streams to prevent 'Waiting for stream' timeouts.
    # timeout;3000000 = 3 s read timeout so cv2 doesn't block indefinitely
    # when the stream stalls — this lets generator cleanup finish quickly
    # on WebSocket disconnect instead of waiting 10+ s for the OS read to return.
    source_str = str(video_path)
    if is_stream and source_str.startswith("rtsp://"):
        import os
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;3000000"

    tracker_path = _resolve_video_tracker(settings.VIDEO_TRACKER)
    results = detector.model.track(
        source=source_str,
        stream=True,
        persist=True,
        conf=settings.CONFIDENCE_THRESHOLD,
        tracker=tracker_path,
        classes=[0, 1, 2, 3],
        vid_stride=stride,
        device=detector.device,
        verbose=False,
        stream_buffer=True,  # Use threaded reader for stable RTSP ingestion
    )

    for processed_frames, result in enumerate(results, start=1):
        await asyncio.sleep(0.01)  # Critical: yield to event loop to keep WebSocket alive
        frame_index = (processed_frames - 1) * stride
        curr_ppe, curr_zone, curr_fall = get_flags()

        # Detect zone being toggled ON mid-stream and retroactively check foot history
        zone_just_enabled = curr_zone and not prev_zone_enabled
        prev_zone_enabled = curr_zone

        if settings_state and settings_state.pop("reload_zones", False):
            zones = load_zones(video_name)
            has_walkway = any(z.zone_type == "WALKWAY" for z in zones)
            logger.info(f"[ZONE] Hot-reloaded {len(zones)} zone(s): {[(z.zone_name, z.zone_type) for z in zones]} has_walkway={has_walkway}")
            if curr_zone:
                zone_just_enabled = True  # treat reload same as fresh enable only if zone monitoring is active

        if zone_just_enabled and zones and foot_history:
            logger.info(f"[ZONE] Zone enabled/reloaded at frame {frame_index} — running retroactive check over {len(foot_history)} track(s)")
            persons_this_frame, _, _, _ = _extract_result_boxes(result)
            temp_response = _build_response(persons_this_frame, [], [], [], 0.0)
            retroactively_reported_workers: set[int] = set()
            for worker, zone, track_id in _retroactive_zone_check(zones, frame_index):
                cv_id = zone.camera_zone_view_id
                if cv_id not in worker.reported_zones:
                    matched_person = next((p for p in temp_response.persons if p.track_id == track_id), None)
                    if matched_person is None:
                        # Person not visible in this frame — build a stand-in from worker's last known state
                        matched_person = PersonResult(
                            person_id=track_id or 0,
                            track_id=track_id,
                            bbox=worker.last_bbox,
                            confidence=1.0,
                            equipment=[],
                            compliant=True,
                        )
                    zv = record_zone_violation(worker, zone, result.orig_img.copy(), matched_person, video_name, frame_index, _save_violation_snapshot)
                    if zv:
                        retroactively_reported_workers.add(id(worker))
                        yield StreamEvent(event="zone_violation", frame_index=frame_index, data=zv.model_dump())
            # Reset zone state for workers that just had a retroactive violation saved so
            # their ongoing live presence is treated as a fresh entry from this point forward.
            for worker in workers:
                if id(worker) in retroactively_reported_workers:
                    worker.reported_zones = set()
                    worker.zone_dwell = {}
                    worker.zone_last_in = {}

        if processed_frames % 60 == 1:
            logger.info(f" [PIPELINE] Frame {frame_index} active state: ppe={curr_ppe}, zone={curr_zone}, fall={curr_fall}")

        persons, helmets, vests, cleaning_coveralls = _extract_result_boxes(result)

        response = _build_response(persons, helmets, vests, cleaning_coveralls, 0.0)
        frame = result.orig_img.copy()
        frame_height, frame_width = frame.shape[:2]
        if curr_fall:
            fall_ttl_frames = max(1, settings.FALL_LIVE_FRAME_STRIDE * 2)
            if fall_unavailable_message is None and fall_live_session is None:
                fall_live_session = _fall_detector.create_live_session(
                    fps=fps,
                    frame_stride=settings.FALL_LIVE_FRAME_STRIDE,
                )
            if fall_unavailable_message is None and frame_index % max(1, settings.FALL_LIVE_FRAME_STRIDE) == 0:
                try:
                    last_fall_payload = fall_live_session.process_frame(
                        frame,
                        frame_index=frame_index,
                        source_name=video_name,
                    )
                    for incident in last_fall_payload.get("incidents", []):
                        yield StreamEvent(event="behavior_incident", frame_index=frame_index, data=incident)
                except FallModelUnavailable as exc:
                    fall_unavailable_message = str(exc)
                    last_fall_payload = {
                        "summary": {
                            "status": "unavailable",
                            "fall_count": 0,
                            "fall_risk_count": 0,
                            "normal_count": 0,
                            "person_count": 0,
                            "top_label": "unavailable",
                            "top_confidence": 0.0,
                            "persisted_incident_ids": [],
                        },
                        "detections": [],
                        "incidents": [],
                    }
                    logger.warning("[FALL] Live fall detection unavailable: %s", fall_unavailable_message)
                except Exception:
                    fall_unavailable_message = "Fall detection failed during live stream processing."
                    last_fall_payload = {
                        "summary": {
                            "status": "unavailable",
                            "fall_count": 0,
                            "fall_risk_count": 0,
                            "normal_count": 0,
                            "person_count": 0,
                            "top_label": "unavailable",
                            "top_confidence": 0.0,
                            "persisted_incident_ids": [],
                        },
                        "detections": [],
                        "incidents": [],
                    }
                    logger.exception("[FALL] Live fall detection failed")
            elif fall_unavailable_message is None and fall_live_session is not None:
                last_fall_payload = fall_live_session.payload_for_frame(
                    frame_index,
                    max_age_frames=fall_ttl_frames,
                )
        else:
            last_fall_payload = None
        used_worker_ids: set[int] = set()
        overlay_person_ids: set[tuple[str, int]] = set()
        current_frame_overlay: list[TrackingOverlayFrame] = []

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
            if curr_ppe:
                candidate_violations += int(decision["candidate"])

            track_camera_zone_view_id = None
            track_physical_zone_id = None
            track_zone_name = None
            track_zone_type = None

            if person.track_id is not None:
                hist = foot_history.setdefault(person.track_id, [])
                fp_for_history = get_person_foot_point(person, frame_width, frame_height)
                hist.append((frame_index, fp_for_history))
                if len(hist) > _FOOT_HISTORY_FRAMES:
                    del hist[0]

            if curr_zone and zones:
                test_point = get_person_foot_point(person, frame_width, frame_height)
                incursion_zones = check_zone_incursion(zones, test_point)
                incursion_ids = {z.camera_zone_view_id for z in incursion_zones}

                # Log every 30 frames so we can see whether foot point ever hits the zone
                if frame_index % 30 == 0:
                    logger.info(
                        f"[ZONE] Frame {frame_index} worker {person.track_id}: "
                        f"foot={test_point} zones_loaded={len(zones)} in_zones={[(z.zone_name, z.zone_type) for z in incursion_zones]}"
                    )
                    for z in zones:
                        logger.info(f"[ZONE]   zone '{z.zone_name}' poly={z.poly[:2]}...  threshold={z.threshold}s")

                for zone in zones:
                    cv_id = zone.camera_zone_view_id
                    in_z = cv_id in incursion_ids
                    worker.zone_last_in[cv_id] = in_z  # always record last known position
                    if zone.zone_type == "WALKWAY":
                        if in_z:
                            # Worker back inside walkway — reset so a future exit can trigger a new incident
                            worker.zone_dwell[cv_id] = 0
                            worker.reported_zones.discard(cv_id)
                        else:
                            worker.zone_dwell[cv_id] = worker.zone_dwell.get(cv_id, 0) + (stride / fps)
                            dwell = worker.zone_dwell[cv_id]
                            if frame_index % 30 == 0:
                                logger.info(f"[ZONE] Frame {frame_index} worker {person.track_id}: WALKWAY '{zone.zone_name}' dwell={dwell:.2f}s / threshold={zone.threshold}s already_reported={cv_id in worker.reported_zones}")
                            if dwell > zone.threshold and cv_id not in worker.reported_zones:
                                logger.info(f"[ZONE] Frame {frame_index} worker {person.track_id}: WALKWAY threshold crossed — recording violation")
                                zv = record_zone_violation(worker, zone, frame, person, video_name, frame_index, _save_violation_snapshot)
                                logger.info(f"[ZONE] record_zone_violation returned: {zv}")
                                if zv:
                                    yield StreamEvent(event="zone_violation", frame_index=frame_index, data=zv.model_dump())
                    else:
                        # Janitors are immune to slippery zone violations
                        if zone.zone_type == "SLIPPERY" and worker.role == "janitor":
                            continue

                        if in_z:
                            worker.zone_dwell[cv_id] = worker.zone_dwell.get(cv_id, 0) + (stride / fps)
                            dwell = worker.zone_dwell[cv_id]
                            logger.info(f"[ZONE] Frame {frame_index} worker {person.track_id} in role {worker.role}: {zone.zone_type} '{zone.zone_name}' dwell={dwell:.2f}s / threshold={zone.threshold}s already_reported={cv_id in worker.reported_zones}")
                            if dwell > zone.threshold and cv_id not in worker.reported_zones:
                                logger.info(f"[ZONE] Frame {frame_index} worker {person.track_id}: {zone.zone_type} threshold crossed — recording violation")
                                zv = record_zone_violation(worker, zone, frame, person, video_name, frame_index, _save_violation_snapshot)
                                logger.info(f"[ZONE] record_zone_violation returned: {zv}")
                                if zv:
                                    yield StreamEvent(event="zone_violation", frame_index=frame_index, data=zv.model_dump())
                        else:
                            # Worker exited the restricted zone — reset so re-entry triggers a new incident
                            worker.reported_zones.discard(cv_id)
                            worker.zone_dwell[cv_id] = 0

                for zone in incursion_zones:
                    if zone.zone_type in ("RESTRICTED", "SLIPPERY"):
                        track_camera_zone_view_id, track_physical_zone_id, track_zone_name, track_zone_type = zone.camera_zone_view_id, zone.physical_zone_id, zone.zone_name, zone.zone_type
                        break
                if track_zone_type is None:
                    walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                    if walkway_zones and not any(z.camera_zone_view_id in incursion_ids for z in walkway_zones):
                        wz = walkway_zones[0]
                        track_camera_zone_view_id, track_physical_zone_id, track_zone_name, track_zone_type = wz.camera_zone_view_id, wz.physical_zone_id, wz.zone_name, "WALKWAY"

            # --- No-walkway enforcement ---
            # When zone monitoring is ON but no WALKWAY zone has been drawn,
            # every detected worker is treated as being outside the walkway.
            # A violation is raised once the worker has been visible for
            # NO_WALKWAY_DWELL_SECONDS consecutive seconds.
            if curr_zone and not has_walkway:
                nw_id = _NO_WALKWAY_SENTINEL_ID
                worker.zone_dwell[nw_id] = worker.zone_dwell.get(nw_id, 0) + (stride / fps)
                dwell = worker.zone_dwell[nw_id]
                if frame_index % 30 == 0:
                    logger.info(
                        f"[ZONE] Frame {frame_index} worker {person.track_id}: "
                        f"NO_WALKWAY dwell={dwell:.2f}s / threshold={_no_walkway_zone.threshold}s "
                        f"already_reported={nw_id in worker.reported_zones}"
                    )
                if dwell > _no_walkway_zone.threshold and nw_id not in worker.reported_zones:
                    logger.info(f"[ZONE] Frame {frame_index} worker {person.track_id}: NO_WALKWAY threshold crossed — recording violation")
                    zv = record_zone_violation(worker, _no_walkway_zone, frame, person, video_name, frame_index, _save_violation_snapshot)
                    if zv:
                        yield StreamEvent(event="zone_violation", frame_index=frame_index, data=zv.model_dump())
                # Mark overlay as outside walkway
                track_camera_zone_view_id = nw_id
                track_physical_zone_id = nw_id
                track_zone_name = "No Walkway Defined"
                track_zone_type = "WALKWAY"

            _append_tracking_overlay_frame(
                overlay_frames=current_frame_overlay,
                seen_person_ids=overlay_person_ids,
                person=person,
                decision=decision,
                frame_index=frame_index,
                fps=fps,
                include_ppe=curr_ppe,
                camera_zone_view_id=track_camera_zone_view_id,
                physical_zone_id=track_physical_zone_id,
                zone_name=track_zone_name,
                zone_type=track_zone_type,
            )

            missing_to_report = decision["missing_to_report"]
            if curr_ppe and missing_to_report:
                old_case_count = len(cases)
                _record_violation_case(cases=cases, frame=frame, person=person, worker=worker, missing=missing_to_report, video_name=video_name, frame_index=frame_index, worker_match_reason=decision.get("worker_match_reason", "unknown"), confirmed_aspect_ratios=confirmed_aspect_ratios)
                if len(cases) > old_case_count:
                    yield StreamEvent(event="violation", frame_index=frame_index, data=cases[-1].report.model_dump())

        if detector.sign_model is not None and frame_width and frame_height and frame_index % settings.SIGN_PASS_FRAME_INTERVAL == 0:
            if settings_state:
                pending_zone = settings_state.get("dismissed_signatures", [])
                if pending_zone:
                    settings_state["dismissed_signatures"] = []
                    for sig in pending_zone:
                        sign_registry.dismiss(sig)
                pending_ppe = settings_state.get("dismissed_ppe_signatures", [])
                if pending_ppe:
                    settings_state["dismissed_ppe_signatures"] = []
                    for sig in pending_ppe:
                        ppe_sign_registry.dismiss(sig)
            sign_results = detector.sign_model.predict(
                frame,
                conf=settings.SIGN_CONFIDENCE_THRESHOLD,
                classes=sign_classes,
                device=detector.device,
                verbose=False,
            )
            if sign_results:
                signs = extract_signs(sign_results[0])
                for suggestion in sign_registry.update(signs, frame_width, frame_height, frame_index, fps):
                    yield StreamEvent(event="zone_suggestion", frame_index=frame_index, data=suggestion.model_dump())
                for suggestion in ppe_sign_registry.update(signs, frame_width, frame_height, frame_index):
                    yield StreamEvent(event="ppe_suggestion", frame_index=frame_index, data=suggestion.model_dump())

        yield StreamEvent(
            event="frame",
            frame_index=frame_index,
            data={
                "frames": [f.model_dump() for f in current_frame_overlay],
                "processed_frames": processed_frames,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "fall_summary": last_fall_payload.get("summary") if curr_fall and last_fall_payload else None,
                "fall_detections": last_fall_payload.get("detections") if curr_fall and last_fall_payload else [],
                "fall_unavailable": fall_unavailable_message if curr_fall else None,
            },
            image_base64=_encode_frame_to_base64(frame) if is_stream else None
        )

    elapsed_ms = (time.perf_counter() - start_wall_time) * 1000
    yield StreamEvent(
        event="summary",
        data=VideoSummary(
            video_name=video_name, total_frames=total_frames, processed_frames=processed_frames,
            fps=round(fps, 2), duration_seconds=round(total_frames / fps if fps > 0 else 0, 2),
            unique_violations=len(cases), candidate_violations=candidate_violations, inference_ms=round(elapsed_ms, 2),
        ).model_dump(),
    )
    yield StreamEvent(event="end", data={})


def mock_process_video(
    detector,
    video_path: Path,
    video_name: str,
    *,
    enable_ppe: bool = True,
    enable_zone: bool = True,
    enable_fall: bool = False,
):
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
    zones = load_zones(video_name) if enable_zone else []

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % stride != 0:
            frame_index += 1
            continue

        processed_frames += 1
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        response = detector._mock_predict(Image.fromarray(rgb))
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
            if enable_ppe:
                candidate_violations += int(decision["candidate"])

            track_zone_id: int | None = None
            track_zone_name: str | None = None
            track_zone_type: str | None = None

            if zones:
                test_point = get_person_foot_point(
                    person, frame_width, frame_height
                )
                incursion_zones = check_zone_incursion(zones, test_point)
                incursion_zone_ids = {z.zone_id for z in incursion_zones}

                for zone in incursion_zones:
                    if zone.zone_type in ("RESTRICTED", "SLIPPERY"):
                        track_zone_id = zone.zone_id
                        track_zone_name = zone.zone_name
                        track_zone_type = zone.zone_type
                        break
                if track_zone_type is None:
                    walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                    if walkway_zones and not any(
                        z.zone_id in incursion_zone_ids for z in walkway_zones
                    ):
                        wz = walkway_zones[0]
                        track_zone_id = wz.zone_id
                        track_zone_name = wz.zone_name
                        track_zone_type = "WALKWAY"

            _append_tracking_overlay_frame(
                overlay_frames=overlay_frames,
                seen_person_ids=overlay_person_ids,
                person=person,
                decision=decision,
                frame_index=frame_index,
                fps=fps,
                include_ppe=enable_ppe,
                physical_zone_id=track_zone_id,
                zone_name=track_zone_name,
                zone_type=track_zone_type,
            )
            missing_to_report = decision["missing_to_report"]
            if not enable_ppe or not missing_to_report:
                continue

            _record_violation_case(
                cases=cases,
                frame=frame,
                person=person,
                worker=decision["worker"],
                missing=missing_to_report,
                video_name=video_name,
                frame_index=frame_index,
                worker_match_reason=decision.get("worker_match_reason", "unknown"),
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


async def mock_stream_video(
    detector,
    video_path: Path,
    video_name: str,
    *,
    enable_ppe: bool = True,
    enable_zone: bool = True,
    enable_fall: bool = False,
):
    import cv2

    fps, total_frames = _video_metadata(video_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        yield StreamEvent(event="error", data={"message": "Could not open video"})
        return

    start_wall_time = time.perf_counter()
    stride = max(1, settings.VIDEO_FRAME_STRIDE)
    frame_index = 0
    processed_frames = 0
    cases: list[ViolationCase] = []
    workers: list[WorkerState] = []
    candidate_violations = 0
    zones = load_zones(video_name) if enable_zone else []

    yield StreamEvent(
        event="start",
        data={
            "video_name": video_name,
            "fps": round(fps, 2),
            "total_frames": total_frames,
        },
    )

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % stride != 0:
            frame_index += 1
            continue

        processed_frames += 1
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        response = detector._mock_predict(Image.fromarray(rgb))
        frame_height, frame_width = frame.shape[:2]
        used_worker_ids: set[int] = set()
        overlay_person_ids: set[tuple[str, int]] = set()
        current_frame_overlay: list[TrackingOverlayFrame] = []

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
            if enable_ppe:
                candidate_violations += int(decision["candidate"])

            track_zone_id: int | None = None
            track_zone_name: str | None = None
            track_zone_type: str | None = None

            if zones:
                test_point = get_person_foot_point(
                    person, frame_width, frame_height
                )
                incursion_zones = check_zone_incursion(zones, test_point)
                incursion_zone_ids = {z.zone_id for z in incursion_zones}

                for zone in incursion_zones:
                    if zone.zone_type in ("RESTRICTED", "SLIPPERY"):
                        track_zone_id = zone.zone_id
                        track_zone_name = zone.zone_name
                        track_zone_type = zone.zone_type
                        break
                if track_zone_type is None:
                    walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                    if walkway_zones and not any(
                        z.zone_id in incursion_zone_ids for z in walkway_zones
                    ):
                        wz = walkway_zones[0]
                        track_zone_id = wz.zone_id
                        track_zone_name = wz.zone_name
                        track_zone_type = "WALKWAY"

            _append_tracking_overlay_frame(
                overlay_frames=current_frame_overlay,
                seen_person_ids=overlay_person_ids,
                person=person,
                decision=decision,
                frame_index=frame_index,
                fps=fps,
                include_ppe=enable_ppe,
                physical_zone_id=track_zone_id,
                zone_name=track_zone_name,
                zone_type=track_zone_type,
            )
            missing_to_report = decision["missing_to_report"]
            if enable_ppe and missing_to_report:
                old_case_count = len(cases)
                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    worker=decision["worker"],
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
                    worker_match_reason=decision.get("worker_match_reason", "unknown"),
                )
                if len(cases) > old_case_count:
                    yield StreamEvent(
                        event="violation",
                        frame_index=frame_index,
                        data=cases[-1].report.model_dump(),
                    )

        # Pacer
        elapsed_processing = time.perf_counter() - start_wall_time
        expected_elapsed = frame_index / fps if fps > 0 else 0
        wait_time = expected_elapsed - elapsed_processing
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        yield StreamEvent(
            event="frame",
            frame_index=frame_index,
            data={
                "frames": [f.model_dump() for f in current_frame_overlay],
                "processed_frames": processed_frames,
            },
        )
        frame_index += 1

    cap.release()
    elapsed_ms = (time.perf_counter() - start_wall_time) * 1000
    duration_seconds = total_frames / fps if fps > 0 else 0.0
    yield StreamEvent(
        event="summary",
        data=VideoSummary(
            video_name=video_name,
            total_frames=total_frames,
            processed_frames=processed_frames,
            fps=round(fps, 2),
            duration_seconds=round(duration_seconds, 2),
            unique_violations=len(cases),
            candidate_violations=candidate_violations,
            inference_ms=round(elapsed_ms, 2),
        ).model_dump(),
    )
    yield StreamEvent(event="end", data={})
