# De Heus PPE Safety Monitor

A smart-factory computer-vision system that scans factory floor images for Personal Protective Equipment (PPE) compliance. The backend is a FastAPI service that wraps a YOLOv8 inference engine; the frontend is a Next.js 14 App Router application with a drag-and-drop upload interface, real-time bounding-box overlay drawn on an HTML canvas, per-detection confidence bars, and a summary dashboard. The system ships fully functional in **mock mode** — it returns realistic simulated detections so the entire UI can be developed and tested before the trained model exists. Swapping in the real model requires dropping one `.pt` file into a folder and setting one environment variable.

---

## Repository structure

```
de-heus-ppe-monitor/
├── backend/
│   ├── app/
│   │   ├── main.py                  FastAPI entry point, CORS, router mount
│   │   ├── routers/
│   │   │   └── detection.py         POST /predict — accepts multipart image
│   │   ├── services/
│   │   │   └── ppe_detector.py      PPEDetector class (real + mock inference)
│   │   ├── models/
│   │   │   └── schemas.py           Pydantic request/response schemas
│   │   └── core/
│   │       └── config.py            pydantic-settings: MODEL_PATH, thresholds
│   ├── weights/
│   │   └── .gitkeep                 Drop your .pt file here
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── frontend/
│   ├── app/
│   │   ├── page.tsx                 Main page — upload → inference → results
│   │   ├── layout.tsx               IBM Plex fonts, metadata
│   │   └── globals.css              Tailwind base + scrollbar styles
│   ├── components/
│   │   ├── UploadZone.tsx           Drag-and-drop + click file selector
│   │   ├── BoundingBoxCanvas.tsx    Canvas overlay: corner-accent boxes + labels
│   │   ├── ResultsPanel.tsx         Summary stats + scrollable detection list
│   │   └── StatusBadge.tsx          COMPLIANT / VIOLATION DETECTED pill
│   ├── lib/
│   │   └── api.ts                   analyzeImage() — typed fetch to /predict
│   ├── types/
│   │   └── detection.ts             TypeScript interfaces (mirrors Pydantic)
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── tsconfig.json
│   ├── package.json
│   └── .env.local.example
├── .gitignore
└── README.md
```

---

## Backend setup

```bash
cd backend

# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and configure environment
cp .env.example .env

# 3. Start the server
uvicorn app.main:app --reload --port 8000
```

The API will be live at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

---

## Frontend setup

```bash
cd frontend

# 1. Install dependencies
npm install

# 2. Copy and configure environment
cp .env.local.example .env.local

# 3. Start the dev server
npm run dev
```

The UI will be live at `http://localhost:3000`.

---

## Adding the trained model (for the ML engineer)

This is the **only** change needed to go from mock detections to real inference.

### Step 1 — Export your trained weights

After training, export the best checkpoint:

```bash
# YOLOv8 training produces runs/detect/train/weights/best.pt
# No conversion needed — just use best.pt directly
```

### Step 2 — Place the file

Copy `best.pt` (or any `.pt` filename you choose) into:

```
backend/weights/best.pt
```

The `weights/` directory already exists; `.gitkeep` is just a placeholder.  
The `.gitignore` excludes `*.pt` so the binary is never committed.

### Step 3 — Set the environment variable

Edit `backend/.env`:

```env
MODEL_PATH=weights/best.pt
```

If you named your file differently (e.g. `ppe_v2_epoch100.pt`), update this path accordingly.

### Step 4 — Update the class map

Open `backend/app/services/ppe_detector.py` and find `PPE_CLASSES`:

```python
PPE_CLASSES: dict[str, str] = {
    "hard_hat": "compliant",
    "safety_vest": "compliant",
    ...
}
```

The **keys must exactly match your YOLOv8 class names** as they appear in your `data.yaml`.  
Values must be `"compliant"` or `"violation"`.

### Step 5 — Restart and verify

```bash
# Restart the backend
uvicorn app.main:app --reload --port 8000
```

On startup you should see:

```
✓ YOLOv8 model loaded from 'weights/best.pt'
```

If you still see the mock warning, check that `MODEL_PATH` matches the actual file path relative to the `backend/` directory.

### Step 6 — Test a frame

```bash
curl -X POST http://localhost:8000/predict \
  -F "file=@/path/to/test_frame.jpg" | python -m json.tool
```

The response schema is identical to mock mode, so the frontend works with zero changes.

---

## API contract

`POST /predict`  
Content-Type: `multipart/form-data`  
Field: `file` (JPEG / PNG / WEBP / BMP)

**Response — `200 OK`**

```json
{
  "detections": [
    {
      "id": 0,
      "label": "Hard Hat",
      "category": "compliant",
      "confidence": 0.9412,
      "bbox": {
        "x1": 87.4,
        "y1": 20.5,
        "x2": 201.6,
        "y2": 112.8
      },
      "color": "#22c55e"
    },
    {
      "id": 1,
      "label": "No Safety Vest",
      "category": "violation",
      "confidence": 0.8112,
      "bbox": {
        "x1": 374.4,
        "y1": 144.0,
        "x2": 561.6,
        "y2": 504.0
      },
      "color": "#ef4444"
    }
  ],
  "summary": {
    "total_persons": 2,
    "compliant": 1,
    "violations": 1,
    "inference_ms": 63.4
  }
}
```

**Notes:**
- `bbox` coordinates are **absolute pixels** relative to the uploaded image dimensions.
- `color` is a hex string: `#22c55e` (compliant / green) or `#ef4444` (violation / red).
- `confidence` is a float in `[0, 1]`.
- `inference_ms` includes PIL decode + model forward pass.
- Errors return standard FastAPI JSON: `{ "detail": "..." }`.

**Health check**

```
GET /health  →  { "status": "ok" }
```

---

## Docker (optional)

```bash
cd backend
docker build -t de-heus-ppe-backend .
docker run -p 8000:8000 \
  -v "$(pwd)/weights:/app/weights" \
  -e MODEL_PATH=weights/best.pt \
  de-heus-ppe-backend
```

Mount the `weights/` volume so you can swap the model without rebuilding the image.
