import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.detection import BoundingBox, EquipmentStatus, PersonResult
from app.schemas.violation import ViolationReport
from app.services import ppe_detector as ppe


VIDEO_NAME = "factory.mp4"


def _person(
    *,
    track_id: int,
    bbox: BoundingBox,
    missing: tuple[str, ...] = ("Vest",),
    person_id: int = 1,
    role: str | None = None,
    uniform_type: str | None = None,
) -> PersonResult:
    return PersonResult(
        person_id=person_id,
        track_id=track_id,
        bbox=bbox,
        confidence=0.95,
        role=role,
        uniform_type=uniform_type,
        equipment=[
            EquipmentStatus(label=label, status="violation", confidence=0.9)
            for label in missing
        ],
        compliant=False,
    )


def _empty_counts() -> dict[str, int]:
    return {
        "Helmet": 0,
        "Vest": 0,
        "Cleaning Coverall": 0,
        "Role Uniform": 0,
    }


def _equipment_person(
    *,
    track_id: int = 1,
    bbox: BoundingBox | None = None,
    role: str | None,
    uniform_type: str | None,
    missing: tuple[str, ...] = (),
    person_id: int = 1,
) -> PersonResult:
    if role == "janitor":
        uniform_label = "Cleaning Coverall"
    elif role == "worker":
        uniform_label = "Vest"
    else:
        uniform_label = "Role Uniform"

    labels = ("Helmet", uniform_label)
    equipment = [
        EquipmentStatus(
            label=label,
            status="violation" if label in missing else "compliant",
            confidence=0.9,
        )
        for label in labels
    ]
    return PersonResult(
        person_id=person_id,
        track_id=track_id,
        bbox=bbox or _clear_bbox(),
        confidence=0.95,
        role=role,
        uniform_type=uniform_type,
        equipment=equipment,
        compliant=not missing and role is not None,
    )


def _compliant_worker(track_id: int = 1, bbox: BoundingBox | None = None) -> PersonResult:
    return _equipment_person(
        track_id=track_id,
        bbox=bbox,
        role="worker",
        uniform_type="vest",
    )


def _compliant_janitor(track_id: int = 1, bbox: BoundingBox | None = None) -> PersonResult:
    return _equipment_person(
        track_id=track_id,
        bbox=bbox,
        role="janitor",
        uniform_type="cleaning_coverall",
    )


