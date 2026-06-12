import pytest

from app.models.camera import Camera
from app.repositories import RepositoryError
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository


def _factory_id(session) -> int:
    factory = FactoryRepository(session).get_or_create_default_factory()
    assert factory.id is not None
    return factory.id


def test_camera_repository_create_read_update_and_deactivate(session):
    repository = CameraRepository(session)
    factory_id = _factory_id(session)
    camera = repository.create(
        Camera(
            factory_id=factory_id,
            name="Loading Bay",
            source_key="loading-bay.mp4",
            source_uri="rtsp://example/loading-bay",
        )
    )

    assert camera.id is not None
    assert repository.get_by_id(camera.id) == camera
    assert repository.get_by_source_key("loading-bay.mp4") == camera
    assert repository.list_all() == [camera]

    camera.name = "Loading Bay Camera"
    updated = repository.update(camera)
    assert updated.name == "Loading Bay Camera"

    deactivated = repository.deactivate(camera.id)
    assert deactivated is not None
    assert deactivated.is_active is False
    assert repository.deactivate(999_999) is None


def test_camera_repository_rolls_back_failed_create(session):
    repository = CameraRepository(session)
    factory_id = _factory_id(session)
    repository.create(
        Camera(
            factory_id=factory_id,
            name="Camera 1",
            source_key="duplicate-source",
        )
    )

    with pytest.raises(RepositoryError):
        repository.create(
            Camera(
                factory_id=factory_id,
                name="Camera 2",
                source_key="duplicate-source",
            )
        )

    created = repository.create(
        Camera(
            factory_id=factory_id,
            name="Camera 3",
            source_key="valid-source",
        )
    )
    assert created.id is not None
