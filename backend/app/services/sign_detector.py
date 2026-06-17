import time
from pathlib import Path

from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import BoundingBox, Detection, DetectionResponse, Summary
from app.services.ppe_detector import _select_inference_device

SIGN_LABELS: dict[int, str] = {
    0: "M014_Helmet",
    1: "M015_Vest",
    2: "P004_NoThoroughfare",
    3: "W011_Slippery",
}

SIGN_COLORS: dict[int, str] = {
    0: "#22c55e",
    1: "#3b82f6",
    2: "#ef4444",
    3: "#f59e0b",
}


class SignDetector:
    def __init__(self) -> None:
        self.model = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self._load_model()

    def _load_model(self) -> None:
        model_path = Path(settings.SIGN_MODEL_PATH).expanduser()
        if not model_path.is_absolute():
            model_path = BACKEND_DIR / model_path
        model_path = model_path.resolve()

        if not model_path.exists():
            return

        try:
            from ultralytics import YOLO

            self.model = YOLO(str(model_path))
        except Exception:
            self.model = None

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return DetectionResponse(
                detections=[],
                persons=[],
                summary=Summary(
                    total_persons=0,
                    compliant=0,
                    violations=0,
                    inference_ms=0.0,
                ),
            )

        start = time.perf_counter()
        results = self.model(
            image,
            conf=settings.CONFIDENCE_THRESHOLD,
            device=self.device,
            verbose=False,
        )

        detections: list[Detection] = []
        det_id = 0
        for result in results:
            if result.boxes is None:
                continue

            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                detections.append(
                    Detection(
                        id=det_id,
                        label=SIGN_LABELS.get(cls_id, f"Sign_{cls_id}"),
                        category="compliant",
                        confidence=round(conf, 4),
                        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                        color=SIGN_COLORS.get(cls_id, "#84cc16"),
                    )
                )
                det_id += 1

        elapsed_ms = (time.perf_counter() - start) * 1000
        return DetectionResponse(
            detections=detections,
            persons=[],
            summary=Summary(
                total_persons=len(detections),
                compliant=len(detections),
                violations=0,
                inference_ms=elapsed_ms,
            ),
        )
