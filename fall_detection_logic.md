# Fall Detection — What's Implemented So Far

Status: backend detection + persistence + streaming verified working end-to-end, both on live
RTSP footage and via `/predict-video` file uploads. Extensively hardened against real failure
modes found by testing against a labeled test-set of real fall clips (see "Session 2" below).
Frontend live-view display gap fixed. See "Known limitations" before demoing.

## Approach

Pure bounding-box geometry — no pose estimation, no second model, no training. We reuse the
person boxes already produced by the existing YOLO + ByteTrack pipeline and watch how a
tracked person's box changes shape, height, and position over time. Two independent signals
feed the same state machine:

- **Aspect ratio** — a standing person's box is tall/narrow (w/h roughly 0.3–0.5); a person
  lying flat is wide (w/h > `FALL_ASPECT_RATIO_THRESHOLD`).
- **Height collapse** — a person who falls onto their buttocks or otherwise ends up
  upright-ish never widens the box enough to trip the aspect-ratio signal, so we also track
  each worker's own recent "standing height" and watch for a sudden, large fractional drop
  from it (`FALL_HEIGHT_DROP_RATIO_THRESHOLD` / `FALL_HARD_HEIGHT_DROP_RATIO`).

A fall is the *rapid transition* into either of those shapes — normally gated by downward
velocity so a slow, deliberate crouch/kneel doesn't get flagged — followed by staying down
for a confirmation window. This is a new signal inside the existing frame loop, following the
same pattern as the existing zone-dwell logic (accumulate over time, gate on a threshold,
guard against duplicate alerts with a `reported` flag).

## Backend changes

### `backend/app/core/config.py`
All fall-related settings, current values (all tunable, no magic numbers in the detection
code):

| Setting | Default | Meaning |
|---|---|---|
| `FALL_ENABLED` | `True` | master on/off switch |
| `FALL_ASPECT_RATIO_THRESHOLD` | `1.1` | w/h above this = "lying" shape |
| `FALL_STANDING_RATIO` | `0.7` | w/h below this = "standing" shape (hysteresis vs. the entry threshold) |
| `FALL_VERTICAL_VELOCITY_THRESHOLD` | `0.12` | normalized downward speed (fraction of frame height/sec) needed to call it a fall, not a slow sit-down |
| `FALL_CONFIRM_SECONDS` | `1.0` | must stay "down" this long before an alert fires |
| `FALL_MIN_BOX_AREA_FRAC` | `0.005` | ignore tiny/distant boxes |
| `FALL_WINDOW_FRAMES` | `8` | rolling box-history length used for velocity |
| `FALL_LYING_GRACE_SECONDS` | `0.5` | total tolerance for "looks recovered" before resetting to standing |
| `FALL_LYING_MISS_STEP_CAP_SECONDS` | `0.2` | cap on how much *one* observation can contribute toward that grace timer — forces several separate readings to agree, not one noisy sample (see Session 2) |
| `FALL_RECOVERY_CONFIRM_SECONDS` | `1.0` | after reset-to-standing, how long height must look genuinely normal before the standing-height baseline is trusted to adapt again |
| `FALL_REMATCH_IOU_THRESHOLD` | `0.05` | looser IOU bar for re-matching a track that's currently falling/lying |
| `FALL_REMATCH_CENTER_DISTANCE_RATIO` | `1.5` | looser center-distance bar, same purpose |
| `FALL_HEIGHT_DROP_RATIO_THRESHOLD` | `0.30` | fraction of standing height suddenly lost = "down" shape (independent of aspect ratio) |
| `FALL_HEIGHT_BASELINE_ALPHA` | `0.15` | EMA smoothing factor for the rolling standing-height baseline |
| `FALL_BASELINE_FREEZE_RATIO` | `0.12` | height-drop beyond which the baseline stops adapting (much smaller than the entry threshold — see Session 2) |
| `FALL_HARD_HEIGHT_DROP_RATIO` | `0.40` | height loss this extreme bypasses the velocity gate entirely |
| `FALL_REENTRY_GAP_SECONDS` | `3.0` | separate, longer-than-zone gap tolerance before the fall state machine gives up and resets (routine zone-dwell resets at `VIDEO_ZONE_REENTRY_GAP_SECONDS` = 1.0s, which is too short for a fall) |

