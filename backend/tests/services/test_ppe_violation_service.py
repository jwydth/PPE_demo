from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.ppe_violation import PPEViolation, PPEViolationSubject
from app.repositories import RepositoryError
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.ppe_violation_service import PPEViolationService


NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _violation() -> PPEViolation:
    return PPEViolation(
        id=10,
        source_key="factory.mp4",
        occurred_at=NOW,
        violation_type="missing_helmet",
        details="Track 42 missing Helmet",
        snapshot_path="incident.jpg",
        frame_index=20,
    )


def _subject() -> PPEViolationSubject:
    return PPEViolationSubject(
        id=11,
        ppe_violation_id=10,
        tracker_id=42,
        person_index=1,
        missing_equipment=["Helmet"],
        confidence=0.95,
        created_at=NOW,
    )


def test_ppe_service_maps_api_fields_to_database_fields():
    repository = Mock()
    repository.create.side_effect = lambda violation: _persist_violation(
        violation
    )
    service = PPEViolationService(repository)

    result = service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="Track 42 missing Helmet",
        snapshot_url="http://localhost:8000/snapshots/incident.jpg",
        video_name="factory.mp4",
        frame_index=20,
    )

    created = repository.create.call_args.args[0]
    assert created.source_key == "factory.mp4"
    assert created.snapshot_path == "incident.jpg"
    assert result.video_name == "factory.mp4"
    assert result.snapshot_url == "/snapshots/incident.jpg"
    assert not isinstance(result, PPEViolation)


def test_ppe_service_maps_tracker_id_to_track_id():
    repository = Mock()
    repository.get_by_id.return_value = _violation()
    repository.get_subjects.return_value = [_subject()]
    repository.create_subject.side_effect = lambda subject: _persist_subject(
        subject
    )
    service = PPEViolationService(repository)

    created = service.create_subject(
        violation_id=10,
        track_id=42,
        person_index=1,
        missing_equipment=["Helmet"],
        confidence=0.95,
    )
    stored = repository.create_subject.call_args.args[0]

    assert stored.tracker_id == 42
    assert created.track_id == 42
    assert service.get_violation(10).track_id == 42
    assert service.get_subjects(10)[0].track_id == 42


def test_ppe_service_recent_validation_and_error_propagation():
    repository = Mock()
    repository.get_recent.return_value = [_violation()]
    service = PPEViolationService(repository)

    assert service.get_recent_violations(25)[0].snapshot_url == (
        "/snapshots/incident.jpg"
    )
    repository.get_recent.assert_called_once_with(25)

    with pytest.raises(ServiceValidationError):
        service.get_recent_violations(0)

    repository.get_by_id.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.get_violation(999)

    repository.get_recent.side_effect = RepositoryError("database failed")
    with pytest.raises(RepositoryError, match="database failed"):
        service.get_recent_violations(10)


def _persist_violation(violation: PPEViolation) -> PPEViolation:
    violation.id = 10
    return violation


def _persist_subject(subject: PPEViolationSubject) -> PPEViolationSubject:
    subject.id = 11
    subject.created_at = NOW
    return subject
