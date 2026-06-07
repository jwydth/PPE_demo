# Part 1: System Architecture & Data Contract (Updated)

You are an expert full-stack engineer. We are building the "Manual Zone Configuration" module for an Industrial Safety Monitoring System using Next.js (Frontend), Fabric.js (Canvas Layer), and FastAPI/SQLite (Backend).

## 1. DATABASE SCHEMA (SQLite via SQLModel/SQLAlchemy)

### `zones` table:
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `video_name`: TEXT (Used as camera_id, Indexed)
- `zone_name`: TEXT
- `zone_type`: TEXT (Enum: 'RESTRICTED', 'WALKWAY')
- `dwell_threshold_seconds`: INTEGER (default=0)
- `is_active`: BOOLEAN (default=1)
- `ui_shape_data`: TEXT (JSON string storing Fabric.js vector metadata: center, radii, path commands)
- `flattened_coordinates`: TEXT (JSON string storing normalized 2D array [[x1, y1], [x2, y2], ...] from 0.0 to 1.0)

### `zone_violations` table:
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `zone_id`: INTEGER (FK to zones.id)
- `track_id`: INTEGER
- `timestamp`: TEXT (ISO format)
- `video_name`: TEXT
- `frame_index`: INTEGER
- `snapshot_path`: TEXT

## 2. TYPESCRIPT / NEXT.JS INTERFACES
Define matching TypeScript types in `frontend/types/zone.ts`:
- `ShapeType`: ('polygon' | 'circle' | 'ellipse' | 'freeform')
- `ZoneType`: ('RESTRICTED' | 'WALKWAY')
- `Point2D`: { x: number; y: number }
- `ZoneConfiguration`: (matching the `zones` table schema)

---

# Part 2: Frontend Canvas Layer (Next.js + Fabric.js)

Implement `ZoneDrawingCanvas.tsx` using `fabric` (v5+).

## 1. UI COMPONENTS & NAVIGATION
- Add "Zone Configuration" tab to `frontend/app/page.tsx`.
- Implement a toolbar overlay for the drawing canvas:
    - [Select]: Modify/move existing shapes.
    - [Polygon]: Click to add points, double-click to close.
    - [Circle/Oval]: Drag to create.
    - [Freeform]: Bézier path support.
    - [Save]: Persist to backend.

## 2. COORDINATE NORMALIZATION & LINEARIZATION
- **Normalize:** Helper to map pixel clicks `(x, y)` to `(x/width, y/height)` as floats (0.0 - 1.0).
- **Linearize:** Function to "flatten" complex Fabric objects (Circles, Ellipses, Paths) into a series of high-density points (e.g., 64 points for a circle) for the `flattened_coordinates` field.

## 3. VISUAL RULES
- **RESTRICTED:** Semi-transparent Red fill, solid Red border.
- **WALKWAY:** Semi-transparent Green fill, solid Green border.
- **INACTIVE:** No fill, Grey dashed border.

---

# Part 3: Backend Zone Management (FastAPI)

Implement `/api/zones` router in `backend/app/routers/zones.py`.

## 1. API ENDPOINTS
- `POST /api/zones`: Save new configuration.
- `GET /api/zones/:video_name`: Fetch all zones for a specific video source.
- `PUT /api/zones/:id`: Update existing zone.
- `DELETE /api/zones/:id`: Remove a zone.

## 2. STORAGE INTEGRATION
- Use `backend/app/services/violation_store.py` patterns for SQLite access.
- Ensure `ui_shape_data` and `flattened_coordinates` are correctly serialized/deserialized as JSON strings.

---

# Part 4: Incursion Engine (Updated)

## 1. SPATIAL ANALYSIS UTILITIES
- `backend/app/services/spatial.py`:
    - `is_point_in_polygon(point, polygon)`: Uses `cv2.pointPolygonTest`.

## 2. INCURSION ENGINE (`PPEDetector`)
- **Worker State Extension**:
    - `zone_dwell: dict[int, float]` (track_id -> zone_id -> dwell_time).
- **Inference Loop Logic**:
    1. Calculate "Foot Point" (bottom-center of person bbox) and normalize to 1000x1000.
    2. Test if Foot Point is inside Zone Polygon (`cv2.pointPolygonTest`).
    3. Accumulate dwell time and trigger `ZoneViolation` when `dwell > threshold`.

## 3. REPORTING & VISUALIZATION
- Implement `save_zone_violation` in `violation_store.py`.
- Update Frontend to display Zone Violations in the history panel.
- **Evidence Snapshots**: Draw the breached zone polygon on the violation snapshot for visual proof.
