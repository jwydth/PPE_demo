from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Sequence

from app.core.config import settings
from app.schemas.zone import PPESuggestion, ZoneSuggestion
from app.services.spatial import is_point_in_polygon
from app.services.zone_service import COORD_SCALE


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


# Fraction of a suggested zone that must already sit inside a saved zone of the
# same type before the suggestion is suppressed. A majority test rather than
# containment: derive_zone_polygon() lays down a rectangle on the floor below
# the sign, and a zone the user drew or dragged around that same sign covers
# most of it but rarely all of it.
AUTO_ZONE_COVERAGE_THRESHOLD = 0.5
_COVERAGE_GRID = 5  # sample points per axis across the suggested rectangle


def is_already_zoned(
    coords: list[dict],
    zone_type: str,
    existing_zones: Sequence[Any],
) -> bool:
    """True when a saved zone of the same type already covers this suggestion.

    The registry's own record of what it has emitted lives in memory, dies with
    the connection, and is never told when the user accepts or deletes a zone.
    On its own it gets this wrong in both directions: it re-raises suggestions
    for zones that already exist (a looped video brings the sign back around
    after the emitted track has been pruned), and it stays silent about a sign
    whose zone was deleted. Deciding from the saved zones instead makes both
    accept and delete take effect on the next sign frame, and survives a
    reconnect because it is derived from persisted state rather than remembered.

    `existing_zones` are ZoneViolationRecords, whose polygons are in COORD_SCALE
    units; `coords` is normalized 0-1.
    """
    same_type = [
        zone
        for zone in existing_zones
        if str(getattr(zone, "zone_type", "")).upper() == zone_type.upper()
    ]
    if not same_type:
        return False

    xs = [point["x"] for point in coords]
    ys = [point["y"] for point in coords]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    if x2 <= x1 or y2 <= y1:
        return False

    inside = 0
    for row in range(_COVERAGE_GRID):
        for col in range(_COVERAGE_GRID):
            # Cell centres, so the rectangle's edges — the part least likely to
            # be covered by a hand-drawn zone — don't dominate the result.
            sx = (x1 + (x2 - x1) * (col + 0.5) / _COVERAGE_GRID) * COORD_SCALE
            sy = (y1 + (y2 - y1) * (row + 0.5) / _COVERAGE_GRID) * COORD_SCALE
            if any(is_point_in_polygon((sx, sy), zone.poly) for zone in same_type):
                inside += 1
    return inside / (_COVERAGE_GRID * _COVERAGE_GRID) >= AUTO_ZONE_COVERAGE_THRESHOLD


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
    # A suggestion for this resting spot is currently outstanding on the client.
    # Cleared again once a saved zone covers the spot, so that deleting that
    # zone re-offers the suggestion — see update().
    emitted: bool = False
    # The user said no. Unlike `emitted` this is final for the connection: a
    # dismissal is an answer, not a pending question.
    dismissed: bool = False
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

    def _forget_emitted(self, cx: float, cy: float) -> None:
        """Drop recorded centers near (cx, cy).

        Called when a saved zone takes over responsibility for this spot. The
        recorded center must not outlive the zone: if it did, deleting the zone
        would leave `_near_emitted` still suppressing the sign for the rest of
        the connection.
        """
        threshold = settings.AUTO_ZONE_DEDUPE_GRID * 2
        self._emitted_centers = [
            (ex, ey)
            for ex, ey in self._emitted_centers
            if not (abs(cx - ex) < threshold and abs(cy - ey) < threshold)
        ]

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
        existing_zones: Sequence[Any] = (),
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

            # The user answered no for this resting spot; never ask again.
            if track.dismissed:
                continue

            # Not held still long enough yet.
            if frame_index - track.stable_since < required_frames:
                continue

            zone_type = settings.SIGN_CLASS_ZONE_MAP[class_id]
            coords = derive_zone_polygon(bbox, frame_w, frame_h, settings.AUTO_ZONE_BUFFER_RATIO)

            # A saved zone of the same type already covers this sign, so there is
            # nothing to suggest. Clearing `emitted` rather than latching it is
            # what makes a later *deletion* of that zone bring the suggestion
            # straight back, instead of the sign staying silently suppressed for
            # the rest of the connection.
            if is_already_zoned(coords, zone_type, existing_zones):
                track.emitted = False
                self._forget_emitted(cx, cy)
                continue

            # A suggestion for this spot — or one close enough to it — is already
            # outstanding on the client.
            if track.emitted or self._near_emitted(cx, cy):
                track.emitted = True
                continue

            track.emitted = True
            self._emitted_centers.append((cx, cy))
            sig = signature(class_id, bbox, frame_w, frame_h)
            track.suggestion_id = sig
            suggestions.append(
                ZoneSuggestion(
                    suggestion_id=sig,
                    zone_type=zone_type,
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
        #
        # Tracks that already carry an answer are kept regardless of how long the
        # sign has been out of frame. On a looped video the sign leaves for far
        # longer than this cutoff, and dropping its track meant the loop brought
        # it back, a fresh track formed, and the same suggestion was raised all
        # over again a few seconds later.
        stale_cutoff = frame_index - required_frames * 2
        self._tracks = [
            t
            for t in self._tracks
            if t.last_seen >= stale_cutoff or t.emitted or t.dismissed
        ]
        return suggestions

    def dismiss(self, sig: str) -> None:
        for track in self._tracks:
            if track.suggestion_id == sig:
                track.dismissed = True
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
