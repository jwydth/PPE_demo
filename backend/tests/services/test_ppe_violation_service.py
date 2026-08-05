from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

from app.models.camera import Camera
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


def _camera(id: int = 7, source_key: str = "factory.mp4") -> Camera:
    return Camera(
        id=id,
        factory_id=1,
        name=source_key,
        source_key=source_key,
        source_uri=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# Existing tests (unchanged behaviour)
# ---------------------------------------------------------------------------

def test_ppe_service_maps_api_fields_to_database_fields():
    repository = Mock()
    repository.create.side_effect = lambda violation: _persist_violation(violation)
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
    repository.create_subject.side_effect = lambda subject: _persist_subject(subject)
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
    repository.get_subjects.return_value = []
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


# ---------------------------------------------------------------------------
# New tests — lazy camera_id resolution
# ---------------------------------------------------------------------------

def test_persist_violation_uses_explicit_camera_id():
    """Explicit camera_id is passed straight through without any lookup."""
    repository = Mock()
    repository.create.side_effect = _persist_violation
    camera_repo = Mock()
    factory_repo = Mock()
    service = PPEViolationService(repository, camera_repository=camera_repo, factory_repository=factory_repo)

    service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="Track 42 missing Helmet",
        video_name="factory.mp4",
        camera_id=99,
    )

    created = repository.create.call_args.args[0]
    assert created.camera_id == 99
    # When an explicit camera_id is given, no lookup should happen.
    camera_repo.get_by_source_key.assert_not_called()
    camera_repo.create.assert_not_called()



def test_persist_violation_resolves_existing_camera_from_video_name():
    """If a Camera with matching source_key exists, its id is used without creation."""
    repository = Mock()
    repository.create.side_effect = _persist_violation
    camera_repo = Mock()
    camera_repo.get_by_source_key.return_value = _camera(id=7, source_key="rtsp://localhost:8554/mystream")
    factory_repo = Mock()
    service = PPEViolationService(repository, camera_repository=camera_repo, factory_repository=factory_repo)

    service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="Track 5 missing Helmet",
        video_name="rtsp://localhost:8554/mystream",
    )

    created = repository.create.call_args.args[0]
    assert created.camera_id == 7
    # Must not attempt to create a duplicate Camera row.
    camera_repo.create.assert_not_called()


def test_persist_violation_creates_camera_when_not_found():
    """If no Camera row exists for the video_name, one is lazily created."""
    repository = Mock()
    repository.create.side_effect = _persist_violation
    new_camera = _camera(id=8, source_key="rtsp://localhost:8554/mystream")
    camera_repo = Mock()
    camera_repo.get_by_source_key.return_value = None  # not found on first call
    camera_repo.create.return_value = new_camera
    factory = Mock()
    factory.id = 1
    factory_repo = Mock()
    factory_repo.get_or_create_default_factory.return_value = factory
    service = PPEViolationService(repository, camera_repository=camera_repo, factory_repository=factory_repo)

    service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="Track 5 missing Helmet",
        video_name="rtsp://localhost:8554/mystream",
    )

    camera_repo.create.assert_called_once()
    created_camera_arg = camera_repo.create.call_args.args[0]
    assert created_camera_arg.source_key == "rtsp://127.0.0.1:8554/mystream"
    assert created_camera_arg.factory_id == 1

    violation_row = repository.create.call_args.args[0]
    assert violation_row.camera_id == 8


def test_persist_violation_second_call_reuses_same_camera():
    """A second violation with the same video_name reuses the existing Camera row."""
    repository = Mock()
    repository.create.side_effect = _persist_violation
    existing_camera = _camera(id=7, source_key="factory.mp4")
    camera_repo = Mock()
    # Both calls find the same camera — create is never called.
    camera_repo.get_by_source_key.return_value = existing_camera
    service = PPEViolationService(repository, camera_repository=camera_repo)

    service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="First violation",
        video_name="factory.mp4",
    )
    service.create_violation(
        timestamp="2026-06-05T12:01:00Z",
        violation_type="missing_vest",
        details="Second violation",
        video_name="factory.mp4",
    )

    assert camera_repo.create.call_count == 0
    assert camera_repo.get_by_source_key.call_count == 2
    for call_args in repository.create.call_args_list:
        assert call_args.args[0].camera_id == 7


def test_persist_violation_keeps_null_camera_id_when_no_video_name():
    """If neither camera_id nor video_name is provided, camera_id stays NULL."""
    repository = Mock()
    repository.create.side_effect = _persist_violation
    camera_repo = Mock()
    service = PPEViolationService(repository, camera_repository=camera_repo)

    service.create_violation(
        timestamp="2026-06-05T12:00:00Z",
        violation_type="missing_helmet",
        details="Unknown source violation",
    )

    created = repository.create.call_args.args[0]
    assert created.camera_id is None
    camera_repo.get_by_source_key.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _persist_violation(violation: PPEViolation) -> PPEViolation:
    violation.id = 10
    return violation


def _persist_subject(subject: PPEViolationSubject) -> PPEViolationSubject:
    subject.id = 11
    subject.created_at = NOW
    return subject
