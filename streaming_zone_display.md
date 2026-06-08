# Zone Configuration – Live Bounding Box Overlay

This document describes the full implementation of the per-frame bounding box overlay shown on the video player inside the Zone Configuration tab. A friend re-implementing the frontend can use this as the specification.

---

## Overview

When the user clicks **Start Monitoring**, the frontend uploads the video to the backend, which processes every frame and returns a `VideoProcessingResponse`. This response includes a `tracking_frames` array — one entry per detected person per sampled frame — containing their bounding box position and current zone status. The frontend replays this data in sync with the video's `onTimeUpdate` event to simulate a live tracking overlay.

---

## Backend Changes

### 1. New model: `PersonTrackFrame` (`backend/app/models/schemas.py`)

```python
class PersonTrackFrame(BaseModel):
    frame_index: int
    track_id: int
    bbox: BoundingBox        # normalized 0.0–1.0 relative to frame width/height
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None  # "RESTRICTED" | "WALKWAY" | None
```

`zone_type` is only set when the person is in a violation state:
- `"RESTRICTED"` — the person's foot point is inside a restricted zone polygon
- `"WALKWAY"` — the person's foot point is outside all walkway zone polygons (i.e., they left the walkway)
- `None` — person is safe (inside walkway or no zones configured)

### 2. Updated `VideoProcessingResponse` (`backend/app/models/schemas.py`)

```python
class VideoProcessingResponse(BaseModel):
    summary: VideoSummary
    reports: List[ViolationReport]
    zone_violations: List[ZoneViolation] = []
    tracking_frames: List[PersonTrackFrame] = []   # ← new field
```

### 3. Collection logic (`backend/app/services/ppe_detector.py`)

Inside the main video processing loop, after computing zone incursions for each person per frame, a `PersonTrackFrame` is appended:

```python
# Determine zone status for display
track_zone_id = None
track_zone_name = None
track_zone_type = None

if zones:
    # incursion_zones = zones the person's foot point is currently inside
    for zone in incursion_zones:
        if zone.zone_type == "RESTRICTED":
            track_zone_id = zone.zone_id
            track_zone_name = zone.zone_name
            track_zone_type = "RESTRICTED"
            break

    if track_zone_type is None:
        walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
        if walkway_zones and not any(z.zone_id in incursion_zone_ids for z in walkway_zones):
            # Person is outside every walkway zone → walkway violation
            wz = walkway_zones[0]
            track_zone_id = wz.zone_id
            track_zone_name = wz.zone_name
            track_zone_type = "WALKWAY"

tracking_frames_list.append(PersonTrackFrame(
    frame_index=frame_index,
    track_id=person.track_id or 0,
    bbox=BoundingBox(
        x1=person.bbox.x1 / frame_width,   # normalize to 0–1
        y1=person.bbox.y1 / frame_height,
        x2=person.bbox.x2 / frame_width,
        y2=person.bbox.y2 / frame_height,
    ),
    zone_id=track_zone_id,
    zone_name=track_zone_name,
    zone_type=track_zone_type,
))
```

Priority: RESTRICTED beats WALKWAY. A person inside a restricted zone will never simultaneously show as a walkway violation.

### 4. `ZoneViolation` also carries `bbox` (`backend/app/models/schemas.py`)

```python
class ZoneViolation(BaseModel):
    ...
    bbox: Optional[BoundingBox] = None   # normalized 0–1 at the violation frame
```

Populated inside `record_zone_violation` in `zone_service.py`:

```python
frame_height, frame_width = frame.shape[:2]
normalized_bbox = BoundingBox(
    x1=person.bbox.x1 / frame_width,
    y1=person.bbox.y1 / frame_height,
    x2=person.bbox.x2 / frame_width,
    y2=person.bbox.y2 / frame_height,
)
```

This is used by the detection history cards; the live overlay uses `tracking_frames` instead.

---

## Frontend Changes

### 1. Type definitions (`frontend/types/detection.ts`)

```typescript
export interface PersonTrackFrame {
  frame_index: number;
  track_id: number;
  bbox: { x1: number; y1: number; x2: number; y2: number };
  zone_id?: number;
  zone_name?: string;
  zone_type?: string;  // "RESTRICTED" | "WALKWAY" | undefined
}

export interface VideoProcessingResponse {
  summary: VideoSummary;
  reports: ViolationReport[];
  zone_violations?: ZoneViolation[];
  tracking_frames?: PersonTrackFrame[];   // ← new field
}
```

`ZoneViolation` in `frontend/types/zone.ts` also gains:
```typescript
bbox?: { x1: number; y1: number; x2: number; y2: number };
```

### 2. Canvas overlay element (inside the video container JSX)

The video container uses four absolute layers stacked by z-index:

| z-index | Element | Purpose |
|---------|---------|---------|
| 0 | `<video>` | Raw video playback |
| 10 | Fabric.js `<canvas>` | Zone polygon drawing/editing |
| 20 | Plain HTML5 `<canvas ref={bboxCanvasRef}>` | Bounding box overlay (pointer-events: none) |
| 30 | Alert badge div | "⚠️ Restricted Area Breach" text badge |

The bbox canvas must be explicitly sized to match the display dimensions (CSS `width/height` alone does not set canvas pixel resolution):

