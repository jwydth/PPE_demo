# Branch: `automaticaly_zone_updated` — Summary

This document describes all the work done on this branch so a teammate can understand what exists, what is working, and what still needs verification.

---

## Goal

Automatically generate safety zones from detected safety signs in video footage, so operators do not have to draw zones manually in the UI. When the model detects a **P004 (No Thoroughfare)** or **W011 (Slippery)** sign in a video frame, it generates a `RESTRICTED` zone polygon around the danger area and includes it in the API response so the frontend can draw it as an overlay on the video player.

---

## What Was Built

### 1. Sign Detection Model (`backend/app/services/detection/sign_detector.py`)

A new module that wraps a second YOLO model (`weights/sign_model.pt`) dedicated to detecting safety signs.

**Key items:**

- `SIGN_CLASS_MAP` — maps class IDs to sign names: `{2: "P004_NoThoroughfare", 3: "W011_Slippery"}`
- `load_sign_model()` — called once at server startup inside `PPEDetector.__init__()`. Resolves the model path relative to the backend directory, logs a warning if the file is missing, and prints a confirmation message when it loads successfully.
- `detect_signs(image: PIL.Image) -> list[SignDetection]` — runs inference on a single frame, returns detections for class 2 and 3 only. Returns `[]` if the model is not loaded.
- `signs_to_zone_records(detections, frame_width, frame_height) -> list[ZoneViolationRecord]` — converts each detected sign into a `ZoneViolationRecord` with a direction-aware expanded polygon:
  - **Wide horizontally**: expands `SIGN_ZONE_EXPAND_RATIO` (default `3.0`) × sign width left and right.
  - **Large downward**: expands `SIGN_ZONE_EXPAND_RATIO × 2.0` × sign height downward (covers the floor danger area).
  - **Small upward**: expands `0.5` × sign height upward only.
  - Polygon points are in 0–1000 normalised coordinates (`COORD_SCALE`).
  - Each sign zone gets `camera_zone_view_id = -class_id` (e.g. class 2 → `-2`, class 3 → `-3`). **Negative IDs mean ephemeral / not stored in the DB.**

**Config settings** (in `backend/app/core/config.py` / `.env`):

```
SIGN_MODEL_PATH=weights/sign_model.pt        # path to sign YOLO weights
SIGN_CONFIDENCE_THRESHOLD=0.4                # lower this if signs are not being detected
SIGN_ZONE_EXPAND_RATIO=3.0                   # how far to expand the zone around the sign
```

---

### 2. Video Processing Loop (`backend/app/services/ppe_detector.py`)

Both `_real_process_video` and `_mock_process_video` were updated to:

1. **Run sign detection on every frame:**

   ```python
   _pil_frame = PIL.Image.fromarray(frame[..., ::-1])  # BGR → RGB
   _sign_detections = detect_signs(_pil_frame)
   _auto_zones = signs_to_zone_records(_sign_detections, frame_width, frame_height)
   active_zones = zones + _auto_zones   # DB zones + sign zones merged
   ```

2. **Collect unique zone polygons** across all frames into `collected_zones: dict[str, ZonePolygon]`:
   - DB-backed zones are stored with key `saved:<zone_name>` and `source="saved"`.
   - Sign-generated zones are stored with key `sign:<zone_name>` and `source="sign"`.
   - Only the first occurrence is stored (zones are stable across frames).

3. **Log sign detection counts per frame** (visible at INFO level in the server console):

   ```
   INFO  Frame 0: 2 sign(s) detected
   ```

4. **Pass collected zones to `TrackingOverlay`** in the response:

   ```python
   TrackingOverlay(..., zones=list(collected_zones.values()))
   ```

5. **Route zone violation recording** via `_record_zone_violation_dispatch()`:
   - Zones with a positive `camera_zone_view_id` → normal DB path via `record_zone_violation()`.
   - Zones with a negative `camera_zone_view_id` (sign zones) → direct `persist_zone_violation()` call with `camera_zone_view_id=None` and `physical_zone_id=None`, bypassing the FK validation that would crash with a negative ID.

---

### 3. API Schema (`backend/app/schemas/detection.py`)

Added `ZonePolygon` model and a `zones` field on `TrackingOverlay`:

```python
class ZonePolygon(BaseModel):
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY"]
    source: Literal["sign", "saved"]   # "sign" = auto-generated, "saved" = drawn by operator
    points: list[tuple[float, float]]  # normalised 0–1000 coordinates

class TrackingOverlay(BaseModel):
    ...
    zones: list[ZonePolygon] = []      # NEW — all zones visible in this video
```