### `backend/app/services/fall_detector.py`
Pure, stateless functions — no I/O except the final persistence call. All mutable state lives
on `WorkerState`.

- `_aspect_ratio(bbox)` — w/h, guards against a zero-height box.
- `box_area_frac(bbox, frame_w, frame_h)` — box area as a fraction of frame area.
- `compute_fall_features(recent_bboxes, recent_bbox_times, frame_h, fps, stride, nominal_gap_seconds=None)`
  — returns `aspect_ratio`, `height_frac` (box height / frame height) of the latest box,
  `vertical_velocity` (last-two-sample), `window_vertical_velocity` (oldest-to-newest sample
  in the rolling window), and `dt` (see "Timing correctness" in Session 2 — this is the most
  subtle part of the whole implementation).
- `update_fall_state(worker, features, frame_area_frac, stride, fps, settings)` — the state
  machine (below). Mutates `worker.fall_state` / `floor_seconds` / `fall_reported` /
  `fall_lying_miss_seconds` / `standing_height_baseline` / `fall_recovery_seconds` /
  `fall_baseline_locked`. Returns `True` exactly once, on the frame a fall is confirmed.
- `record_fall_violation(worker_state, frame, person, video_name, frame_index, save_snapshot_fn)`
  — persists a confirmed fall and returns a `ViolationReport`.

**State machine (current form):**

```
is_down_shape   = aspect > FALL_ASPECT_RATIO_THRESHOLD  OR  height_drop > FALL_HEIGHT_DROP_RATIO_THRESHOLD
hard_collapse   = height_drop > FALL_HARD_HEIGHT_DROP_RATIO
vy_effective    = max(last-step velocity, whole-window velocity)

standing --(hard_collapse OR (is_down_shape AND vy_effective > VELOCITY_THRESHOLD))--> falling
falling  --(is_down_shape)--> lying
falling  --(aspect < FALL_STANDING_RATIO AND height_drop <= entry threshold)--> standing   (recovered / false trigger)
lying    --(is_down_shape, accumulate floor_seconds)--> CONFIRMED once floor_seconds >= FALL_CONFIRM_SECONDS
lying    --(NOT is_down_shape, capped-dt miss timer exceeds FALL_LYING_GRACE_SECONDS)--> standing
```

Both gates (shape *and* velocity) must be true together to leave "standing" via the normal
path — this is what stops a slow sit-down/crouch from being flagged as a fall. `hard_collapse`
is the one exception: a height loss that extreme bypasses velocity entirely, because some
falls (buttocks-first) barely move the box centroid even while height crashes, so velocity is
simply the wrong signal for that motion shape.

### Persistence decision — reused the PPE violation table, not the zone one
The plan originally suggested mirroring `record_zone_violation`, but that persists via
`zone_violation_service` and requires a `camera_zone_view_id` / `physical_zone_id` — there's
no such DB entity for a fall. Instead, `record_fall_violation` calls
`ppe_violation_service.persist_violation` directly with `violation_type="FALL"` and
`missing_equipment=["Fall detected"]` (that service's fields are generic free text, not a
PPE-specific enum, so this fits without any schema changes). This means fall violations:
- get real DB persistence "for free",
- show up in the existing `GET /violations` endpoint and the "Recent Violations" panel with
  zero extra backend/frontend plumbing for the persisted view.

### `backend/app/services/ppe_detector.py`
- `WorkerState` gained (across both sessions): `fall_state`, `floor_seconds`, `fall_reported`,
  `fall_lying_miss_seconds`, `recent_bbox_times` (monotonic timestamps paired with
  `recent_bboxes`), `standing_height_baseline`, `fall_recovery_seconds`,
  `fall_baseline_locked`, `last_gap_seconds` (true video-time elapsed since the worker's prior
  observation — see Session 2).
