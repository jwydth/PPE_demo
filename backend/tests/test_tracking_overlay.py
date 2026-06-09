from types import SimpleNamespace

import numpy as np

from app.schemas.detection import (
    BoundingBox,
    DetectionResponse,
    EquipmentStatus,
    PersonResult,
    Summary,
)
from app.services import ppe_detector as ppe


def _compliant_response(track_id: int | None = None) -> DetectionResponse:
    return DetectionResponse(
        detections=[],
        persons=[
            PersonResult(
                person_id=1,
                track_id=track_id,
                bbox=BoundingBox(x1=20, y1=10, x2=80, y2=110),
                confidence=0.95,
                equipment=[
                    EquipmentStatus(label="Helmet", status="compliant"),
                    EquipmentStatus(label="Vest", status="compliant"),
                ],
                compliant=True,
            )
        ],
        summary=Summary(
            total_persons=1,
            compliant=1,
            violations=0,
            inference_ms=0,
        ),
    )


def test_mock_video_tracking_overlay_respects_stride(monkeypatch):
    frames = [np.zeros((120, 200, 3), dtype=np.uint8) for _ in range(5)]

    class FakeCapture:
        def __init__(self, _path):
            self.frames = iter(frames)

        def isOpened(self):
            return True

        def read(self):
            try:
                return True, next(self.frames)
            except StopIteration:
                return False, None

        def release(self):
            return None

    monkeypatch.setattr(ppe, "_video_metadata", lambda _path: (10.0, 5))
    monkeypatch.setattr(ppe.settings, "VIDEO_FRAME_STRIDE", 2)
    monkeypatch.setattr("cv2.VideoCapture", FakeCapture)

    detector = ppe.PPEDetector.__new__(ppe.PPEDetector)
    detector._mock_predict = lambda _image: _compliant_response()

    result = detector._mock_process_video("ignored.mp4", "factory.mp4")

    assert result.tracking_overlay is not None
    assert result.tracking_overlay.fps == 10.0
    assert result.tracking_overlay.stride == 2
    assert result.tracking_overlay.frame_width == 200
    assert result.tracking_overlay.frame_height == 120
    assert [frame.frame_index for frame in result.tracking_overlay.frames] == [
        0,
        2,
        4,
    ]
    assert [frame.time_seconds for frame in result.tracking_overlay.frames] == [
        0.0,
        0.2,
        0.4,
    ]
    assert all(frame.track_id == 1 for frame in result.tracking_overlay.frames)
    assert all(frame.person_id == 1 for frame in result.tracking_overlay.frames)
    assert all(frame.bbox.x1 == 20 for frame in result.tracking_overlay.frames)


def test_real_video_tracking_overlay_uses_processed_frame_indexes(monkeypatch):
    results = [
        SimpleNamespace(orig_img=np.zeros((100, 160, 3), dtype=np.uint8)),
        SimpleNamespace(orig_img=np.zeros((100, 160, 3), dtype=np.uint8)),
    ]

    class FakeModel:
        def track(self, **kwargs):
            assert kwargs["vid_stride"] == 3
            return iter(results)

    person = {
        "x1": 20,
        "y1": 10,
        "x2": 80,
        "y2": 90,
        "conf": 0.95,
        "track_id": 42,
    }
    helmet = {"x1": 30, "y1": 10, "x2": 60, "y2": 30, "conf": 0.9}
    vest = {"x1": 25, "y1": 35, "x2": 75, "y2": 70, "conf": 0.9}

    monkeypatch.setattr(ppe, "_video_metadata", lambda _path: (10.0, 6))
    monkeypatch.setattr(ppe, "_extract_result_boxes", lambda _result: ([person], [helmet], [vest]))
    monkeypatch.setattr(ppe, "load_zones", lambda _video_name: [])
    monkeypatch.setattr(ppe.settings, "VIDEO_FRAME_STRIDE", 3)

    detector = ppe.PPEDetector.__new__(ppe.PPEDetector)
    detector.model = FakeModel()
    detector.device = "cpu"

    result = detector._real_process_video("ignored.mp4", "factory.mp4")

    assert result.tracking_overlay is not None
    assert [frame.frame_index for frame in result.tracking_overlay.frames] == [0, 3]
    assert [frame.time_seconds for frame in result.tracking_overlay.frames] == [
        0.0,
        0.3,
    ]
    assert all(frame.track_id == 42 for frame in result.tracking_overlay.frames)
    assert all(frame.missing_equipment == [] for frame in result.tracking_overlay.frames)


def test_tracking_overlay_deduplicates_person_and_reports_unknown_status():
    person = _compliant_response(track_id=9).persons[0]
    worker = ppe.WorkerState(
        track_ids={9},
        first_frame=0,
        last_frame=0,
        last_bbox=person.bbox,
        recent_bboxes=[person.bbox],
        status="unknown",
    )
    frames = []
    seen = set()

    for _ in range(2):
        ppe._append_tracking_overlay_frame(
            overlay_frames=frames,
            seen_person_ids=seen,
            person=person,
            decision={"unknown": True, "worker": worker},
            frame_index=0,
            fps=0,
        )

    assert len(frames) == 1
    assert frames[0].status == "unknown"
    assert frames[0].time_seconds == 0.0


def test_tracking_overlay_includes_missing_equipment_and_violation_status():
    person = PersonResult(
        person_id=2,
        track_id=10,
        bbox=BoundingBox(x1=30, y1=15, x2=90, y2=115),
        confidence=0.91,
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Vest", status="violation"),
        ],
        compliant=False,
    )
    worker = ppe.WorkerState(
        track_ids={10},
        first_frame=4,
        last_frame=4,
        last_bbox=person.bbox,
        recent_bboxes=[person.bbox],
        status="violation",
    )
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={"unknown": False, "worker": worker},
        frame_index=4,
        fps=20,
        camera_zone_view_id=12,
        physical_zone_id=3,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
    )

    assert frames[0].missing_equipment == ["Vest"]
    assert frames[0].status == "violation"
    assert frames[0].compliant is False
    assert frames[0].zone_id == 12
    assert frames[0].camera_zone_view_id == 12
    assert frames[0].physical_zone_id == 3
    assert frames[0].zone_name == "Restricted Area"
    assert frames[0].zone_type == "RESTRICTED"
