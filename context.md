# Project Context: De Heus PPE Safety Monitor

## Overview

The application detects PPE compliance and monitors configurable safety zones
in uploaded images and videos.

## Architecture

### Backend

- FastAPI provides detection, zone CRUD, violation history, and health routes.
- YOLOv8 performs person, helmet, and vest detection and video tracking.
- PostgreSQL stores cameras, zones, PPE violations, and zone violations through
  SQLModel repositories and services.
- MinIO stores PPE and zone evidence snapshots.
- OpenCV performs point-in-polygon checks and draws evidence overlays.

PostgreSQL is the only runtime database.

The documented target database schema adds factories and login users, and
separates real factory areas (`physical_zones`) from per-camera detection
polygons (`camera_zone_views`). Cameras and physical zones have a many-to-many
relationship through camera-zone views. Detection uses normalized coordinates
from the camera-zone view, while reporting and history group by the physical
zone. The target also adds incident status, severity, acknowledgement, and
resolution fields to PPE and zone violations.

This target is documentation only. SQLModel models, migrations, routes, and
tests still describe the currently implemented schema. Users are for login and
incident acknowledgement only; roles and permissions are deferred. Behavior
violations are deferred until danger behavior detection exists.

### Frontend

- Next.js and TypeScript provide the dashboard and history views.
- Fabric.js provides zone drawing and editing.
- Zone API response fields remain compatible with the frontend.

## Zone Monitoring

In the target schema, a physical zone may have a floor-plan polygon, while
each camera-zone view stores its own normalized polygon for video detection.
Normalized camera-view points are scaled to a 1000 by 1000 logical grid for
point-in-polygon checks. BEV calibration and homography are not used.

Supported zone types:

- `RESTRICTED`: a violation occurs when a worker foot point remains inside the
  zone beyond its dwell threshold.
- `WALKWAY`: a violation occurs when a worker foot point remains outside the
  zone beyond its dwell threshold.

Zone violation evidence draws a semi-transparent zone polygon and solid
boundary on the snapshot before the image is uploaded to MinIO. Restricted
zones use red; walkways use green.

## Persistence

- PPE violations are persisted through `PPEViolationService`.
- Zone violations are persisted through `ZoneViolationService`.
- Zone CRUD is persisted through `ZoneService` and PostgreSQL repositories.
- Evidence files are uploaded through `EvidenceStorage`.
- Local temporary snapshots are removed after successful persistence.

## Key Backend Files

- `backend/app/routers/detection.py`: detection and violation-history routes.
- `backend/app/routers/testing.py`: PostgreSQL and MinIO health routes.
- `backend/app/routers/zones.py`: PostgreSQL-backed zone CRUD and history.
- `backend/app/services/ppe_detector.py`: inference and evidence rendering.
- `backend/app/services/zone_service.py`: zone loading, incursion logic, and
  zone violation persistence.
- `backend/app/services/spatial.py`: point-in-polygon utility.
- `backend/app/storage/evidence_storage.py`: MinIO evidence handling.

## Commands

```text
uvicorn app.main:app --app-dir backend --reload --port 8000
python -m pytest
python -m ruff check app tests
```