- `_merge_worker_observation`'s rolling-window cap widened to
  `max(2, FALL_WINDOW_FRAMES, VIDEO_STABILITY_WINDOW_FRAMES)` so `recent_bboxes` holds enough
  history for velocity, without shrinking the existing PPE stability window. Also appends to
  `recent_bbox_times` in lockstep, and records `worker.last_gap_seconds` every call.
- Fall state resets (all the fall-specific `WorkerState` fields above) on its own,
  longer-than-zone gap tolerance (`FALL_REENTRY_GAP_SECONDS`), decoupled from the zone-dwell
  reset (`VIDEO_ZONE_REENTRY_GAP_SECONDS`) — see Session 2 for why one shared threshold wasn't
  enough.
- `_find_spatial_worker_match` gets a looser IOU/center-distance bar
  (`FALL_REMATCH_IOU_THRESHOLD` / `FALL_REMATCH_CENTER_DISTANCE_RATIO`) specifically when the
  candidate worker is currently `"falling"`/`"lying"`, since the fall motion itself is what
  most often breaks ByteTrack's ID continuity.
- In `_real_video_pipeline`'s per-person loop, right after the existing zone/no-walkway block:
  computes fall features, advances the state machine, and on confirmation calls
  `record_fall_violation` and yields a `StreamEvent(event="fall_violation", ...)`.
- Diagnostic logging (`[FALL] ...`): a periodic snapshot every 30 frames (state, aspect ratio,
  velocity, window velocity, area fraction, height drop, floor seconds), a line on every state
  transition, and a line when a fall is confirmed/persisted. This was essential for debugging
  across both sessions — there was previously zero visibility into the fall pipeline.
- `_append_tracking_overlay_frame` sets `fall_status` on the overlay frame and treats
  `fall_state == "lying"` as a `"violation"` status — reuses the *existing* red-bbox
  rendering rule (no new frontend color logic needed).
- Fixed a real gap: `_real_process_video` (the synchronous, non-streaming `/predict-video`
  path) was silently dropping `fall_violation` events — it only checked for `"violation"` and
  `"zone_violation"`. Without this fix, a fall would show up live during streaming but vanish
  from the final synchronous response.

### `backend/app/schemas/streaming.py` / `backend/app/schemas/detection.py`
- `StreamEvent.event` Literal gained `"fall_violation"`.
- `TrackingOverlayFrame` gained `fall_status: Literal["standing","falling","lying"] | None`.

## Frontend changes

- `dashboard-shell.tsx`: WebSocket handler treats `"fall_violation"` the same as
  `"violation"` (pushes into the same `reports` array used by PPE incidents — no new state
  array needed).
- `filterTrackingOverlay`'s `hasViolation` check now also fires on `fall_status === "lying"`,
  so the red box survives even if the PPE/zone display toggles are off (fall isn't gated by
  either toggle).
- `result-panels.tsx` (`IncidentCard`): a violation with `violation_type === "FALL"` now gets
  a distinct purple **"Fall"** badge and **"Fall Detected"** title, instead of a generic
  "PPE" badge.
- `video-tracking-overlay.tsx`: adds a **"Fall detected"** text label on the video overlay
  when `fall_status === "lying"`.
- **Live-stream display gap fix** (affects *all* violation types, not just fall): the
  "Analysis Result / Incident Evidence" panel only used to render once `phase === "done"`,
  which only happens when a `"summary"` event arrives — and the backend only sends that after
  the video source ends. For a continuous RTSP feed that never happens, so the panel never
  showed *any* live incidents (PPE, zone, or fall) during an ongoing stream. Fixed by also
  rendering the panel while `isStreaming` is true, with a "Live Monitoring" header and
  appropriate empty-state text before the first incident arrives.

## How to verify a fall was detected

