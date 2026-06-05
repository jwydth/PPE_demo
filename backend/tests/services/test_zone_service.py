import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.camera import Camera
from app.models.zone import Zone as ZoneModel
from app.repositories import RepositoryError
from app.schemas.zone import Zone
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.zone_service import ZoneService


def _camera() -> Camera:
    now = datetime.now(timezone.utc)
    return Camera(
        id=7,
        name="Factory",
        source_key="factory.mp4",
        created_at=now,
        updated_at=now,
    )


def _zone_model() -> ZoneModel:
    now = datetime.now(timezone.utc)
    return ZoneModel(
        id=3,
        camera_id=7,
        name="Restricted Area",
        zone_type="RESTRICTED",
        dwell_threshold_seconds=2,
        is_active=True,
        ui_shape_data={"type": "polygon"},
        normalized_coordinates=[{"x": 0.1, "y": 0.2}],
        created_at=now,
        updated_at=now,
    )


def _zone_schema() -> Zone:
    return Zone(
        video_name="factory.mp4",
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        dwell_threshold_seconds=2,
        ui_shape_data='{"type":"polygon"}',
        flattened_coordinates='[{"x":0.1,"y":0.2}]',
    )


def test_zone_service_maps_video_name_and_json_fields():
    zone_repository = Mock()
    camera_repository = Mock()
    camera_repository.get_by_source_key.return_value = _camera()
    zone_repository.create.return_value = _zone_model()
    service = ZoneService(zone_repository, camera_repository)

    result = service.create_zone(_zone_schema())

    created = zone_repository.create.call_args.args[0]
    assert created.camera_id == 7
    assert created.ui_shape_data == {"type": "polygon"}
    assert created.normalized_coordinates == [{"x": 0.1, "y": 0.2}]
    assert result.video_name == "factory.mp4"
    assert result.zone_name == "Restricted Area"
    assert json.loads(result.flattened_coordinates) == [{"x": 0.1, "y": 0.2}]
    assert not isinstance(result, ZoneModel)


def test_zone_service_reads_updates_and_deletes():
    zone_repository = Mock()
    camera_repository = Mock()
    zone_repository.get_by_id.return_value = _zone_model()
    zone_repository.get_by_source_key.return_value = [_zone_model()]
    zone_repository.update.side_effect = lambda zone: zone
    zone_repository.delete.return_value = True
    camera_repository.get_by_id.return_value = _camera()
    camera_repository.get_by_source_key.return_value = _camera()
    service = ZoneService(zone_repository, camera_repository)

    assert service.get_zone(3).video_name == "factory.mp4"
    assert service.get_zones_by_source_key("factory.mp4")[0].zone_name == (
        "Restricted Area"
    )
    assert service.update_zone(3, _zone_schema()).id == 3
    assert service.delete_zone(3) is True

    zone_repository.delete.return_value = False
    with pytest.raises(ServiceNotFoundError):
        service.delete_zone(99)


def test_zone_service_validation_and_error_propagation():
    zone_repository = Mock()
    camera_repository = Mock()
    camera_repository.get_by_source_key.return_value = _camera()
    service = ZoneService(zone_repository, camera_repository)
    invalid = _zone_schema().model_copy(update={"ui_shape_data": "[]"})

    with pytest.raises(ServiceValidationError):
        service.create_zone(invalid)

    zone_repository.get_by_source_key.side_effect = RepositoryError(
        "database failed"
    )
    with pytest.raises(RepositoryError, match="database failed"):
        service.get_zones_by_source_key("factory.mp4")
