from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.schemas import BoundingBox, EquipmentStatus, PersonResult, ViolationReport
from app.services import ppe_detector as ppe


def _person(missing: tuple[str, ...]) -> PersonResult:
    return PersonResult(
        person_id=1,
        track_id=42,
        bbox=BoundingBox(x1=10, y1=20, x2=50, y2=120),
        confidence=0.95,
        equipment=[
            EquipmentStatus(label=label, status="violation", confidence=0.9)
            for label in missing
        ],
        compliant=False,
    )


def test_existing_incident_title_is_not_upgraded(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=ppe.logger.name)
    saved_reports: list[ViolationReport] = []
    monkeypatch.setattr(ppe, "_save_violation_snapshot", lambda **_: "snapshot.jpg")

    def fake_save_violation(**kwargs):
        report = ViolationReport(
            id=len(saved_reports) + 1,
            timestamp=kwargs["timestamp"],
            violation_type=kwargs["violation_type"],
            details=kwargs["details"],
            snapshot_url=kwargs["snapshot_filename"],
            video_name=kwargs["video_name"],
            frame_index=kwargs["frame_index"],
            track_id=kwargs["track_id"],
        )
        saved_reports.append(report)
        return report

    monkeypatch.setattr(ppe, "save_violation", fake_save_violation)

    cases: list[ppe.ViolationCase] = []
    aspect_ratios: list[float] = []
    first_worker = ppe.WorkerState(
        track_ids={42},
        first_frame=0,
        last_frame=10,
        last_bbox=_person(("Vest",)).bbox,
        recent_bboxes=[],
    )
    second_worker = ppe.WorkerState(
        track_ids={42},
        first_frame=0,
        last_frame=20,
        last_bbox=_person(("Helmet", "Vest")).bbox,
        recent_bboxes=[],
    )

    ppe._record_violation_case(
        cases=cases,
        frame=None,
        person=_person(("Vest",)),
        worker=first_worker,
        missing=["Vest"],
        video_name="factory.mp4",
        frame_index=10,
        confirmed_aspect_ratios=aspect_ratios,
    )
    ppe._record_violation_case(
        cases=cases,
        frame=None,
        person=_person(("Helmet", "Vest")),
        worker=second_worker,
        missing=["Helmet", "Vest"],
        video_name="factory.mp4",
        frame_index=20,
        confirmed_aspect_ratios=aspect_ratios,
    )

    assert len(saved_reports) == 1
    assert len(cases) == 1
    assert cases[0].report.violation_type == "missing_vest"
    assert aspect_ratios == [2.5, 2.5]
    assert "action=create" in caplog.text
    assert "action=match_existing_immutable" in caplog.text
    assert "aspect_ratio=2.500" in caplog.text
