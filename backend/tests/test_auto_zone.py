"""Tests for auto_zone: derive_zone_polygon, signature, SignZoneRegistry, and pipeline integration."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import settings
from app.schemas.detection import BoundingBox, EquipmentStatus, PersonResult, Summary
from app.services import ppe_detector as ppe
from app.services import video_pipeline
from app.services.auto_zone import SignZoneRegistry, derive_zone_polygon, signature
from app.services.zone_service import (
    COORD_SCALE,
    ZoneViolationRecord,
    check_zone_incursion,
    get_person_foot_point,
)


# ── derive_zone_polygon ────────────────────────────────────────────────────────


class TestDeriveZonePolygon:
    def test_buffer_expands_bbox(self):
        # 40×40 box at (80,80)–(120,120) in a 400×300 frame, buffer=0.2
        # pad_x = 40*0.2 = 8  →  ex1 = 72/400 = 0.18,  ex2 = 128/400 = 0.32
        # pad_y = 40*0.2 = 8  →  ey1 = 72/300,          ey2 = 128/300
        poly = derive_zone_polygon((80.0, 80.0, 120.0, 120.0), 400, 300, 0.2)
        assert poly[0] == pytest.approx({"x": 72 / 400, "y": 72 / 300})
        assert poly[1] == pytest.approx({"x": 128 / 400, "y": 72 / 300})
        assert poly[2] == pytest.approx({"x": 128 / 400, "y": 128 / 300})
        assert poly[3] == pytest.approx({"x": 72 / 400, "y": 128 / 300})

    def test_all_coords_in_unit_range(self):
        # Oversized buffer – all corners must stay in [0, 1]
        poly = derive_zone_polygon((10.0, 10.0, 90.0, 90.0), 100, 100, 5.0)
        for pt in poly:
            assert 0.0 <= pt["x"] <= 1.0
            assert 0.0 <= pt["y"] <= 1.0

    def test_clamp_at_top_left_boundary(self):
        # Box touching top-left corner; buffer would produce negative pixel coords
        poly = derive_zone_polygon((0.0, 0.0, 50.0, 50.0), 100, 100, 0.5)
        assert poly[0]["x"] == pytest.approx(0.0)
        assert poly[0]["y"] == pytest.approx(0.0)

    def test_clamp_at_bottom_right_boundary(self):
        # Box touching bottom-right corner
        poly = derive_zone_polygon((60.0, 60.0, 100.0, 100.0), 100, 100, 0.5)
        assert poly[2]["x"] == pytest.approx(1.0)
        assert poly[2]["y"] == pytest.approx(1.0)

    def test_tl_tr_br_bl_ordering(self):
        poly = derive_zone_polygon((40.0, 30.0, 80.0, 70.0), 200, 200, 0.0)
        tl, tr, br, bl = poly
        assert tl["x"] < tr["x"]   # left < right (top edge)
        assert bl["x"] < br["x"]   # left < right (bottom edge)
        assert tl["y"] < bl["y"]   # top < bottom (left edge)
        assert tr["y"] < br["y"]   # top < bottom (right edge)

    def test_zero_buffer_equals_normalized_bbox(self):
        poly = derive_zone_polygon((20.0, 30.0, 80.0, 70.0), 100, 100, 0.0)
        assert poly[0] == pytest.approx({"x": 0.20, "y": 0.30})
        assert poly[2] == pytest.approx({"x": 0.80, "y": 0.70})

    def test_returns_exactly_four_points(self):
        poly = derive_zone_polygon((10.0, 10.0, 50.0, 50.0), 100, 100, 0.1)
        assert len(poly) == 4


# ── signature ─────────────────────────────────────────────────────────────────


class TestSignature:
    def test_known_format(self):
        # center (500, 700) in 1000×1000 → cx=0.5, cy=0.7
        # grid=0.05 → gx=round(10.0)=10, gy=round(14.0)=14
        assert signature(2, (400.0, 600.0, 600.0, 800.0), 1000, 1000) == "2:10:14"

    def test_stable_under_small_jitter(self):
        # Two detections whose centers both fall inside the same 5%-wide grid cell
        bbox1 = (490.0, 490.0, 510.0, 510.0)  # center exactly (500, 500)
        bbox2 = (492.0, 493.0, 512.0, 513.0)  # jittered by 2 px
        assert signature(2, bbox1, 1000, 1000) == signature(2, bbox2, 1000, 1000)

    def test_different_positions_produce_different_keys(self):
        assert (
            signature(2, (100.0, 100.0, 200.0, 200.0), 1000, 1000)
            != signature(2, (700.0, 700.0, 800.0, 800.0), 1000, 1000)
        )

    def test_different_class_produces_different_key(self):
        bbox = (100.0, 100.0, 200.0, 200.0)
        assert signature(2, bbox, 1000, 1000) != signature(3, bbox, 1000, 1000)


# ── SignZoneRegistry ───────────────────────────────────────────────────────────


def _sign(
    class_id: int = 2,
    bbox: tuple[float, float, float, float] = (200.0, 200.0, 400.0, 400.0),
    conf: float = 0.9,
) -> dict:
    return {"class_id": class_id, "bbox": bbox, "conf": conf}


class TestSignZoneRegistry:
    # update() now needs fps to convert AUTO_ZONE_STATIONARY_SECONDS into a frame
    # count: required_frames = AUTO_ZONE_STATIONARY_SECONDS * fps.
    FPS = 10.0

    def test_no_emit_before_stationary_time(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 2.0)  # 20 frames @ 10fps
        reg = SignZoneRegistry()
        sign = _sign()
        assert reg.update([sign], 1000, 1000, 0, self.FPS) == []
        assert reg.update([sign], 1000, 1000, 10, self.FPS) == []  # 10 < 20

    def test_emits_after_stationary_time(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 2.0)  # 20 frames
        reg = SignZoneRegistry()
        sign = _sign()
        reg.update([sign], 1000, 1000, 0, self.FPS)
        reg.update([sign], 1000, 1000, 10, self.FPS)
        result = reg.update([sign], 1000, 1000, 20, self.FPS)  # 20 >= 20
        assert len(result) == 1
        s = result[0]
        assert s.zone_type == "RESTRICTED"
        assert s.source_class == "P004_NoThoroughfare"
        assert 0.0 < s.confidence <= 1.0
        assert len(s.normalized_coordinates) == 4
        assert s.frame_index == 20

    def test_carried_sign_never_emits(self, monkeypatch):
        """A sign that moves each pass keeps restarting its still-streak → no zone."""
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)  # 10 frames
        reg = SignZoneRegistry()
        # Each pass the sign moves far beyond AUTO_ZONE_MOVE_TOLERANCE (0.03 → 30px).
        for i, x in enumerate([100.0, 250.0, 400.0, 550.0, 700.0]):
            bbox = (x, 100.0, x + 80.0, 200.0)
            assert reg.update([_sign(bbox=bbox)], 1000, 1000, i * 10, self.FPS) == []

    def test_emits_only_once(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)  # 10 frames
        reg = SignZoneRegistry()
        sign = _sign()
        reg.update([sign], 1000, 1000, 0, self.FPS)
        first = reg.update([sign], 1000, 1000, 10, self.FPS)
        assert len(first) == 1
        assert reg.update([sign], 1000, 1000, 20, self.FPS) == []
        assert reg.update([sign], 1000, 1000, 30, self.FPS) == []

    def test_dismiss_suppresses_reemit(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)
        reg = SignZoneRegistry()
        sign = _sign()
        reg.update([sign], 1000, 1000, 0, self.FPS)
        [suggestion] = reg.update([sign], 1000, 1000, 10, self.FPS)
        reg.dismiss(suggestion.suggestion_id)
        assert reg.update([sign], 1000, 1000, 20, self.FPS) == []

    def test_accept_suppresses_reemit(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)
        reg = SignZoneRegistry()
        sign = _sign()
        reg.update([sign], 1000, 1000, 0, self.FPS)
        [suggestion] = reg.update([sign], 1000, 1000, 10, self.FPS)
        reg.accept(suggestion.suggestion_id)
        assert reg.update([sign], 1000, 1000, 20, self.FPS) == []

    def test_two_distinct_signs_emit_independently(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)
        reg = SignZoneRegistry()
        sign_a = _sign(class_id=2, bbox=(100.0, 100.0, 200.0, 200.0))
        sign_b = _sign(class_id=3, bbox=(700.0, 700.0, 800.0, 800.0))
        reg.update([sign_a, sign_b], 1000, 1000, 0, self.FPS)
        results = reg.update([sign_a, sign_b], 1000, 1000, 10, self.FPS)
        assert len(results) == 2
        source_classes = {r.source_class for r in results}
        assert source_classes == {"P004_NoThoroughfare", "W011_Slippery"}

    def test_dismissed_sign_does_not_block_new_sign_at_different_position(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)
        reg = SignZoneRegistry()
        sign_a = _sign(class_id=2, bbox=(100.0, 100.0, 200.0, 200.0))
        sign_b = _sign(class_id=2, bbox=(700.0, 700.0, 800.0, 800.0))
        reg.update([sign_a], 1000, 1000, 0, self.FPS)
        [s] = reg.update([sign_a], 1000, 1000, 10, self.FPS)
        reg.dismiss(s.suggestion_id)
        # sign_b at a different position should still be tracked independently
        reg.update([sign_b], 1000, 1000, 20, self.FPS)
        results = reg.update([sign_b], 1000, 1000, 30, self.FPS)
        assert len(results) == 1


# ── Suppression driven by the saved zones ─────────────────────────────────────


def _saved_zone(suggestion, zone_type: str) -> ZoneViolationRecord:
    """The zone the user gets when they accept `suggestion` as-is."""
    return ZoneViolationRecord(
        camera_zone_view_id=1,
        physical_zone_id=1,
        zone_name="Auto zone",
        zone_type=zone_type,
        poly=[
            (point["x"] * COORD_SCALE, point["y"] * COORD_SCALE)
            for point in suggestion.normalized_coordinates
        ],
        threshold=0.5,
    )


class TestSavedZoneSuppression:
    """The registry's in-memory record dies with the connection and is never told
    when a zone is accepted or deleted, so suppression is decided from the saved
    zones instead. These cover both directions of getting that wrong."""

    FPS = 10.0

    @pytest.fixture(autouse=True)
    def _fast_stationary(self, monkeypatch):
        monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 1.0)  # 10 frames

    def _emit_once(self, reg, sign):
        reg.update([sign], 1000, 1000, 0, self.FPS)
        [suggestion] = reg.update([sign], 1000, 1000, 10, self.FPS)
        return suggestion

    def test_looped_video_does_not_re_suggest_an_accepted_zone(self):
        """The sign leaves frame, the loop brings it back long after the emitted
        track would once have been pruned, and the zone already exists."""
        reg = SignZoneRegistry()
        sign = _sign(class_id=3)
        suggestion = self._emit_once(reg, sign)
        zones = [_saved_zone(suggestion, "SLIPPERY")]

        for frame in range(20, 400, 10):  # sign out of frame for ~38 s at 10 fps
            assert reg.update([], 1000, 1000, frame, self.FPS, existing_zones=zones) == []
        # Loop wraps and the sign comes back to the same resting spot.
        assert reg.update([sign], 1000, 1000, 400, self.FPS, existing_zones=zones) == []
        assert reg.update([sign], 1000, 1000, 410, self.FPS, existing_zones=zones) == []

    def test_deleting_the_zone_makes_the_sign_suggestible_again(self):
        """The reported bug: after deleting the zone the sign was never flagged
        again for the life of the connection."""
        reg = SignZoneRegistry()
        sign = _sign(class_id=3)
        suggestion = self._emit_once(reg, sign)
        zones = [_saved_zone(suggestion, "SLIPPERY")]

        assert reg.update([sign], 1000, 1000, 20, self.FPS, existing_zones=zones) == []
        # User deletes the zone; reload_zones empties the pipeline's list.
        [again] = reg.update([sign], 1000, 1000, 30, self.FPS, existing_zones=[])
        assert again.suggestion_id == suggestion.suggestion_id

    def test_a_dismissal_still_outranks_a_deleted_zone(self):
        reg = SignZoneRegistry()
        sign = _sign(class_id=3)
        suggestion = self._emit_once(reg, sign)
        reg.dismiss(suggestion.suggestion_id)
        assert reg.update([sign], 1000, 1000, 20, self.FPS, existing_zones=[]) == []

    @pytest.mark.parametrize(
        "zone_type, expected_suggestions",
        [
            # Same hazard already zoned — nothing to offer.
            ("SLIPPERY", 0),
            # Covers the same floor, but a walkway says nothing about the
            # slippery hazard the sign is warning about.
            ("WALKWAY", 1),
        ],
    )
    def test_only_a_zone_of_the_same_type_suppresses(self, zone_type, expected_suggestions):
        covering = ZoneViolationRecord(
            camera_zone_view_id=1,
            physical_zone_id=1,
            zone_name="Covers the whole floor",
            zone_type=zone_type,
            poly=[
                (0.0, 0.0),
                (COORD_SCALE, 0.0),
                (COORD_SCALE, COORD_SCALE),
                (0.0, COORD_SCALE),
            ],
            threshold=0.5,
        )
        reg = SignZoneRegistry()
        sign = _sign(class_id=3)  # W011_Slippery -> SLIPPERY
        reg.update([sign], 1000, 1000, 0, self.FPS, existing_zones=[covering])
        results = reg.update([sign], 1000, 1000, 10, self.FPS, existing_zones=[covering])
        assert len(results) == expected_suggestions

    def test_a_distant_zone_does_not_suppress(self):
        reg = SignZoneRegistry()
        far = ZoneViolationRecord(
            camera_zone_view_id=1,
            physical_zone_id=1,
            zone_name="Far away",
            zone_type="SLIPPERY",
            poly=[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)],
            threshold=0.5,
        )
        reg.update([_sign(class_id=3)], 1000, 1000, 0, self.FPS, existing_zones=[far])
        results = reg.update(
            [_sign(class_id=3)], 1000, 1000, 10, self.FPS, existing_zones=[far]
        )
        assert len(results) == 1


# ── Pipeline: sign inference → zone_suggestion ────────────────────────────────


class _Xyxy:
    """Minimal xyxy tensor stub for FakeBox."""
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeBox:
    def __init__(self, cls_id: int, conf: float, x1: float, y1: float, x2: float, y2: float) -> None:
        self.cls = [cls_id]
        self.conf = [conf]
        self.xyxy = [_Xyxy([x1, y1, x2, y2])]


class _FakeSignModel:
    """Returns one P004 sign at (200,200)–(400,400) on every predict() call."""
    def predict(self, frame, conf, classes, device, verbose):
        return [SimpleNamespace(boxes=[_FakeBox(2, 0.85, 200.0, 200.0, 400.0, 400.0)])]


class _FakePPEResult:
    def __init__(self, frame: np.ndarray) -> None:
        self.orig_img = frame
        self.boxes = None


class _FakePPEModel:
    def __init__(self, frame: np.ndarray, n: int) -> None:
        self._results = [_FakePPEResult(frame)] * n

    def track(self, **kwargs):
        return iter(self._results)


_FRAME_W = 1000
_FRAME_H = 1000
_SIGN_BBOX = (200.0, 200.0, 400.0, 400.0)


def _make_detector(ppe_model, sign_model) -> ppe.PPEDetector:
    det = ppe.PPEDetector.__new__(ppe.PPEDetector)
    det.model = ppe_model
    det.sign_model = sign_model
    det.device = "cpu"
    return det


def test_pipeline_emits_exactly_one_zone_suggestion(monkeypatch):
    """3 frames each containing the same stationary sign → exactly 1 zone_suggestion."""
    # fps from _video_metadata is 10.0, so 0.1s → 1 frame of stationarity required.
    monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 0.1)
    monkeypatch.setattr(settings, "SIGN_PASS_FRAME_INTERVAL", 1)
    monkeypatch.setattr(settings, "SIGN_CONFIDENCE_THRESHOLD", 0.5)

    frame = np.zeros((_FRAME_H, _FRAME_W, 3), dtype=np.uint8)
    monkeypatch.setattr(video_pipeline, "_video_metadata", lambda _: (10.0, 3))
    monkeypatch.setattr(video_pipeline, "_extract_result_boxes", lambda _r: ([], [], []))
    monkeypatch.setattr(video_pipeline, "load_zones", lambda _: [])

    detector = _make_detector(_FakePPEModel(frame, 3), _FakeSignModel())

    events = list(
        detector._real_video_pipeline(
            "test.mp4", "test.mp4", stride=1, enable_ppe=False, enable_zone=False
        )
    )
    suggestions = [e for e in events if e.event == "zone_suggestion"]

    assert len(suggestions) == 1
    data = suggestions[0].data
    assert data["zone_type"] == "RESTRICTED"
    assert data["source_class"] == "P004_NoThoroughfare"
    assert len(data["normalized_coordinates"]) == 4
    for pt in data["normalized_coordinates"]:
        assert 0.0 <= pt["x"] <= 1.0
        assert 0.0 <= pt["y"] <= 1.0


def test_pipeline_does_not_emit_suggestion_twice(monkeypatch):
    """6 frames with same stationary sign → still only 1 zone_suggestion (emit is terminal)."""
    monkeypatch.setattr(settings, "AUTO_ZONE_STATIONARY_SECONDS", 0.1)
    monkeypatch.setattr(settings, "SIGN_PASS_FRAME_INTERVAL", 1)
    monkeypatch.setattr(settings, "SIGN_CONFIDENCE_THRESHOLD", 0.5)

    frame = np.zeros((_FRAME_H, _FRAME_W, 3), dtype=np.uint8)
    monkeypatch.setattr(video_pipeline, "_video_metadata", lambda _: (10.0, 6))
    monkeypatch.setattr(video_pipeline, "_extract_result_boxes", lambda _r: ([], [], []))
    monkeypatch.setattr(video_pipeline, "load_zones", lambda _: [])

    detector = _make_detector(_FakePPEModel(frame, 6), _FakeSignModel())

    events = list(
        detector._real_video_pipeline(
            "test.mp4", "test.mp4", stride=1, enable_ppe=False, enable_zone=False
        )
    )
    assert len([e for e in events if e.event == "zone_suggestion"]) == 1


# ── Geometric integration: accepted polygon covers expected foot-point ─────────


def test_accepted_polygon_contains_person_foot_point():
    """
    The polygon produced by derive_zone_polygon for _SIGN_BBOX must pass
    check_zone_incursion for a person whose foot-point is geometrically inside it.
    Confirms the auto-zone coordinate system is consistent with zone_service.
    """
    poly_norm = derive_zone_polygon(_SIGN_BBOX, _FRAME_W, _FRAME_H, settings.AUTO_ZONE_BUFFER_RATIO)
    poly_scaled = [(pt["x"] * COORD_SCALE, pt["y"] * COORD_SCALE) for pt in poly_norm]

    zone = ZoneViolationRecord(
        camera_zone_view_id=99,
        physical_zone_id=99,
        zone_name="Auto-Suggested",
        zone_type="RESTRICTED",
        poly=poly_scaled,
        threshold=0.0,
    )

    # Person centred at x=300, foot at y=350 (both within the buffered bbox)
    person = PersonResult(
        person_id=1,
        track_id=None,
        bbox=BoundingBox(x1=200.0, y1=100.0, x2=400.0, y2=350.0),
        confidence=0.9,
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Vest", status="compliant"),
        ],
        compliant=True,
    )

    foot = get_person_foot_point(person, _FRAME_W, _FRAME_H)
    incursions = check_zone_incursion([zone], foot)

    assert len(incursions) == 1
    assert incursions[0].zone_type == "RESTRICTED"


def test_foot_point_outside_polygon_not_detected():
    """Person standing well outside the sign polygon must NOT trigger incursion."""
    poly_norm = derive_zone_polygon(_SIGN_BBOX, _FRAME_W, _FRAME_H, settings.AUTO_ZONE_BUFFER_RATIO)
    poly_scaled = [(pt["x"] * COORD_SCALE, pt["y"] * COORD_SCALE) for pt in poly_norm]

    zone = ZoneViolationRecord(
        camera_zone_view_id=99,
        physical_zone_id=99,
        zone_name="Auto-Suggested",
        zone_type="RESTRICTED",
        poly=poly_scaled,
        threshold=0.0,
    )

    # Person far to the right (x center ~800, foot y=500) – outside the zone
    person = PersonResult(
        person_id=2,
        track_id=None,
        bbox=BoundingBox(x1=700.0, y1=300.0, x2=900.0, y2=500.0),
        confidence=0.9,
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Vest", status="compliant"),
        ],
        compliant=True,
    )

    foot = get_person_foot_point(person, _FRAME_W, _FRAME_H)
    assert check_zone_incursion([zone], foot) == []
