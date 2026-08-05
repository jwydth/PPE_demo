from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_session
from app.models.camera import Camera
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_identity import normalize_camera_source_key


@dataclass(frozen=True)
class CameraDTO:
    id: int
    name: str
    source_key: str
    source_uri: str | None
    calibration_source_points: list[Any] | None
    home_zone_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CameraService:
    def __init__(
        self,
        repository: Annotated[CameraRepository, Depends(CameraRepository)],
        factory_repository: Annotated[
            FactoryRepository,
            Depends(FactoryRepository),
        ],
        physical_zone_repository: Annotated[
            PhysicalZoneRepository,
            Depends(PhysicalZoneRepository),
        ],
    ) -> None:
        self.repository = repository
        self.factory_repository = factory_repository
        self.physical_zone_repository = physical_zone_repository

    def create_camera(
        self,
        *,
        name: str,
        source_key: str,
        source_uri: str | None = None,
        calibration_source_points: list[Any] | None = None,
        is_active: bool = True,
    ) -> CameraDTO:
        normalized_name = _require_text(name, "name")
        normalized_source_key = normalize_camera_source_key(
            _require_text(source_key, "source_key")
        )
        if self.repository.get_by_source_key(normalized_source_key) is not None:
            raise ServiceValidationError(
                f"Camera source_key '{normalized_source_key}' already exists."
            )

        factory_id = self._get_default_factory_id()
        camera = self.repository.create(
            Camera(
                factory_id=factory_id,
                name=normalized_name,
                source_key=normalized_source_key,
                source_uri=_optional_text(source_uri),
                calibration_source_points=calibration_source_points,
                is_active=is_active,
            )
        )
        return _to_dto(camera)

    def get_camera(self, camera_id: int) -> CameraDTO:
        camera = self.repository.get_by_id(_require_positive_id(camera_id, "camera_id"))
        if camera is None:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")
        return _to_dto(camera)

    def list_cameras(self) -> list[CameraDTO]:
        return [_to_dto(camera) for camera in self.repository.list_all()]

    def get_or_create_camera(self, *, name: str, source_key: str) -> CameraDTO:
        normalized_source_key = normalize_camera_source_key(
            _require_text(source_key, "source_key")
        )
        normalized_name = _require_text(name, "name")
        camera = self.repository.get_by_source_key(normalized_source_key)
        if camera is not None:
            if camera.name != normalized_name:
                camera.name = normalized_name
                self.repository.update(camera)
            return _to_dto(camera)

        factory_id = self._get_default_factory_id()
        camera = self.repository.create(
            Camera(
                factory_id=factory_id,
                name=normalized_name,
                source_key=normalized_source_key,
                is_active=True,
            )
        )
        return _to_dto(camera)

    def set_home_zone(self, camera_id: int, zone_id: int | None) -> CameraDTO:
        normalized_id = _require_positive_id(camera_id, "camera_id")
        if zone_id is not None:
            if zone_id <= 0:
                raise ServiceValidationError("zone_id must be greater than zero.")
            if self.physical_zone_repository.get_by_id(zone_id) is None:
                raise ServiceNotFoundError(f"Physical zone {zone_id} was not found.")

        camera = self.repository.set_home_zone(normalized_id, zone_id)
        if camera is None:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")
        return _to_dto(camera)

    def get_camera_by_source_key(self, source_key: str) -> CameraDTO:
        normalized_source_key = normalize_camera_source_key(
            _require_text(source_key, "source_key")
        )
        camera = self.repository.get_by_source_key(normalized_source_key)
        if camera is None:
            raise ServiceNotFoundError(
                f"Camera source_key '{normalized_source_key}' was not found."
            )
        return _to_dto(camera)

    def deactivate_camera(self, camera_id: int) -> CameraDTO:
        normalized_id = _require_positive_id(camera_id, "camera_id")
        camera = self.repository.deactivate(normalized_id)
        if camera is None:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")
        return _to_dto(camera)

    def delete_camera(self, camera_id: int) -> None:
        normalized_id = _require_positive_id(camera_id, "camera_id")
        deleted = self.repository.delete(normalized_id)
        if not deleted:
            raise ServiceNotFoundError(f"Camera {camera_id} was not found.")

    def _get_default_factory_id(self) -> int:
        factory = self.factory_repository.get_or_create_default_factory()
        if factory.id is None:
            raise ServiceValidationError("Default factory is missing an ID.")
        return factory.id


def get_camera_service(
    session: Annotated[Session, Depends(get_session)],
) -> CameraService:
    return CameraService(
        CameraRepository(session),
        FactoryRepository(session),
        PhysicalZoneRepository(session),
    )


def _to_dto(camera: Camera) -> CameraDTO:
    if camera.id is None:
        raise ServiceValidationError("Persisted camera is missing an ID.")
    return CameraDTO(
        id=camera.id,
        name=camera.name,
        source_key=camera.source_key,
        source_uri=camera.source_uri,
        calibration_source_points=camera.calibration_source_points,
        home_zone_id=camera.home_zone_id,
        is_active=camera.is_active,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
    )


def _require_positive_id(value: int, field_name: str) -> int:
    if value <= 0:
        raise ServiceValidationError(f"{field_name} must be greater than zero.")
    return value


def _require_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ServiceValidationError(f"{field_name} must not be empty.")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
