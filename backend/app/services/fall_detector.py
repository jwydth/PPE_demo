import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from app.schemas.detection import BoundingBox, PersonResult
from app.schemas.violation import ViolationReport
from app.services.ppe_violation_service import open_ppe_violation_service
from app.storage.local_paths import SNAPSHOT_DIR

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

FALL_LABEL = "Fall detected"


def _aspect_ratio(bbox: BoundingBox) -> float:
    """Box width / height. Returns 0.0 for a degenerate (zero-height) box."""
    w = bbox.x2 - bbox.x1
    h = bbox.y2 - bbox.y1
    if h <= 0:
        return 0.0
    return w / h


def box_area_frac(bbox: BoundingBox, frame_w: float, frame_h: float) -> float:
    """Box area as a fraction of the frame area."""
    if frame_w <= 0 or frame_h <= 0:
        return 0.0
    w = max(0.0, bbox.x2 - bbox.x1)
    h = max(0.0, bbox.y2 - bbox.y1)
    return (w * h) / (frame_w * frame_h)


def compute_fall_features(
    recent_bboxes: list[BoundingBox],
    recent_bbox_times: list[float] | None,
    frame_h: float,
    fps: float,
    stride: int,
    nominal_gap_seconds: float | None = None,
) -> dict:
    """Aspect ratio of the latest box and normalized downward velocity of its
    centre, computed between the last two boxes in `recent_bboxes`.

    `dt` (the elapsed time between those two boxes) is `max(nominal, measured)`
    — the larger of a nominal video-time estimate and the actual wall-clock
    gap in `recent_bbox_times`. `nominal_gap_seconds` should be the caller's
    true frame-index gap since the worker's last observation (e.g.
    `WorkerState.last_gap_seconds`) when available; it correctly reflects a
    multi-step detection gap (a few frames of missed/low-confidence
    detection), unlike the fallback `stride / fps`, which only assumes one
    ordinary polling step passed. Wall-clock time only approximates
    video-content time when playback is throttled to real-time:
    - Live RTSP with a stall: the real gap can be *larger* than nominal
      (frames were delayed/dropped) — measured wins, so floor-time/velocity
      aren't diluted by treating a multi-second stall as one nominal frame.
    - Offline file processing (e.g. `/predict-video`): frames are decoded and
      inferred as fast as the hardware allows, decoupled from the source's
      real fps, so wall-clock time is *smaller* than nominal — nominal wins,
      preventing floor time from being under-counted (and velocity from
      being inflated) just because the CPU/GPU finished quickly.
    """
    if len(recent_bboxes) < 2:
        return {
            "aspect_ratio": 0.0,
            "vertical_velocity": 0.0,
            "window_vertical_velocity": 0.0,
            "dt": 0.0,
            "height_frac": 0.0,
        }

    last = recent_bboxes[-1]
    prev = recent_bboxes[-2]
    aspect_ratio = _aspect_ratio(last)
    height_frac = max(0.0, last.y2 - last.y1) / frame_h if frame_h > 0 else 0.0

    effective_fps = fps if fps > 0 else 30.0
    dt = nominal_gap_seconds if nominal_gap_seconds and nominal_gap_seconds > 0 else stride / effective_fps
    if recent_bbox_times and len(recent_bbox_times) >= 2:
        measured_dt = recent_bbox_times[-1] - recent_bbox_times[-2]
        dt = max(dt, measured_dt)

    if dt <= 0 or frame_h <= 0:
        vertical_velocity = 0.0
    else:
        last_center_y = (last.y1 + last.y2) / 2
        prev_center_y = (prev.y1 + prev.y2) / 2
        vertical_velocity = ((last_center_y - prev_center_y) / frame_h) / dt

    # Velocity measured across the *whole* rolling window (oldest vs newest
    # sample) rather than just the last step. If a stalled/dropped frame puts
    # the entire fast part of a real fall between two widely-spaced samples,
    # the last-two-sample velocity above gets diluted by the inflated `dt`
    # even though the person genuinely moved fast. The window measurement
    # recovers the signal as long as the window still spans the fall.
    window_vertical_velocity = 0.0
    if frame_h > 0:
        window_steps = len(recent_bboxes) - 1
        window_dt = stride * window_steps / effective_fps
        if recent_bbox_times and len(recent_bbox_times) >= 2:
            measured_window_dt = recent_bbox_times[-1] - recent_bbox_times[0]
            window_dt = max(window_dt, measured_window_dt)
        if window_dt > 0:
            first = recent_bboxes[0]
            first_center_y = (first.y1 + first.y2) / 2
            last_center_y = (last.y1 + last.y2) / 2
            window_vertical_velocity = ((last_center_y - first_center_y) / frame_h) / window_dt

    return {
        "aspect_ratio": aspect_ratio,
        "vertical_velocity": vertical_velocity,
        "window_vertical_velocity": window_vertical_velocity,
        "dt": dt,
        "height_frac": height_frac,
    }


