# Project Context: De Heus PPE Safety Monitor

## 1. Project Overview
The **De Heus PPE Safety Monitor** is an industrial safety system designed to monitor factory floor compliance with Personal Protective Equipment (PPE) standards using computer vision. It identifies workers and their equipment (helmets, vests) and analyzes spatial violations based on configurable safety zones.

## 2. Technical Stack
### Backend (FastAPI)
- **Framework:** FastAPI (Python 3.11+)
- **Computer Vision:** YOLOv8 (Ultralytics) for detection and tracking.
- **Tracking:** BoT-SORT / ByteTrack (YOLOv8 defaults) for maintaining worker identity across frames.
- **Inference Device:** Supports CPU, CUDA (Auto-detection), and a "Mock Mode" for GPU-less development.
- **Database:** SQLite (SQLModel/SQLAlchemy) for persistence.
- **Image Processing:** OpenCV (cv2) for spatial logic, drawing, and snapshot generation.

### Frontend (Next.js)
- **Framework:** Next.js 14 (App Router, TypeScript)
- **Styling:** Tailwind CSS
- **Interactive Canvas:** Fabric.js (v5) for drawing safety zones and rendering detections.
- **API Client:** Standard `fetch` API wrapped in `lib/api.ts`.

## 3. Core Domain Logic

### 3.1 Spatial Incursion Engine
The system maps perspective camera views to a logical coordinate system for accurate spatial testing.
- **Coordinate System:** All spatial logic operates on a **1000x1000 normalized grid**.
- **Normalization:** Points are stored as `0.0 to 1.0` in the database and UI, then scaled to `1000` for OpenCV processing.
- **Worker Foot-Point:** To determine if a person is "in" a zone, the system calculates the bottom-center of their bounding box: `( (x1 + x2) / 2, y2 )`.
- **Zone Types & Behavior:**
    - **RESTRICTED:** A violation is triggered if a person's foot-point enters the zone for longer than the `dwell_threshold`.
    - **WALKWAY:** A violation is triggered if a person's foot-point stays *outside* the zone for longer than the `dwell_threshold`.

### 3.2 PPE Compliance Logic
- **Overlap Check:** Equipment (Helmet/Vest) is assigned to a person if it significantly overlaps their bounding box (calculated via IoU and relative area).
- **Stability Window:** To prevent flickering, violations are only reported if missing PPE is confirmed over multiple frames (defined by `VIDEO_VIOLATION_CONFIRM_SECONDS`).
- **Memory:** The system "remembers" recently seen PPE to avoid false violations during brief occlusions.

## 4. Database Schema (SQLite)

### `violations` (PPE breaches)
- `id`: Primary Key
- `timestamp`: ISO-8601 string
- `violation_type`: Enum (`missing_helmet`, `missing_vest`, `missing_helmet_and_vest`)
- `details`: Text description
- `snapshot_path`: Filename of the generated evidence image
- `video_name`: Source file name
- `frame_index`: Frame where violation was confirmed
- `track_id`: YOLOv8 tracker ID

### `zones` (Safety Area Configuration)
- `id`: Primary Key
- `video_name`: Associated video source
- `zone_name`: User-defined label
- `zone_type`: Enum (`RESTRICTED`, `WALKWAY`)
- `dwell_threshold_seconds`: Time allowed before breach is recorded
- `is_active`: Boolean toggle
- `ui_shape_data`: Raw Fabric.js JSON for UI reconstruction
- `flattened_coordinates`: JSON array of normalized `[x, y]` points for backend processing

### `zone_violations` (Spatial breaches)
- `id`: Primary Key
- `zone_id`: Link to `zones`
- `zone_name`, `zone_type`: Denormalized for reporting
- `track_id`: ID of the worker
- `timestamp`: ISO-8601 string
- `video_name`, `frame_index`: Context
- `snapshot_path`: Evidence image with zone polygon overlay

## 5. Key Architecture & File Roles

### Backend Services (`/backend/app/services`)
- `ppe_detector.py`: The "Brain". Manages the YOLOv8 tracking loop, integrates PPE checks, and calls `zone_service`.
- `zone_service.py`: Encapsulates zone loading, foot-point calculation, and incursion testing.
- `violation_store.py`: Handles all SQLite CRUD operations and directory management for snapshots.
- `spatial.py`: Low-level OpenCV utilities (Point-in-Polygon).

### Backend Routers (`/backend/app/routers`)
- `detection.py`: Endpoints for single image (`/predict`) and video (`/predict-video`) processing.
- `zones.py`: CRUD endpoints for safety zones and fetching history.

### Frontend Components (`/frontend/components`)
- `ZoneDrawingCanvas.tsx`: The complex Fabric.js editor for zones. Handles normalization and linearization of circles/paths.
- `BoundingBoxCanvas.tsx`: Renders the "live" results of detection/monitoring.
- `ViolationReportCard.tsx` / `ZoneViolationCard.tsx`: Display items for the unified history feed.

## 6. Snapshot Evidence Generation
When a violation occurs, the system generates a `.jpg` snapshot:
- **PPE Violation:** Draws a red box around the person and labels missing items.
- **Zone Violation:** Draws the **semi-transparent colored polygon** of the breached zone (Red for Restricted, Green for Walkway) and a box around the person.

## 7. Operational Commands
- **Backend:** `uvicorn app.main:app --app-dir backend --reload --port 8000`
- **Frontend:** `npm run dev` (Port 3000)
- **Environment:** `MODEL_PATH`, `VIOLATION_DB_PATH`, and `SNAPSHOT_DIR` are configurable via `.env`.
