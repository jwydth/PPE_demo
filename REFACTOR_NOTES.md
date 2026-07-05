# Modular structure refactor — summary

This branch (`refactor/modular-structure`) is a **structural refactor only**:
no API routes, request/response schemas, DB models, or detection logic
changed. Every step was verified against the Step 0 baseline (backend:
118 passed / 8 pre-existing failures; frontend: clean build) and ended at
that same baseline. This file records what moved where, and — most
importantly — the import rule that keeps PPE detection and zone monitoring
decoupled going forward.

## The rule

**`app/services/ppe/` and `app/services/zone_service.py` must never import
from each other.** `app/services/video_pipeline/` is the only module
allowed to import from both. If you find yourself reaching from one into
the other directly, the code you're writing belongs in `video_pipeline/`
instead.

This is enforced by convention and a comment at the top of `ppe/__init__.py`
and `zone_service.py`, not by tooling — there's no lint rule or import
boundary check wired up. When touching either module, a quick sanity check:

```bash
grep -rn "zone_service" backend/app/services/ppe/           # should be empty
grep -rn "services.ppe\b\|ppe_detector" backend/app/services/zone_service.py  # should be empty
```

## What moved where

### Root cleanup
- `check_model.py`, `test_ws.py` → `backend/scripts/` (alongside
  `check_gpu_runtime.py`).
- `context.md`, `automatically_updated_zone.md` → `docs/dev-notes/`.
- Added `backend/pytest.ini` (`testpaths = tests`) — needed once
  `scripts/` held a file matching pytest's `test_*.py` discovery pattern;
  `scripts/` holds manual/debug scripts, not automated tests.

### Backend: `app/services/ppe_detector.py` → `app/services/ppe/` + `app/services/video_pipeline/`

The original 2,307-line `ppe_detector.py` held 59 top-level symbols mixing
three concerns: pure PPE detection, zone monitoring, and auto-zone/sign
suggestions. The map in `backend/docs/ppe_detector_refactor_map.md` has the
full per-symbol breakdown; the short version:

- **`app/services/ppe/`** — pure PPE logic, zero dependency on zone
  monitoring:
  - `constants.py` — labels, colors, `MISSING_LABEL_ORDER`
  - `geometry.py` — bbox math (area, IoU, distance, stability)
  - `device.py` — inference device + tracker path resolution
  - `worker_tracking.py` — `WorkerState`, matching/merging/judgeability
  - `violation_matching.py` — `ViolationCase`, dedup/matching logic
  - `response_builder.py` — response/overlay assembly, frame encoding,
    video metadata
  - `detector.py` — the `PPEDetector` class; its three methods that
    interleave PPE + zone logic (`_real_video_pipeline`,
    `_mock_process_video`, `_mock_stream_video`) are thin delegators into
    `video_pipeline/`
  - `__init__.py` — re-exports the above; exposes `PPEDetector` lazily via
    `__getattr__` (a plain import would circularly import
    `video_pipeline`, which imports sibling `ppe/*` submodules, while
    `ppe/__init__.py` is still initializing)

- **`app/services/video_pipeline/`** — the orchestration boundary. Contains
  the actual bodies of `real_video_pipeline`, `mock_process_video`,
  `mock_stream_video` (per-frame PPE+zone+auto-zone interleaving), plus
  `_record_violation_case` / `_save_violation_snapshot` / `save_violation`
  (this trio needs `zone_service.COORD_SCALE` for drawing zone polygons on
  violation snapshots, so it can't live in `ppe/`).

- **`app/services/zone_service.py`** — left as a single file. At 464 lines
  it didn't warrant splitting into a `zone_monitoring/` package; only the
  boundary comment was added.

- **`app/services/ppe_detector.py`** — now a 48-line backward-compatible
  re-export shim, so `from app.services.ppe_detector import PPEDetector`
  and `from app.services import ppe_detector as ppe` keep working
  unchanged for routers, scripts, and tests.

**Two things worth knowing about if you're debugging in this area:**
- `PPEDetector.predict()` calls `self._real_predict`, which is not defined
  anywhere in the codebase — a latent bug that predates this refactor,
  found while mapping the file, deliberately left alone (behavior
  preservation, not a structural concern).
- Five functions have zero callers anywhere in the repo (`_is_near_frame_edge`,
  `_is_bbox_stable`, `_find_existing_case`, `_format_track_ids`,
  `_append_unmatched_equipment`) — moved as-is, not deleted, since removing
  them wasn't part of this refactor's scope.

### Frontend: `dashboard-shell.tsx` → hooks + components

The 1,985-line file is now ~700 lines plus:

- **`frontend/src/hooks/`**:
  - `useDetectionUpload.ts` — file/image/video result state
  - `useZoneDrawing.ts` — draw/modify/drag/curve/point-insert state machine
  - `useLiveStream.ts` — WebSocket lifecycle, live/RTSP toggle, PPE/zone
    settings sync, video playback sync
  - `useAutoZoneSuggestions.ts` — sign-detection-driven zone/PPE
    suggestions (consumes suggestion state from `useLiveStream`, since
    that's where the websocket events actually arrive — see note below)
  - `camera-panel-types.ts` — shared `AnalysisPhase` / `DraftZone` types

- **`frontend/src/components/dashboard/`** — presentational components:
  `top-bar.tsx`, `zone-sidebar.tsx`, `zone-button.tsx`, `metric-card.tsx`,
  `icon-button.tsx` (the originally-listed extractions), plus
  `zone-overlay-svg.tsx`, `zone-config-panel.tsx`, `analysis-result-panel.tsx`
  (extracted in addition, because the JSX bulk — not hook logic — was
  what kept `CameraPanel` large).

`CameraPanel` itself is ~400 lines — close to but not fully under the
~300-line target. Further splitting the remaining upload/toggle JSX
started to feel forced, so it was left as-is.

**Two non-obvious things from that pass:**
- `useAutoZoneSuggestions` takes suggestion state as *parameters* rather
  than owning it, to avoid a circular dependency with `useLiveStream`
  (which needs `sendMessage`/`isStreaming` back from `useAutoZoneSuggestions`
  if the state lived there instead).
- Ref callbacks passed across a hook boundary (`ref={hook.setter}`) trip a
  newer ESLint rule (`react-hooks/refs`) into flagging every other property
  read on that hook's return value as "cannot access ref during render."
  Wrapping the callback locally (`ref={(el) => hook.setter(el)}`) avoids it
  with no behavior change — see `zone-overlay-svg` and `videoElement`
  usage in `dashboard-shell.tsx` for the pattern if you add another DOM ref
  to a hook.

## Not done here

- `refactor/modular-structure` has not been merged or deleted — that's a
  separate decision.
- No dead code was removed, no bugs were fixed, no lint/type issues
  outside the touched files were addressed — all confirmed pre-existing
  against the Step 0 baseline.