def update_fall_state(
    worker,
    features: dict,
    frame_area_frac: float,
    stride: int,
    fps: float,
    settings: "Settings",
) -> bool:
    """Advance `worker`'s fall state machine by one frame.

    Mutates `worker.fall_state` / `worker.floor_seconds` / `worker.fall_reported`.
    Returns True exactly once, on the frame a fall is first confirmed.
    """
    a = features["aspect_ratio"]
    vy = features["vertical_velocity"]
    window_vy = features.get("window_vertical_velocity", 0.0)
    h_frac = features.get("height_frac", 0.0)
    effective_fps = fps if fps > 0 else 30.0
    dt = features.get("dt") or (stride / effective_fps)

    # Ignore tiny/distant detections entirely.
    if frame_area_frac < settings.FALL_MIN_BOX_AREA_FRAC:
        return False

    # How far the box height has suddenly collapsed relative to this worker's
    # own recent standing height — catches a fall that lands the person
    # upright-ish (e.g. onto the buttocks), which never widens the box enough
    # to cross the aspect-ratio "lying" threshold.
    height_drop_ratio = 0.0
    if worker.standing_height_baseline and worker.standing_height_baseline > 0 and h_frac > 0:
        height_drop_ratio = 1.0 - (h_frac / worker.standing_height_baseline)

    is_down_shape = (
        a > settings.FALL_ASPECT_RATIO_THRESHOLD
        or height_drop_ratio > settings.FALL_HEIGHT_DROP_RATIO_THRESHOLD
    )
    # Prefer whichever velocity measurement shows the clearer downward
    # motion — the last-step one for a cleanly-sampled fall, the whole-window
    # one when a stalled/dropped frame diluted the last step's `dt`.
    vy_effective = max(vy, window_vy)
    # An extreme, sudden collapse is unambiguous regardless of measured
    # velocity (see FALL_HARD_HEIGHT_DROP_RATIO docstring in config).
    hard_collapse = height_drop_ratio > settings.FALL_HARD_HEIGHT_DROP_RATIO

    # STANDING -> FALLING: box flattening or collapsing fast AND dropping fast.
    if worker.fall_state == "standing":
        if hard_collapse or (is_down_shape and vy_effective > settings.FALL_VERTICAL_VELOCITY_THRESHOLD):
            worker.fall_state = "falling"
            worker.floor_seconds = 0.0
            worker.fall_lying_miss_seconds = 0.0
            worker.fall_recovery_seconds = 0.0
            # Once a fall has ever looked serious enough to enter "falling",
            # never trust this worker's baseline again -- see the lock check
            # below for why a recovery-confirmation delay alone isn't enough.
            worker.fall_baseline_locked = True
        elif h_frac > 0 and not worker.fall_baseline_locked:
            # Track the person's "normal" standing height so a later sudden
            # collapse can be measured against it. Only adapt while height
            # looks stable/normal (drop below the base threshold) -- a fall
            # that unfolds gradually (over a second or more, still short of
            # the velocity/hard-collapse bars) would otherwise have every one
            # of its own in-progress frames feed the EMA, dragging the
            # baseline down right alongside the real height and making the
            # drop-vs-baseline gap impossible to ever cross.
            if worker.standing_height_baseline is None:
                worker.standing_height_baseline = h_frac
            elif height_drop_ratio < settings.FALL_BASELINE_FREEZE_RATIO:
                # Height looks normal relative to the *existing* baseline, but
                # don't trust that immediately if we just came back from a
                # falling/lying state -- a brief box-size blip (noise, a limb
                # shifting) while still on the ground can look like enough of
                # a "recovery" to reset the state machine, and if the baseline
                # re-adapts right away it permanently bakes in that
                # still-on-the-ground height as the new "normal", hiding any
                # later, deeper collapse measured against it. Require this to
                # hold for a sustained window before trusting it.
                worker.fall_recovery_seconds += dt
                if worker.fall_recovery_seconds >= settings.FALL_RECOVERY_CONFIRM_SECONDS:
                    alpha = settings.FALL_HEIGHT_BASELINE_ALPHA
                    worker.standing_height_baseline = (
                        (1 - alpha) * worker.standing_height_baseline + alpha * h_frac
                    )
            else:
                worker.fall_recovery_seconds = 0.0

    # FALLING -> LYING: sustained flat shape or collapsed height.
    elif worker.fall_state == "falling":
        if is_down_shape:
            worker.fall_state = "lying"
            worker.floor_seconds = 0.0
            worker.fall_lying_miss_seconds = 0.0
        elif a < settings.FALL_STANDING_RATIO and height_drop_ratio <= settings.FALL_HEIGHT_DROP_RATIO_THRESHOLD:
            worker.fall_state = "standing"  # recovered / false trigger
            worker.fall_recovery_seconds = 0.0

    # LYING: accumulate floor time, confirm after threshold.
    if worker.fall_state == "lying":
        if is_down_shape:
            worker.fall_lying_miss_seconds = 0.0
            worker.floor_seconds += dt
            if worker.floor_seconds >= settings.FALL_CONFIRM_SECONDS and not worker.fall_reported:
                worker.fall_reported = True
                return True  # fall CONFIRMED this frame
        else:
            # Tolerate a brief dip below threshold (detection noise, a jittery
            # box, or a delayed/dropped frame) before concluding the person
            # actually stood back up — a single bad sample no longer wipes out
            # an otherwise-real fall. Capped per-step so one borderline
            # reading right after a multi-frame detection gap can't
            # single-handedly cross the whole grace window — sustained
            # evidence across several observations is required instead.
            worker.fall_lying_miss_seconds += min(dt, settings.FALL_LYING_MISS_STEP_CAP_SECONDS)
            if worker.fall_lying_miss_seconds >= settings.FALL_LYING_GRACE_SECONDS:
                worker.fall_state = "standing"
                worker.floor_seconds = 0.0
                worker.fall_reported = False
                worker.fall_lying_miss_seconds = 0.0
                worker.fall_recovery_seconds = 0.0

    return False


