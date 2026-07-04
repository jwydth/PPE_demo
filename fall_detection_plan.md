# Fall Detection Implementation Plan (bounding-box classification)

## Context for Claude Code

This repo is a Smart Factory Safety Monitoring System (FastAPI backend, Next.js frontend).
We are adding **fall detection** to the existing PPE + zone-monitoring video pipeline.

**Approach: pure bounding-box geometry.** We do NOT train a new model and we do NOT use pose
estimation. We reuse the person boxes already produced by the existing YOLO + ByteTrack pipeline,
compute the aspect ratio and vertical velocity of each tracked person's box over time, and fire a
fall alert when a person's box rapidly flattens (goes from tall-and-narrow to wide-and-flat) and
then stays flat for a short confirmation period.

This mirrors the existing **zone-dwell** logic almost exactly. The fall detector is a new *signal*
inside the current frame loop — not a new pipeline. Follow the existing code's patterns and style.

### Why this approach
- A standing person's box is tall/narrow (aspect ratio w/h ≈ 0.3–0.5).
- A fallen person's box is wide/flat (aspect ratio w/h ≈ 1.2–3.0).
- A fall is the rapid transition between them, with the box centre dropping fast.
- Reusing the person detector avoids domain-shift problems from training on public fall datasets.

### Key files (already in repo)
- `backend/app/services/ppe_detector.py` — main detector. Contains `WorkerState` dataclass,
  the `_real_video_pipeline` frame loop, `_update_worker_status` (populates `recent_bboxes`),
  `_extract_result_boxes`, `_build_response`, `_save_violation_snapshot`.
- `backend/app/services/zone_service.py` — contains `record_zone_violation(...)`, the pattern
  the fall recorder should copy.
- `backend/app/schemas/streaming.py` — `StreamEvent` (has an `event` Literal to extend).
- `backend/app/schemas/detection.py` — detection schemas, incl. `TrackingOverlayFrame`.
- `backend/app/core/config.py` — settings. Relevant existing keys: `VIDEO_STABILITY_WINDOW_FRAMES=5`,
  `CONFIDENCE_THRESHOLD=0.3`, `VIDEO_TRACKER="bytetrack.yaml"`, `VIDEO_ZONE_REENTRY_GAP_SECONDS=1.0`.
- `backend/app/routers/detection.py` — `/predict-video` endpoint (test entry point).

### What already exists that we reuse (do NOT rebuild)
- `self.model.track(..., persist=True, tracker="bytetrack.yaml")` → stable `track_id` per person.
- `WorkerState.recent_bboxes` → per-track rolling history of recent boxes (currently capped at
  `VIDEO_STABILITY_WINDOW_FRAMES = 5` in `_update_worker_status`).
- The zone-dwell accumulation pattern: `worker.zone_dwell[cv_id] += stride / fps`, then a
  threshold-and-report gate with a `reported_*` flag to avoid duplicate alerts.
- `StreamEvent` emission + snapshot saving via `_save_violation_snapshot`.

---

## IMPORTANT — do these investigation steps FIRST, before writing code

1. Open `backend/app/services/ppe_detector.py` and read:
   - the `WorkerState` dataclass and its `__post_init__`
   - `_update_worker_status(...)` — confirm exactly where `worker.recent_bboxes.append(person.bbox)`
     happens and that it runs for EVERY tracked person every frame (not only reported ones).
   - the `_real_video_pipeline(...)` frame loop — find the `for person in ...` block and the
     `if curr_zone and zones:` zone block. The fall block goes right after the zone block.
   - how `curr_ppe, curr_zone = get_flags()` works, so a `curr_fall` flag can be added the same way.
2. Confirm the config setting name for the rolling window: `VIDEO_STABILITY_WINDOW_FRAMES` (=5).
   Fall velocity needs ~5–10 frames of history. If 5 is too short after testing, raise it — but do
   NOT lower the existing value if other code depends on it; add a separate window if needed.
3. Confirm `record_zone_violation(worker_state, zone, frame, person, video_name, frame_index,
   save_snapshot_fn)` signature in `zone_service.py` — the fall recorder copies this shape.
4. Confirm the `StreamEvent.event` Literal values in `schemas/streaming.py`.
5. Check how the frontend consumes `StreamEvent` events (search the frontend for `event ===`
   handling and how `zone_violation` is rendered) so the new `fall_violation` event can reuse it.

Do not proceed to implementation until these five points are confirmed against the actual code.

---

## Implementation — do it in these ordered, independently-testable steps

### Step 1 — Add config settings
File: `backend/app/core/config.py`

