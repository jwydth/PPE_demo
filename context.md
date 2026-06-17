# Project Context: De Heus PPE Safety Monitor

## Overview

The application detects PPE compliance (helmets and vests) and monitors configurable safety zones in uploaded images and videos. It is designed for smart factory safety monitoring.

## Architecture

### Backend

- **Framework:** FastAPI provides the REST API and WebSocket endpoints.
- **Detection & Tracking:** YOLOv8 (via `ultralytics` library) performs person, helmet, and vest detection. It also handles object tracking for video processing.
- **Video Processing Engine:** A unified generator-based pipeline handles both batch processing and real-time simulated streaming. It yields frame-by-frame events, tracking overlays, and immediate violation alerts. The pipeline supports **dynamic settings updates** (PPE/Zone toggles) in real-time without interrupting the stream.
- **Database:** PostgreSQL with SQLModel (SQLAlchemy) stores cameras, zones, and violation history.
- **Storage:** MinIO is used for persistent evidence storage (snapshots). Local storage is used for temporary snapshots during processing and storing uploaded videos (`/storage/uploads`).
- **Inference Logic:**
    - **PPE Detection:** Checks overlap between person and equipment (helmet/vest) detections.
    - **Zone Monitoring:** Performs point-in-polygon checks using a normalized 1000x1000 grid. Foot points of tracked persons are used for incursion detection.
    - **Dwell Threshold:** Violations are triggered when a person stays in a restricted zone (or outside a walkway) longer than a configured threshold.
    - **Persistence Resilience:** Evidence counters (e.g., consecutive frames missing a helmet) are preserved even if a person momentarily moves near a frame edge or becomes unstable, ensuring reliable incident logging across transient tracking gaps.

### Frontend

- **Framework:** Next.js (App Router) with TypeScript.
- **Styling:** Tailwind CSS (v4).
- **Icons:** Lucide React.
- **Zone Drawing:** Custom SVG overlay on the video element for drawing and displaying polygons. Supports advanced interactions like vertex dragging, edge-click point insertion, and synchronized sidebar configuration.
- **Dashboard:** A single-page dashboard (`DashboardShell`) that manages camera feeds, violation logs, safety rules, and orchestrates real-time WebSocket communication for video streams. It supports **real-time settings synchronization**, sending UI toggle changes (PPE/Zone) to the backend instantly via the active WebSocket.

## Data Model

- **Camera:** Represents a video source (often identified by filename in this demo).
- **Zone:** Configuration for a safety zone (RESTRICTED or WALKWAY). Includes `dwell_threshold_seconds`, `ui_shape_data`, and `normalized_coordinates`.
- **PPEViolation:** Recorded incident of missing PPE.
- **PPEViolationSubject:** Specific person in a PPE violation, listing missing equipment.
- **ZoneViolation:** Recorded incident of a zone incursion.
- **StreamEvent:** (Runtime) Event-driven schema for WebSockets, representing frames, active violations, dynamic settings updates, and processing summaries.

## Session Updates (June 2026)

- **Fixed Simulated Streaming Integration:** 
    - Resolved a critical bug where `frame_width` and `frame_height` were missing from the stream, breaking frontend bounding box scaling.
    - Removed ~250 lines of redundant/duplicate code in `ppe_detector.py` to establish a single source of truth for the inference pipeline.
    - Increased frontend frame sync tolerance (from 2 to 30 frames) to handle latency during slow CPU-based inference.
- **Resolved Violation Persistence:** 
    - Loosened judgeability constraints (grace period, height ratios) to ensure reliable detection of smaller/distant workers.
    - Fixed a bug where evidence counters were aggressively reset during transient tracking issues (e.g., person near edge), ensuring violations are eventually confirmed and logged to PostgreSQL.
- **Implemented Dynamic Monitoring Toggles:** 
    - Users can now toggle "PPE Detection" and "Zone Monitoring" in real-time during a live stream.
    - Added `asyncio.sleep(0.01)` in the detection loop to prevent event loop starvation, ensuring the backend stays responsive to UI control signals during intensive inference.
- **Log Optimization:** 
    - Cleaned up the backend console by removing all verbose/informational logs.
    - Standardized diagnostic logs to use human-readable **Track IDs** (e.g., `T1`) to match the frontend monitoring experience.


## Session Updates (June 2026 — Live RTSP Streaming)

