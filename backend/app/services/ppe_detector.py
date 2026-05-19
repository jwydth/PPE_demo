"""
PPE Detector service.

─────────────────────────────────────────────────────────────────────────────
MOCK MODE (current default)
─────────────────────────────────────────────────────────────────────────────
No .pt file is present, so every call goes through _mock_predict().
Mock detections are schema-identical to real model output — the frontend
never needs to change when the real model is dropped in.

PRODUCTION MODE (after ML engineer drops in the model)
─────────────────────────────────────────────────────────────────────────────
1. Place the trained weights file at the path specified by MODEL_PATH in .env
   (default: weights/best.pt inside the backend/ directory).
2. The class names in PPE_CLASSES must match your YOLOv8 training labels exactly.
   Update them here if your label names differ.
3. Restart the server — _load_model() auto-detects the file on startup.
"""

import logging
import time
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.models.schemas import BoundingBox, Detection, DetectionResponse, Summary

logger = logging.getLogger(__name__)

# ── Class map ────────────────────────────────────────────────────────────────
# Keys = YOLOv8 class names as they appear in your data.yaml / model.names
# Values = "compliant" | "violation"
# Update this dict to match your training labels exactly.
PPE_CLASSES: dict[str, str] = {
    "hard_hat": "compliant",
    "safety_vest": "compliant",
    "gloves": "compliant",
    "safety_glasses": "compliant",
    "no_hard_hat": "violation",
    "no_safety_vest": "violation",
    "no_gloves": "violation",
    "no_safety_glasses": "violation",
}

COMPLIANT_COLOR = "#22c55e"
VIOLATION_COLOR = "#ef4444"


def _category_color(category: str) -> str:
    return COMPLIANT_COLOR if category == "compliant" else VIOLATION_COLOR


class PPEDetector:
    def __init__(self) -> None:
        self.model = None
        self._load_model()

    # ── Startup ──────────────────────────────────────────────────────────────

    def _load_model(self) -> None:
        model_path = Path(settings.MODEL_PATH)

        if not model_path.exists():
            logger.warning(
                "⚠  Model weights not found at '%s'. "
                "Running in MOCK mode — predictions are simulated. "
                "Drop a trained .pt file at that path and restart to enable real inference.",
                model_path,
            )
            return

        try:
            from ultralytics import YOLO  # imported lazily so the server starts without GPU

            self.model = YOLO(str(model_path))
            logger.info("✓ YOLOv8 model loaded from '%s'", model_path)
        except Exception as exc:
            logger.error("Failed to load model from '%s': %s", model_path, exc)
            logger.warning("Falling back to MOCK mode.")

    # ── Public API ───────────────────────────────────────────────────────────

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return self._mock_predict(image)
        return self._real_predict(image)

    # ── Real inference ───────────────────────────────────────────────────────

    def _real_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()

        results = self.model(image, conf=settings.CONFIDENCE_THRESHOLD)

        detections: list[Detection] = []
        detection_id = 0

        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label_raw: str = result.names[cls_id]
                conf = float(box.conf[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())

                category = PPE_CLASSES.get(label_raw, "violation")
                label_display = label_raw.replace("_", " ").title()

                detections.append(
                    Detection(
                        id=detection_id,
                        label=label_display,
                        category=category,  # type: ignore[arg-type]
                        confidence=round(conf, 4),
                        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                        color=_category_color(category),
                    )
                )
                detection_id += 1

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(detections, elapsed_ms)

    # ── Mock inference ───────────────────────────────────────────────────────

    def _mock_predict(self, image: Image.Image) -> DetectionResponse:
        """
        Returns hard-coded but realistic detections whose schema is identical to
        what _real_predict() produces.  The frontend works unchanged in both modes.
        """
        start = time.perf_counter()
        time.sleep(0.06)  # simulate realistic inference latency

        width, height = image.size

        # Bounding boxes expressed as fractions of image dimensions
        raw: list[dict] = [
            {
                "label": "Hard Hat",
                "category": "compliant",
                "confidence": 0.9412,
                "rel": (0.12, 0.04, 0.28, 0.22),
            },
            {
                "label": "Safety Vest",
                "category": "compliant",
                "confidence": 0.8897,
                "rel": (0.10, 0.22, 0.32, 0.70),
            },
            {
                "label": "No Hard Hat",
                "category": "violation",
                "confidence": 0.8734,
                "rel": (0.55, 0.03, 0.74, 0.22),
            },
            {
                "label": "No Safety Vest",
                "category": "violation",
                "confidence": 0.8112,
                "rel": (0.52, 0.20, 0.78, 0.72),
            },
            {
                "label": "Safety Glasses",
                "category": "compliant",
                "confidence": 0.7653,
                "rel": (0.14, 0.07, 0.27, 0.18),
            },
        ]

        detections = [
            Detection(
                id=i,
                label=d["label"],
                category=d["category"],  # type: ignore[arg-type]
                confidence=d["confidence"],
                bbox=BoundingBox(
                    x1=d["rel"][0] * width,
                    y1=d["rel"][1] * height,
                    x2=d["rel"][2] * width,
                    y2=d["rel"][3] * height,
                ),
                color=_category_color(d["category"]),
            )
            for i, d in enumerate(raw)
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(detections, elapsed_ms, total_persons=2)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_response(
    detections: list[Detection],
    elapsed_ms: float,
    total_persons: int | None = None,
) -> DetectionResponse:
    compliant = sum(1 for d in detections if d.category == "compliant")
    violations = len(detections) - compliant

    if total_persons is None:
        total_persons = max(1, len(detections) // 2) if detections else 0

    return DetectionResponse(
        detections=detections,
        summary=Summary(
            total_persons=total_persons,
            compliant=compliant,
            violations=violations,
            inference_ms=round(elapsed_ms, 2),
        ),
    )
