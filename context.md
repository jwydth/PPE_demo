# Project Context: De Heus PPE Safety Monitor

## 1. Project Overview
The **De Heus PPE Safety Monitor** is an industrial safety system designed to monitor factory floor compliance with Personal Protective Equipment (PPE) standards using computer vision (YOLOv8). It identifies workers and their equipment (helmets, vests, etc.) and analyzes spatial violations based on configurable safety zones.

## 2. Technical Stack
### Backend (FastAPI)
- **Framework:** FastAPI (Python)
- **Inference Engine:** YOLOv8 (Ultralytics), supports CUDA acceleration and a "Mock Mode" for development.
- **Database:** SQLite (via SQLModel/SQLAlchemy) for storing zone configurations and violations.
- **Spatial Analysis:** OpenCV (cv2) for homography transformations and point-in-polygon tests.
- **Static Assets:** Serves snapshots of violations.

### Frontend (Next.js)
- **Framework:** Next.js 14 (App Router, TypeScript)
- **Styling:** Tailwind CSS
- **Canvas Interaction:** Fabric.js (v5) for drawing and managing safety zones.
- **Components:** React Dropzone for uploads, custom canvas overlays for bounding boxes.

## 3. Core Features
- **PPE Detection:** Real-time (or near real-time) detection of people and PPE items.
- **Compliance Monitoring:** Categorizes detections as "compliant" or "violation".
- **Manual Zone Configuration:** UI to draw restricted areas, walkways, and forklift paths.
- **Spatial Violation Detection:** Detects when persons enter restricted zones based on a Bird-Eye View (BEV) transformation.
- **Incident History:** Dashboard to review past violations with snapshots.

## 4. Key Files & Structure
### Backend (`/backend`)
- `app/main.py`: Entry point, middleware, and router inclusion.
- `app/routers/detection.py`: Handles image upload and PPE inference.
- `app/routers/zones.py`: CRUD for safety zones and calibrations.
- `app/services/ppe_detector.py`: Core logic for YOLOv8 inference and mock data.
- `app/services/spatial.py`: Homography and BEV logic.
- PostgreSQL repositories and services persist zones and violations; MinIO stores evidence.
- `app/models/schemas.py`: Pydantic models for API requests/responses.

### Frontend (`/frontend`)
- `app/page.tsx`: Main dashboard and tab navigation (Detections / Zones / History).
- `components/ZoneDrawingCanvas.tsx`: Fabric.js-based zone editor.
- `components/BoundingBoxCanvas.tsx`: Renders detection boxes over images/video.
- `components/UploadZone.tsx`: Handles file selection and upload trigger.
- `lib/api.ts`: Centralized API client using `fetch`.
- `types/`: TypeScript definitions mirroring backend schemas (`detection.ts`, `zone.ts`).

## 5. Development Workflows
- **Mock Mode:** Enable via environment variables to develop UI without a local GPU or trained model.
- **Zone Drawing:** Shapes drawn in the UI are normalized (0.0 - 1.0) and "flattened" into points before being sent to the backend.
- **BEV Calibration:** 4-point ground calibration maps perspective views to a 1000x1000 coordinate system for accurate spatial logic.

## 6. Commands
- **Backend:** `uvicorn app.main:app --app-dir backend --reload --port 8000`
- **Frontend:** `npm run dev` (running on port 3000)
