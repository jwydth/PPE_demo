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

## What still needs work / known gaps

- [ ] **Accept during streaming**: if the worker passes through the zone in the window between sign detection and the user clicking Accept, the retroactive check covers ~30 s of history — but if the passage happened earlier it will be missed. Consider lowering `AUTO_ZONE_CONFIRM_FRAMES` so suggestions appear faster.
- [ ] **No `accept_suggestion` WS message to backend**: the registry marks suggestions as EMITTED (terminal) so they won't re-fire, but the backend never explicitly transitions them to ACCEPTED. This is fine for now but means re-running the same video will re-emit suggestions for the same signs.
- [ ] **Sign model classes**: currently classes 2 and 3 are mapped. Update `SIGN_CLASS_ZONE_MAP` and `SIGN_CLASS_NAMES` in `.env` to match actual class IDs in your `sign_model.pt`.
- [ ] **Zone editing after accept**: accepted auto-zones appear in the saved zones list and can be edited/deleted via the existing zone UI.
- [ ] **Tests for pipeline integration**: `backend/tests/test_auto_zone.py` covers the pure logic (22 tests pass). Pipeline-level tests for hot-reload and retroactive check are not yet written.
