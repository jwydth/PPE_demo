from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories import RepositoryError
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.zone_violation_service import ZoneViolationService
from app.storage.evidence_storage import StorageObject


NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _violation() -> ZoneViolationModel:
    return ZoneViolationModel(
        id=5,
        zone_id=3,
        zone_name="Restricted Area",
        source_key="factory.mp4",
        tracker_id=42,
        occurred_at=NOW,
        frame_index=20,
        snapshot_path="zone.jpg",
    )


def test_zone_violation_service_maps_api_and_database_fields():
    repository = Mock()
    repository.create.side_effect = lambda violation: _persist(violation)
    service = ZoneViolationService(repository)

    result = service.create_zone_violation(
        zone_id=3,
        zone_name="Restricted Area",
        video_name="factory.mp4",
        track_id=42,
        timestamp="2026-06-05T12:00:00Z",
        frame_index=20,
        snapshot_url="/snapshots/zone.jpg",
    )

    created = repository.create.call_args.args[0]
    assert created.source_key == "factory.mp4"
    assert created.tracker_id == 42
    assert created.snapshot_path == "zone.jpg"
    assert result.video_name == "factory.mp4"
    assert result.track_id == 42
    assert result.snapshot_path == "/snapshots/zone.jpg"
    assert not isinstance(result, ZoneViolationModel)


def test_zone_violation_service_uploads_snapshot_and_stores_object_key():
    repository = Mock()
    repository.create.side_effect = lambda violation: _persist(violation)
    storage = Mock()
    storage.upload_zone_snapshot.return_value = StorageObject(
        object_key="zone-violations/2026/06/05/evidence.jpg",
        object_url="http://minio/zone-evidence",
        bucket_name="safety-monitoring-evidence",
    )
    service = ZoneViolationService(repository, storage)

    result = service.persist_zone_violation(
        zone_id=3,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        video_name="factory.mp4",
        track_id=42,
        timestamp=NOW,
        frame_index=20,
        local_snapshot_path="local-zone.jpg",
    )

    created = repository.create.call_args.args[0]
    assert created.snapshot_path == (
        "zone-violations/2026/06/05/evidence.jpg"
    )
    assert result.snapshot_path == "http://minio/zone-evidence"
    assert result.zone_name == "Restricted Area"
    assert result.zone_type == "RESTRICTED"
    storage.upload_zone_snapshot.assert_called_once_with("local-zone.jpg")


def test_zone_violation_service_read_recent_validation_and_errors():
    repository = Mock()
    repository.get_by_id.return_value = _violation()
    repository.get_recent.return_value = [_violation()]
    service = ZoneViolationService(repository)

    assert service.get_zone_violation(5).track_id == 42
    assert service.get_recent_zone_violations(20)[0].video_name == (
        "factory.mp4"
    )
    repository.get_recent.assert_called_once_with(20)

    with pytest.raises(ServiceValidationError):
        service.get_recent_zone_violations(0)

    repository.get_by_id.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.get_zone_violation(999)

    repository.get_recent.side_effect = RepositoryError("database failed")
    with pytest.raises(RepositoryError, match="database failed"):
        service.get_recent_zone_violations(10)


def test_zone_violation_service_returns_detached_zone_with_null_id():
    repository = Mock()
    repository.get_recent.return_value = [
        _violation().model_copy(update={"zone_id": None})
    ]
    storage = Mock()
    storage.get_object_url.return_value = "http://minio/read-url"
    zone_repository = Mock()
    service = ZoneViolationService(
        repository,
        storage,
        zone_repository,
    )

    result = service.get_recent_zone_violations()[0]

    assert result.zone_id is None
    assert result.zone_name == "Restricted Area"
    assert result.zone_type is None
    assert result.video_name == "factory.mp4"
    assert result.timestamp == "2026-06-05T12:00:00+00:00"
    assert result.frame_index == 20
    assert result.snapshot_path == "http://minio/read-url"
    zone_repository.get_by_id.assert_not_called()


def test_zone_violation_service_deletes_records():
    repository = Mock()
    repository.delete.return_value = True
    repository.delete_all.return_value = 3
    service = ZoneViolationService(repository)

    assert service.delete_zone_violation(5) is True
    assert service.delete_all_zone_violations() == 3
    repository.delete.assert_called_once_with(5)
    repository.delete_all.assert_called_once_with()


def _persist(violation: ZoneViolationModel) -> ZoneViolationModel:
    violation.id = 5
    return violation
