import pytest

from app.models.camera import Camera
from app.models.camera_zone_view import CameraZoneView
from app.models.factory import Factory
from app.models.physical_zone import PhysicalZone
from app.repositories import RepositoryError
from app.repositories.camera_repository import CameraRepository
from app.repositories.camera_zone_view_repository import CameraZoneViewRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository


def _factory_id(session, name: str = "Default Factory") -> int:
    repository = FactoryRepository(session)
    factory = repository.get_by_name(name)
    if factory is None:
        factory = repository.create(Factory(name=name))
    assert factory.id is not None
    return factory.id


def _camera(session, factory_id: int, source_key: str = "factory.mp4") -> Camera:
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory_id,
            name=source_key,
            source_key=source_key,
        )
    )
    assert camera.id is not None
    return camera


def _physical_zone(
    session,
    factory_id: int,
    name: str = "Restricted Area",
) -> PhysicalZone:
    zone = PhysicalZoneRepository(session).create(
        PhysicalZone(
            factory_id=factory_id,
            name=name,
            zone_type="RESTRICTED",
            dwell_threshold_seconds=2,
        )
    )
    assert zone.id is not None
    return zone


def _view(camera_id: int, physical_zone_id: int) -> CameraZoneView:
    return CameraZoneView(
        camera_id=camera_id,
        physical_zone_id=physical_zone_id,
        ui_shape_data={"type": "polygon"},
        normalized_coordinates=[
            {"x": 0.1, "y": 0.1},
            {"x": 0.9, "y": 0.1},
            {"x": 0.9, "y": 0.9},
        ],
    )


def test_camera_zone_view_repository_create_read_update_and_deactivate(session):
    factory_id = _factory_id(session)
    camera = _camera(session, factory_id)
    zone = _physical_zone(session, factory_id)
    assert camera.id is not None
    assert zone.id is not None
    repository = CameraZoneViewRepository(session)

    view = repository.create(_view(camera.id, zone.id))

    assert view.id is not None
    assert repository.get_by_id(view.id) == view
    assert repository.get_by_camera(camera.id) == [view]
    assert repository.get_by_physical_zone(zone.id) == [view]
    assert repository.get_by_camera_and_physical_zone(camera.id, zone.id) == view
    assert repository.get_active_by_camera_source_key("factory.mp4") == [view]

    view.ui_shape_data = {"type": "polygon", "label": "updated"}
    updated = repository.update(view)
    assert updated.ui_shape_data["label"] == "updated"

    deactivated = repository.deactivate(view.id)
    assert deactivated is not None
    assert deactivated.is_active is False
    assert repository.get_active_by_camera_source_key("factory.mp4") == []
    assert repository.deactivate(999_999) is None


def test_camera_zone_view_repository_source_key_and_delete_helpers(session):
    factory_id = _factory_id(session)
    camera = _camera(session, factory_id)
    first_zone = _physical_zone(session, factory_id, "First Area")
    second_zone = _physical_zone(session, factory_id, "Second Area")
    assert camera.id is not None
    assert first_zone.id is not None
    assert second_zone.id is not None
    repository = CameraZoneViewRepository(session)

    first = repository.create(_view(camera.id, first_zone.id))
    second = repository.create(_view(camera.id, second_zone.id))

    assert repository.deactivate_by_camera_source_key("factory.mp4") == 2
    assert repository.get_active_by_camera_source_key("factory.mp4") == []
    first.is_active = True
    second.is_active = True
    repository.update(first)
    repository.update(second)

    assert repository.delete_by_camera_source_key("missing.mp4") == 0
    assert repository.delete_by_camera_source_key("factory.mp4") == 2
    assert repository.get_by_camera(camera.id) == []


def test_camera_zone_view_repository_rolls_back_failed_create(session):
    factory_id = _factory_id(session)
    camera = _camera(session, factory_id)
    first_zone = _physical_zone(session, factory_id, "First Area")
    second_zone = _physical_zone(session, factory_id, "Second Area")
    assert camera.id is not None
    assert first_zone.id is not None
    assert second_zone.id is not None
    repository = CameraZoneViewRepository(session)

    repository.create(_view(camera.id, first_zone.id))
    with pytest.raises(RepositoryError):
        repository.create(_view(camera.id, first_zone.id))

    created = repository.create(_view(camera.id, second_zone.id))
    assert created.id is not None
