# Project Context: De Heus PPE Safety Monitor

## Overview

The application detects PPE compliance (helmets and vests) and monitors configurable safety zones in uploaded images and videos. It is designed for smart factory safety monitoring.

## Architecture

### Backend

- **Framework:** FastAPI provides the REST API.
- **Detection & Tracking:** YOLOv8 (via `ultralytics` library) performs person, helmet, and vest detection. It also handles object tracking for video processing.
- **Database:** PostgreSQL with SQLModel (SQLAlchemy) stores cameras, zones, and violation history.
- **Storage:** MinIO is used for persistent evidence storage (snapshots). Local storage is used for temporary snapshots during processing.
- **Inference Logic:**
    - **PPE Detection:** Checks overlap between person and equipment (helmet/vest) detections.
    - **Zone Monitoring:** Performs point-in-polygon checks using a normalized 1000x1000 grid. Foot points of tracked persons are used for incursion detection.
    - **Dwell Threshold:** Violations are triggered when a person stays in a restricted zone (or outside a walkway) longer than a configured threshold.

### Frontend

- **Framework:** Next.js (App Router) with TypeScript.
- **Styling:** Tailwind CSS (v4).
- **Icons:** Lucide React.
- **Zone Drawing:** Custom SVG overlay on the video element for drawing and displaying polygons (replaces previous Fabric.js mentions).
- **Dashboard:** A single-page dashboard (`DashboardShell`) that manages camera feeds, violation logs, and safety rules.

## Data Model

- **Camera:** Represents a video source (often identified by filename in this demo).
- **Zone:** Configuration for a safety zone (RESTRICTED or WALKWAY). Includes `dwell_threshold_seconds`, `ui_shape_data`, and `normalized_coordinates`.
- **PPEViolation:** Recorded incident of missing PPE.
- **PPEViolationSubject:** Specific person in a PPE violation, listing missing equipment.
- **ZoneViolation:** Recorded incident of a zone incursion.

## Folder Structure

### Backend (`/backend`)
- `app/main.py`: Application entry point and router inclusion.
- `app/core/config.py`: Configuration and environment settings.
- `app/models/`: SQLModel table definitions (`camera.py`, `zone.py`, `ppe_violation.py`, `zone_violation.py`).
- `app/repositories/`: Database abstraction layer (CRUD).
- `app/routers/`: API endpoints (`detection.py`, `zones.py`, `testing.py`).
- `app/schemas/`: Pydantic models for API requests/responses.
- `app/services/`:
    - `ppe_detector.py`: YOLOv8 inference and tracking logic.
    - `zone_service.py`: Zone management and incursion detection.
    - `ppe_violation_service.py` / `zone_violation_service.py`: Violation persistence logic.
    - `spatial.py`: Geometric utilities (point-in-polygon).
- `app/storage/`: Evidence storage handling (MinIO).

### Frontend (`/frontend`)
- `src/app/`: Next.js App Router pages and layout.
- `src/components/`:
    - `dashboard/`: `DashboardShell.tsx` (main UI) and data constants.
    - `ppe/`: UI components for detection results, video overlays, and file uploads.
- `src/lib/ppe-api.ts`: API client for communicating with the backend.
- `src/types/`: TypeScript interfaces for detection and zone data.

## Key Workflows

1. **Zone Configuration:** Users can upload a video and draw polygons over the frame to define safety zones. These are saved to the backend.
2. **Inference:**
    - For images: A single-pass detection returns PPE status.
    - For videos: Tracking-based inference monitors persons across frames, applying temporal filters to reduce false positives and detecting zone incursions based on dwell time.
3. **Violation Reporting:** When a violation is confirmed, a snapshot is taken, annotated with the violation details, and saved to MinIO. The event is recorded in PostgreSQL.

## Commands

```bash
# Backend
uvicorn app.main:app --app-dir backend --reload --port 8000
python -m pytest

# Frontend
cd frontend
npm run dev
```
