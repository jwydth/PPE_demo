from __future__ import annotations

from enum import Enum, auto

from app.core.config import settings
from app.schemas.zone import PPESuggestion, ZoneSuggestion


_ALL_SIGN_CLASSES = None

def _all_sign_classes() -> set[int]:
    global _ALL_SIGN_CLASSES
    if _ALL_SIGN_CLASSES is None:
        _ALL_SIGN_CLASSES = {*settings.SIGN_CLASS_ZONE_MAP, *settings.SIGN_CLASS_PPE_TRIGGER}
    return _ALL_SIGN_CLASSES


def extract_signs(result) -> list[dict]:
    """Return sign detections from a YOLO result that pass class filter and confidence."""
    signs: list[dict] = []
    if result.boxes is None:
        return signs
    for box in result.boxes:
        class_id = int(box.cls[0])
        if class_id not in _all_sign_classes():
            continue
        conf = float(box.conf[0])
        if conf < settings.SIGN_CONFIDENCE_THRESHOLD:
            continue
        x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
        signs.append({"bbox": (x1, y1, x2, y2), "conf": conf, "class_id": class_id})
    return signs


def derive_zone_polygon(
    bbox: tuple[float, float, float, float],
    frame_w: int,
    frame_h: int,
    buffer_ratio: float,
) -> list[dict]:
    """Create a restricted zone on the floor below the detected sign.

    - Horizontally: centred on the sign, extending buffer_ratio × frame_w each side.
    - Vertically: starts at the sign's bottom edge and extends buffer_ratio × 2 × frame_h downward.
    """
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2

    half_w = max((x2 - x1) / 2, frame_w * buffer_ratio)
    zone_h = frame_h * buffer_ratio * 2

    ex1 = max(0.0, cx - half_w) / frame_w
    ex2 = min(float(frame_w), cx + half_w) / frame_w
    ey1 = y2 / frame_h                                    # top = bottom of sign
    ey2 = min(1.0, (y2 + zone_h) / frame_h)              # bottom = floor below sign
    return [
        {"x": ex1, "y": ey1},  # TL
        {"x": ex2, "y": ey1},  # TR
        {"x": ex2, "y": ey2},  # BR
        {"x": ex1, "y": ey2},  # BL
    ]


def signature(class_id: int, bbox: tuple[float, float, float, float], frame_w: int, frame_h: int) -> str:
    """Stable dedupe key: quantize bbox center by AUTO_ZONE_DEDUPE_GRID."""
    cx = ((bbox[0] + bbox[2]) / 2) / frame_w
    cy = ((bbox[1] + bbox[3]) / 2) / frame_h
    grid = settings.AUTO_ZONE_DEDUPE_GRID
    gx = round(cx / grid)
    gy = round(cy / grid)
    return f"{int(class_id)}:{gx}:{gy}"


class _State(Enum):
    COUNTING = auto()
    EMITTED = auto()
    DISMISSED = auto()
    ACCEPTED = auto()


_TERMINAL = {_State.EMITTED, _State.DISMISSED, _State.ACCEPTED}


class SignZoneRegistry:
    def __init__(self) -> None:
        self._hits: dict[str, int] = {}
        self._state: dict[str, _State] = {}
        self._last_sign: dict[str, dict] = {}
        self._emitted_centers: list[tuple[float, float]] = []

    def _near_emitted(self, cx: float, cy: float) -> bool:
        """True if (cx, cy) is within 2× the dedupe grid of any already-emitted suggestion center."""
        threshold = settings.AUTO_ZONE_DEDUPE_GRID * 2
        return any(abs(cx - ex) < threshold and abs(cy - ey) < threshold for ex, ey in self._emitted_centers)

    def update(self, signs: list[dict], frame_w: int, frame_h: int, frame_index: int) -> list[ZoneSuggestion]:
        suggestions: list[ZoneSuggestion] = []
        for sign in signs:
            class_id = int(sign["class_id"])
            if class_id not in settings.SIGN_CLASS_ZONE_MAP:
                continue
            bbox = sign["bbox"]
            sig = signature(class_id, bbox, frame_w, frame_h)

            state = self._state.get(sig, _State.COUNTING)
            if state in _TERMINAL:
                continue

            self._hits[sig] = self._hits.get(sig, 0) + 1
            self._last_sign[sig] = sign

            if self._hits[sig] >= settings.AUTO_ZONE_CONFIRM_FRAMES:
                self._state[sig] = _State.EMITTED
                cx = ((bbox[0] + bbox[2]) / 2) / frame_w
                cy = ((bbox[1] + bbox[3]) / 2) / frame_h
                if self._near_emitted(cx, cy):
                    # Duplicate of an already-emitted nearby sign — suppress silently
                    continue
                self._emitted_centers.append((cx, cy))
                coords = derive_zone_polygon(bbox, frame_w, frame_h, settings.AUTO_ZONE_BUFFER_RATIO)
                suggestions.append(
                    ZoneSuggestion(
                        suggestion_id=sig,
                        zone_type=settings.SIGN_CLASS_ZONE_MAP[class_id],
                        source_class=settings.SIGN_CLASS_NAMES.get(class_id, str(class_id)),
                        confidence=sign["conf"],
                        normalized_coordinates=coords,
                        frame_index=frame_index,
                    )
                )
        return suggestions

    def dismiss(self, sig: str) -> None:
        self._state[sig] = _State.DISMISSED

    def accept(self, sig: str) -> None:
        self._state[sig] = _State.ACCEPTED


class SignPPERegistry:
    """Tracks PPE-requirement sign detections and emits PPESuggestion once stable."""

    def __init__(self) -> None:
        self._hits: dict[str, int] = {}
        self._state: dict[str, _State] = {}
        self._last_sign: dict[str, dict] = {}

    def update(self, signs: list[dict], frame_w: int, frame_h: int, frame_index: int) -> list[PPESuggestion]:
        suggestions: list[PPESuggestion] = []
        for sign in signs:
            class_id = int(sign["class_id"])
            if class_id not in settings.SIGN_CLASS_PPE_TRIGGER:
                continue
            bbox = sign["bbox"]
            sig = signature(class_id, bbox, frame_w, frame_h)

            state = self._state.get(sig, _State.COUNTING)
            if state in _TERMINAL:
                continue

            self._hits[sig] = self._hits.get(sig, 0) + 1
            self._last_sign[sig] = sign

            if self._hits[sig] >= settings.AUTO_PPE_CONFIRM_FRAMES:
                self._state[sig] = _State.EMITTED
                suggestions.append(
                    PPESuggestion(
                        suggestion_id=sig,
                        source_class=settings.SIGN_CLASS_NAMES.get(class_id, str(class_id)),
                        confidence=sign["conf"],
                        frame_index=frame_index,
                    )
                )
        return suggestions

    def dismiss(self, sig: str) -> None:
        self._state[sig] = _State.DISMISSED
