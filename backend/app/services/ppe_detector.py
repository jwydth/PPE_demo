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
2. Restart the server — _load_model() auto-detects the file on startup.

Model class IDs expected:
  0 → person
  1 → helmet
  2 → vest

Compliance logic:
  For each detected person, the system checks whether a helmet and vest
  bounding box overlaps with the person box by at least PPE_OVERLAP_THRESHOLD
  (fraction of the equipment box area). If an item is missing the person is
  flagged with a violation for that item.
"""

import logging
import time
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.models.schemas import (
    BoundingBox,
    Detection,
    DetectionResponse,
    EquipmentStatus,
    PersonResult,
    Summary,
)

logger = logging.getLogger(__name__)

COMPLIANT_COLOR = "#22c55e"
VIOLATION_COLOR = "#ef4444"
PERSON_COLOR    = "#f97316"   # orange — used for person bbox on canvas


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _area(b: dict) -> float:
    return max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])


def _inter_area(a: dict, b: dict) -> float:
    xA = max(a["x1"], b["x1"])
    yA = max(a["y1"], b["y1"])
    xB = min(a["x2"], b["x2"])
    yB = min(a["y2"], b["y2"])
    return max(0.0, xB - xA) * max(0.0, yB - yA)


def _overlap_ratio(equipment: dict, person: dict) -> float:
    """Fraction of the *equipment* box that lies inside the person box."""
    eq_area = _area(equipment)
    if eq_area == 0:
        return 0.0
    return _inter_area(equipment, person) / eq_area


# ── Main class ────────────────────────────────────────────────────────────────

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

        persons: list[dict] = []
        helmets: list[dict] = []
        vests:   list[dict] = []

        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf   = float(box.conf[0])
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                entry = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": conf}

                if cls_id == 0:
                    persons.append(entry)
                elif cls_id == 1:
                    helmets.append(entry)
                elif cls_id == 2:
                    vests.append(entry)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)

    # ── Mock inference ───────────────────────────────────────────────────────

    def _mock_predict(self, image: Image.Image) -> DetectionResponse:
        """
        Returns hard-coded but realistic detections whose schema is identical to
        what _real_predict() produces.  The frontend works unchanged in both modes.
        """
        start = time.perf_counter()
        time.sleep(0.06)  # simulate realistic inference latency

        w, h = image.size

        # All coordinates expressed as fractions of image dimensions
        def px(rel_box: tuple) -> dict:
            rx1, ry1, rx2, ry2 = rel_box
            return {"x1": rx1 * w, "y1": ry1 * h, "x2": rx2 * w, "y2": ry2 * h}

        persons = [
            {**px((0.05, 0.02, 0.40, 0.98)), "conf": 0.96},   # worker 1 — compliant
            {**px((0.55, 0.04, 0.95, 0.96)), "conf": 0.91},   # worker 2 — missing vest
        ]
        helmets = [
            {**px((0.10, 0.03, 0.35, 0.20)), "conf": 0.94},   # matches person 0
        ]
        vests = [
            {**px((0.08, 0.22, 0.38, 0.68)), "conf": 0.89},   # matches person 0
        ]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)


# ── Response builder ──────────────────────────────────────────────────────────

def _build_response(
    persons: list[dict],
    helmets: list[dict],
    vests:   list[dict],
    elapsed_ms: float,
) -> DetectionResponse:
    """
    Matches equipment to persons using the configurable overlap threshold,
    infers absences, then builds both the flat Detection list (for the canvas)
    and the grouped PersonResult list (for the results panel).
    """
    threshold = settings.PPE_OVERLAP_THRESHOLD

    # ── Match equipment → person ──────────────────────────────────────────────
    # Each equipment box is assigned to the person it overlaps with most,
    # provided that overlap is >= threshold.

    def best_match(equip_list: list[dict]) -> list[int | None]:
        """Returns list of person indices (or None) for each equipment item."""
        assignments: list[int | None] = []
        for eq in equip_list:
            best_idx: int | None = None
            best_ratio: float = threshold  # must beat the threshold
            for i, p in enumerate(persons):
                ratio = _overlap_ratio(eq, p)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = i
            assignments.append(best_idx)
        return assignments

    helmet_assignments = best_match(helmets)
    vest_assignments   = best_match(vests)

    # ── Build per-person equipment map ────────────────────────────────────────
    # Keep only the highest-confidence match per person per equipment type.

    person_helmets: dict[int, dict] = {}
    for eq, p_idx in zip(helmets, helmet_assignments):
        if p_idx is None:
            continue
        if p_idx not in person_helmets or person_helmets[p_idx]["conf"] < eq["conf"]:
            person_helmets[p_idx] = eq

    person_vests: dict[int, dict] = {}
    for eq, p_idx in zip(vests, vest_assignments):
        if p_idx is None:
            continue
        if p_idx not in person_vests or person_vests[p_idx]["conf"] < eq["conf"]:
            person_vests[p_idx] = eq

    # ── Build PersonResult list ───────────────────────────────────────────────
    person_results: list[PersonResult] = []
    det_id = 0
    flat_detections: list[Detection] = []

    for i, p in enumerate(persons):
        p_bbox = BoundingBox(x1=p["x1"], y1=p["y1"], x2=p["x2"], y2=p["y2"])

        # Person box on canvas (orange)
        flat_detections.append(Detection(
            id=det_id, label=f"Person {i + 1}",
            category="compliant",   # person box itself is always neutral/compliant color
            confidence=round(p["conf"], 4),
            bbox=p_bbox,
            color=PERSON_COLOR,
        ))
        det_id += 1

        equipment_statuses: list[EquipmentStatus] = []

        # ── Helmet ────────────────────────────────────────────────────────────
        h = person_helmets.get(i)
        if h is not None:
            h_bbox = BoundingBox(x1=h["x1"], y1=h["y1"], x2=h["x2"], y2=h["y2"])
            flat_detections.append(Detection(
                id=det_id, label="Helmet", category="compliant",
                confidence=round(h["conf"], 4), bbox=h_bbox, color=COMPLIANT_COLOR,
            ))
            det_id += 1
            equipment_statuses.append(EquipmentStatus(
                label="Helmet", status="compliant",
                confidence=round(h["conf"], 4), bbox=h_bbox,
            ))
        else:
            equipment_statuses.append(EquipmentStatus(
                label="Helmet", status="violation",
            ))

        # ── Vest ──────────────────────────────────────────────────────────────
        v = person_vests.get(i)
        if v is not None:
            v_bbox = BoundingBox(x1=v["x1"], y1=v["y1"], x2=v["x2"], y2=v["y2"])
            flat_detections.append(Detection(
                id=det_id, label="Vest", category="compliant",
                confidence=round(v["conf"], 4), bbox=v_bbox, color=COMPLIANT_COLOR,
            ))
            det_id += 1
            equipment_statuses.append(EquipmentStatus(
                label="Vest", status="compliant",
                confidence=round(v["conf"], 4), bbox=v_bbox,
            ))
        else:
            equipment_statuses.append(EquipmentStatus(
                label="Vest", status="violation",
            ))

        is_compliant = all(eq.status == "compliant" for eq in equipment_statuses)
        person_results.append(PersonResult(
            person_id=i + 1,
            bbox=p_bbox,
            confidence=round(p["conf"], 4),
            equipment=equipment_statuses,
            compliant=is_compliant,
        ))

    # ── Unmatched equipment boxes (no associated person) ──────────────────────
    matched_helmets = {id(h) for h, p_idx in zip(helmets, helmet_assignments) if p_idx is not None}
    for h in helmets:
        if id(h) not in matched_helmets:
            flat_detections.append(Detection(
                id=det_id, label="Helmet (unassigned)", category="compliant",
                confidence=round(h["conf"], 4),
                bbox=BoundingBox(x1=h["x1"], y1=h["y1"], x2=h["x2"], y2=h["y2"]),
                color=COMPLIANT_COLOR,
            ))
            det_id += 1

    matched_vests = {id(v) for v, p_idx in zip(vests, vest_assignments) if p_idx is not None}
    for v in vests:
        if id(v) not in matched_vests:
            flat_detections.append(Detection(
                id=det_id, label="Vest (unassigned)", category="compliant",
                confidence=round(v["conf"], 4),
                bbox=BoundingBox(x1=v["x1"], y1=v["y1"], x2=v["x2"], y2=v["y2"]),
                color=COMPLIANT_COLOR,
            ))
            det_id += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    compliant_count  = sum(1 for pr in person_results if pr.compliant)
    violation_count  = len(person_results) - compliant_count

    return DetectionResponse(
        detections=flat_detections,
        persons=person_results,
        summary=Summary(
            total_persons=len(person_results),
            compliant=compliant_count,
            violations=violation_count,
            inference_ms=round(elapsed_ms, 2),
        ),
    )
