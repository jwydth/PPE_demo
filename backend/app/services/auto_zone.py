from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _SignTrack:
    """A zone sign being watched for stationarity across sign-passes."""

    class_id: int
    anchor_cx: float        # normalized center where the current still-streak began
    anchor_cy: float
    stable_since: int       # frame_index when the still-streak started
    last_seen: int          # frame_index of the most recent detection
    bbox: tuple             # most recent bbox (used to derive the polygon on emit)
    conf: float
    emitted: bool = False
    suggestion_id: str | None = None  # set when emitted; lets dismiss/accept target this track


class SignZoneRegistry:
    """Emits a zone suggestion only after a sign has stayed still long enough.

    A sign carried across the floor keeps drifting beyond AUTO_ZONE_MOVE_TOLERANCE,
    which restarts its still-streak each time, so it never confirms. Once the sign
    is put down and left in place, the streak runs uninterrupted for
    AUTO_ZONE_STATIONARY_SECONDS and a single suggestion is emitted.
    """

    def __init__(self) -> None:
        self._tracks: list[_SignTrack] = []
        self._emitted_centers: list[tuple[float, float]] = []

    def _near_emitted(self, cx: float, cy: float) -> bool:
        """True if (cx, cy) is within 2× the dedupe grid of any already-emitted suggestion center."""
        threshold = settings.AUTO_ZONE_DEDUPE_GRID * 2
        return any(abs(cx - ex) < threshold and abs(cy - ey) < threshold for ex, ey in self._emitted_centers)

    def _match(self, class_id: int, cx: float, cy: float, tol: float) -> _SignTrack | None:
        """Find an existing track of the same class whose anchor is within tol of (cx, cy)."""
        for track in self._tracks:
            if track.class_id != class_id:
                continue
            if abs(cx - track.anchor_cx) <= tol and abs(cy - track.anchor_cy) <= tol:
                return track
        return None

    def update(
        self,
        signs: list[dict],
        frame_w: int,
        frame_h: int,
        frame_index: int,
        fps: float,
    ) -> list[ZoneSuggestion]:
        suggestions: list[ZoneSuggestion] = []
        tol = settings.AUTO_ZONE_MOVE_TOLERANCE
        required_frames = settings.AUTO_ZONE_STATIONARY_SECONDS * (fps if fps and fps > 0 else 24.0)

        for sign in signs:
            class_id = int(sign["class_id"])
            if class_id not in settings.SIGN_CLASS_ZONE_MAP:
                continue
            bbox = sign["bbox"]
            cx = ((bbox[0] + bbox[2]) / 2) / frame_w
            cy = ((bbox[1] + bbox[3]) / 2) / frame_h

            track = self._match(class_id, cx, cy, tol)
            if track is None:
                # New position — start watching it for stationarity.
                self._tracks.append(
                    _SignTrack(class_id, cx, cy, frame_index, frame_index, bbox, sign["conf"])
                )
                continue

            track.last_seen = frame_index
            track.bbox = bbox
            track.conf = sign["conf"]

            # Already emitted for this resting spot — don't re-emit.
            if track.emitted:
                continue

            # Held still long enough → emit one suggestion.
            if frame_index - track.stable_since >= required_frames:
                track.emitted = True
                if self._near_emitted(cx, cy):
                    continue  # duplicate of a nearby already-emitted sign
                self._emitted_centers.append((cx, cy))
                sig = signature(class_id, bbox, frame_w, frame_h)
                track.suggestion_id = sig
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

        # A detection that drifts beyond tol from its anchor won't match its old
        # track, so a fresh (un-emitted) track is started at the new position —
        # i.e. moving the sign restarts the still-streak. Prune tracks that
        # haven't been seen for a while so a returning sign starts clean.
        stale_cutoff = frame_index - required_frames * 2
        self._tracks = [t for t in self._tracks if t.last_seen >= stale_cutoff]
        return suggestions

    def dismiss(self, sig: str) -> None:
        # Already-emitted tracks never re-emit; this just makes the intent explicit
        # for the specific suggestion the user dismissed.
        for track in self._tracks:
            if track.suggestion_id == sig:
                track.emitted = True

    def accept(self, sig: str) -> None:
        for track in self._tracks:
            if track.suggestion_id == sig:
                track.emitted = True


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
                x1, y1, x2, y2 = bbox
                norm_bbox = (x1 / frame_w, y1 / frame_h, x2 / frame_w, y2 / frame_h)
                suggestions.append(
                    PPESuggestion(
                        suggestion_id=sig,
                        source_class=settings.SIGN_CLASS_NAMES.get(class_id, str(class_id)),
                        confidence=sign["conf"],
                        frame_index=frame_index,
                        bbox=norm_bbox,
                    )
                )
        return suggestions

    def dismiss(self, sig: str) -> None:
        self._state[sig] = _State.DISMISSED
