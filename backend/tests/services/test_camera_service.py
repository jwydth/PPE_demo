from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.camera import Camera
from app.models.factory import Factory
from app.models.physical_zone import PhysicalZone
from app.repositories import RepositoryError
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_service import CameraService


def _camera() -> Camera:
    now = datetime.now(timezone.utc)
    return Camera(
        id=1,
        factory_id=1,
        name="Warehouse",
        source_key="warehouse.mp4",
        source_uri=None,
        home_zone_id=None,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def test_camera_service_creates_dto_and_coordinates_repository():
    repository = Mock()
    factory_repository = Mock()
    repository.get_by_source_key.return_value = None
    repository.create.side_effect = lambda camera: _persist_camera(camera)
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    service = CameraService(repository, factory_repository, Mock())

    result = service.create_camera(
        name=" Warehouse ",
        source_key=" warehouse.mp4 ",
    )

    persisted = repository.create.call_args.args[0]
    assert persisted.factory_id == 1
    assert persisted.name == "Warehouse"
    assert persisted.source_key == "warehouse.mp4"
    assert result.id == 1
    assert result.source_key == "warehouse.mp4"
    assert not isinstance(result, Camera)


def test_camera_service_get_deactivate_and_validation():
    repository = Mock()
    repository.get_by_id.return_value = _camera()
    repository.get_by_source_key.return_value = _camera()
    repository.deactivate.return_value = _camera().model_copy(
        update={"is_active": False}
    )
    service = CameraService(repository, Mock(), Mock())

    assert service.get_camera(1).name == "Warehouse"
    assert service.get_camera_by_source_key("warehouse.mp4").id == 1
    assert service.deactivate_camera(1).is_active is False

    repository.get_by_id.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.get_camera(2)
    with pytest.raises(ServiceValidationError):
        service.get_camera(0)


def test_camera_service_propagates_repository_errors():
    repository = Mock()
    repository.get_by_id.side_effect = RepositoryError("database failed")

    with pytest.raises(RepositoryError, match="database failed"):
        CameraService(repository, Mock(), Mock()).get_camera(1)


def test_camera_service_list_cameras():
    repository = Mock()
    repository.list_all.return_value = [_camera()]

    result = CameraService(repository, Mock(), Mock()).list_cameras()

    assert len(result) == 1
    assert result[0].id == 1


def test_camera_service_set_home_zone_assigns_and_clears():
    repository = Mock()
    zone_repository = Mock()
    zone_repository.get_by_id.return_value = PhysicalZone(
        id=2, factory_id=1, name="Warehouse Intake", zone_type="WALKWAY"
    )
    repository.set_home_zone.return_value = _camera().model_copy(
        update={"home_zone_id": 2}
    )
    service = CameraService(repository, Mock(), zone_repository)

    result = service.set_home_zone(1, 2)

    assert result.home_zone_id == 2
    repository.set_home_zone.assert_called_once_with(1, 2)

    # Clearing (zone_id=None) should not touch the zone repository.
    repository.set_home_zone.return_value = _camera()
    zone_repository.get_by_id.reset_mock()
    cleared = service.set_home_zone(1, None)
    assert cleared.home_zone_id is None
    zone_repository.get_by_id.assert_not_called()


def test_camera_service_set_home_zone_validation_and_not_found():
    repository = Mock()
    zone_repository = Mock()
    service = CameraService(repository, Mock(), zone_repository)

    with pytest.raises(ServiceValidationError):
        service.set_home_zone(1, 0)

    zone_repository.get_by_id.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.set_home_zone(1, 999)

    zone_repository.get_by_id.return_value = PhysicalZone(
        id=2, factory_id=1, name="Warehouse Intake", zone_type="WALKWAY"
    )
    repository.set_home_zone.return_value = None
    with pytest.raises(ServiceNotFoundError):
        service.set_home_zone(999, 2)


def test_camera_service_get_or_create_camera_returns_existing():
    repository = Mock()
    repository.get_by_source_key.return_value = _camera()
    service = CameraService(repository, Mock(), Mock())

    result = service.get_or_create_camera(name="Ignored", source_key="warehouse.mp4")

    assert result.id == 1
    repository.create.assert_not_called()


def test_camera_service_get_or_create_camera_creates_when_missing():
    repository = Mock()
    factory_repository = Mock()
    repository.get_by_source_key.return_value = None
    repository.create.side_effect = lambda camera: _persist_camera(camera)
    factory_repository.get_or_create_default_factory.return_value = Factory(
        id=1,
        name="Default Factory",
    )
    service = CameraService(repository, factory_repository, Mock())

    result = service.get_or_create_camera(name="Loading Bay", source_key="loading-bay.mp4")

    persisted = repository.create.call_args.args[0]
    assert persisted.source_key == "loading-bay.mp4"
    assert result.id == 1


def test_camera_service_delete_camera():
    repository = Mock()
    repository.delete.return_value = True
    service = CameraService(repository, Mock(), Mock())

    service.delete_camera(1)
    repository.delete.assert_called_once_with(1)

    repository.delete.return_value = False
    with pytest.raises(ServiceNotFoundError):
        service.delete_camera(999)


def test_camera_service_get_or_create_camera_updates_name_if_different():
    repository = Mock()
    existing_camera = _camera()
    repository.get_by_source_key.return_value = existing_camera
    repository.update.side_effect = lambda camera: camera
    service = CameraService(repository, Mock(), Mock())

    result = service.get_or_create_camera(name="New Name", source_key="warehouse.mp4")

    assert existing_camera.name == "New Name"
    assert result.name == "New Name"
    repository.update.assert_called_once_with(existing_camera)


def _persist_camera(camera: Camera) -> Camera:
    camera.id = 1
    return camera
