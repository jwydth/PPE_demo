from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import BoundingBox, EquipmentStatus, PersonResult, ViolationReport
from app.services import ppe_detector as ppe


def _person(
    *,
    track_id: int,
    bbox: BoundingBox,
    missing: tuple[str, ...] = ("Vest",),
    person_id: int = 1,
) -> PersonResult:
    return PersonResult(
        person_id=person_id,
        track_id=track_id,
        bbox=bbox,
        confidence=0.95,
        equipment=[
            EquipmentStatus(label=label, status="violation", confidence=0.9)
            for label in missing
        ],
        compliant=False,
    )


def _clear_bbox(x_offset: float = 0.0) -> BoundingBox:
    return BoundingBox(x1=100 + x_offset, y1=100, x2=160 + x_offset, y2=280)


def _unclear_bbox(x_offset: float = 0.0) -> BoundingBox:
    return BoundingBox(x1=100 + x_offset, y1=100, x2=220 + x_offset, y2=200)


def _configure_video_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(ppe.settings, "VIDEO_NEW_TRACK_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(ppe.settings, "VIDEO_STABILITY_WINDOW_FRAMES", 2)
    monkeypatch.setattr(ppe.settings, "VIDEO_VIOLATION_CONFIRM_SECONDS", 2.0)
    monkeypatch.setattr(ppe.settings, "VIDEO_RECENT_PPE_MEMORY_SECONDS", 0.0)
    monkeypatch.setattr(ppe.settings, "VIDEO_MIN_PERSON_HEIGHT_RATIO", 0.01)
    monkeypatch.setattr(ppe.settings, "VIDEO_MIN_CLEAR_PERSON_ASPECT_RATIO", 1.20)
    monkeypatch.setattr(ppe.settings, "VIDEO_POSTURE_HEIGHT_DROP_RATIO", 0.70)
    monkeypatch.setattr(ppe.settings, "VIDEO_POSTURE_HISTORY_MIN_FRAMES", 3)


def _update(
    workers: list[ppe.WorkerState],
    person: PersonResult,
    frame_index: int,
    used_worker_ids: set[int] | None = None,
) -> dict:
    return ppe._update_worker_status(
        workers=workers,
        person=person,
        frame_index=frame_index,
        fps=1.0,
        frame_width=1000,
        frame_height=1000,
        used_worker_ids=used_worker_ids if used_worker_ids is not None else set(),
    )


def _record(monkeypatch, cases: list[ppe.ViolationCase], decision: dict, person: PersonResult) -> None:
    monkeypatch.setattr(ppe, "_save_violation_snapshot", lambda **_: "snapshot.jpg")
    monkeypatch.setattr(
        ppe,
        "save_violation",
        lambda **kwargs: ViolationReport(
            id=len(cases) + 1,
            timestamp=kwargs["timestamp"],
            violation_type=kwargs["violation_type"],
            details=kwargs["details"],
            snapshot_url=kwargs["snapshot_filename"],
            video_name=kwargs["video_name"],
            frame_index=kwargs["frame_index"],
            track_id=kwargs["track_id"],
        ),
    )
    ppe._record_violation_case(
        cases=cases,
        frame=None,
        person=person,
        worker=decision["worker"],
        missing=decision["missing_to_report"],
        video_name="factory.mp4",
        frame_index=10,
    )


def test_unclear_posture_worker_does_not_report_or_accumulate_counts(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    _update(workers, _person(track_id=1, bbox=_unclear_bbox()), 0)
    decision = _update(workers, _person(track_id=1, bbox=_unclear_bbox()), 1)

    assert decision["reason"] == "unclear_posture"
    assert decision["missing_to_report"] == []
    assert workers[0].missing_counts == {"Helmet": 0, "Vest": 0}
    assert workers[0].reported is False


def test_clear_worker_can_report_after_confirmation(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    assert _update(workers, _person(track_id=1, bbox=_clear_bbox()), 0)["missing_to_report"] == []
    assert _update(workers, _person(track_id=1, bbox=_clear_bbox()), 1)["missing_to_report"] == []
    decision = _update(workers, _person(track_id=1, bbox=_clear_bbox()), 2)

    assert decision["missing_to_report"] == ["Vest"]


def test_reported_worker_with_same_track_does_not_report_again(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers = [
        ppe.WorkerState(
            track_ids={1},
            first_frame=0,
            last_frame=4,
            last_bbox=_clear_bbox(),
            recent_bboxes=[_clear_bbox(), _clear_bbox()],
            reported=True,
            status="violation",
        )
    ]

    decision = _update(workers, _person(track_id=1, bbox=_clear_bbox(), missing=("Helmet", "Vest")), 5)

    assert decision["reason"] == "already_reported"
    assert decision["missing_to_report"] == []
    assert workers[0].status == "violation"
    assert workers[0].missing_counts == {"Helmet": 0, "Vest": 0}


def test_reported_worker_with_new_track_reconnects_and_does_not_report_again(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers = [
        ppe.WorkerState(
            track_ids={1},
            first_frame=0,
            last_frame=4,
            last_bbox=_clear_bbox(),
            recent_bboxes=[_clear_bbox(), _clear_bbox()],
            reported=True,
            status="violation",
        )
    ]

    decision = _update(workers, _person(track_id=99, bbox=_clear_bbox(2), missing=("Helmet", "Vest")), 5)

    assert len(workers) == 1
    assert workers[0].track_ids == {1, 99}
    assert decision["reason"] == "already_reported"
    assert decision["missing_to_report"] == []


def test_new_worker_in_same_frame_can_still_report(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    reported_worker = ppe.WorkerState(
        track_ids={1},
        first_frame=0,
        last_frame=9,
        last_bbox=_clear_bbox(),
        recent_bboxes=[_clear_bbox(), _clear_bbox()],
        reported=True,
        status="violation",
    )
    unreported_worker = ppe.WorkerState(
        track_ids={2},
        first_frame=0,
        last_frame=9,
        last_bbox=_clear_bbox(500),
        recent_bboxes=[_clear_bbox(500), _clear_bbox(500)],
        missing_counts={"Helmet": 0, "Vest": 1},
    )
    workers = [reported_worker, unreported_worker]
    used_worker_ids: set[int] = set()

    suppressed = _update(
        workers,
        _person(track_id=99, bbox=_clear_bbox(2), missing=("Helmet", "Vest")),
        10,
        used_worker_ids,
    )
    allowed = _update(
        workers,
        _person(track_id=2, bbox=_clear_bbox(500), missing=("Vest",), person_id=2),
        10,
        used_worker_ids,
    )

    assert suppressed["missing_to_report"] == []
    assert allowed["missing_to_report"] == ["Vest"]


def test_recorded_confirmed_violation_marks_worker_reported(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []
    cases: list[ppe.ViolationCase] = []

    _update(workers, _person(track_id=1, bbox=_clear_bbox()), 0)
    _update(workers, _person(track_id=1, bbox=_clear_bbox()), 1)
    decision = _update(workers, _person(track_id=1, bbox=_clear_bbox()), 2)
    _record(monkeypatch, cases, decision, _person(track_id=1, bbox=_clear_bbox()))

    assert workers[0].reported is True
    assert len(cases) == 1