Add (keep the existing style and defaults conservative):
```
FALL_ENABLED: bool = True
FALL_ASPECT_RATIO_THRESHOLD: float = 1.2      # w/h above this = "lying" shape
FALL_STANDING_RATIO: float = 0.7              # w/h below this = "standing" shape
FALL_VERTICAL_VELOCITY_THRESHOLD: float = 0.15 # normalized (frac of frame height) per second
FALL_CONFIRM_SECONDS: float = 1.0             # must stay "lying" this long to confirm a fall
FALL_MIN_BOX_AREA_FRAC: float = 0.005         # ignore tiny/distant boxes (fraction of frame area)
FALL_WINDOW_FRAMES: int = 8                   # rolling box history length for velocity
```
Test: import config, assert the new attributes exist with these defaults.

### Step 2 — Add fall fields to `WorkerState`
File: `backend/app/services/ppe_detector.py`

Add three fields to the `WorkerState` dataclass, following the existing `Optional`-with-`__post_init__`
convention used by `zone_dwell` / `reported_zones`:
```
fall_state: str = "standing"        # "standing" | "falling" | "lying"
floor_seconds: float = 0.0          # accumulated time in "lying" shape
fall_reported: bool = False         # guard against duplicate alerts
```
Test: construct a `WorkerState`, assert defaults are `"standing"`, `0.0`, `False`.

### Step 3 — Ensure `recent_bboxes` holds enough frames
File: `backend/app/services/ppe_detector.py`, function `_update_worker_status`.

The window is currently `max(2, settings.VIDEO_STABILITY_WINDOW_FRAMES)` = 5. Fall velocity is more
reliable with ~8 frames. Change the cap in `_update_worker_status` to
`max(settings.FALL_WINDOW_FRAMES, settings.VIDEO_STABILITY_WINDOW_FRAMES)` so both features are
satisfied, without shrinking the existing stability window.
Test: run a short video, log `len(worker.recent_bboxes)` and confirm it grows to the new cap.

### Step 4 — Create the fall detector service (pure functions, stateless)
New file: `backend/app/services/fall_detector.py`

Mirror the style of `spatial.py` / the zone helpers: plain functions, no class, no I/O. All state
stays on `WorkerState`; the logic lives here.

Implement:
- `def _aspect_ratio(bbox) -> float` — returns `w / h` guarding against `h == 0`.
- `def _box_area_frac(bbox, frame_w, frame_h) -> float` — box area as a fraction of frame area.
- `def compute_fall_features(recent_bboxes, frame_h, fps, stride) -> dict` — returns
  `{"aspect_ratio": float, "vertical_velocity": float}` where `vertical_velocity` is the normalized
  downward speed of the box centre between the last two boxes, divided by `(stride / fps)` seconds,
  and expressed as a fraction of `frame_h` per second. Return zeros if `< 2` boxes.
- `def update_fall_state(worker, features, frame_area_frac, stride, fps, settings) -> bool` — runs
  the state machine below, mutates `worker.fall_state` / `worker.floor_seconds` / `worker.fall_reported`,
  and returns `True` exactly once, on the frame a fall is first confirmed.

State machine (inside `update_fall_state`):
```
a  = features["aspect_ratio"]
vy = features["vertical_velocity"]

# ignore tiny/distant detections entirely
if frame_area_frac < settings.FALL_MIN_BOX_AREA_FRAC:
    return False

# STANDING -> FALLING: box flattening fast AND dropping fast, from a standing shape
if worker.fall_state == "standing":
    if a > settings.FALL_ASPECT_RATIO_THRESHOLD and vy > settings.FALL_VERTICAL_VELOCITY_THRESHOLD:
        worker.fall_state = "falling"
        worker.floor_seconds = 0.0

# FALLING -> LYING: sustained flat shape
elif worker.fall_state == "falling":
    if a > settings.FALL_ASPECT_RATIO_THRESHOLD:
        worker.fall_state = "lying"
        worker.floor_seconds = 0.0
    elif a < settings.FALL_STANDING_RATIO:
        worker.fall_state = "standing"   # recovered / false trigger

# LYING: accumulate floor time, confirm after threshold
if worker.fall_state == "lying":
    if a > settings.FALL_ASPECT_RATIO_THRESHOLD:
        worker.floor_seconds += stride / fps
        if worker.floor_seconds >= settings.FALL_CONFIRM_SECONDS and not worker.fall_reported:
            worker.fall_reported = True
            return True   # fall CONFIRMED this frame
    else:
        # stood back up before confirmation -> reset
        worker.fall_state = "standing"
        worker.floor_seconds = 0.0
        worker.fall_reported = False

return False
```
Notes:
- The `stride / fps` term matches the existing zone-dwell accumulation — reuse the same value in scope.
- `fall_reported` prevents duplicate alerts, exactly like `reported_zones`.
- Add a re-entry reset (mirroring the existing gap-based zone reset in `_update_worker_status`): if a
  track disappears and reappears after `VIDEO_ZONE_REENTRY_GAP_SECONDS`, reset `fall_state="standing"`,
  `floor_seconds=0.0`, `fall_reported=False` so a fresh fall can be detected later.

Test (unit, no video): feed synthetic `recent_bboxes` sequences to `compute_fall_features` and
`update_fall_state` — a standing→flat sequence should return `True` once after ~1s; a
sit-down (flat but slow, low vy) should NOT; a person already lying (no standing history) should NOT.

