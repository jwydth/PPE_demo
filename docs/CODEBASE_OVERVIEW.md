# De Heus PPE / Safety Monitor — Codebase Overview

This document is a deep-dive reference to the entire repository: what each backend
service, API, database table, and frontend component does, how they connect, and
where the implementation currently diverges from other hand-written docs. It was
produced by direct code reading (not by trusting other docs at face value) as of
2026-08-10, and it supersedes/cross-checks `README.md`, `AGENTS.md`,
`docs/architecture/context.md`, and `backend/docs/*.md` where they had drifted from
the code. Those files still contain useful operational instructions (local setup,
session-by-session change history) and are not duplicated here in full.

> Living-document caveat: this is a snapshot. Treat exact line numbers/counts as
> approximate and re-verify anything load-bearing (a flag name, an endpoint path)
> against the source before depending on it.

## Table of contents

1. [What this system does](#1-what-this-system-does)
2. [Tech stack](#2-tech-stack)
3. [High-level architecture](#3-high-level-architecture)
4. [Backend — entry point, runtime, config](#4-backend--entry-point-runtime-config)
5. [Backend — data model](#5-backend--data-model)
6. [Backend — schemas (API I/O)](#6-backend--schemas-api-io)
7. [Backend — repositories](#7-backend--repositories)
8. [Backend — API routers](#8-backend--api-routers)
9. [Backend — detection & streaming pipeline (core AI services)](#9-backend--detection--streaming-pipeline-core-ai-services)
10. [Backend — zone monitoring & auto-zone](#10-backend--zone-monitoring--auto-zone)
11. [Backend — incidents, analytics, camera identity, features](#11-backend--incidents-analytics-camera-identity-features)
12. [Backend — reporting (PDF / email / scheduling)](#12-backend--reporting-pdf--email--scheduling)
13. [Backend — storage (MinIO + local)](#13-backend--storage-minio--local)
14. [Backend — scripts, tests, weights, trackers](#14-backend--scripts-tests-weights-trackers)
15. [Frontend — stack & routes](#15-frontend--stack--routes)
16. [Frontend — dashboard components](#16-frontend--dashboard-components)
17. [Frontend — PPE/video components](#17-frontend--ppevideo-components)
18. [Frontend — analytics components](#18-frontend--analytics-components)
19. [Frontend — 3D factory view](#19-frontend--3d-factory-view)
20. [Frontend — hooks](#20-frontend--hooks)
21. [Frontend — lib (API client, geometry, timeline sync)](#21-frontend--lib-api-client-geometry-timeline-sync)
22. [Frontend — types & design system](#22-frontend--types--design-system)
23. [Local infrastructure (Docker, MediaMTX, RTSP publisher)](#23-local-infrastructure-docker-mediamtx-rtsp-publisher)
24. [Known gaps, dead code, and doc discrepancies](#24-known-gaps-dead-code-and-doc-discrepancies)

---

## 1. What this system does

A smart-factory safety monitoring system for "De Heus": cameras (live RTSP or
uploaded video) are analyzed in real time for **PPE compliance** (helmet/vest/
cleaning-coverall detection), **zone incursions** (restricted/walkway/slippery
polygon zones with dwell thresholds), and **dangerous behavior** (falling/running,
via a pose-based classifier). Violations are persisted with an evidence snapshot,
surfaced live over WebSocket to a Next.js dashboard, aggregated into analytics, and
can be exported/emailed as a PDF report on demand or on a recurring schedule. A
server-side compositor also burns all AI results into the live video and republishes
it as an annotated LL-HLS stream so the operator's dashboard shows one
already-synchronized picture instead of a separately-drawn browser overlay racing an
independent video element.

## 2. Tech stack

**Backend**: Python, FastAPI + SQLModel/SQLAlchemy, PostgreSQL, MinIO (S3-compatible
object storage), Ultralytics YOLO (PPE detection, pose estimation, sign detection),
BoT-SORT/ByteTrack tracking, scikit-learn `ExtraTreesClassifier` (behavior
classifier), FFmpeg (annotated stream re-publishing), MediaMTX (RTSP/LL-HLS media
server), ReportLab (PDF), APScheduler (recurring report checks), smtplib (email).

**Frontend**: Next.js 16 (App Router) + React 19 + TypeScript, Tailwind CSS v4,
`@tanstack/react-query` (data fetching/polling), `hls.js` (LL-HLS playback),
`recharts` (analytics charts), `three` / `@react-three/fiber` / `@react-three/drei`
(3D factory view), `lucide-react` (icons).

## 3. High-level architecture

```
 Camera (RTSP) / uploaded video
        │
        ▼
 CameraFrameHub (one cv2.VideoCapture per source, shared)
        │  "latest" and "ordered" subscriptions
        ├──────────────┬───────────────┬───────────────────┐
        ▼              ▼               ▼                    ▼
   PPE detector    Sign worker    Behavior worker      Annotated
   (YOLO + track)  (YOLO sign)    (Pose batch + ExtraTrees)  compositor
        │              │               │                    │
        └──────┬───────┴───────┬───────┘                    │
               ▼               ▼                             │
        video_pipeline (orchestrator: PPE + zone + sign + behavior)
               │                                              │
     ┌─────────┼─────────────────┐                            │
     ▼         ▼                 ▼                            ▼
 PPEViolation ZoneViolation  BehaviorIncident            FFmpeg → MediaMTX
 (Postgres +   (Postgres +    (Postgres +                 (_annotated RTSP → LL-HLS)
  MinIO)        MinIO)         MinIO)                            │
     │         │                 │                                │
     └─────────┴───────┬─────────┘                                │
                        ▼                                         │
              UnifiedIncidentService / AnalyticsService            │
                        │                                          │
                        ▼                                          ▼
                 REST API (FastAPI)  ◄──── WebSocket /ws/stream ────┘
                        │                        │
                        ▼                        ▼
              Next.js dashboard (analytics, incident log, camera feeds)
              plays the annotated LL-HLS stream (LlHlsVideo) and overlays
              zone-drawing SVG + timestamped detection metadata
```

Two independent transports carry AI results to the browser: the **WebSocket**
(`/ws/stream`) carries timestamped JSON events (violations, suggestions, summaries,
and — for uploaded-file playback — raw JPEG preview frames), while the **live
camera** picture itself is *not* sent frame-by-frame over the socket; instead the
backend composes AI overlays directly onto the video and republishes it as a second
RTSP stream (`..._annotated`) that MediaMTX serves as LL-HLS and the dashboard plays
with `hls.js`. This avoids the earlier approach of drawing a separate browser-side
bounding-box layer synced to raw video by wall-clock time, which proved unstable.

---

## 4. Backend — entry point, runtime, config

### `app/main.py`
- Configures root logging, then calls `configure_inference_runtime()`
  (`app/core/runtime.py`) **before** importing any router — this sets thread-pool
  env vars (`OMP_NUM_THREADS` etc.) and resolves the inference device once, so
  native libraries (torch/cv2) don't each spin up a full-machine thread pool, and so
  every router's module-level singleton (`PPEDetector()`, `FallDetector()`) inherits
  the correct settings at import time.
- Builds the FastAPI app (title "De Heus PPE Detection API"), enables CORS for
  `settings.ALLOWED_ORIGINS` (defaults `localhost:3000`/`127.0.0.1:3000`), and
  **exposes** the `Content-Disposition` response header (needed for the frontend's
  PDF-download filename parsing — it's not in the CORS-safelisted header set by
  default).
- Registers routers with no URL prefixes (all mount at root): `analytics`,
  `cameras`, `detection`, `fall_detection`, `features`, `reports`, `streaming`,
  `zones`, `testing`.
- Mounts local snapshot files as static content at `/snapshots`.
- On `startup`: starts an in-process APScheduler `BackgroundScheduler` that calls
  `check_and_send_scheduled_report()` every 15 minutes (recurring PDF-email
  schedule check — see §12). On `shutdown`: stops the scheduler, closes all frame
  hubs, shuts down the behavior inference scheduler.
- `GET /health` → `{"status": "ok"}` liveness check.

### `app/core/config.py` — `Settings` (pydantic-settings, loaded from `backend/.env`)

Grouped by concern (defaults in parentheses):

- **Model/inference core**: `MODEL_PATH` (`weights/ppe_v4.pt`), `INFERENCE_DEVICE`
  (`auto`), `MAX_CONCURRENT_STREAMS` (1 — sized for GPU memory budget alongside the
  behavior worker), `INFERENCE_HALF` (True), `INFERENCE_IMGSZ` (640),
  `CONFIDENCE_THRESHOLD` (0.3), `PPE_OVERLAP_THRESHOLD` (0.3), `VIDEO_FRAME_STRIDE`
  (1), `LIVE_PPE_TARGET_FPS` (8.0).
- **Annotated/composed live stream**: `ANNOTATED_STREAM_ENABLED` (True),
  `ANNOTATED_STREAM_DELAY_SECONDS` (3.0), `ANNOTATED_STREAM_QUEUE_SIZE` (180),
  `ANNOTATED_PPE_TTL_FRAMES` (8), `ANNOTATED_SIGN_TTL_SECONDS` (3.0),
  `ANNOTATED_PPE_MATCH_IOU` (0.20), `ANNOTATED_RTSP_BASE_URL`
  (`rtsp://127.0.0.1:8554`), `ANNOTATED_PATH_SUFFIX` (`_annotated`),
  `ANNOTATED_ENCODER` (`auto` → NVENC if available, else libx264).
- **Video/tracking case & violation heuristics** (bytetrack + judgeability rules):
  `VIDEO_TRACKER` (`bytetrack.yaml`), `VIDEO_CASE_IOU_THRESHOLD`,
  `VIDEO_CASE_CENTER_DISTANCE_RATIO`, `VIDEO_CASE_MAX_FRAME_GAP` (90),
  `VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP` (300), `VIDEO_EDGE_MARGIN_RATIO`,
  `VIDEO_NEW_TRACK_GRACE_SECONDS` (0.5), `VIDEO_VIOLATION_CONFIRM_SECONDS` (0.2),
  `VIDEO_RECENT_PPE_MEMORY_SECONDS` (1.5), `VIDEO_MIN_PERSON_HEIGHT_RATIO`,
  `VIDEO_STABILITY_WINDOW_FRAMES`, `VIDEO_MIN_CLEAR_PERSON_ASPECT_RATIO`,
  `VIDEO_POSTURE_HEIGHT_DROP_RATIO`, `VIDEO_ZONE_REENTRY_GAP_SECONDS` (1.0).
- **Storage/DB**: `DATABASE_URL`, `MINIO_ENDPOINT`/`ACCESS_KEY`/`SECRET_KEY`/
  `BUCKET_NAME`, `MINIO_SECURE` (False), `SNAPSHOT_DIR`, `UPLOAD_DIR`.
- **CORS**: `ALLOWED_ORIGINS`.
- **Sign detection / auto-zone**: `SIGN_MODEL_PATH`, `SIGN_CONFIDENCE_THRESHOLD`
  (0.35), `SIGN_CLASS_ZONE_MAP` (`{2:"RESTRICTED", 3:"SLIPPERY"}`),
  `SIGN_CLASS_PPE_TRIGGER` (`{0,1}` — hardhat/vest sign classes),
  `AUTO_ZONE_BUFFER_RATIO`, `LIVE_SIGN_TARGET_FPS` (1.0), `LIVE_SIGN_PHASE_FRAME`
  (13 — offsets sign inference from the PPE cadence so they never compete for the
  same source frame), `AUTO_ZONE_CONFIRM_FRAMES`, `AUTO_PPE_CONFIRM_FRAMES` (1),
  `AUTO_ZONE_STATIONARY_SECONDS` (3.0 — a sign must sit still this long before a
  zone is suggested, so a *carried* sign never fires), `AUTO_ZONE_MOVE_TOLERANCE`
  (0.03).
- **Behavior/fall detection & pose pipeline**: `FALL_MODEL_PATH` (`weights/pose.pt`),
  `FALL_BEHAVIOR_MODEL_PATH` (`weights/best_behavior_model.joblib` — **the current
  primary classifier**), `FALL_REID_MODEL_PATH` (`weights/reid.pt`),
  `FALL_BEHAVIOR_WINDOW_FRAMES` (60), `FALL_BEHAVIOR_WINDOW_STRIDE` (12),
  `FALL_BEHAVIOR_CANONICAL_FPS` (24), `FALL_BEHAVIOR_MIN_CONFIDENCE` (0.50),
  `FALL_TRACK_MAX_MISSING_SAMPLES` (12), `FALL_LIVE_FRAME_STRIDE` (**must stay 1** —
  validator-enforced; the classifier requires an ordered 24 FPS timeline),
  `FALL_BEHAVIOR_PORTABLE_MODEL_PATH` (`weights/behavior.ubj` — legacy XGBoost
  fallback only), `FALL_INCIDENT_COOLDOWN_SECONDS` (10.0).
  Multi-camera batching: `BEHAVIOR_ORDERED_QUEUE_SIZE` (180),
  `BEHAVIOR_BATCH_MAX_SIZE` (4), `BEHAVIOR_CAMERA_BURST_SIZE` (2, must be ≤
  `BEHAVIOR_BATCH_MAX_SIZE`), `BEHAVIOR_BATCH_WAIT_MS` (4.0), `BEHAVIOR_POSE_IMGSZ`
  (448), `BEHAVIOR_FIXED_CAMERA` (True → forces `BEHAVIOR_GMC_METHOD="none"`),
  `BEHAVIOR_REID_INTERVAL_FRAMES` (4), `BEHAVIOR_POSE_REPAIR_MAX_GAP` (8),
  `BEHAVIOR_LIVE_WARMUP_FRAMES` (3), `BEHAVIOR_START_COHORT_WAIT_MS` (1200.0). CPU
  thread tuning: `BEHAVIOR_TORCH_THREADS`, `BEHAVIOR_TORCH_INTEROP_THREADS`,
  `BEHAVIOR_OPENCV_THREADS`, `BEHAVIOR_XGBOOST_THREADS`.
- **Analytics**: `ANALYTICS_LIMIT` (20000 — per-category row cap; analytics
  aggregates in Python, not SQL, so this bounds worst-case memory/latency and
  silently drops the oldest rows past the cap, with a logged warning).
- **Reporting/PDF**: `REPORT_TIMEZONE` (`Asia/Ho_Chi_Minh`, display only — storage is
  always UTC), `REPORT_COMPANY_NAME` (`De Heus LLC`), `REPORT_LOGO_PATH`,
  `REPORT_MAX_INCIDENT_ROWS` (25), `REPORT_MAX_SNAPSHOTS` (6),
  `REPORT_ARCHIVE_TO_MINIO` (True).
- **SMTP/email**: `REPORT_EMAIL_ENABLED` (**False** — master off switch), `SMTP_HOST`
  /`USERNAME`/`PASSWORD`, `SMTP_PORT` (587), `SMTP_USE_STARTTLS` (True),
  `SMTP_USE_SSL` (False, mutually exclusive with STARTTLS), `SMTP_FROM_EMAIL`,
  `REPORT_RECIPIENT_ALLOWLIST` (empty = allow any — a security-relevant default
  since the email endpoint has no auth; see §24), `REPORT_MAX_RECIPIENTS` (10),
  `REPORT_EMAIL_RATE_LIMIT_PER_HOUR` (20, in-process only).
- **Validators** enforce cross-field invariants: SMTP SSL/STARTTLS mutual
  exclusivity, `BEHAVIOR_CAMERA_BURST_SIZE ≤ BEHAVIOR_BATCH_MAX_SIZE`,
  `FALL_LIVE_FRAME_STRIDE == 1` (hard requirement), valid `ANNOTATED_ENCODER`/
  `BEHAVIOR_GMC_METHOD` enum values, IOU thresholds in `[0,1]`, and forcing
  `BEHAVIOR_GMC_METHOD="none"` whenever `BEHAVIOR_FIXED_CAMERA` is true.

### `app/core/runtime.py`
`configure_inference_runtime()` — idempotent, sets OMP/MKL/OpenBLAS/NumExpr thread
env vars from `BEHAVIOR_TORCH_THREADS`, configures `torch`/`cv2` thread counts
(catching `RuntimeError` if torch was already initialized, which happens in test
imports), and resolves+logs the inference device via the shared device-selection
helper in `app/services/ppe/device.py`.

### `app/db/session.py` / `app/db/init_db.py`
- `get_engine()` normalizes `postgres://`/`postgresql://` URLs to
  `postgresql+psycopg://` (forces the psycopg3 driver), builds a cached SQLAlchemy
  engine with `pool_pre_ping=True`. `get_session()` is the FastAPI DI dependency;
  raises `HTTPException(503)` if `DATABASE_URL` is unset or invalid.
- `create_db_and_tables()` (manual script, run via `python -m app.db.init_db`, not
  called automatically at app startup) calls `SQLModel.metadata.create_all()` and
  seeds three default `Feature` rows (`ppe_detection`, `zone_monitoring`,
  `behavior_detection`), including an in-place rename of a legacy
  `key="fall_detection"` row to `behavior_detection` if found, so existing
  `camera_feature_configs` FK links survive the rename.

---

## 5. Backend — data model

SQLModel tables (`app/models/*.py`), all with BigInteger autoincrement PKs and
UTC timestamps.

- **`Factory`** — `name` (unique), `location`, `is_active`. Has many `Camera`s and
  `PhysicalZone`s. A "Default Factory" row is lazily created on first use.
- **`Camera`** — `factory_id`, `name`, `source_key` (unique, indexed — the stable
  identity; canonicalized via `camera_identity.normalize_camera_source_key` so
  `localhost`/`::1` and `127.0.0.1` never create duplicate rows), `source_uri`,
  `home_zone_id` (FK → `PhysicalZone`, `SET NULL` — the physical area this camera
  lives in; drives analytics zone-rollup), `is_active`. Cascades: `camera_zone_views`
  and `camera_feature_configs` delete with the camera; `ppe_violations` survive with
  `camera_id=NULL`.
- **`PhysicalZone`** — `factory_id`, `name`, `zone_type` (free string:
  `RESTRICTED`/`WALKWAY`/`SLIPPERY` for drawn polygons, or `AREA` for a
  non-drawn factory-area used only for analytics grouping/camera home-zone),
  `floor_plan_polygon` (JSONB), `dwell_threshold_seconds` (Float, default 0 —
  **not** an integer despite what the DBML doc says), `is_active`.
- **`CameraZoneView`** — the per-camera drawn instance of a `PhysicalZone`:
  `camera_id`, `physical_zone_id`, `ui_shape_data` (JSONB, frontend drawing
  metadata), `normalized_coordinates` (JSONB polygon), `is_active`. Unique on
  `(camera_id, physical_zone_id)`.
- **`Feature`** — system-wide capability registry: `key` (unique — e.g.
  `ppe_detection`, `zone_monitoring`, `behavior_detection`), `name`, `description`,
  `is_active`.
- **`CameraFeatureConfig`** — per-camera enablement of a `Feature`: `camera_id`,
  `feature_id`, `is_enabled`, `config_params` (JSONB). Unique on
  `(camera_id, feature_id)`.
- **`PPEViolation`** + **`PPEViolationSubject`** — `PPEViolation` is the event row
  (`camera_id`, `source_key`, `occurred_at`, `violation_type`, `details`,
  `snapshot_path`, `frame_index`); `PPEViolationSubject` (1:N) carries per-person
  evidence (`tracker_id`, `person_index`, `missing_equipment` JSONB list,
  `bounding_box`, `confidence`). No `status`/`severity` columns exist on
  `PPEViolation` (unlike the DBML doc's target schema — severity is derived
  on-the-fly by `incident_normalization.py` instead).
- **`ZoneViolation`** — flat table (no subject rows): `camera_id`,
  `physical_zone_id`, `camera_zone_view_id` (all `SET NULL` on delete), `zone_name`/
  `zone_type` (denormalized copies, retained for history even after the zone
  config changes), `source_key`, `tracker_id`, `occurred_at`, `frame_index`,
  `snapshot_path`, `status` (default `OPEN`), `severity`.
- **`BehaviorIncident`** + **`BehaviorIncidentSubject`** + **`BehaviorEvidence`** —
  mirrors the PPE shape but for behavior events. `BehaviorType` enum:
  `FALL_DETECTED`/`RUNNING_DETECTED`/`FAINT_DETECTED`/`COLLAPSE_DETECTED` (only the
  first two are actually reachable from the current 3-class `others/running/falling`
  classifier — the latter two are forward-provisioned labels). `status` defaults
  `NEW` (`NEW`/`REVIEWED`/`RESOLVED`/`FALSE_POSITIVE`), `severity` defaults `HIGH`.
  Subject rows carry `keypoints`/`features` (the pose/behavior feature vector) in
  addition to bbox/confidence. Evidence rows are snapshot object keys.
- **`ReportDelivery`** — append-only audit trail of every PDF/email report send
  (`report_type`, `range_param`, `zone_id` — **no FK**, since a zone can be deleted
  after a delivery is recorded and this is an audit log not a live reference —
  `recipients`, `subject`, `filename`, `object_key`, `status`, `error_message`).
- **`ReportSchedule`** — **singleton row (id=1)**: site-wide recurring report config
  (`frequency`: `off`/`weekly`/`monthly`, `day_of_week` 0=Mon..6=Sun,
  `day_of_month` 1–28 clamped, `hour`/`minute`, `zone_id` — no FK, `recipients`
  comma-joined, `include_snapshots`, custom `subject`/`message`, `last_sent_at`).
  There is no auth/multi-tenancy in this app, so one global schedule is intentional.

## 6. Backend — schemas (API I/O)

Pydantic models under `app/schemas/` are the request/response contract, distinct
from the persisted SQLModel tables above.

- **`detection.py`** — `BoundingBox`, `Detection`, `EquipmentStatus`,
  `PersonResult` (per-person role/equipment/compliance), `DetectionResponse`
  (`POST /predict` response), `TrackingOverlayFrame`/`TrackingOverlay` (the
  per-frame detection shape driving both the live overlay and uploaded-video
  playback overlay — carries bbox, role, `missing_equipment`, and zone
  fields), `VideoProcessingResponse` (`POST /predict-video` response).
- **`streaming.py`** — `StreamEvent`: the WebSocket envelope. `event` is one of
  `frame`/`violation`/`zone_violation`/`zone_suggestion`/`ppe_suggestion`/
  `sign_prediction`/`behavior_incident`/`summary`/`error`/`start`/`end`. Carries
  timeline metadata (`frame_index`, `stream_epoch`, `media_pts_ms`,
  `source_time_ms`, `inference_completed_ms`, `discontinuity_sequence`) so the
  frontend can correlate independently-cadenced PPE/sign/behavior events to the
  right physical frame. `image_bytes` is excluded from JSON serialization — the raw
  JPEG is sent as a separate binary WebSocket frame to avoid base64 inflation.
- **`zone.py`** — `ZoneSuggestion`/`PPESuggestion` (auto-zone/PPE-sign suggestion
  payloads), `PhysicalZoneRead`/`Create`, `Zone` (the "flat" drawn-zone shape used
  by the legacy `/zones` CRUD routes — `zone_type` is `RESTRICTED`/`WALKWAY`/
  `SLIPPERY`, geometry as JSON strings).
- **`violation.py`** — `ViolationReport`/`ViolationDetail` (PPE), `ZoneViolation`
  (API shape, distinct from the DB model of the same name).
- **`camera.py`** — `CameraRead`, `HomeZoneUpdate`, `CameraEnsure` (idempotent
  get-or-create request keyed by `source_key`).
- **`feature.py`** — `FeatureRead`, `CameraFeatureConfigRead`/`Update`.
- **`incident.py`** — `UnifiedIncidentRead`: the normalized cross-category
  (`ppe`/`zone`/`behavior`) incident feed row.
- **`analytics.py`** — `AnalyticsRange` (`24H`/`7D`/`30D`), `SeverityCounts`,
  `ZoneTotal`, `TypeCount`, `AnalyticsSummary`, `TrendPoint`/`AnalyticsTrend`,
  `ComparePoint`/`SeverityDelta`/`AnalyticsCompare`.
- **`report.py`** — `ReportEmailRequest`/`Response`, `ReportPreview`.
- **`report_schedule.py`** — `ScheduleFrequency`, `ReportScheduleRequest`/
  `Response`.
- **`fall_detection.py`** — `BehaviorIncidentRead` (+ subject/evidence read
  shapes), `FallPoseDetection` (`status`: `unknown`/`others`/`running`/`falling` —
  `unknown` is a live-only placeholder while a track collects its first 60-frame
  window), `FallDetectionSummary`, `FallImagePredictionResponse`/
  `FallVideoPredictionResponse` (+ `FallVideoMetadata`/`FallTimelineItem`).

## 7. Backend — repositories

`app/repositories/*.py` — thin, FastAPI-DI-injectable CRUD wrappers around each
table, all wrapping SQLAlchemy errors into a shared `RepositoryError`.

- **`CameraRepository`** — create/get (by id or `source_key`, normalized before
  lookup)/list/update/deactivate/`set_home_zone`/delete.
- **`CameraFeatureConfigRepository`** — create/get (by id, by camera+feature, by
  camera)/update. No delete (configs are meant to persist).
- **`CameraZoneViewRepository`** — create/get (by id/camera/physical-zone/both)/
  `get_active_by_camera_source_key` (the hot lookup path used at detection
  time)/update/deactivate (single or bulk by camera)/delete (single, bulk by
  camera, or by camera source key).
- **`FactoryRepository`** — create/`get_by_name`/`get_or_create_default_factory`
  (race-safe via `IntegrityError` retry).
- **`FeatureRepository`** — create/get (by id/key)/list.
- **`PhysicalZoneRepository`** — create/get (by id, by factory, by factory+name)/
  update/deactivate/delete.
- **`PPEViolationRepository`** / **`ZoneViolationRepository`** — mirror-image CRUD:
  create/get/list (desc by time)/`get_recent(limit)`/`list_between(dates)`/delete/
  `delete_all`; PPE additionally has `create_subject`/`get_subjects`.
- **`BehaviorIncidentRepository`** — create (+ `create_subject`/`create_evidence`)/
  get/`get_evidence_for_incidents(ids)` (**batched** to avoid N+1 in the unified
  incident feed)/`list_recent`(filterable by type/status/camera)/`list_between`/
  delete (relies on DB `CASCADE`)/`delete_all` (explicit multi-table bulk delete).
- **`ReportDeliveryRepository`** — `create` only (append-only audit log).
- **`ReportScheduleRepository`** — singleton pattern (`_SINGLETON_ID = 1`):
  `get_or_create()`/`update()`.

## 8. Backend — API routers

All routers mount at root (no path prefix). Swagger UI: `http://localhost:8000/docs`.

### `detection.py` (tag `detection`)
Owns a module-level `PPEDetector(enable_stream_pool=False)` singleton used only for
one-shot (non-WebSocket) inference.
- `POST /predict` — single image → `DetectionResponse`. No persistence.
- `POST /predict-video` — uploaded video (form fields `file`, `enable_ppe`,
  `enable_zone`) → `VideoProcessingResponse`. Streams to a temp file, always cleans
  up in `finally`.
- `POST /upload-video` — stages a raw video file under `UPLOAD_DIR` for later
  `/ws/stream` simulated playback.
- `GET /violations` / `GET /violations/{id}` / `DELETE /violations/{id}` — PPE
  violation list/detail/delete.
- `DELETE /violations` — deletes **all** PPE + zone + behavior incidents in one
  call, returns per-category and total counts.
- `DELETE /zone-violations/{id}` — (lives in this router, not `zones.py`).

### `streaming.py` (tag `streaming`) — `WS /ws/stream`
The one real-time endpoint. Query params: `video_name` (filename, local path, or
`rtsp(s)/rtmp/http(s)` URL), `enable_ppe`, `enable_zone`, `enable_fall`,
`enable_sign`, `metadata_only`.
- A per-`video_name` `asyncio.Lock` serializes connections to the same source; a
  connecting client waits up to 12s for it.
- A per-`video_name` cancel event lets a **newer connection supersede** an orphaned
  one — closes the old socket with code **4001** (`"superseded"`); the frontend must
  not auto-reconnect on this code. This handles browser reload / React StrictMode
  double-mount leaking sockets that never send a close frame.
- A background settings-listener task handles inbound client messages:
  `update_settings` (toggles, including per-camera `features`), `dismiss_suggestion`
  /`dismiss_ppe_suggestion`, `reload_zones` (hot-reload polygons mid-stream),
  `playback_metrics` (client-reported HLS/overlay health, fed into
  `stream_health.py`). Sends a `ping` if the client is silent for 15s.
- **Producer/consumer split**: `"frame"` events are coalesced to a single
  latest-only slot (older ones dropped and counted); every other event type
  (violations, suggestions, summary, etc.) goes on an unbounded reliable FIFO that
  is never dropped. `send_event` sends the raw JPEG via `send_bytes` first, then the
  JSON envelope via `send_text`.
- Teardown always fully closes the generator (`aclose`, 6s timeout) **before**
  releasing the lock, so the next connection never races the shared singleton
  `PPEDetector`'s tracker state during its own teardown.

### `zones.py` (tag `zones`)
- `GET`/`POST /physical-zones`, `PUT`/`DELETE /physical-zones/{id}` — the named
  factory-area zone catalog (analytics grouping / camera home-zone picker).
- `POST /zones` — creates a drawn "flat" zone (auto-creates the camera if
  `video_name` is unknown; creates a `PhysicalZone` + `CameraZoneView` under the
  hood).
- `GET /zones?video_name=...` / `DELETE /zones/video?video_name=...` — **query
  params, not path params**, deliberately: an RTSP URL like
  `rtsp://localhost:8554/mystream` breaks path-param slash-decoding. (The `DELETE`
  route is registered before `/zones/{zone_id}` so the literal string `"video"` is
  never matched as a zone id.)
- `PUT`/`DELETE /zones/{zone_id}`.
- `GET /zone-violations` / `GET /zone-violations/{id}`.

### `testing.py` (tag `testing`) — health/diagnostics
- `GET /health/db`, `GET /health/storage`, `GET /health/smtp` — connectivity checks
  (503 on failure).
- `GET /health/streams` — full per-camera live health snapshot (see §9,
  `stream_health.py`). `DELETE /health/streams` — resets ephemeral perf counters
  for controlled benchmarking.

### `cameras.py` (tag `cameras`)
- `GET /cameras` — list.
- `POST /cameras` — idempotent get-or-create keyed by `source_key`
  (`CameraEnsure{name, source_key}`) — lets the frontend bind a configured stream to
  a backend camera row before any incident has occurred.
- `PUT /cameras/{id}/home-zone` — assign the physical "home" zone.
- `DELETE /cameras/{id}`.

### `fall_detection.py` (tag `behavior-detection`)
Owns a module-level `FallDetector()` singleton. Each predict endpoint is registered
under **two paths**: the canonical `/behavior-detection/...` and a
`deprecated=True` legacy alias `/fall-detection/...` (same handler).
- `POST /behavior-detection/predict` / `predict-video` — image/video → pose+behavior
  classification (`FallImagePredictionResponse`/`FallVideoPredictionResponse`).
  503 if the model assets are unavailable.
- `GET /behavior-incidents` (filterable by type/status/camera_id) / `GET
  /behavior-incidents/{id}` / `DELETE /behavior-incidents/{id}` (hard delete).

### `features.py` (tag `features`)
- `GET /features` — the global feature catalog.
- `GET /cameras/{id}/features` — lazily creates default per-camera configs, then
  returns them.
- `PUT /cameras/{id}/features` — bulk-updates a list of `CameraFeatureConfigUpdate`
  by `feature_key`.

### `reports.py` (tag `reports`)
Has an in-process sliding-window rate limiter (`REPORT_EMAIL_RATE_LIMIT_PER_HOUR`,
does **not** survive multi-worker uvicorn).
- `GET /reports/incidents/preview` — quick stats + insights, no PDF rendering.
- `GET /reports/incidents.pdf` — renders and returns the PDF (ReportLab rendering
  runs in a threadpool so it doesn't block other open WebSocket connections).
- `POST /reports/incidents/email` — 503 if `REPORT_EMAIL_ENABLED` is false; 429 on
  rate limit; 502/504 on SMTP failure/timeout.
- `GET`/`PUT /reports/schedule` — the singleton recurring-schedule config.

### `analytics.py` (tag `analytics`)
- `GET /analytics/incidents` — unified PPE+zone+behavior feed (with zone/severity
  fields the individual violation endpoints don't carry).
- `GET /analytics/summary`, `GET /analytics/trend`, `GET /analytics/compare`.

---

## 9. Backend — detection & streaming pipeline (core AI services)

This is the heart of the system, under `backend/app/services/`. The historical
single ~2300-line `ppe_detector.py` has been split: `ppe_detector.py` is now a thin
re-export shim; real PPE logic lives in the `ppe/` subpackage, and orchestration
(PPE + zone + sign + behavior together) lives in `video_pipeline/`, which is the
**only** module allowed to import from both `app.services.ppe` and
`app.services.zone_service`.

### `ppe/` subpackage
- **`detector.py` — `PPEDetector`**: loads the main PPE YOLO model and an optional
  sign model. When `enable_stream_pool=True` (the `/ws/stream` instance only),
  pre-warms a bounded pool of model instances sized to `MAX_CONCURRENT_STREAMS`
  (default 1) — because Ultralytics' `track(persist=True)` keeps tracker state on
  the model/predictor object, so concurrent streams can't share one instance.
  `acquire_model_instance()`/`release_model_instance()` borrow/return from the pool
  (30s timeout). `predict()`/`process_video`/`stream_video` dispatch to mock (no
  model loaded) or real paths, delegating the real work to `video_pipeline`.
- **`constants.py`** — label/color constants (`HELMET_LABEL`, `VEST_LABEL`,
  `CLEANING_COVERALL_LABEL`, `ROLE_UNIFORM_LABEL`, compliant/violation colors).
- **`device.py`** — `_select_inference_device()` is the single source of truth for
  CPU/GPU selection (used by `PPEDetector`, `FallDetector`, indirectly by the
  behavior scheduler); `_resolve_video_tracker()` resolves a tracker YAML path.
- **`geometry.py`** — shared bbox math: area/intersection/overlap-ratio, IoU,
  center-distance-ratio, aspect ratio, frame-edge proximity.
- **`response_builder.py`** — turns raw YOLO boxes into the API/domain model:
  splits boxes by class (person/helmet/vest/coverall), assigns equipment to the
  best-overlapping person (`PPE_OVERLAP_THRESHOLD`, one item per person, highest
  confidence wins ties), infers role (coverall → `janitor`, vest → `worker`,
  neither → violation), and computes overall compliance. Also builds the
  per-frame tracking-overlay record and JPEG-encodes preview frames (640px wide,
  quality 60, sent as raw bytes — not base64).
- **`violation_matching.py` — `ViolationCase` dedup**: matches a new violation
  observation to an existing open case by track id, then IoU within a frame-gap
  window, then center-distance fallback; a separate duplicate-suppression pass
  prevents a re-appearing/re-tracked worker from generating a second persisted
  violation for the same real-world event even after track continuity was lost.
- **`worker_tracking.py` — `WorkerState`**: the richest state machine in the PPE
  path, intentionally mixing PPE fields (`missing_counts`, `reported_missing`) with
  zone fields (`zone_dwell`, `reported_zones`) since `video_pipeline` owns their
  interleaving. Worker identity resolution prefers same-track-id, then spatial
  match among recently-seen already-reported workers (avoids double-reporting a
  worker who lost tracking after being flagged), then unreported workers, else
  creates a new worker. Per-frame update: merges the new observation, applies
  "sticky" role assignment (once a role is established it's re-used even if the
  coverall/vest isn't detected in-frame), gates judgeability (grace period after
  first seen, not near frame edge, sufficient bbox height, no unclear posture like
  bending/crouching), debounces flicker (a label seen compliant recently suppresses
  a momentary miss), then requires a missing label to persist for
  `VIDEO_VIOLATION_CONFIRM_SECONDS` of consecutive frames before it's "confirmed"
  and eligible to be reported. Evidence counters are **not** reset when a worker
  becomes momentarily unjudgeable (e.g. near frame edge) — this is a deliberate fix
  so transient tracking hiccups don't erase confirmed-but-unreported evidence.

### `video_pipeline/__init__.py` — the orchestrator
A single ~1500-line async-generator module (`real_video_pipeline`) that is the
unified entry point for both uploaded-file batch processing and live RTSP,
branching on URL scheme:
- **Live sources** subscribe to the shared `frame_hubs` registry (see below)
  instead of opening their own capture — one decode per camera shared by PPE
  preview, the sign worker, the behavior worker, and the annotated compositor.
- **Local files** use Ultralytics' own `model.track(stream=True, persist=True)`
  generator directly.
- **Cadence gates** (`ModelCadence`/`CadenceGate`) throttle PPE to
  `LIVE_PPE_TARGET_FPS` (8.0) and sign to `LIVE_SIGN_TARGET_FPS` (1.0, phase-offset
  so it never competes with PPE for the same source frame).
- **Dynamic settings**: a shared `settings_state` dict (mutated by the WebSocket
  route's message listener) is read every loop iteration — `enable_ppe`/
  `enable_zone`/`enable_fall`/`enable_sign` can flip live, `viewing` gates whether
  preview JPEGs are produced at all (an enabled-but-unwatched camera still runs
  detection/incident logic, just without the encode/send cost), and
  `reload_zones`/dismissed-suggestion lists are drained each pass.
- **Sign detection** runs on its own task (live) subscribed independently to the
  frame hub, decoupled from the PPE cadence, pushing `sign_prediction`/
  `zone_suggestion`/`ppe_suggestion` events.
- **Behavior/fall** starts/stops a `BehaviorStreamWorker` when `enable_fall` flips
  (live); for local files it runs inline via `FallDetector.create_live_session()`.
- **Zone incursion loop**: per tracked person, computes a foot point, checks it
  against all loaded zone polygons. `WALKWAY` zones accumulate dwell while
  **outside** the polygon ("left the walkway"); `RESTRICTED`/`SLIPPERY` accumulate
  while **inside** (`SLIPPERY` exempts `janitor`-role workers). Once dwell exceeds
  the zone's threshold and it hasn't already been reported, persists a zone
  violation.
- **Retroactive zone check**: keeps a rolling ~30s foot-position history per track.
  When zone monitoring is toggled on mid-stream (or zones are hot-reloaded), replays
  this history against the newly-active zones using max-continuous-dwell (not
  cumulative) and fires any violations that would have fired had monitoring been on
  the whole time, then resets that worker's zone state so live tracking continues
  cleanly.
- Emits a `StreamEvent` stream: `start` → many `violation`/`zone_violation`/
  `sign_prediction`/`zone_suggestion`/`ppe_suggestion`/`behavior_incident`/`frame`
  events → `summary` → `end`, each carrying full timeline metadata for downstream
  correlation.
- `mock_process_video`/`mock_stream_video` are simplified no-model fallback paths
  used when model weights are missing (own `cv2.VideoCapture`, same worker/zone
  logic, no sign/auto-zone/behavior/annotated-stream integration).

### `frame_hub.py` — shared per-camera frame capture
- `CameraFrameHub` owns one background capture thread per live source, RTSP forced
  to TCP transport. Detects source discontinuities (PTS going backward, or jumping
  forward by >12× the expected interval) by rotating a `stream_epoch` UUID — every
  downstream temporal consumer (behavior window, annotated compositor) discards
  stale cross-epoch state rather than silently bridging a gap.
- Two subscription policies: `"latest"` (size-1 queue, drop-to-newest — PPE preview,
  sign worker) and `"ordered"` (bounded FIFO, default 180 — behavior, annotated
  publisher; on overflow the **whole queue is discarded** and the next packet is
  explicitly marked with a gap rather than silently consuming a reordered/corrupted
  sequence).
- `FrameHubRegistry` (`frame_hubs`, process-wide singleton) lazily creates/starts a
  hub per unique source and stops+evicts it once the last subscriber releases —
  an idle camera with zero consumers doesn't keep decoding in the background.

### Behavior/fall pipeline: `behavior_features.py`, `fall_detector.py`, `behavior_inference.py`, `behavior_stream.py`
- **`behavior_features.py`** — feature extraction that must exactly match the
  trained classifier's contract: operates on a fixed **60-frame window** at
  canonical 24 FPS. Derives per-frame geometry (torso angle, "compression" =
  head-to-hip/height, spread ratio, ground point, body size), frame-to-frame speed
  deltas, gap interpolation (up to 6 samples with valid endpoints), and produces
  summary stats + strided samples + "final" heuristic features + data-quality
  metrics (`valid_frame_ratio` must be ≥0.70 to count as `"good"` quality).
- **`fall_detector.py` — `FallDetector`/`FallLiveSession`**: lazy-loads a YOLO-pose
  model and the **ExtraTrees behavior classifier** (`best_behavior_model.joblib`;
  falls back to a legacy XGBoost/UBJ portable model only if the primary is
  missing). `classify()` applies training-time feature transforms (clamps, sqrt,
  double-log) before `predict_proba`, expecting exactly 3 classes
  (`others`/`running`/`falling`). `FallLiveSession` resamples each camera's native
  frame rate onto the canonical 24 FPS timeline using capture/media timestamps (not
  wall clock) so variable/dropped-frame RTSP delivery never distorts the
  speed-derived features; a discontinuity fully clears all windows. Short same-track
  pose gaps (≤8 samples) are linearly repaired if safe (confidence/center-shift
  checks). Predictions run once a 60-frame window fills and then every 12-sample
  stride, smoothed over a 3-sample probability history. A label transition to
  `running`/`falling` above the confidence threshold (respecting a 10s per-track
  cooldown) persists a `BehaviorIncident`.
- **`behavior_inference.py` — `BehaviorInferenceScheduler`**: micro-batches ready
  frames from **every** camera through one finite CUDA pose-predictor call (max
  batch 4), rather than one model instance per stream. GPU pose/ReID work runs
  under the shared priority lock (priority 0 — highest); CPU BoT-SORT tracker
  updates run **outside** the lock via a small thread pool, explicitly separated
  because holding the GPU lock across CPU tracker work previously caused 500+ms
  lock waits for unrelated PPE work. ReID re-encodes only every
  `BEHAVIOR_REID_INTERVAL_FRAMES` frames per camera. Cameras enabled together are
  released onto ordered capture together after a cohort-wait grace window to avoid
  staggered CUDA warm-up costs.
- **`behavior_stream.py` — `BehaviorStreamWorker`**: the live-streaming counterpart
  — pure `source → pose/ReID (via scheduler) → FallLiveSession classification →
  incident buffer`, no PPE/zone/WebSocket concerns. Warms up with a few "latest"
  frames before switching to an "ordered" subscription so it doesn't start already
  behind. `snapshot()` is the pull-based interface `video_pipeline` calls each
  frame.

### Cadence & GPU coordination: `model_cadence.py`, `inference_coordination.py`
- **`CadenceGate`** implements deadline-aging admission: accepts the first
  available frame at or after each computed deadline (rather than requiring an
  exact frame index), bounding worst-case staleness instead of drifting when the
  pipeline falls behind.
- **`PriorityInferenceLock`** — a re-entrant, priority-ordered lock serializing
  short CUDA launch sections across PPE (priority 1), sign (priority 2), and
  behavior pose/ReID (priority 0 — highest). Enforces deadline-aging anti-starvation:
  priorities 1 and 2 have max-wait budgets (40ms / 500ms); once exceeded, a waiter
  is promoted ahead of nominally higher-priority waiters, so lower-priority work
  can't starve indefinitely even though behavior retains priority under normal
  contention.

### `annotated_stream.py` — server-side compositor/republisher
- **`AnnotatedStateStore`** buffers pose, PPE, and sign state independently, each
  toggleable and epoch-isolated (any state update from a different `stream_epoch`
  clears all buffers). Synthesizes short interpolated pose frames to smooth
  frame-by-frame display even though behavior only samples every Nth frame. Applies
  bounded TTL staleness rules when composing a snapshot for one output frame (8
  frames for PPE, 3 seconds for signs) and falls back to drawing PPE-only "pose
  shapes" from PPE boxes when behavior is off/has nothing, so PPE violations still
  render without a skeleton.
- **`RawFramePublisher`** spawns an `ffmpeg` subprocess (auto-selects
  `h264_nvenc` else `libx264`) publishing composed BGR frames as RTSP/TCP to
  MediaMTX; restarts once on a write failure, falling back to libx264 if NVENC
  fails.
- **`AnnotatedStreamPublisher`** paces publication to
  `captured_monotonic + ANNOTATED_STREAM_DELAY_SECONDS` (3s fixed buffer — the
  intentional latency budget letting AI overlays "catch up" to a frame before it's
  published; a 2s delay was tried and found insufficient for the first 60-frame
  Behavior window). Re-reads the live feature-flag callback every frame so toggling
  a model during a live view immediately changes the composited output without
  restarting the publisher. A module-level ownership guard ensures only one
  publisher owns a given output URL at a time.

### `stream_health.py` — `/health/streams` metrics
Thread-safe in-memory rolling metrics keyed by a hashed source id (credentials
stripped from the label). Tracks capture cadence, PPE/behavior/sign
processed/dropped/gap counts, preview delivery (generated/sent/coalesced/gap),
HLS rebuffer/dropped-frame counts, and the full annotated-publisher metric set
(composed/published/dropped/deadline-misses/encoder-restarts/queue-depth). Named
per-stage latency timings (`RollingSamples`, 60s window) report
last/mean/p50/p95/p99/max — this is what let the team find that a standalone
two-camera OpenCV capture reproduced ~746ms periodic delivery gaps independent of
FastAPI, and that narrowing the GPU critical section (see above) fixed avoidable
PPE blocking.

---

## 10. Backend — zone monitoring & auto-zone

- **`spatial.py`** — `is_point_in_polygon` (thin `cv2.pointPolygonTest` wrapper,
  boundary-inclusive).
- **`zone_service.py`** — `ZoneService` (forbidden from importing PPE code; only
  `video_pipeline` bridges both): CRUD for drawn zones (`PhysicalZone` +
  `CameraZoneView` pair) and for named "AREA" analytics zones (no drawn shape).
  Detection-time helpers: `load_zones(video_name)` converts normalized 0–1 UI
  coordinates to a fixed 1000-unit integer coordinate space for precise
  `cv2.pointPolygonTest`; `get_person_foot_point` uses bottom-center of the bbox as
  the "feet" proxy; `record_zone_violation` builds a human label per zone type
  (`"Left Walkway: ..."`, `"Entered Slippery Area: ..."`, `"Entered Zone: ..."`),
  draws the polygon + person box onto a snapshot, and persists via
  `ZoneViolationService`. Any snapshot/DB failure is logged and swallowed rather
  than crashing the detection loop.
- **`zone_violation_service.py`** — resolves camera/physical-zone/camera-zone-view
  context, normalizes the source key, uploads the snapshot to MinIO, creates the
  `ZoneViolation` row.
- **`auto_zone.py`** — sign-detection-driven suggestions:
  - `derive_zone_polygon` builds a rectangle on the floor below a detected sign
    (horizontally centered, extending down from the sign's bottom edge).
  - **`SignZoneRegistry`** — a **stationarity** state machine: a sign must stay
    within a small position tolerance of its first-seen anchor for
    `AUTO_ZONE_STATIONARY_SECONDS` (3s) before a zone suggestion fires. A sign being
    *carried* keeps restarting its still-streak and never confirms; only a sign
    that's been put down and left in place does. `SIGN_CLASS_ZONE_MAP` maps sign
    class 2 → `RESTRICTED`, class 3 → `SLIPPERY` (the `SLIPPERY` zone type was added
    specifically for "no-thoroughfare/slippery" signs and is monitored like
    `RESTRICTED`, i.e. dwell-based, with no separate DB migration needed since
    `zone_type` is a plain string column).
  - **`SignPPERegistry`** — a simpler hit-counter for wall-mounted PPE signs (hard
    hat/vest), firing instantly (`AUTO_PPE_CONFIRM_FRAMES=1`, "first reliable
    detection").
  - Both registries have an `ACCEPTED` state defined but effectively unreachable in
    code — `accept()` only ever sets a generic `emitted=True`, matching a known gap
    called out in `docs/features/automatically_updated_zone.md`.

---

## 11. Backend — incidents, analytics, camera identity, features

- **`camera_identity.py`** — `normalize_camera_source_key`: canonicalizes
  `localhost`/`::1` RTSP hostnames to `127.0.0.1` (preserving port/userinfo) so the
  same physical stream is never registered as two different `Camera` rows. Used
  everywhere a camera is looked up/created by source string.
- **`camera_service.py`** — standard `Camera` CRUD, always normalizing source keys
  before lookup/creation. `set_home_zone` is the link every analytics/incident
  aggregation path uses to resolve a camera's zone.
- **`feature_service.py`** — `ensure_camera_feature_configs` lazily creates a
  `CameraFeatureConfig` per active global feature for a camera (defaulting only
  `ppe_detection` to enabled). `update_camera_feature_config` includes a
  backward-compat shim: an unrecognized `behavior_detection` key falls back to the
  legacy `fall_detection` key for pre-migration DBs.
- **`incident_normalization.py`** — pure, DB-free severity/label rules shared by
  every read path: PPE severity is `Low` (proximity) / `High` (multi-item missing)
  / `Medium` (default) — **never `Critical`** by design; zone/behavior severity
  come from a free-text column with `Medium`/`High` defaults; type labels map
  snake_case enum values to Title Case with explicit overrides for zone types
  (`RESTRICTED`→"Restricted Zone Incursion" etc.).
- **`incident_service.py` — `UnifiedIncidentService`**: the single normalized read
  layer over PPE/zone/behavior incidents (forbidden from importing detection-pipeline
  internals). Fetches each category independently (capped by `limit`), resolves
  each incident's zone via the **camera's home zone** (not the incident's own zone
  reference — e.g. a PPE violation's "zone" in the unified feed is the camera's
  configured home zone), batch-fetches behavior evidence to avoid N+1, then filters/
  sorts/truncates — logging a warning whenever a category hit its own limit or the
  merged set still exceeds the requested limit (an accepted, documented tradeoff
  bounded by `ANALYTICS_LIMIT=20000`).
- **`behavior_incident_service.py`** — persistence for `BehaviorIncident` +
  subject + evidence rows, mirroring the PPE/zone violation services.
- **`analytics_service.py`** — aggregates **in Python, not SQL** (a deliberate,
  documented tradeoff: normalizing three differently-shaped tables in SQL `CASE`
  expressions would reintroduce the drift risk `incident_normalization.py` exists
  to prevent). `get_summary` computes grand total, per-zone breakdown (always
  computed over the full unfiltered set so the zone chart always shows all zones,
  even when the request itself is zone-filtered), severity/type counts (zone-
  filtered), "active" zones (any incident in the last 15 minutes), open incidents
  (Critical+High), active/total camera counts. `get_trend` buckets by hour (24H) or
  day (7D/30D). `get_compare` does week-over-week or month-over-month deltas. A
  per-request range cache dedupes repeated identical-range fetches within one
  request (not cross-request).

---

## 12. Backend — reporting (PDF / email / scheduling)

`app/services/reporting/` — restricted by its own docstring to reading only from
`analytics_service`, `incident_service`, `factory_repository`,
`physical_zone_repository`, and `evidence_storage` (never detection-pipeline
internals).

- **`report_data.py` — `ReportDataBuilder`**: assembles a `ReportData` dataclass
  from `AnalyticsSummary`/`Trend`/`Compare` plus the top incidents (capped at
  `REPORT_MAX_INCIDENT_ROWS=25`, sorted by severity then recency), resolving the
  display timezone (`REPORT_TIMEZONE`, falling back to UTC if unavailable).
- **`insights.py`** — deterministic, rule-based (no LLM) "Key Insights" bullets:
  trend direction, hotspot zone (≥30% share), dominant incident type (≥25%),
  critical-severity flag, severity-shift alerts, peak-activity day, zero-incident
  zones, camera coverage gaps, an "all clear" message when there's nothing.
  `build_caveats()` separately flags data-quality issues (hit the analytics cap,
  zero cameras registered, zone-filtered scope, UTC fallback).
- **`pdf_renderer.py`** — pure rendering (no I/O — caller pre-fetches snapshot
  bytes, so it's fully unit-testable without DB/MinIO). ReportLab A4 layout: header
  band, metadata table, optional data-caveats box, KPI cards, insights, severity
  distribution bar, incidents-by-zone bar chart, trend line chart, current-vs-prior
  bar chart, incident-types table, priority-incidents table, and an optional
  evidence appendix (image grid of Critical/High snapshots). Uses vendored DejaVu
  Sans fonts for Unicode, falling back to Helvetica.
- **`email_sender.py` — `SmtpEmailSender`**: stdlib-only (`smtplib` +
  `email.message.EmailMessage`), supports either implicit TLS (port 465) or
  STARTTLS (port 587, mutually exclusive), builds a
  `multipart/mixed(multipart/alternative)` message (text+HTML+PDF attachment —
  attachment must come after the alternative body or Gmail hides the body text).
  Never logs raw `SMTPAuthenticationError` text (some servers echo credentials).
- **`report_service.py` — `ReportService`**: `fetch_snapshots()` concurrently
  downloads up to `REPORT_MAX_SNAPSHOTS=6` evidence images for Critical/High
  incidents (local disk, path-traversal-guarded, or MinIO presigned URL), every
  failure swallowed so a missing thumbnail never fails the whole report;
  downscales to 800px/JPEG q80. `validate_recipients()` enforces a recipient cap,
  email-shape regex, a header-injection guard, and an **allowlist** check
  (`REPORT_RECIPIENT_ALLOWLIST` — `@domain.com` matches a whole domain, exact
  strings match one address, empty = allow anyone) — this exists specifically
  because the email endpoint has no authentication. `email_report()` order:
  validate SMTP config → validate recipients → build report/PDF → optionally
  archive to MinIO (failure swallowed, never blocks delivery) → send → always write
  a `report_deliveries` audit row (sent or failed).
- **`schedule.py`** — pure date-math (no I/O, fully unit-testable): weekly uses
  `day_of_week` (Python `date.weekday()` convention), monthly clamps
  `day_of_month` to 1–28 to avoid "day 31 skips February". `most_recent_slot()`/
  `next_run_at()`/`is_due()`.
- **`schedule_service.py` — `run_due_schedule()`**: the periodic tick logic — no-op
  if `REPORT_EMAIL_ENABLED` is off; if due and recipients exist, sends with range
  `"7D"` (weekly) or `"30D"` (monthly); on failure does **not** stamp
  `last_sent_at`, so the next 15-minute tick retries rather than waiting a full
  period.
- **`scheduler.py` — `check_and_send_scheduled_report()`**: the function
  `app/main.py`'s APScheduler calls every 15 minutes; builds its own DB
  session directly (runs outside any HTTP request scope), skips gracefully if
  DB/MinIO aren't configured. 15-minute granularity is coarser than exact-minute
  cron but is fine since the date-math finds the correct scheduled slot regardless
  of tick alignment; does not survive multi-worker uvicorn (each worker would tick
  independently).

## 13. Backend — storage (MinIO + local)

- **`minio_client.py`** — `get_minio_client()` is a cached process-wide singleton;
  `ensure_bucket_exists()` auto-provisions the bucket lazily on first use (not at
  app startup).
- **`local_paths.py`** — resolves and creates `SNAPSHOT_DIR`/`UPLOAD_DIR`.
- **`evidence_storage.py` — `EvidenceStorage`**: typed upload methods per category
  (`upload_ppe_snapshot`/`upload_zone_snapshot`/`upload_behavior_snapshot`/
  `upload_report`), each validating extension. Object key convention:
  `<category>/YYYY/MM/DD/<uuid>.jpg` (or `reports/YYYY/MM/<uuid>.pdf`), all
  UTC-timestamped. `get_object_url()` returns a 1-hour presigned GET URL — the DB
  only ever stores the object *key*, never the presigned URL, since it's
  short-lived. `object_exists()`/`delete_object()` treat "not found" from MinIO as
  a clean `False`/no-op rather than an exception.
- **Local vs. MinIO**: local `storage/snapshots` is a temporary staging area — the
  pipeline writes there first, uploads to MinIO, then best-effort deletes the local
  copy. MinIO is the authoritative evidence archive; Postgres stores only the
  object key. Deleting a violation row does **not** delete its MinIO object
  (orphaned objects are an accepted tradeoff, not a bug).

## 14. Backend — scripts, tests, weights, trackers

### `backend/scripts/`
| Script | Purpose |
|---|---|
| `apply_new_indexes.py` | Since there's no migration framework (`create_all()` only adds whole missing tables, never alters existing ones), this creates any missing `Index` objects on an already-existing DB (idempotent). |
| `benchmark_llhls.py` | Measures LL-HLS playlist continuity and WS metadata delivery age for a set of RTSP sources; writes a JSON report. |
| `benchmark_streams.py` | Benchmarks live preview cadence/backend stage timings across feature-combination matrices (`ppe`, `ppe_sign`, `ppe_behavior`, `ppe_behavior_sign`). |
| `check_gpu_runtime.py` | Diagnostic: prints torch/CUDA/Ultralytics info and whether `PPEDetector` actually loaded on GPU. Always exits successfully (prints CPU/mock status if CUDA is unavailable). |
| `check_model.py` | Smoke test: loads `PPEDetector`, runs one dummy prediction. |
| `probe_stream_timing.py` | Opens an RTSP source directly to isolate whether lag originates at the source. |
| `probe_ws_timing.py` | Connects to `/ws/stream` like the frontend to isolate whether lag is backend-introduced. |
| `reset_db.py` | Destructive: drops and recreates every table from the current model schema. |
| `test_sign_detection.py` | Manual visual test for the sign-detection model (not a pytest suite despite the name). |
| `test_ws.py` | Minimal manual WebSocket smoke test (also not a pytest suite). |

### `backend/tests/`
A large suite covering repositories (`tests/repositories/`), routers
(`tests/routers/`), services including the full `reporting/` package
(`tests/services/`), storage (`tests/storage/`), and top-level unit/integration
tests for annotated-stream rendering, auto-zone, behavior inference/runtime config,
fall detection API/model, frame hub, incident diagnostics, inference device
selection, model cadence, PPE role logic, runtime cleanup, snapshot generation,
spatial geometry, stream health, streaming timeline contracts, tracking overlay
rendering, and worker finalization.

### Weights (`backend/weights/`)
`ppe_v4.pt` (PPE YOLO model), `pose.pt` (pose-estimation model), `reid.pt`
(re-identification embedding model), `best_behavior_model.joblib` (**current
primary** ExtraTrees behavior classifier), `behavior.joblib`/`behavior.ubj`
(alternate/legacy checkpoints, XGBoost fallback only used if the primary is
missing).

### Tracker configs
`backend/trackers/bytetrack.yaml` — live PPE tracking (long `track_buffer: 300` to
reduce ID churn during short occlusions). `backend/app/inference/
botsort_dedicated_reid.yaml` — BoT-SORT+ReID profile used by the behavior model's
training pipeline (`with_reid: true`, GMC disabled for fixed factory cameras).

---

## 15. Frontend — stack & routes

Next.js 16 (App Router) + React 19 + TypeScript, Tailwind CSS v4 (no separate
config file — `@theme inline` in `globals.css`), `@tanstack/react-query` for nearly
all REST polling, `hls.js` for LL-HLS playback, `recharts` for analytics charts,
`three`/`@react-three/fiber`/`@react-three/drei` for the 3D factory view,
`lucide-react` icons, Geist Sans/Mono fonts.

- **`app/layout.tsx`** — root layout, fonts, wraps everything in `QueryProvider`.
- **`app/page.tsx`** (`/`) — renders `<DashboardShell/>`, the entire "Camera Feeds"
  dashboard.
- **`app/analytics/page.tsx`** (`/analytics`) — standalone (non-embedded) copy of
  `AnalyticsDashboard`, dynamically imported with `ssr: false`.
- **`app/globals.css`** — Tailwind entry, theme variable mapping, the one shared
  `chip-in` micro-animation (respects `prefers-reduced-motion`).

## 16. Frontend — dashboard components

`src/components/dashboard/` — the largest and most complex part of the frontend.

- **`dashboard-shell.tsx`** (~1800 lines) contains:
  - **`DashboardShell()`** — top-level component; owns the active tab
    (`feeds`/`violations`/`factory3d`/`analytics`, synced to `?view=`), reconciles
    localStorage camera config against the backend (`ensureCamera`), renders the
    shared KPI row (same query key as the Analytics tab, so numbers never
    disagree), and mounts all four view bodies — `CameraPanel` and
    `AnalyticsDashboard` are kept **always mounted** and merely CSS-hidden on other
    tabs, so WebSocket connections and React Query polling survive tab switches.
  - **`CameraPanel`** — the "Camera Feeds" tab, the single most complex component:
    per-camera feature-toggle overrides (`getCameraFeatures`/`updateCameraFeatures`,
    optimistic with rollback), unifies uploaded-file and live-WebSocket sources
    behind one `sourceKey`, wires zone drawing (`useZoneDrawing`) and auto-zone
    suggestions (`useAutoZoneSuggestions`), camera lifecycle (add/remove/rename,
    RTSP URL, "3D blueprint zone" vs. the real analytics "home zone"), matrix view
    (grid of `LlHlsVideo` tiles with inline per-camera toggle badges) vs. single
    view (full `ZoneOverlaySvg` + `TrackingOverlayLayer` + suggestion banners +
    `ZoneConfigPanel`), and the run/analyze flow (blocks on overlapping zone
    conflicts before running).
  - **`ModelToggle`** — small on/off switch for per-camera model toggles.
  - **`IncidentPanel`** — the "Incident Log" tab: merged PPE+zone+behavior feed,
    client-side pagination, per-item and bulk delete, opens
    `IncidentDetailModal`.
  - Three separate "zone" concepts coexist and are easy to conflate: **`PhysicalZone`**
    (coarse factory-area, used for analytics scoping and camera home-zone),
    **`DraftZone`/`ZoneConfiguration`** (the drawn polygon per camera for
    restricted/walkway/slippery detection), and the **3D blueprint `ZoneId`**
    (`Z01`/`Z02`/`Z03`, used only by the 3D visualization's camera→block mapping).
- **`analysis-result-panel.tsx`** — post-run summary (video/live) with a compact
  incident list and a "Rerun selected models" action.
- **`data.ts`** — static nav config (3D Map deliberately hidden from the nav, though
  its route still works) and top-bar action icons.
- **`icon-button.tsx`**, **`metric-card.tsx`**, **`top-bar.tsx`**,
  **`zone-button.tsx`** — small presentational pieces (KPI tile, app header, one
  zone-list row with inline rename/delete).
- **`incident-detail-modal.tsx`** — fetches one incident's full detail by
  category+id, resolves camera/home-zone labels, computes severity client-side,
  supports "mark as false positive" delete.
- **`zone-config-panel.tsx`** — the zone-drawing sidebar: draw/modify mode switch,
  add-point/draw-curve toggles, name/type/dwell inputs, save/clear with animated
  state transitions.
- **`zone-overlay-svg.tsx`** — renders all zones as an absolutely-positioned
  `viewBox="0 0 1 1"` SVG overlay: filled/stroked polygons (supporting
  quadratic-Bezier curved edges), edge hit-targets for point insertion, and drag
  handles when a zone is selected.
- **`zone-sidebar.tsx`** — left rail listing `PhysicalZone`s with create/rename/
  delete and per-zone camera counts.

## 17. Frontend — PPE/video components

`src/components/ppe/`

- **`bounding-box-view.tsx`** — draws `Detection[]` boxes over an uploaded image.
- **`detection-chip.tsx`** — the shared "signature element": every "the model
  noticed something, your move" surface (zone-sign suggestion, PPE-sign banner)
  is built from this one chip component so they read as one visual system (sky
  tone = suggestion state).
- **`file-upload.tsx`** — drag-and-drop/click file picker.
- **`ll-hls-video.tsx` — `LlHlsVideo`**: the core video component for both live and
  annotated streams. Creates an `Hls` instance with low-latency config
  (`liveSyncDuration: 2s`, `liveMaxLatencyDuration: 3s`), falls back to native HLS
  for Safari-like browsers, retries on `NETWORK_ERROR`, recovers `MEDIA_ERROR`.
  Guards playback resume against a hidden tab or a different fullscreen owner.
  Reports live-delay/rebuffer/dropped-frame metrics every second and a per-rendered
  -frame timeline callback (via `requestVideoFrameCallback`) that drives overlay
  sync. Sizes an inner ratio-locked "stage" so zone SVG overlays remain correctly
  positioned before/during/after fullscreen.
- **`result-panels.tsx`** — `DetectionSummary`, `PeopleResults`, and the universal
  **`IncidentCard`** (discriminates PPE/behavior/zone incidents to pick the right
  image/title/badges), plus shared `EmptyState`/`LoadingState`/`ErrorState`.
- **`video-tracking-overlay.tsx`** (largest ppe component): **`TrackingOverlayLayer`**
  (live) selects the right overlay frame for the current playback moment via
  `stream-timeline.ts`'s interpolation logic, merges in live behavior detections by
  track-id/IoU match, and reports rendered-frame skew back up for backend
  telemetry; **`VideoTrackingOverlay`** (uploaded-file variant, driven by
  `requestAnimationFrame` polling of `video.currentTime`); **`TrackingBoxes`**
  (shared SVG box+label renderer, red for any violation, lime otherwise);
  **`SuggestionOverlayLayer`**/**`PPESuggestionBanner`** (auto-zone/PPE-sign
  suggestion UI).

## 18. Frontend — analytics components

`src/components/analytics/`

- **`analytics-dashboard.tsx`** (~940 lines) — the full Incident Analytics view
  (standalone at `/analytics` or embedded in the dashboard's Analytics tab, gating
  chart rendering on visibility to avoid recharts work while hidden). Polls summary/
  trend/compare/feed independently every 5s. Hand-rolled SVG "Zone Pulse" radial
  diagram (not recharts) with a center "ALL ZONES" reset node and per-zone nodes
  sized by incident total; a live feed panel that flags newly-arrived rows for a
  fade-in animation; stacked area/line/pie/bar recharts visualizations; and the
  export/schedule dialogs.
- **`report-export-dialog.tsx`** — one-off PDF download or email send, with a
  live preview (total count, caveats, top insights) fetched on open.
- **`schedule-report-dialog.tsx`** — recurring schedule configuration (frequency,
  day/time, zone scope, recipients, snapshot inclusion), shows next/last-sent.

## 19. Frontend — 3D factory view

`src/components/factory3d/` — a stylized top-down 3D floor-plan visualization built
on three.js via `@react-three/fiber`/`@react-three/drei`.

- **`factory-layout.ts`** — static blueprint geometry (`ZONES` Z01 Production/Z02
  Warehouse/Z03 Packing — position/size/color/camera-matching config; currently
  only Z01 is `active` with a real camera).
- **`use-zone-incidents.ts`** — buckets merged incidents into per-zone aggregates
  (total, per-category counts, 24h trend delta), resolving each incident to a zone
  via a priority chain (direct zone match → camera match → video-name substring
  match → fallback to the single active zone).
- **`zone-block.tsx`** — one 3D block per zone: color lerps toward red as incident
  count rises (heat ramp), dims when inactive, hover/select highlighting, floating
  label, hover tooltip.
- **`zone-detail-panel.tsx`** — DOM panel for the selected zone's summary and
  incident list (reuses `IncidentCard`).
- **`factory-3d-view.tsx`** — top-level component; merges live camera config onto
  the static zones, auto-refreshes every 20s, renders a non-3D DOM fallback
  zone-list (for WebGL-less/screen-reader access) alongside the 3D canvas.
  Clicking an active zone can switch the dashboard's "feeds" tab to that zone's
  camera.

## 20. Frontend — hooks

`src/hooks/`

- **`useLiveStream.ts`** (~730 lines) — the WebSocket orchestrator: manages **one
  socket per active camera**, keyed by RTSP URL (so matrix view can stream several
  concurrently). Sends `update_settings` immediately on open and again whenever
  toggle/feature state changes. Distinguishes intentional closes, superseded closes
  (backend code 4001 — must not reconnect), and unexpected closes (retried with
  backoff, capped at 5 attempts). Handles all inbound event types (`frame`,
  `violation`, `zone_violation`, `behavior_incident`, `zone_suggestion`,
  `ppe_suggestion`, `summary`, `error`, `ping`), maintaining per-camera ring buffers
  for overlay/behavior history. Paces uploaded-video playback against inference
  progress. Forwards HLS timeline/playback-metrics back to the backend so it knows
  how stale the operator's actual rendered view is.
- **`useZoneDrawing.ts`** (~520 lines) — the full polygon editor state machine:
  draw/modify modes, vertex/edge/curve-control-point dragging, click-to-add-point,
  keyboard delete, persistence (delete-all-then-resave, flattening Bezier curves to
  line segments for the backend), and overlap validation before save.
  `flattenPoints()` approximates each curve with 8 line segments for the
  polygon-only backend geometry engine.
- **`useAutoZoneSuggestions.ts`** — accept/dismiss/enable behavior layered over
  suggestion state that lives in `useLiveStream` (since that's where the WS events
  land); accepting force-enables zone monitoring and, if not currently streaming,
  opens modify mode for fine-tuning.
- **`useDetectionUpload.ts`** — uploaded-file/image inference state.
- **`useSafetyKpis.ts`** — thin React Query wrapper around the analytics summary,
  producing the shared 4-tile KPI row (two tiles — PPE Compliance, People On Shift —
  are documented as not yet wired, shown as `"—"` placeholders).
- **`camera-panel-types.ts`** — shared types (`AnalysisPhase`, `DraftZone`).

## 21. Frontend — lib (API client, geometry, timeline sync)

`src/lib/`

- **`ppe-api.ts`** (~450 lines) — the full REST client, grouped by resource:
  inference (`analyzeImage`/`uploadVideo`/`analyzeVideo`), PPE/zone/behavior
  violations (get/detail/delete, plus `getSafetyEvents()` which fetches all three
  in parallel and merges), unified incidents, drawn zones, analytics, physical
  zones, cameras (including the idempotent `ensureCamera`), features, and reports
  (preview/PDF-download/email/schedule).
- **`stream-timeline.ts`** — the overlay time-sync engine: `rtspToHlsUrl()`
  converts an RTSP source into its HLS playlist URL; `appendOverlayRing()`
  maintains a 10-second ring buffer of tracking frames; **`selectOverlayAtTime`**
  is the core per-track algorithm choosing which detection box to render at the
  current playback instant — exact match if available, a short bounded "hold" of a
  slightly-stale box, linear interpolation between surrounding samples (capped at 8
  frames of span), or dropping the box rather than ever painting a future
  detection on an earlier frame. Returns diagnostics (`mode`, `signedSkewMs`) used
  both for an "AI delayed" UI indicator and reported back to the backend.
- **`spatial-utils.ts`** — `isPointInPolygon`, `doSegmentsIntersect`,
  `doPolygonsOverlap` (used to block saving/running overlapping cross-type zones).
- **`format.ts`** — pluralization helpers. **`messages.ts`** — shared confirm-dialog
  strings.

## 22. Frontend — types & design system

`src/types/` mirrors the backend Pydantic schemas closely: `analytics.ts`,
`behavior.ts` (includes the live-only `FallLiveDetection`/`FallLiveStatus` shapes),
`camera.ts`, `detection.ts` (the core `TrackingOverlayFrame`/`StreamEvent`
contract), `report.ts`, `zone.ts` (`Point2D` with an optional `curveControl` — what
makes an edge a Bezier curve instead of a straight line).

**Design system** (`frontend/DESIGN_TOKENS.md`/`REDESIGN_NOTES.md`): color
semantics are deliberately collapsed to exactly four states used identically
everywhere — **Suggestion** (sky), **Pending** (amber), **Active** (lime, the
brand accent), **Violation** (red) — replacing three near-identical warm hues that
were previously doing different jobs. Geist Mono is used specifically for scannable
numerals (counts, deltas, timestamps), not just chart ticks. Motion is deliberately
minimal (one 160ms chip-in transition, `prefers-reduced-motion`-aware).
`DetectionChip` is called out as the app's one signature element — every
model-initiated suggestion surface shares its visual language and
`suggested → accepted/dismissed` state machine.

---

## 23. Local infrastructure (Docker, MediaMTX, RTSP publisher)

- **`docker-compose.yml`** — three services, no backend container (the backend runs
  natively): `mediamtx` (RTSP `:8554`, HLS `:8888`), `postgres:16` (host port 5433),
  `minio` (API `:9000`, console host port 9011). `docker compose down -v`
  permanently deletes DB/object data.
- **`mediamtx.yml`** — RTSP on `:8554` (TCP only), LL-HLS on `:8888` with
  low-latency tuning (`hlsVariant: lowLatency`, 1s segments, 200ms parts). WebRTC/
  RTMP/SRT disabled. Paths are only live while something is actively publishing.
- **`scripts/publish-rtsp.ps1`** — wraps `ffmpeg` to loop-publish a local video
  file as an RTSP stream, simulating a factory camera for local dev. Normalizes to
  CFR 24 FPS, H.264, 1s GOP, no B-frames (matches the backend's 24 FPS behavior-
  pipeline assumption). Key params: `-InputPath`, `-StreamName`, `-MaxWidth`
  (default 1920), `-Crf` (23), `-MaxRate` (8M), `-RateControlBuffer` (16M),
  `-NoLoop`.

---

## 24. Known gaps, dead code, and doc discrepancies

Findings surfaced during this review that are worth knowing before relying on
adjacent documentation or certain code paths:

- **`PPEDetector.predict()` calls a nonexistent `_real_predict`** when a real model
  is loaded (`ppe/detector.py`) — latent dead code, unexercised because the only
  production callers (`process_video`/`stream_video`) route through
  `video_pipeline` instead of `predict()` directly. Confirmed still present; also
  flagged in `backend/docs/ppe_detector_refactor_map.md`.
- **Auto-zone/PPE-sign suggestion registries** (`SignZoneRegistry`,
  `SignPPERegistry` in `auto_zone.py`) define an `ACCEPTED` terminal state that is
  never actually assigned — `accept()` only sets a generic `emitted=True`, so the
  backend can't distinguish "operator accepted" from "operator dismissed" beyond
  both being terminal. Matches a known gap documented in
  `docs/features/automatically_updated_zone.md`.
- **`backend/docs/api_routes.md` is significantly stale** relative to the actual
  routers: it's missing the `cameras`, `features`, `analytics`,
  `fall_detection`/`behavior-detection`, and report-schedule endpoints entirely; it
  documents `GET /zones/{video_name}`-style path params that are actually query
  params in code (`GET /zones?video_name=...`); and it omits `GET`/`DELETE
  /health/streams` and the `behavior_incidents_deleted` field in `DELETE
  /violations`'s response.
- **`backend/docs/database_schema.dbml`** documents a target/aspirational schema
  in places, not the current one: it types `dwell_threshold_seconds` as `integer`
  (code: `Float`), documents `status`/`severity` columns on `ppe_violations` that
  don't exist on the actual model, and its own header note says "behavior
  violations... deferred" — which is stale, since `behavior_incidents` and its
  subject/evidence tables are fully implemented.
- **Two `BehaviorType` enum values are currently unreachable**: `FAINT_DETECTED`
  and `COLLAPSE_DETECTED` are defined on the model and in frontend types, but the
  live classifier only ever produces 3 classes (`others`/`running`/`falling`), so
  only `RUNNING_DETECTED`/`FALL_DETECTED` are actually persisted today.
- **Reporting email endpoint has no authentication** — `POST
  /reports/incidents/email` is reachable by anyone who can reach the backend.
  `REPORT_EMAIL_ENABLED` defaults to `false` specifically to guard this, and
  `REPORT_RECIPIENT_ALLOWLIST` should be set non-empty before enabling it in any
  deployment reachable outside a trusted network (see
  `backend/docs/internal_deployment.md`). The rate limiter is in-process only and
  does not survive multi-worker uvicorn deployments.
- **The report-schedule background job (APScheduler, 15-minute tick) and the
  MinIO-bucket auto-provisioning are both in-process, single-worker assumptions** —
  running multiple uvicorn workers would multiply scheduled-report sends and is
  explicitly called out as unsupported by current code.
- **A per-camera reload-survival ("session grace") feature was prototyped and then
  fully reverted** (per `docs/architecture/context.md`, August 2026 session notes):
  there is currently no `stream_sessions.py`, no `STREAM_DISCONNECT_GRACE_SECONDS`,
  and browser page reload still tears down and restarts that camera's backend
  pipeline via the existing lock/supersede WebSocket lifecycle.
- **Two frontend KPI tiles are placeholders**: "PPE Compliance" and "People On
  Shift" in `useSafetyKpis.ts` render `"—"` because the underlying data isn't wired
  yet — don't mistake this for a bug.
- **`wscat`** appears only as a dev-time raw-WebSocket debugging tool
  (`package.json` devDependency, referenced in session notes) — not a runtime
  dependency of the app.
