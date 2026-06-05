from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.models.camera import Camera
from app.repositories import RepositoryError
from app.schemas.zone import CameraCalibration
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_service import CameraService


def _camera() -> Camera:
    now = datetime.now(timezone.utc)
    return Camera(
        id=1,
        name="Warehouse",
        source_key="warehouse.mp4",
        source_uri=None,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def test_camera_service_creates_dto_and_coordinates_repository():
    repository = Mock()
    repository.get_by_source_key.return_value = None
    repository.create.side_effect = lambda camera: _persist_camera(camera)
    service = CameraService(repository)

    result = service.create_camera(
        name=" Warehouse ",
        source_key=" warehouse.mp4 ",
    )

    persisted = repository.create.call_args.args[0]
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
    service = CameraService(repository)

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
        CameraService(repository).get_camera(1)


def test_camera_service_saves_and_reads_calibration():
    repository = Mock()
    repository.get_by_source_key.side_effect = [None, _camera().model_copy(
        update={
            "calibration_source_points": [
                [0.1, 0.2],
                [0.8, 0.2],
                [0.8, 0.9],
                [0.1, 0.9],
            ]
        }
    )]
    repository.create.side_effect = lambda camera: _persist_camera(camera)
    service = CameraService(repository)
    calibration = CameraCalibration(
        video_name="warehouse.mp4",
        source_points="[[0.1,0.2],[0.8,0.2],[0.8,0.9],[0.1,0.9]]",
    )

    saved = service.save_calibration(calibration)
    loaded = service.get_calibration("warehouse.mp4")

    created = repository.create.call_args.args[0]
    assert created.source_key == "warehouse.mp4"
    assert created.calibration_source_points == [
        [0.1, 0.2],
        [0.8, 0.2],
        [0.8, 0.9],
        [0.1, 0.9],
    ]
    assert saved == calibration
    assert loaded == calibration


def _persist_camera(camera: Camera) -> Camera:
    camera.id = 1
    return camera