def _helmet_only_unknown(track_id: int = 1, bbox: BoundingBox | None = None) -> PersonResult:
    return _equipment_person(
        track_id=track_id,
        bbox=bbox,
        role=None,
        uniform_type=None,
        missing=("Role Uniform",),
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


def _install_violation_spies(monkeypatch) -> list[dict]:
    saved_reports: list[dict] = []
    monkeypatch.setattr(ppe, "_save_violation_snapshot", lambda **_: "snapshot.jpg")

    def fake_save_violation(**kwargs) -> ViolationReport:
        saved_reports.append(kwargs)
        return ViolationReport(
            id=len(saved_reports),
            timestamp=kwargs["timestamp"],
            violation_type=kwargs["violation_type"],
            details=kwargs["details"],
            snapshot_url=kwargs["snapshot_filename"],
            video_name=kwargs["video_name"],
            frame_index=kwargs["frame_index"],
            track_id=kwargs["track_id"],
        )

    monkeypatch.setattr(ppe, "save_violation", fake_save_violation)
    return saved_reports


def _worker(track_id: int, bbox: BoundingBox) -> ppe.WorkerState:
    return ppe.WorkerState(
        track_ids={track_id},
        first_frame=0,
        last_frame=0,
        last_bbox=bbox,
        recent_bboxes=[bbox],
    )


def _record_direct(
    *,
    cases: list[ppe.ViolationCase],
    person: PersonResult,
    worker: ppe.WorkerState,
    missing: list[str],
    frame_index: int,
    video_name: str = VIDEO_NAME,
) -> None:
    ppe._record_violation_case(
        cases=cases,
        frame=None,
        person=person,
        worker=worker,
        missing=missing,
        video_name=video_name,
        frame_index=frame_index,
        worker_match_reason="test",
    )


def test_unclear_posture_worker_does_not_report_or_accumulate_counts(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    _update(workers, _person(track_id=1, bbox=_unclear_bbox()), 0)
    decision = _update(workers, _person(track_id=1, bbox=_unclear_bbox()), 1)

    assert decision["reason"] == "unclear_posture"
    assert decision["missing_to_report"] == []
    assert workers[0].missing_counts == _empty_counts()
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
    assert workers[0].missing_counts == _empty_counts()


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


def test_role_persists_when_worker_uniform_temporarily_missing(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    _update(workers, _compliant_worker(), 0)
    decision = _update(workers, _helmet_only_unknown(), 1)

    assert workers[0].role == "worker"
    assert decision["worker"].uniform_type == "vest"
    assert decision["missing_to_report"] == []


def test_known_worker_reports_missing_vest_after_confirmation(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    _update(workers, _compliant_worker(), 0)
    _update(workers, _helmet_only_unknown(), 1)
    _update(workers, _helmet_only_unknown(), 2)
    decision = _update(workers, _helmet_only_unknown(), 3)

    assert workers[0].role == "worker"
    assert decision["missing_to_report"] == ["Vest"]


def test_known_janitor_reports_missing_cleaning_coverall_after_confirmation(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []

    _update(workers, _compliant_janitor(), 0)
    _update(workers, _helmet_only_unknown(), 1)
    _update(workers, _helmet_only_unknown(), 2)
    decision = _update(workers, _helmet_only_unknown(), 3)

    assert workers[0].role == "janitor"
    assert decision["missing_to_report"] == ["Cleaning Coverall"]


def test_cleaning_coverall_temporary_disappearance_is_suppressed(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    monkeypatch.setattr(ppe.settings, "VIDEO_RECENT_PPE_MEMORY_SECONDS", 2.0)
    workers: list[ppe.WorkerState] = []

    _update(workers, _compliant_janitor(), 0)
    decision = _update(workers, _helmet_only_unknown(), 1)

    assert workers[0].role == "janitor"
    assert decision["missing"] == []
    assert decision["missing_to_report"] == []


def test_worker_reconnects_with_new_track_without_duplicate_violation(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []
    cases: list[ppe.ViolationCase] = []

    _update(workers, _compliant_worker(), 0)
    _update(
        workers,
        _equipment_person(role="worker", uniform_type="vest", missing=("Vest",)),
        1,
    )
    _update(
        workers,
        _equipment_person(role="worker", uniform_type="vest", missing=("Vest",)),
        2,
    )
    decision = _update(
        workers,
        _equipment_person(role="worker", uniform_type="vest", missing=("Vest",)),
        3,
    )
    _record(
        monkeypatch,
        cases,
        decision,
        _equipment_person(role="worker", uniform_type="vest", missing=("Vest",)),
    )

    reconnected = _update(
        workers,
        _equipment_person(
            track_id=99,
            role="worker",
            uniform_type="vest",
            missing=("Vest",),
            bbox=_clear_bbox(2),
        ),
        4,
    )
    _record(
        monkeypatch,
        cases,
        reconnected,
        _equipment_person(
            track_id=99,
            role="worker",
            uniform_type="vest",
            missing=("Vest",),
            bbox=_clear_bbox(2),
        ),
    )

    assert len(workers) == 1
    assert workers[0].track_ids == {1, 99}
    assert reconnected["reason"] == "already_reported"
    assert reconnected["missing_to_report"] == ["Vest"]
    assert len(cases) == 1


def test_janitor_reconnects_with_new_track_without_duplicate_violation(monkeypatch):
    _configure_video_thresholds(monkeypatch)
    workers: list[ppe.WorkerState] = []
    cases: list[ppe.ViolationCase] = []

    _update(workers, _compliant_janitor(), 0)
    _update(
        workers,
        _equipment_person(
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
        ),
        1,
    )
    _update(
        workers,
        _equipment_person(
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
        ),
        2,
    )
    decision = _update(
        workers,
        _equipment_person(
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
        ),
        3,
    )
    _record(
        monkeypatch,
        cases,
        decision,
        _equipment_person(
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
        ),
    )

    reconnected = _update(
        workers,
        _equipment_person(
            track_id=99,
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
            bbox=_clear_bbox(2),
        ),
        4,
    )
    _record(
        monkeypatch,
        cases,
        reconnected,
        _equipment_person(
            track_id=99,
            role="janitor",
            uniform_type="cleaning_coverall",
            missing=("Cleaning Coverall",),
            bbox=_clear_bbox(2),
        ),
    )

    assert len(workers) == 1
    assert workers[0].track_ids == {1, 99}
    assert workers[0].role == "janitor"
    assert reconnected["reason"] == "already_reported"
    assert reconnected["missing_to_report"] == ["Cleaning Coverall"]
    assert len(cases) == 1


def test_resolve_video_tracker_prefers_repo_local_tracker():
    resolved = Path(ppe._resolve_video_tracker("bytetrack.yaml"))

    assert resolved.exists()
    assert resolved.name == "bytetrack.yaml"
    assert resolved.parent.name == "trackers"


def test_resolve_video_tracker_warns_and_falls_back_for_missing_path(caplog):
    configured = "trackers/missing-bytetrack.yaml"

    with caplog.at_level(logging.WARNING, logger=ppe.logger.name):
        resolved = ppe._resolve_video_tracker(configured)

    assert resolved == configured
    assert "was not found" in caplog.text


def test_new_track_same_worker_within_ppe_suppression_window_saves_one_report(monkeypatch):
    monkeypatch.setattr(ppe.settings, "VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP", 300)
    saved_reports = _install_violation_spies(monkeypatch)
    cases: list[ppe.ViolationCase] = []

    first = _equipment_person(
        track_id=53,
        role="worker",
        uniform_type="vest",
        missing=("Helmet",),
        bbox=_clear_bbox(),
    )
    _record_direct(
        cases=cases,
        person=first,
        worker=_worker(53, _clear_bbox()),
        missing=["Helmet"],
        frame_index=10,
    )

    reconnected = _equipment_person(
        track_id=51,
        role="worker",
        uniform_type="vest",
        missing=("Helmet",),
        bbox=_clear_bbox(4),
    )
    reconnected_worker = _worker(51, _clear_bbox(4))
    _record_direct(
        cases=cases,
        person=reconnected,
        worker=reconnected_worker,
        missing=["Helmet"],
        frame_index=200,
    )

    assert len(saved_reports) == 1
    assert len(cases) == 1
    assert cases[0].track_ids == {53, 51}
    assert reconnected_worker.reported is True


def test_new_track_same_janitor_within_ppe_suppression_window_saves_one_report(monkeypatch):
    monkeypatch.setattr(ppe.settings, "VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP", 300)
    saved_reports = _install_violation_spies(monkeypatch)
    cases: list[ppe.ViolationCase] = []

    first = _equipment_person(
        track_id=53,
        role="janitor",
        uniform_type="cleaning_coverall",
        missing=("Cleaning Coverall",),
        bbox=_clear_bbox(),
    )
    _record_direct(
        cases=cases,
        person=first,
        worker=_worker(53, _clear_bbox()),
        missing=["Cleaning Coverall"],
        frame_index=10,
    )

    reconnected = _equipment_person(
        track_id=55,
        role="janitor",
        uniform_type="cleaning_coverall",
        missing=("Cleaning Coverall",),
        bbox=_clear_bbox(4),
    )
    reconnected_worker = _worker(55, _clear_bbox(4))
    _record_direct(
        cases=cases,
        person=reconnected,
        worker=reconnected_worker,
        missing=["Cleaning Coverall"],
        frame_index=200,
    )

    assert len(saved_reports) == 1
    assert len(cases) == 1
    assert cases[0].track_ids == {53, 55}
    assert reconnected_worker.reported is True


def test_new_track_different_worker_far_away_saves_separate_report(monkeypatch):
    monkeypatch.setattr(ppe.settings, "VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP", 300)
    saved_reports = _install_violation_spies(monkeypatch)
    cases: list[ppe.ViolationCase] = []

    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=53,
            role="worker",
            uniform_type="vest",
            missing=("Helmet",),
            bbox=_clear_bbox(),
        ),
        worker=_worker(53, _clear_bbox()),
        missing=["Helmet"],
        frame_index=10,
    )
    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=51,
            role="worker",
            uniform_type="vest",
            missing=("Helmet",),
            bbox=_clear_bbox(700),
        ),
        worker=_worker(51, _clear_bbox(700)),
        missing=["Helmet"],
        frame_index=200,
    )

    assert len(saved_reports) == 2
    assert len(cases) == 2


def test_new_track_different_violation_type_saves_separate_report(monkeypatch):
    monkeypatch.setattr(ppe.settings, "VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP", 300)
    saved_reports = _install_violation_spies(monkeypatch)
    cases: list[ppe.ViolationCase] = []

    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=53,
            role="worker",
            uniform_type="vest",
            missing=("Helmet",),
            bbox=_clear_bbox(),
        ),
        worker=_worker(53, _clear_bbox()),
        missing=["Helmet"],
        frame_index=10,
    )
    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=51,
            role="worker",
            uniform_type="vest",
            missing=("Vest",),
            bbox=_clear_bbox(4),
        ),
        worker=_worker(51, _clear_bbox(4)),
        missing=["Vest"],
        frame_index=200,
    )

    assert len(saved_reports) == 2
    assert len(cases) == 2


def test_new_track_after_ppe_suppression_window_saves_new_report(monkeypatch):
    monkeypatch.setattr(ppe.settings, "VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP", 300)
    saved_reports = _install_violation_spies(monkeypatch)
    cases: list[ppe.ViolationCase] = []

    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=53,
            role="worker",
            uniform_type="vest",
            missing=("Helmet",),
            bbox=_clear_bbox(),
        ),
        worker=_worker(53, _clear_bbox()),
        missing=["Helmet"],
        frame_index=10,
    )
    _record_direct(
        cases=cases,
        person=_equipment_person(
            track_id=51,
            role="worker",
            uniform_type="vest",
            missing=("Helmet",),
            bbox=_clear_bbox(4),
        ),
        worker=_worker(51, _clear_bbox(4)),
        missing=["Helmet"],
        frame_index=311,
    )

    assert len(saved_reports) == 2
    assert len(cases) == 2