### Step 5 — Wire the fall check into the frame loop
File: `backend/app/services/ppe_detector.py`, function `_real_video_pipeline`.

- Near the top of the loop where `curr_ppe, curr_zone = get_flags()` is read, also derive
  `curr_fall = settings.FALL_ENABLED` (or extend `get_flags()` to return a fall toggle so it can be
  switched from the UI later — match how `curr_zone` is toggled).
- Inside the `for person in ...` block, AFTER the existing `if curr_zone and zones:` block, add:
```
if curr_fall and person.track_id is not None:
    features = compute_fall_features(worker.recent_bboxes, frame_height, fps, stride)
    area_frac = _box_area_frac(person.bbox, frame_width, frame_height)
    if update_fall_state(worker, features, area_frac, stride, fps, settings):
        fv = record_fall_violation(worker, frame, person, video_name, frame_index,
                                   _save_violation_snapshot)
        if fv:
            yield StreamEvent(event="fall_violation", frame_index=frame_index,
                              data=fv.model_dump())
```
- `frame_width`, `frame_height`, `fps`, `stride`, `frame`, `frame_index`, and `worker` are all
  already in scope in this loop (the zone block uses them). Confirm before writing.

### Step 6 — Add the fall violation recorder
File: `backend/app/services/fall_detector.py` (or next to `record_zone_violation` in `zone_service.py`
— pick whichever keeps imports cleanest; match the existing choice).

Implement `record_fall_violation(worker_state, frame, person, video_name, frame_index,
save_snapshot_fn) -> ViolationReport | None` by copying the structure of `record_zone_violation`:
- build a label like `"Fall detected"`,
- call `save_snapshot_fn(frame=frame, person=person, missing=["Fall detected"], ...)` matching the
  existing call signature,
- return a `ViolationReport` (or a `ZoneViolation`-shaped object if that is what the frontend/incident
  list already consumes — check which schema the incident list uses and reuse it rather than adding a
  new one).

### Step 7 — Extend the event + schema surface
- `backend/app/schemas/streaming.py`: add `"fall_violation"` to the `StreamEvent.event` `Literal[...]`.
- (Optional, for overlays) `backend/app/schemas/detection.py`: add an optional
  `fall_status: Literal["standing","falling","lying"] | None = None` to `TrackingOverlayFrame`, and set
  it in `_append_tracking_overlay_frame` so the frontend can colour boxes. Keep it optional so nothing
  else breaks.

### Step 8 — Frontend (minimal, reuse existing zone handling)
- Find where the frontend handles `event === "zone_violation"` and add a parallel branch for
  `"fall_violation"` that pushes it into the same incident list / alert UI.
- If box colouring is wanted, colour the person box red when `fall_status === "lying"` in the overlay
  renderer (reuse the existing box-drawing component — do not build a new one).

### Step 9 — Persistence (only if the app persists other violations)
If PPE/zone violations are written to the DB (check `ppe_violation_service.py` /
`zone_violation_service.py` and the models), add a fall record the same way so it appears in history and
reports. If unsure, defer this — the demo works from the streamed event alone.

---

## Testing checklist (in order)
1. Unit test `fall_detector.py` with synthetic box sequences (Step 4 test above). No video needed.
2. Run `/predict-video` (see `routers/detection.py`) on a short clip of a staged fall. Confirm a single
   `fall_violation` event fires ~1s after the person is on the ground, and a snapshot is saved.
3. Run a clip of someone sitting down / crouching / bending — confirm NO fall fires (this validates the
   `vy` + standing-history + confirmation gates are doing their job).
4. Run a clip with two people, one falling — confirm only the falling track alerts and the other is
   untouched (validates per-track state isolation).
5. Confirm PPE and zone detection still behave exactly as before (no regressions in the shared loop).

## Guardrails for Claude Code
- Match the existing code style: dataclass fields with `__post_init__`, `stride / fps` accumulation,
  `settings.*` for all thresholds (no magic numbers in the loop), `StreamEvent` emission, snapshot via
  `_save_violation_snapshot`.
- Do NOT add pose estimation, a second model, or a new training pipeline.
- Do NOT create a new endpoint — reuse `/predict-video` and the existing streaming path.
- Keep `fall_detector.py` pure/stateless; all mutable state lives on `WorkerState`.
- Every threshold is a `settings` value so it can be tuned on real De Heus footage later.
- Make one logically-complete change per commit, and run the relevant test from the checklist after each.

## Known limitation to document (for the report, not a blocker)
Aspect-ratio fall detection weakens on a near-vertical (true top-down) camera, where standing and
fallen people both look compact from above. Most factory CCTV is mounted high at an oblique angle, where
this works. If a specific De Heus camera turns out to be near-vertical, the fallback signal is box
footprint-area increase + centroid going still + floor duration (all computable from the same box data).
Add that fallback later if testing on real footage shows it is needed.