1. **Logs** — look for the `[FALL]` lines in the backend log:
   ```
   [FALL] Frame 300 worker 3: state=standing aspect_ratio=0.71 vy=-0.023 window_vy=-0.018 area_frac=0.02 height_drop=0.05 floor_s=0.00
   [FALL] Frame 306 worker 73: state standing -> falling (aspect_ratio=1.30, vy=0.19, window_vy=0.15, height_drop=0.41)
   [FALL] Frame 308 worker 73: state falling -> lying (aspect_ratio=1.30, vy=-0.01, window_vy=0.09, height_drop=0.44)
   [FALL] Frame 312 worker 73: fall CONFIRMED — recording violation
   [FALL] record_fall_violation returned: id=60 ...
   ```
2. **Live view** — the person's bounding box turns red with a "Fall detected" label as soon
   as `fall_state` becomes `"lying"` (before confirmation even completes); once confirmed,
   an incident card with a purple "Fall" badge appears in the "Live Monitoring" panel.
3. **Persisted view** — the "Recent Violations" panel (fetches `GET /violations`) shows the
   same incident after processing finishes or on manual refresh.
4. **Isolated file testing** (recommended over live RTSP for debugging the detection logic
   itself) — `POST /predict-video` with a raw video file (`enable_ppe`/`enable_zone` as form
   fields) runs the exact same `_real_video_pipeline` but reads the file directly and
   deterministically (no RTSP, no MediaMTX, no loop, no "Waiting for stream" stalls). This
   was the single most useful tool during Session 2 for separating real fall-logic bugs from
   RTSP/loop instability artifacts:
   ```bash
   curl -X POST "http://localhost:8000/predict-video" \
     -F "file=@clip.mp4;type=video/mp4" -F "enable_ppe=true" -F "enable_zone=false"
   ```
   The response's `tracking_overlay.frames` array has every processed frame's bbox and
   `fall_status`, useful for reconstructing exactly what the state machine saw.

## Session 2 — robustness hardening (found via a labeled test-set of real fall clips)

The first pass above worked on the original hand-tested clip, but testing against a broader
test-set (multiple real fall recordings, some with a single subject, some with 2–3 people)
surfaced several distinct, real bugs and one hard model-quality limitation. Each is described
with its root cause, since the fixes are non-obvious and the *why* matters for future tuning.

### 1. Timing correctness — `dt` must reflect true elapsed video-time, not an assumption
The original `vertical_velocity`/`floor_seconds` math assumed every processed frame represents
exactly `stride / fps` seconds of video content. That assumption breaks in three different
directions, and each one needed a different piece of the final fix:

- **Live RTSP with a stream stall**: the real gap between two frames can be *much larger*
  than nominal (the stream stalled, frames were delayed). Using the nominal value understates
  elapsed time, which *dilutes* velocity below threshold and *undercounts* floor-time.
  Fix: prefer the measured wall-clock gap (`recent_bbox_times[-1] - recent_bbox_times[-2]`)
  when it's larger than nominal.
- **Fast offline file processing** (`/predict-video`): frames are decoded/inferred as fast as
  the hardware allows, completely decoupled from the source's real fps. Wall-clock time
  between processed frames is *much smaller* than nominal (e.g. 27ms measured vs. 167ms
  nominal), which *inflates* velocity and *undercounts* floor-time in the opposite way. Fix:
  `dt = max(nominal, measured)` — take whichever is larger. This single formula correctly
  handles both directions: a live stall makes measured > nominal (measured wins, as needed);
  fast batch processing makes nominal > measured (nominal wins, as needed).
- **Multi-frame detection gaps** (a specific worker isn't detected for a few consecutive
  frames — confidence dip, brief occlusion by another person — then reappears): "nominal"
  dt was hardcoded as `stride / fps` (one polling step), which doesn't know the gap was
  actually several steps wide. Fix: `WorkerState.last_gap_seconds` is computed once, correctly,
  in `_merge_worker_observation` (`(frame_index - worker.last_frame) / fps`, the same
  computation already used for the zone/fall re-entry-gap checks) and passed into
  `compute_fall_features` as `nominal_gap_seconds`, replacing the naive `stride/fps` guess.

