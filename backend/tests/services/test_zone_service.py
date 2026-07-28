import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

import numpy as np
import pytest

from app.models.camera import Camera
from app.models.camera_zone_view import CameraZoneView
from app.models.factory import Factory
from app.models.physical_zone import PhysicalZone
from app.repositories import RepositoryError
from app.schemas.detection import BoundingBox, PersonResult
from app.schemas.violation import ZoneViolation
from app.schemas.zone import Zone
from app.services import (
    ServiceNotFoundError,
    ServiceValidationError,
)
from app.services.zone_service import (
    ZoneService,
    ZoneViolationRecord,
    record_zone_violation,
)


def _camera() -> Camera:
    now = datetime.now(timezone.utc)
    return Camera(
        id=7,
        factory_id=1,
        name="Factory",
        source_key="factory.mp4",
        created_at=now,
        updated_at=now,
    )


def _physical_zone_model() -> PhysicalZone:
    now = datetime.now(timezone.utc)
    return PhysicalZone(
        id=3,
        factory_id=1,
        name="Restricted Area",
        zone_type="RESTRICTED",
        dwell_threshold_seconds=2,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _camera_zone_view_model() -> CameraZoneView:
    now = datetime.now(timezone.utc)
    return CameraZoneView(
        id=5,
        camera_id=7,
        physical_zone_id=3,
        ui_shape_data={"type": "polygon"},
        normalized_coordinates=[{"x": 0.1, "y": 0.2}],
        is_active=True,
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
    physical_zone_repository = Mock()
    camera_zone_view_repository = Mock()
    camera_repository = Mock()
    factory_repository = Mock()
    camera_repository.get_by_source_key.return_value = _camera()
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    physical_zone_repository.create.return_value = _physical_zone_model()
    camera_zone_view_repository.create.return_value = _camera_zone_view_model()
    service = ZoneService(
        physical_zone_repository,
        camera_zone_view_repository,
        camera_repository,
        factory_repository,
    )

    result = service.create_zone(_zone_schema())

    created_zone = physical_zone_repository.create.call_args.args[0]
    created_view = camera_zone_view_repository.create.call_args.args[0]
    assert created_zone.factory_id == 1
    assert created_zone.name == "Restricted Area"
    assert created_zone.zone_type == "RESTRICTED"
    assert created_view.camera_id == 7
    assert created_view.physical_zone_id == 3
    assert created_view.ui_shape_data == {"type": "polygon"}
    assert created_view.normalized_coordinates == [{"x": 0.1, "y": 0.2}]
    assert result.id == 5
    assert result.video_name == "factory.mp4"
    assert result.zone_name == "Restricted Area"
    assert json.loads(result.flattened_coordinates) == [{"x": 0.1, "y": 0.2}]
    assert not isinstance(result, PhysicalZone)


def test_zone_service_creates_missing_camera():
    physical_zone_repository = Mock()
    camera_zone_view_repository = Mock()
    camera_repository = Mock()
    factory_repository = Mock()
    camera_repository.get_by_source_key.return_value = None
    camera_repository.create.side_effect = lambda camera: camera.model_copy(
        update={"id": 7}
    )
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    physical_zone_repository.create.return_value = _physical_zone_model()
    camera_zone_view_repository.create.return_value = _camera_zone_view_model()
    service = ZoneService(
        physical_zone_repository,
        camera_zone_view_repository,
        camera_repository,
        factory_repository,
    )

    result = service.create_zone(_zone_schema())

    created_camera = camera_repository.create.call_args.args[0]
    assert created_camera.factory_id == 1
    assert created_camera.name == "factory.mp4"
    assert created_camera.source_key == "factory.mp4"
    assert created_camera.source_uri is None
    assert created_camera.is_active is True
    assert result.video_name == "factory.mp4"


def test_zone_service_reads_updates_and_deletes():
    physical_zone_repository = Mock()
    camera_zone_view_repository = Mock()
    camera_repository = Mock()
    physical_zone_repository.get_by_id.return_value = _physical_zone_model()
    physical_zone_repository.update.side_effect = lambda zone: zone
    physical_zone_repository.delete.return_value = True
    camera_zone_view_repository.get_by_id.return_value = _camera_zone_view_model()
    camera_zone_view_repository.get_by_camera.return_value = [_camera_zone_view_model()]
    camera_zone_view_repository.update.side_effect = lambda view: view
    camera_zone_view_repository.delete.return_value = True
    camera_repository.get_by_id.return_value = _camera()
    camera_repository.get_by_source_key.return_value = _camera()
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    service = ZoneService(
        physical_zone_repository,
        camera_zone_view_repository,
        camera_repository,
        factory_repository,
    )

    assert service.get_zone(5).video_name == "factory.mp4"
    assert service.get_zones_by_source_key("factory.mp4")[0].zone_name == (
        "Restricted Area"
    )
    assert service.update_zone(5, _zone_schema()).id == 5
    assert service.delete_zone(5) is True


def test_zone_service_lists_physical_zones_for_default_factory():
    physical_zone_repository = Mock()
    camera_zone_view_repository = Mock()
    camera_repository = Mock()
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    zone = _physical_zone_model()
    zone.zone_type = "AREA"
    physical_zone_repository.get_by_factory.return_value = [zone]
    service = ZoneService(
        physical_zone_repository,
        camera_zone_view_repository,
        camera_repository,
        factory_repository,
    )

    zones = service.list_physical_zones()

    assert zones == [zone]
    physical_zone_repository.get_by_factory.assert_called_once_with(1)

    camera_zone_view_repository.get_by_id.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.delete_zone(99)


def test_zone_service_validation_and_error_propagation():
    physical_zone_repository = Mock()
    camera_zone_view_repository = Mock()
    camera_repository = Mock()
    camera_repository.get_by_source_key.return_value = _camera()
    factory_repository = Mock()
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    service = ZoneService(
        physical_zone_repository,
        camera_zone_view_repository,
        camera_repository,
        factory_repository,
    )
    invalid = _zone_schema().model_copy(update={"ui_shape_data": "[]"})

    with pytest.raises(ServiceValidationError):
        service.create_zone(invalid)

    camera_repository.get_by_source_key.side_effect = RepositoryError(
        "database failed"
    )
    with pytest.raises(RepositoryError, match="database failed"):
        service.get_zones_by_source_key("factory.mp4")


def test_record_zone_violation_passes_zone_name_to_storage(
    monkeypatch,
    tmp_path,
):
    violation_service = Mock()
    violation_service.persist_zone_violation.return_value = ZoneViolation(
        id=5,
        zone_id=5,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        track_id=42,
        timestamp="2026-06-05T12:00:00+00:00",
        video_name="factory.mp4",
        frame_index=15,
        snapshot_path="http://minio/zone.jpg",
    )

    @contextmanager
    def open_service():
        yield violation_service

    monkeypatch.setattr(
        "app.services.zone_service.open_zone_violation_service",
        open_service,
    )
    monkeypatch.setattr("app.services.zone_service.SNAPSHOT_DIR", tmp_path)
    worker_state = Mock(reported_zones=set())
    zone = ZoneViolationRecord(
        camera_zone_view_id=5,
        physical_zone_id=3,
        zone_name="Restricted Area",
        zone_type="RESTRICTED",
        poly=[],
        threshold=0,
    )
    person = PersonResult(
        person_id=1,
        track_id=42,
        bbox=BoundingBox(x1=10, y1=20, x2=30, y2=80),
        confidence=0.9,
        equipment=[],
        compliant=True,
    )

    snapshot_path = tmp_path / "zone.jpg"
    snapshot_kwargs = {}

    def save_snapshot(**kwargs):
        snapshot_kwargs.update(kwargs)
        snapshot_path.write_bytes(b"image-data")
        return snapshot_path.name

    result = record_zone_violation(
        worker_state=worker_state,
        zone=zone,
        frame=np.zeros((100, 100, 3), dtype=np.uint8),
        person=person,
        video_name="factory.mp4",
        frame_index=15,
        save_snapshot_fn=save_snapshot,
    )

    violation_service.persist_zone_violation.assert_called_once()
    persisted = violation_service.persist_zone_violation.call_args.kwargs
    assert snapshot_kwargs["polygon"] == zone.poly
    assert snapshot_kwargs["zone_type"] == "RESTRICTED"
    assert persisted["camera_zone_view_id"] == 5
    assert persisted["physical_zone_id"] == 3
    assert persisted["zone_name"] == "Restricted Area"
    assert persisted["zone_type"] == "RESTRICTED"
    assert persisted["video_name"] == "factory.mp4"
    assert persisted["track_id"] == 42
    assert persisted["frame_index"] == 15
    assert persisted["local_snapshot_path"].endswith("zone.jpg")
    assert result.zone_name == "Restricted Area"
    assert result.zone_type == "RESTRICTED"
    assert result.track_id == 42
    assert worker_state.reported_zones == {5}
    assert not snapshot_path.exists()