---

### 4. Frontend Types (`frontend/src/types/detection.ts`)

Added the matching TypeScript interface and extended `TrackingOverlay`:

```typescript
export interface ZonePolygon {
  zone_name: string;
  zone_type: "RESTRICTED" | "WALKWAY";
  source: "sign" | "saved";
  points: [number, number][];   // normalised 0–1000
}

export interface TrackingOverlay {
  ...
  zones?: ZonePolygon[];
}
```

---

### 5. Frontend Overlay Rendering (`frontend/src/components/ppe/video-tracking-overlay.tsx`)

Added a `ZonePolygons` SVG component that renders zone polygons on top of the video:

- **RESTRICTED zones** (sign-generated and manually drawn): red fill + red stroke (`#ef4444`).
- **WALKWAY zones**: blue fill + blue stroke (`#3b82f6`).
- Zone name label is drawn at the top-left corner of each polygon.
- Points are converted from 0–1000 normalised coordinates to the SVG viewport: `px = (nx / 1000) * frameWidth`.
- Zones are rendered **independently of person detection** — the SVG is shown even when zero workers are on screen (previously the SVG was hidden when `currentBoxes.length === 0`).
- Both `VideoTrackingOverlay` (full player) and `TrackingOverlayLayer` (inline overlay) were updated.

---

## File Map

| File                                                     | Change                                                                                  |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `backend/app/services/detection/sign_detector.py`        | **New file** — sign model wrapper                                                       |
| `backend/app/services/ppe_detector.py`                   | Sign detection wired into both video loops; zone collection; ephemeral zone dispatch    |
| `backend/app/schemas/detection.py`                       | Added `ZonePolygon` model; added `zones` field to `TrackingOverlay`                     |
| `frontend/src/types/detection.ts`                        | Added `ZonePolygon` interface; extended `TrackingOverlay`                               |
| `frontend/src/components/ppe/video-tracking-overlay.tsx` | Added `ZonePolygons` SVG component; SVG now shows even with 0 workers                   |
| `backend/app/core/config.py`                             | Added `SIGN_MODEL_PATH`, `SIGN_CONFIDENCE_THRESHOLD`, `SIGN_ZONE_EXPAND_RATIO` settings |

---

## How to Verify It Is Working

### 1. Check the sign model loads at startup

When you run `uvicorn app.main:app --reload`, look for this line in the console (it appears before the uvicorn banner, mixed with YOLO output):

```
[sign_detector] Sign model loaded OK — .../weights/sign_model.pt (conf=0.5)
```

If you see this instead, the model file is missing or corrupt:

```
WARNING: Sign model not found at ... — sign detection disabled
```

### 2. Check sign detection during video processing

After submitting a video, look for per-frame logs like:

```
INFO  Frame 0: 0 sign(s) detected
INFO  Frame 5: 2 sign(s) detected
```

If every frame shows `0 sign(s)`, lower the confidence threshold in `.env`:

```
SIGN_CONFIDENCE_THRESHOLD=0.25
```

### 3. Check the API response

After processing a video that contains signs, the `tracking_overlay.zones` field in the JSON response should contain entries like:

```json
{
  "zones": [
    {
      "zone_name": "P004_NoThoroughfare",
      "zone_type": "RESTRICTED",
      "source": "sign",
      "points": [
        [120.5, 80.0],
        [630.2, 80.0],
        [630.2, 850.3],
        [120.5, 850.3]
      ]
    }
  ]
}
```

### 4. Check the frontend overlay

Open the video result page in the browser. Red polygon overlays should appear on the video wherever a sign was detected. The zone name (`P004_NoThoroughfare` or `W011_Slippery`) is shown as a label at the top-left corner of each polygon.

---

## Known Limitations / Next Steps

- Sign zones are **re-detected on every frame** which means they may flicker if the model detects the sign inconsistently across frames. A future improvement would be to smooth or persist sign detections across frames.
- The sign zone polygon uses the **first frame where the sign appears** as the canonical polygon — if the camera moves, the polygon position will be stale.
- Currently only two sign classes trigger zones: class 2 (`P004_NoThoroughfare`) and class 3 (`W011_Slippery`). Add more entries to `SIGN_CLASS_MAP` and `SIGN_ZONE_TYPE_MAP` in `sign_detector.py` to support additional signs.
- Sign zone violations are saved to the database with `camera_zone_view_id=NULL` and `physical_zone_id=NULL` because they are not tied to a manually configured zone.
