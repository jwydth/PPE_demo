from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.zone_violation import ZoneViolation as ZoneViolationModel
from app.repositories import RepositoryError
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.zone_violation_service import ZoneViolationService


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


def _persist(violation: ZoneViolationModel) -> ZoneViolationModel:
    violation.id = 5
    return violation
