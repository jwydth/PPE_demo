# Auto-Zone from Sign Detection — Feature Summary

Branch: `feature/camera-rtsp`  
Last updated: 2026-06-15

---

## What this feature does

When a video is streamed, a second YOLO model (`sign_model.pt`) scans every N frames for safety signs (e.g. "No Thoroughfare", "Slippery"). When a sign is detected with enough confidence across multiple frames, a **zone suggestion** is emitted to the frontend as a dashed orange polygon placed on the floor below the sign. The user can:

- **Accept** → zone is saved to DB and immediately enforced in the running pipeline (no restart needed)
- **Dismiss** → suggestion is suppressed for the rest of the session

Accepted zones behave identically to hand-drawn zones: incursion is tracked via foot-point-in-polygon, dwell time accumulates, and violations are recorded once the threshold is crossed.

---

## Files changed

### Backend

| File | Change |
|---|---|
| `app/core/config.py` | 8 new settings for sign detection (see below) |
| `app/schemas/streaming.py` | Added `"zone_suggestion"` to `StreamEvent.event` Literal |
| `app/schemas/zone.py` | New `ZoneSuggestion` pydantic model |
| `app/services/auto_zone.py` | **New file** — pure sign-detection logic (no DB/IO) |
| `app/services/ppe_detector.py` | Loads sign model, runs it every N frames, yields suggestions, hot-reloads zones, retroactive violation check, GPU warm-up |
| `app/services/zone_service.py` | `record_zone_violation` now catches snapshot/DB errors and logs them instead of crashing silently |
| `app/routers/streaming.py` | `listen_for_settings` handles `dismiss_suggestion` and `reload_zones` WS events; `settings_state` carries `dismissed_signatures` and `reload_zones` flags |

### Frontend

| File | Change |
|---|---|
| `src/types/zone.ts` | New `ZoneSuggestion` interface |
| `src/components/ppe/video-tracking-overlay.tsx` | New `SuggestionOverlayLayer` component (dashed orange polygon + Accept/Dismiss popup) |
| `src/components/dashboard/dashboard-shell.tsx` | WS handler for `zone_suggestion`; `handleAcceptSuggestion` / `handleDismissSuggestion`; buffer-before-play sync logic |

---

## New config settings (`app/core/config.py`)

```python
SIGN_MODEL_PATH: str = "weights/sign_model.pt"       # path to the sign YOLO weights
SIGN_CONFIDENCE_THRESHOLD: float = 0.3               # min confidence to count a detection
SIGN_CLASS_ZONE_MAP: dict[int, str] = {2: "RESTRICTED", 3: "RESTRICTED"}  # class → zone type
SIGN_CLASS_NAMES: dict[int, str] = {2: "P004_NoThoroughfare", 3: "W011_Slippery"}
AUTO_ZONE_BUFFER_RATIO: float = 0.25                 # zone extends 25% of frame below/beside sign
SIGN_PASS_FRAME_INTERVAL: int = 15                   # run sign model every N frames
AUTO_ZONE_CONFIRM_FRAMES: int = 3                    # detections needed before emitting suggestion
AUTO_ZONE_DEDUPE_GRID: float = 0.05                  # quantisation grid for deduplication (5% of frame)
```

Override any of these in `.env` without touching code.

---

## Key design decisions

### Zone polygon placement
`derive_zone_polygon` in `auto_zone.py` creates a rectangle that starts at the **bottom edge of the sign** and extends downward. Width is centred on the sign and spans at least `AUTO_ZONE_BUFFER_RATIO × frame_width` on each side. This covers the floor area workers walk through rather than the sign itself.

### Deduplication
`SignZoneRegistry` quantises each detected sign's centre into a `AUTO_ZONE_DEDUPE_GRID` grid cell. If a second signature fires within `2 × grid` distance of an already-emitted one (sign jitter across frames), it is suppressed — so only **one popup per physical sign**.

### State machine (per signature)
```
COUNTING → EMITTED → DISMISSED | ACCEPTED   (all terminal, no re-emit)
```

### Hot-reload on Accept
When the user clicks Accept:
1. Zone is saved to DB
2. Frontend sends `{"event": "reload_zones"}` over the open WebSocket
3. Pipeline reloads zones on the next frame without restarting
4. A **retroactive check** replays the last 30 s of foot-point history against the new zone and fires any violations that were missed while the suggestion was pending

### Streaming sync (bounding box delay)
- Backend removed fps-paced throttle; frames are sent as fast as inference finishes
- Frontend **buffers** until 2 s of processed frames exist before starting video playback
- Auto-pause triggers if video gets more than **15 frames** ahead of inference; resumes when within **5 frames**
- GPU warm-up: a dummy inference runs at server startup so CUDA is hot before the first real video

---

## Runtime requirements

- `sign_model.pt` must exist at `backend/weights/sign_model.pt` (or override `SIGN_MODEL_PATH` in `.env`). If missing, the feature is silently disabled and normal PPE detection continues.
- PyTorch with CUDA support (`torch==2.12.0+cu126`) is installed. RTX 3050 confirmed working.
- `.env` has `VIDEO_FRAME_STRIDE=2` set for better GPU throughput.

---

## Bug fixes & improvements (post-initial implementation)

### PPE false positives on corner entry
**Problem:** Workers entering from the edge of the frame were incorrectly flagged for missing a helmet because the model couldn't see the helmet during the first few frames.

**Root cause:** `_is_worker_judgeable` had a hardcoded 0.05 s grace period (1–2 frames) and a 1% edge margin — both far too lenient.