def record_fall_violation(
    worker_state,
    frame: np.ndarray,
    person: PersonResult,
    video_name: str,
    frame_index: int,
    save_snapshot_fn,
) -> ViolationReport | None:
    """Persist a confirmed fall the same way PPE violations are persisted —
    there is no zone/DB entity for a fall, so this reuses the generic PPE
    violation table (`violation_type` is free text) rather than the
    zone-violation path, which requires a camera_zone_view_id.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    details = (
        f"Fall detected (track {person.track_id})"
        if person.track_id is not None
        else "Fall detected"
    )

    try:
        snapshot_filename = save_snapshot_fn(
            frame=frame,
            person=person,
            missing=[FALL_LABEL],
            video_stem=Path(video_name).stem,
            frame_index=frame_index,
        )
    except Exception as exc:
        logger.error(f"[FALL] Snapshot save failed: {exc}", exc_info=True)
        return None

    try:
        with open_ppe_violation_service() as service:
            report = service.persist_violation(
                timestamp=timestamp,
                violation_type="FALL",
                details=details,
                local_snapshot_path=str(SNAPSHOT_DIR / snapshot_filename),
                video_name=video_name,
                frame_index=frame_index,
                track_id=person.track_id,
                person_index=person.person_id,
                missing_equipment=[FALL_LABEL],
                bounding_box=person.bbox.model_dump(),
                confidence=person.confidence,
            )
    except Exception as exc:
        logger.error(f"[FALL] DB persist failed: {exc}", exc_info=True)
        return None

    logger.info(f"[FALL] Violation persisted: track={person.track_id} frame={frame_index}")
    return report
