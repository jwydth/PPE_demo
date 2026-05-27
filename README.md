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
│   │   ├── .gitkeep
│   │   └── ppe_v1.pt                YOLOv8 trained weights
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

# 1. Create and activate a virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and configure environment
cp .env.example .env

# 4. Start the server
uvicorn app.main:app --reload --port 8000
```

You can also start the backend from the repository root without import-path issues:

```bash
uvicorn app.main:app --app-dir backend --reload --port 8000
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
      "label": "Person 1",
      "category": "compliant",
      "confidence": 0.96,
      "bbox": {
        "x1": 36.0,
        "y1": 14.4,
        "x2": 288.0,
        "y2": 705.6
      },
      "color": "#f97316"
    }
  ],
  "persons": [
    {
      "person_id": 1,
      "bbox": {
        "x1": 36.0,
        "y1": 14.4,
        "x2": 288.0,
        "y2": 705.6
      },
      "confidence": 0.96,
      "equipment": [
        {
          "label": "Helmet",
          "status": "compliant",
          "confidence": 0.94,
          "bbox": {
            "x1": 72.0,
            "y1": 21.6,
            "x2": 252.0,
            "y2": 144.0
          }
        }
      ],
      "compliant": true
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
  -e MODEL_PATH=weights/ppe_v1.pt \
  de-heus-ppe-backend
```

Mount the `weights/` volume so you can swap the model later (e.g. `ppe_v2.pt`) without rebuilding the image.