- **Real RTSP Live Feed:** Added support for live camera streams (e.g. MediaMTX at `rtsp://localhost:8554/mystream`). Live sources are detected by URL scheme (`rtsp://`, `rtmp://`, `http(s)://`); the `StreamEvent` schema now carries an `image_base64` field so the frontend renders annotated frames directly via an `<img>` tag for live feeds (vs. the synced `<video>` overlay used for uploaded files).
- **Fixed `KeyError(0)` crash:** Removed a stale-tracker reset (`predictor.trackers = {}`) in `ppe_detector.py`. Wiping the dict made Ultralytics raise `KeyError(0)` on the first frame, which surfaced to the client as `{"event":"error","data":{"message":"0"}}` immediately after the `start` event.
- **Single-stream lifecycle with supersede:** `PPEDetector` is a singleton with one shared YOLO model, so only one `model.track()` session can run at a time, guarded by an `asyncio.Lock`. A new WebSocket now **supersedes** the previous one via a shared cancel event instead of waiting on it. This handles browsers that leak sockets on reload / React StrictMode double-mount and never send a close frame — the newest client always wins, and superseded streams close cleanly (code 1000).
- **Reliable disconnect detection:** The settings-listener task sets a `disconnect_event` on `WebSocketDisconnect` (or any receive error); the send loop breaks on it so an infinite RTSP stream stops when the client genuinely leaves. Added `await asyncio.sleep(0)` per frame in the streaming loop because `send_text()` often completes without suspending, which previously starved the listener task and stopped it from ever reading the close frame.
- **Correct teardown ordering:** The generator is now fully closed (`aclose`) **before** the lock is released, so the next stream never starts `model.track()` while the previous session is still tearing down the shared model (which corrupted the predictor and stalled the new stream). RTSP read blocking is capped via `OPENCV_FFMPEG_CAPTURE_OPTIONS` (`rtsp_transport;tcp|timeout;3000000`); lock-acquire timeout is 12s for margin.
- **Per-connection tracing:** Added a monotonic connection id (`[conn N]`) to all lifecycle logs (accept, lock acquire/release, supersede, disconnect, cleanup) for debuggable stream handoffs.

> Note: `wscat` was used only as a raw-WebSocket debugging client to isolate backend vs. frontend issues. It is not a runtime dependency — the browser is the real client.

## Folder Structure

### Backend (`/backend`)
- `app/main.py`: Application entry point and router inclusion.
- `app/core/config.py`: Configuration and environment settings.
- `app/models/`: SQLModel table definitions (`camera.py`, `zone.py`, `ppe_violation.py`, `zone_violation.py`).
- `app/repositories/`: Database abstraction layer (CRUD).
- `app/routers/`: API endpoints (`detection.py`, `streaming.py`, `zones.py`, `testing.py`).
- `app/schemas/`: Pydantic models for API requests/responses and streaming events.
- `app/services/`:
    - `ppe_detector.py`: YOLOv8 unified inference engine (batch and stream).
    - `zone_service.py`: Zone management and incursion detection.
    - `ppe_violation_service.py` / `zone_violation_service.py`: Violation persistence logic.
    - `spatial.py`: Geometric utilities (point-in-polygon).
- `app/storage/`: Evidence storage handling (MinIO) and local file management.

### Frontend (`/frontend`)
- `src/app/`: Next.js App Router pages and layout.
- `src/components/`:
    - `dashboard/`: `DashboardShell.tsx` (main UI) and data constants.
    - `ppe/`: UI components for detection results, video overlays, and file uploads.
- `src/lib/ppe-api.ts`: API client for communicating with the backend (REST and WebSockets).
- `src/types/`: TypeScript interfaces for detection, zone data, and streaming events.

## Key Workflows

1. **Zone Configuration:** Users can upload a video and define safety zones using a custom SVG tool.
    - **Drawing:** Users click to define vertices for new polygon zones.
    - **Modification:** Existing zones can be selected to move the entire shape or drag individual vertices.
    - **Advanced Editing:** A specialized "Add Point" mode allows users to insert new vertices by clicking on polygon edges.
    - **State Sync:** The configuration sidebar (name, type) is conditionally enabled and synchronized in real-time with the selected zone.
    - **Persistence:** Configurations are stored in the backend and associated with specific video filenames.
2. **Inference (Simulated Streaming):**
    - For images: A single-pass detection returns PPE status.
    - For videos: The system uses an event-driven **Simulated Streaming** flow.
        - The frontend uploads the video (`POST /upload-video`).
        - The frontend connects to a WebSocket (`ws://.../ws/stream`).
        - The backend processes the local file frame-by-frame, applying a "Pacer" to match the original FPS.
        - Bounding boxes and status updates are pushed instantly to the frontend to sync with the HTML5 `<video>` player.
3. **Violation Reporting:** When a violation is confirmed via dwell thresholds, an event is immediately dispatched over the WebSocket. A snapshot is taken, annotated, saved to MinIO, and the event is recorded in PostgreSQL. The frontend instantly adds this to the "Incident Evidence" sidebar.

## Commands

```bash
# Backend
uvicorn app.main:app --app-dir backend --reload --port 8000
python -m pytest

# Frontend
cd frontend
npm run dev
```