### 2. Height-collapse baseline must not chase its own decline
`standing_height_baseline` is an EMA of the worker's box height while "standing", used to
detect a sudden fractional drop (for falls that never widen the box — see Approach). Two
distinct baseline bugs were found and fixed:

- **Baseline chasing a gradual fall-in-progress**: if a fall unfolds over ~1–2 seconds (not
  instantaneous), each individual frame's drop is too small to cross
  `FALL_HEIGHT_DROP_RATIO_THRESHOLD` on its own, so the baseline kept adapting *during* the
  collapse itself, permanently eroding the reference and making the *eventual* drop look
  smaller than it really was relative to true standing height. Fix: baseline only adapts while
  drop is below the much smaller `FALL_BASELINE_FREEZE_RATIO` (0.12), so it locks in early,
  before a slow fall has eaten into it. Trade-off: someone who legitimately walks far from the
  camera (a real, gradual height decrease) also freezes early and reads as increasingly
  "collapsed" — a real false-trigger risk this introduces, worth watching.
- **Baseline re-corrupted by a false recovery**: once reset from `falling`/`lying` back to
  `standing` (e.g. a brief box-size blip), the baseline used to resume adapting immediately —
  which, if the "recovery" was actually just noise while the person was still down, bakes in
  the still-on-the-ground height as the new "normal", permanently hiding any later, deeper
  collapse. Two independent fixes, because a delay alone wasn't sufficient once the underlying
  decline is genuinely gradual:
  - `FALL_RECOVERY_CONFIRM_SECONDS`: require sustained (not momentary) "looks normal" readings
    before resuming baseline adaptation.
  - `fall_baseline_locked`: once a fall has *ever* been seriously suspected (entered
    `"falling"`) for a given worker, the baseline is frozen **permanently** for that worker —
    no further adaptation, ever. This was necessary because even a multi-second recovery delay
    eventually elapses, after which a sufficiently slow continued decline just resumes chasing
    the baseline down again.

### 3. A single noisy/borderline reading shouldn't single-handedly reset a real fall
Even with correct `dt`, one more failure mode remained: a worker genuinely still lying on the
ground, observed continuously for well over `FALL_CONFIRM_SECONDS`, could still fail to
confirm if a multi-frame detection gap (ordinary YOLO box jitter, not a real recovery) was
immediately followed by a single "looks recovered" sample on reappearance. Verified visually
by extracting the actual video frames — the person was in the *identical* fallen pose across
the entire gap, just with ~8% box-height jitter on one frame. Because `dt` correctly reflected
the true (possibly multi-second) gap by this point, that one borderline sample's implied
duration alone could cross the entire `FALL_LYING_GRACE_SECONDS` grace window in a single step.
Fix: `FALL_LYING_MISS_STEP_CAP_SECONDS` caps how much *any single observation* can contribute
to the recovery-grace timer, regardless of the real elapsed gap — forcing several separate
observations to agree before resetting to standing, rather than trusting one sample's
time-weight at face value. (Floor-time accumulation while genuinely down is *not* capped this
way — we want full credit for real elapsed down-time; only the "recovered-looking" grace timer
needed this defensive cap, since a wrongful reset is far more costly than a slightly-delayed
confirmation.)

