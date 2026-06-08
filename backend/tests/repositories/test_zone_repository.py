from app.models.camera import Camera
from app.models.zone import Zone
from app.models.zone_violation import ZoneViolation
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.zone_repository import ZoneRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository


def _factory_id(session) -> int:
    factory = FactoryRepository(session).get_or_create_default_factory()
    assert factory.id is not None
    return factory.id


def _zone(camera_id: int) -> Zone:
    return Zone(
        camera_id=camera_id,
        name="Restricted Area",
        zone_type="RESTRICTED",
        dwell_threshold_seconds=2,
        ui_shape_data={"type": "polygon"},
        normalized_coordinates=[
            {"x": 0.1, "y": 0.1},
            {"x": 0.9, "y": 0.1},
            {"x": 0.9, "y": 0.9},
        ],
    )


def test_zone_repository_create_read_update_and_delete(session):
    factory_id = _factory_id(session)
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory_id,
            name="Warehouse",
            source_key="warehouse.mp4",
        )
    )
    assert camera.id is not None
    repository = ZoneRepository(session)

    zone = repository.create(_zone(camera.id))
    assert zone.id is not None
    assert repository.get_by_id(zone.id) == zone
    assert repository.get_by_camera(camera.id) == [zone]
    assert repository.get_by_source_key(camera.source_key) == [zone]
    assert repository.list_all() == [zone]

    zone.name = "Updated Restricted Area"
    updated = repository.update(zone)
    assert updated.name == "Updated Restricted Area"

    violation = ZoneViolationRepository(session).create(
        ZoneViolation(
            zone_id=zone.id,
            zone_name=zone.name,
            source_key=camera.source_key,
            occurred_at=zone.created_at,
            frame_index=10,
        )
    )
    assert repository.delete(zone.id) is True
    session.refresh(violation)
    assert violation.zone_id is None
    assert repository.get_by_id(zone.id) is None
    assert repository.delete(zone.id) is False


def test_zone_repository_deletes_all_zones_for_camera(session):
    factory_id = _factory_id(session)
    camera = CameraRepository(session).create(
        Camera(
            factory_id=factory_id,
            name="Warehouse",
            source_key="warehouse.mp4",
        )
    )
    assert camera.id is not None
    repository = ZoneRepository(session)
    repository.create(_zone(camera.id))
    repository.create(_zone(camera.id))

    assert repository.delete_by_camera(camera.id) == 2
    assert repository.get_by_camera(camera.id) == []