**Fix:** Now uses `VIDEO_NEW_TRACK_GRACE_SECONDS` (default `0.5 s`) and `VIDEO_EDGE_MARGIN_RATIO` (default `5%`) from config, consistent with the rest of the system.

---

### Zone re-entry not triggering a new incident
**Problem:** After a worker was flagged for entering a restricted zone and then walked back out and in again, no second incident was recorded.

**Root cause:** `reported_zones` and `zone_dwell` were never cleared when the worker exited a zone — they persisted for the lifetime of the `WorkerState`.

**Fix:** Added explicit exit-reset for both zone types:
- **RESTRICTED:** when person is detected *outside* the zone, `zone_dwell` resets to 0 and the zone is removed from `reported_zones`.
- **WALKWAY:** when person is detected *back inside* the walkway, same reset fires.

---

### Tracker gap while inside zone causing duplicate violations
**Problem:** If the tracker dropped a person briefly (e.g. sign occlusion, ~1 s) while they were still physically inside a restricted zone, the gap-based zone reset fired and the person accumulated dwell from zero again — triggering a duplicate violation for the same continuous incursion.

**Root cause:** The gap-based reset in `_merge_worker_observation` was unconditional — it reset all zone state for any gap ≥ `VIDEO_ZONE_REENTRY_GAP_SECONDS`, even if the person was inside the zone when the tracker dropped them.

**Fix:** Added `zone_last_in: dict[int, bool]` to `WorkerState`. Updated every frame the worker is detected in any zone. The gap-based reset now only fires for zones where `zone_last_in[cv_id]` was `False` (person was last seen *outside*). Workers occluded while inside a zone keep their accumulated dwell and `reported_zones` intact.

---

### Retroactive check never fired on zone toggle (only on hot-reload)
**Problem:** The retroactive foot-history check only ran when a zone was drawn mid-stream (`reload_zones` signal). If zones were already loaded but zone detection was toggled from disabled → enabled, all foot history accumulated during the disabled period was ignored.

**Fix:** Added `prev_zone_enabled` tracking in the pipeline loop. When `curr_zone` transitions `False → True`, the retroactive check now fires against all existing zones and the current `foot_history`.

---

### Retroactive dwell calculation summed all in-zone frames ever
**Problem:** The old retroactive dwell was `sum(stride/fps for each in-zone frame)`, which accumulated across multiple separate visits. A person who entered briefly twice could exceed the threshold even if neither visit was long enough individually.

**Fix:** Replaced with a **max-continuous-streak** calculation — only the longest unbroken sequence of in-zone frames counts. Matches real-time dwell behaviour exactly.

---

### Retroactive check used stale `response` from previous frame
**Problem:** `_build_response` was called later in the loop body, so `response.persons` used inside the retroactive block was one frame behind.

**Fix:** The retroactive block now calls `_extract_result_boxes` and `_build_response` itself on the current frame before looking up persons.

---

### Retroactive check silently skipped workers not visible in current frame
**Problem:** The retroactive violation required `matched_person` to be in the current frame. Workers who were no longer on screen when the zone was enabled were silently skipped with `continue`.

**Fix:** When `matched_person` is `None`, a synthetic `PersonResult` is constructed from the worker's `last_bbox` and `track_id`. The violation is recorded using this stand-in so the snapshot and DB entry are still created.

---

### Ongoing live presence blocked after retroactive violation
**Problem:** After a retroactive violation was saved, `record_zone_violation` added the zone's `cv_id` to `worker.reported_zones`. When the same worker (same `WorkerState`) continued walking in the zone in real-time, `already_reported=True` from the very first frame — the current live entry was permanently blocked.

**Fix:** After all retroactive violations are saved, zone state (`reported_zones`, `zone_dwell`, `zone_last_in`) is reset for every worker that had a retroactive violation fired. This lets real-time zone detection start fresh from that point and capture the current live incursion as a separate incident.

---

## New config settings added

```python
VIDEO_ZONE_REENTRY_GAP_SECONDS: float = 1.0   # tracker-drop gap treated as zone exit
```

---

## New files added

| File | Purpose |
|---|---|
| `backend/scripts/test_sign_detection.py` | Stand-alone script to test `sign_model.pt` on a single image — draws bounding boxes, prints class/confidence/coords, saves annotated result |

Usage:
```bash
# from backend/ directory
python scripts/test_sign_detection.py path/to/image.jpg
python scripts/test_sign_detection.py path/to/image.jpg --conf 0.2 --out result.jpg
```

---

## What still needs work / known gaps

- [ ] **Accept during streaming**: if the worker passes through the zone in the window between sign detection and the user clicking Accept, the retroactive check covers ~30 s of history — but if the passage happened earlier it will be missed. Consider lowering `AUTO_ZONE_CONFIRM_FRAMES` so suggestions appear faster.
- [ ] **No `accept_suggestion` WS message to backend**: the registry marks suggestions as EMITTED (terminal) so they won't re-fire, but the backend never explicitly transitions them to ACCEPTED. This is fine for now but means re-running the same video will re-emit suggestions for the same signs.
- [ ] **Sign model classes**: currently classes 2 and 3 are mapped. Update `SIGN_CLASS_ZONE_MAP` and `SIGN_CLASS_NAMES` in `.env` to match actual class IDs in your `sign_model.pt`.
- [ ] **Zone editing after accept**: accepted auto-zones appear in the saved zones list and can be edited/deleted via the existing zone UI.
- [ ] **Tests for pipeline integration**: `backend/tests/test_auto_zone.py` covers the pure logic (22 tests pass). Pipeline-level tests for hot-reload and retroactive check are not yet written.