### 4. RTSP/MediaMTX stream stability (infra, not fall-logic)
Independent of the state-machine work: the `OPENCV_FFMPEG_CAPTURE_OPTIONS` env var used for
RTSP sources was widened from just `rtsp_transport;tcp|timeout;3000000` to also include
`buffer_size`, `max_delay`, and `reorder_queue_size`, giving the FFmpeg demuxer more room to
absorb jitter (e.g. around the test publisher's `-stream_loop -1` restart point) before a read
stalls. If testing against a short looped clip, also worth moving the fall event away from the
clip's tail (loop-restart hiccups reliably land there) via `tpad=stop_mode=clone` padding, or
pre-rendering a long looped file so restarts are rare instead of eliminating the loop point.

### 5. Tried and reverted: relaxing detection confidence for the person class
**Root cause investigation**: on multi-person clips, a fallen/prone person's YOLO detection
confidence degrades steadily as their pose flattens (measured directly: ~0.87 while standing,
decaying to ~0.12–0.20 once fully prone, over roughly 1–2 seconds). Once a detection dips below
`CONFIDENCE_THRESHOLD` (0.3), Ultralytics discards it *before* any of our code sees it — the
person doesn't get occluded, their detection just silently stops existing that frame. This is
a genuine model-quality gap (the person-detector was evidently trained mostly on
upright/walking poses), not a code bug.

**Attempted fix**: lowered Ultralytics' own `model.track(conf=...)` floor to
`FALL_TRACK_MIN_CONFIDENCE` (0.15), re-filtered equipment classes back up to
`CONFIDENCE_THRESHOLD` in code (since only the person class has this problem), and added a
downstream admission gate: a person detected below 0.3 confidence was only processed further if
≥0.15 *and* it spatially matched an existing worker already `"falling"`/`"lying"`.

**Why it was reverted**: it did let through real signal that was previously discarded (one
run confirmed a fall that otherwise wouldn't have), but it also measurably increased track
identity churn and produced at least one clearly spurious reading (`aspect_ratio=2.09,
vy=-1.189` — a physically implausible instantaneous velocity, almost certainly a duplicate/
overlapping low-confidence box getting matched instead of the real one). Given detection
quality degrades similarly for *all* prone people in a scene (not just one), the fix also
didn't reliably solve the "only ever confirms one person out of several fallen" symptom — it's
close to a coin-flip per run regardless, because the underlying detection signal is genuinely
marginal for everyone in that pose, not just the one that happened to fail. Net assessment: the
added noise wasn't worth the inconsistent recall improvement. **The real fix is fine-tuning the
detection model on lying/prone-pose examples — a data/training project, out of scope for
logic-only tuning.**

## Known limitations / not yet done

- **Model confidence collapse for prone poses** (see Session 2 §5) — the single largest
  remaining source of missed falls on crowded/multi-person clips. Not fixable by
  state-machine tuning; needs the detection model fine-tuned on lying-person examples.
- **Multi-person occlusion / track loss near-simultaneous falls** — when two people fall in
  the same area, one person's track can be lost (to the confidence issue above) permanently
  before their floor-time accumulation completes, with no later observation for any gap
  tolerance to apply to. `FALL_REENTRY_GAP_SECONDS` helps when the *same* track ID
  reappears after a gap, but can't retroactively recover a track that never reappears at all.
- **Ambiguous camera angles for "crouch-then-fall"** — from an elevated/oblique camera, a
  person deliberately crouching to inspect something can look geometrically very similar to
  someone who has just fallen into a seated position (both are "compact" shapes). The
  `FALL_BASELINE_FREEZE_RATIO` fix (Session 2 §2) mitigates this by locking the baseline before
  the crouch erodes it, but raises false-trigger risk for people walking far from the camera —
  worth monitoring on real footage.
- **No frontend toggle for fall detection** — it's controlled only by the backend
  `FALL_ENABLED` setting, unlike PPE/zone which have UI toggles.
- **`ppeEnabled` gating quirk** — fall incidents share the `reports` array with PPE
  incidents in the live-stream incident *count*, so turning off the PPE toggle mid-stream
  will also hide fall incidents from that summary count (the persisted "Recent Violations"
  panel is unaffected).
- **Top-down cameras** — very-near-vertical camera angles weaken *all* box-geometry signals
  (aspect ratio and height-drop both), where standing and fallen people can look similarly
  compact from directly above. Most factory CCTV is mounted at an oblique angle, where this
  works; a footprint-area + centroid-stillness fallback is documented in
  `fall_detection_plan.md` if this turns out to matter on real De Heus footage.
- Frontend was verified by code review and a TypeScript typecheck, not a live browser
  click-through — worth a manual pass.
