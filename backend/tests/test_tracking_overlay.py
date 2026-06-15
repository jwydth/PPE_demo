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
                role="worker",
                uniform_type="vest",
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


def _person_result(
    *,
    track_id: int = 10,
    role: str | None,
    uniform_type: str | None,
    equipment: list[EquipmentStatus],
    compliant: bool,
) -> PersonResult:
    return PersonResult(
        person_id=2,
        track_id=track_id,
        bbox=BoundingBox(x1=30, y1=15, x2=90, y2=115),
        confidence=0.91,
        role=role,
        uniform_type=uniform_type,
        equipment=equipment,
        compliant=compliant,
    )


def _worker_state(person: PersonResult, *, status: str) -> ppe.WorkerState:
    return ppe.WorkerState(
        track_ids={person.track_id} if person.track_id is not None else set(),
        first_frame=4,
        last_frame=4,
        last_bbox=person.bbox,
        recent_bboxes=[person.bbox],
        status=status,
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
    monkeypatch.setattr(
        ppe,
        "_extract_result_boxes",
        lambda _result: ([person], [helmet], [vest], []),
    )
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
    assert all(
        frame.missing_equipment == [] for frame in result.tracking_overlay.frames
    )


def test_tracking_overlay_deduplicates_person_and_reports_compliant_worker_status():
    person = _compliant_response(track_id=9).persons[0]
    worker = _worker_state(person, status="unknown")
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
    assert frames[0].status == "compliant"
    assert frames[0].time_seconds == 0.0


def test_tracking_overlay_reports_compliant_janitor_with_unknown_worker_status():
    person = _person_result(
        role="janitor",
        uniform_type="cleaning_coverall",
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Cleaning Coverall", status="compliant"),
        ],
        compliant=True,
    )
    worker = _worker_state(person, status="unknown")
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={"unknown": True, "worker": worker},
        frame_index=4,
        fps=20,
    )

    assert frames[0].missing_equipment == []
    assert frames[0].status == "compliant"
    assert frames[0].role == "janitor"


def test_tracking_overlay_confirmed_missing_vest_is_violation():
    person = _person_result(
        role="worker",
        uniform_type="vest",
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Vest", status="violation"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="violation")
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "worker": worker,
            "missing": ["Vest"],
            "missing_to_report": ["Vest"],
        },
        frame_index=4,
        fps=20,
    )

    assert frames[0].missing_equipment == ["Vest"]
    assert frames[0].status == "violation"


def test_tracking_overlay_pending_missing_vest_stays_unknown_without_missing_label():
    person = _person_result(
        role="worker",
        uniform_type="vest",
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Vest", status="violation"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="unknown")
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "candidate": True,
            "worker": worker,
            "missing": ["Vest"],
            "missing_to_report": [],
        },
        frame_index=4,
        fps=20,
    )

    assert frames[0].missing_equipment == []
    assert frames[0].status == "unknown"


def test_tracking_overlay_unknown_role_non_compliant_stays_unknown():
    person = _person_result(
        role=None,
        uniform_type=None,
        equipment=[
            EquipmentStatus(label="Helmet", status="compliant"),
            EquipmentStatus(label="Role Uniform", status="violation"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="unknown")
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "candidate": True,
            "worker": worker,
            "missing": ["Role Uniform"],
            "missing_to_report": [],
        },
        frame_index=4,
        fps=20,
    )

    assert frames[0].missing_equipment == []
    assert frames[0].status == "unknown"
    assert frames[0].role is None


def test_tracking_overlay_zone_violation_overrides_compliant_status():
    person = _compliant_response(track_id=10).persons[0]
    worker = _worker_state(person, status="unknown")
    frames = []

    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={"unknown": True, "worker": worker, "missing_to_report": []},
        frame_index=4,
        fps=20,
        camera_zone_view_id=12,
        physical_zone_id=3,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
    )

    assert frames[0].missing_equipment == []
    assert frames[0].status == "violation"
    assert frames[0].compliant is False
    assert frames[0].zone_id == 12
    assert frames[0].camera_zone_view_id == 12
    assert frames[0].physical_zone_id == 3
    assert frames[0].zone_name == "Restricted Area"
    assert frames[0].zone_type == "RESTRICTED"


def test_tracking_overlay_reported_worker_preserves_missing_on_subsequent_frame():
    """
    Verify that after a violation is confirmed and worker.reported=True,
    subsequent frames for the same worker still show the violation with
    the original missing equipment in the overlay.

    This tests the fix for the PPE overlay mismatch where "PPE status pending"
    was shown instead of the confirmed violation.
    """
    person = _person_result(
        role="worker",
        uniform_type="vest",
        equipment=[
            EquipmentStatus(label="Helmet", status="violation"),
            EquipmentStatus(label="Vest", status="compliant"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="violation")
    worker.reported = True
    worker.reported_missing = {"Helmet"}

    frames = []

    # Simulate frame N+1 after violation was reported
    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "candidate": False,
            "reason": "already_reported",
            "missing": [],
            "worker": worker,
            "missing_to_report": ["Helmet"],  # Preserved from reported_missing
        },
        frame_index=50,
        fps=20,
    )

    assert len(frames) == 1
    assert frames[0].status == "violation"
    assert frames[0].missing_equipment == ["Helmet"]
    assert frames[0].compliant is False
    assert frames[0].frame_index == 50


def test_tracking_overlay_reported_worker_maintains_violation_context():
    """
    After violation confirmation at frame N, verify that the same worker
    at frame N+1 continues to show violation status with missing equipment,
    not "PPE status pending" (status="unknown").
    """
    person = _person_result(
        role="worker",
        uniform_type="vest",
        equipment=[
            EquipmentStatus(label="Helmet", status="violation"),
            EquipmentStatus(label="Vest", status="compliant"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="violation")
    worker.reported = True
    worker.reported_missing = {"Helmet"}

    frames = []

    # This is what decision would contain after fix in _update_worker_status
    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "candidate": False,
            "reason": "already_reported",
            "missing": [],
            "worker": worker,
            "missing_to_report": ["Helmet"],
        },
        frame_index=51,
        fps=20,
    )

    # Should NOT show "PPE status pending" (which would require
    # status="unknown" AND role="worker"/"janitor" AND missing_equipment=[]
    assert frames[0].status == "violation"
    assert frames[0].missing_equipment == ["Helmet"]


def test_tracking_overlay_pending_missing_shows_unknown_not_violation():
    """
    Verify that unconfirmed/pending missing equipment (before reaching
    confirmation threshold) still shows as "unknown" status with empty
    missing_to_report, not as a violation.
    """
    person = _person_result(
        role="worker",
        uniform_type="vest",
        equipment=[
            EquipmentStatus(label="Helmet", status="violation"),
            EquipmentStatus(label="Vest", status="compliant"),
        ],
        compliant=False,
    )
    worker = _worker_state(person, status="unknown")
    frames = []

    # Pending confirmation: missing_to_report is empty
    ppe._append_tracking_overlay_frame(
        overlay_frames=frames,
        seen_person_ids=set(),
        person=person,
        decision={
            "unknown": False,
            "candidate": True,
            "reason": "",
            "missing": ["Helmet"],
            "worker": worker,
            "missing_to_report": [],  # NOT confirmed yet
        },
        frame_index=10,
        fps=20,
    )

    assert frames[0].status == "unknown"
    assert frames[0].missing_equipment == []
    assert frames[0].compliant is False