```typescript
useEffect(() => {
  const canvas = bboxCanvasRef.current;
  if (!canvas) return;
  canvas.width = dimensions.width;
  canvas.height = dimensions.height;
}, [dimensions]);
```

### 3. Drawing logic — `drawBboxOverlay`

Called on every `playbackTime` change via `useCallback` + `useEffect`.

#### Step 1 — Compute current frame

```typescript
const fps = analysisResult.summary.fps || 30;
const stride = Math.max(1, Math.round(
  (analysisResult.summary.total_frames || 1) /
  Math.max(1, analysisResult.summary.processed_frames || 1)
));
const currentFrame = Math.floor(playbackTime * fps);
```

`stride` is reconstructed from `total_frames / processed_frames`. With `VIDEO_FRAME_STRIDE=1` (default) stride = 1.

#### Step 2 — Deduplicate by track_id

```typescript
const byTrackId = new Map<number, PersonTrackFrame>();
for (const f of analysisResult.tracking_frames ?? []) {
  const dist = Math.abs(f.frame_index - currentFrame);
  if (dist > stride * 2) continue;              // outside visible window
  const prev = byTrackId.get(f.track_id);
  if (!prev || Math.abs(prev.frame_index - currentFrame) > dist) {
    byTrackId.set(f.track_id, f);               // keep only the closest frame
  }
}
const visible = Array.from(byTrackId.values());
```

**Why deduplicate?** Without this, every sampled frame within the window matches for the same track_id, causing N stacked boxes at the same position.

#### Step 3 — Color by zone status

```typescript
const isRestricted = tf.zone_type === "RESTRICTED";
const isWalkway    = tf.zone_type === "WALKWAY";
const inViolation  = isRestricted || isWalkway;

const color = isRestricted ? "#ef4444"   // red
            : isWalkway    ? "#3b82f6"   // blue
                           : "#22c55e";  // green (safe)
```

#### Step 4 — Draw bounding box

For each person in `visible`:
1. Scale normalized bbox to canvas pixels: `cx1 = x1 * canvas.width`, etc.
2. Semi-transparent fill rect
3. Stroked rectangle border (thicker when in violation)
4. L-shaped corner accents at all four corners

#### Step 5 — Draw info panel

- **In violation** → 4-line panel: zone type label / zone name / Track ID / Frame index
- **Safe** → 1-line panel: Track ID only

Panel is positioned below the bbox by default; flips above if it would overflow the canvas bottom. Right-aligns if it would overflow the canvas right edge.

Color coding matches the box:
- `"RESTRICTED ZONE"` label in red `#ef4444`
- `"WALKWAY VIOLATION"` label in blue `#3b82f6`
- Track ID in `#a1a1aa`, Frame in `#71717a`
- Panel background `rgba(9,9,11,0.88)`, border in accent color

### 4. Zone polygon highlight rules

Zone polygons on the Fabric.js canvas are **never recolored** during monitoring. They always stay in their original design-time styles:

| Zone type | Fill | Stroke |
|-----------|------|--------|
| RESTRICTED | `rgba(239, 68, 68, 0.3)` | `#ef4444` |
| WALKWAY | `rgba(34, 197, 94, 0.3)` | `#22c55e` |

The bounding box color on the worker is the only visual indicator of a breach — zone polygons do not pulse or change color.

### 5. Alert badge rule

The `"⚠️ Restricted Area Breach"` badge only appears when at least one active zone violation (within the current frame window) has `zone_type === "RESTRICTED"`. Walkway violations do not trigger it.

```typescript
setActiveZoneBreaches(
  activeZoneViolations.some((zv) => zv.zone_type === "RESTRICTED")
);
```

---

## Data Flow Summary

```
User draws zones  ──POST /zones──►  DB (flattened_coordinates, zone_type)
                                         │
User clicks "Start Monitoring"           │
  │                                      │
  ├─ POST /predict-video ──► backend loads zones from DB
  │                          for each frame × each person:
  │                            - normalize bbox to 0–1
  │                            - check foot-point vs zone polygons
  │                            - set zone_type (RESTRICTED / WALKWAY / None)
  │                            - append PersonTrackFrame
  │
  └─ VideoProcessingResponse ◄──────────────────────────────┘
       └─ tracking_frames[]   (one entry per person per sampled frame)

Video plays  ──onTimeUpdate──►  currentFrame = floor(playbackTime × fps)
                                 byTrackId = nearest frame per track_id within stride×2
                                 for each entry → draw bbox + info panel on Canvas overlay
```

---

## Key Implementation Notes

- **No live streaming.** The bounding boxes are pre-computed offline and replayed. The `onTimeUpdate` event drives the illusion of real-time tracking.
- **Coordinate system.** All bboxes stored and transmitted as normalized floats `0.0–1.0`. Multiply by `canvas.width` / `canvas.height` to get display pixels.
- **One box per person.** The `Map<track_id, frame>` deduplication is essential — without it, multiple frames in the window window stack identical boxes on the same position.
- **Foot-point zone testing.** The backend tests the bottom-center of the person's bbox (foot point) against zone polygons, not the full bbox. This prevents false positives when a person stands near but not inside a zone.
- **RESTRICTED priority.** A person inside a restricted zone will never simultaneously show a walkway warning, even if they are also outside a walkway zone boundary.
