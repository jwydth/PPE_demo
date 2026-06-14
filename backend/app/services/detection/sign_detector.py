import logging
from dataclasses import dataclass

from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.services.zone_service import COORD_SCALE, ZoneViolationRecord

logger = logging.getLogger(__name__)

SIGN_CLASS_MAP = {
    2: "P004_NoThoroughfare",
    3: "W011_Slippery",
}

SIGN_ZONE_TYPE_MAP = {
    2: "RESTRICTED",
    3: "RESTRICTED",
}

_sign_model = None


def load_sign_model() -> None:
    global _sign_model
    from pathlib import Path

    model_path = Path(settings.SIGN_MODEL_PATH).expanduser()
    if not model_path.is_absolute():
        model_path = BACKEND_DIR / model_path
    model_path = model_path.resolve()

    if not model_path.exists():
        logger.warning("Sign model not found at %s — sign detection disabled", model_path)
        return

    try:
        from ultralytics import YOLO
        _sign_model = YOLO(str(model_path))
    except Exception:
        logger.warning("Failed to load sign model from %s", model_path, exc_info=True)


@dataclass
class SignDetection:
    label: str
    class_id: int
    confidence: float
    bbox: dict  # {x1, y1, x2, y2} in pixel coords


def detect_signs(image: Image.Image) -> list[SignDetection]:
    if _sign_model is None:
        return []

    results = _sign_model(
        image,
        conf=settings.SIGN_CONFIDENCE_THRESHOLD,
        verbose=False,
    )

    detections: list[SignDetection] = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            cls_id = int(box.cls[0])
            if cls_id not in SIGN_CLASS_MAP:
                continue
            conf = float(box.conf[0])
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            detections.append(
                SignDetection(
                    label=SIGN_CLASS_MAP[cls_id],
                    class_id=cls_id,
                    confidence=conf,
                    bbox={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                )
            )
    return detections


def signs_to_zone_records(
    detections: list[SignDetection],
    frame_width: int,
    frame_height: int,
) -> list[ZoneViolationRecord]:
    ratio = settings.SIGN_ZONE_EXPAND_RATIO
    records: list[ZoneViolationRecord] = []

    for det in detections:
        b = det.bbox
        w = b["x2"] - b["x1"]
        h = b["y2"] - b["y1"]
        pad_x = w * (ratio - 1) / 2
        pad_y = h * (ratio - 1) / 2

        ex1 = max(0.0, b["x1"] - pad_x)
        ey1 = max(0.0, b["y1"] - pad_y)
        ex2 = min(float(frame_width), b["x2"] + pad_x)
        ey2 = min(float(frame_height), b["y2"] + pad_y)

        def _norm_x(x: float) -> float:
            return (x / frame_width) * COORD_SCALE

        def _norm_y(y: float) -> float:
            return (y / frame_height) * COORD_SCALE

        poly = [
            (_norm_x(ex1), _norm_y(ey1)),
            (_norm_x(ex2), _norm_y(ey1)),
            (_norm_x(ex2), _norm_y(ey2)),
            (_norm_x(ex1), _norm_y(ey2)),
        ]

        records.append(
            ZoneViolationRecord(
                camera_zone_view_id=-1,
                physical_zone_id=-1,
                zone_name=det.label,
                zone_type=SIGN_ZONE_TYPE_MAP[det.class_id],
                poly=poly,
                threshold=0.0,
            )
        )

    return records
