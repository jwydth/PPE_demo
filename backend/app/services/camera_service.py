from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_session
from app.models.camera import Camera
from app.repositories.camera_repository import CameraRepository
from app.services import ServiceNotFoundError, ServiceValidationError


@dataclass(frozen=True)
class CameraDTO:
    id: int
    name: str
    source_key: str
    source_uri: str | None
    calibration_source_points: list[Any] | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CameraService:
    def __init__(
        self,
        repository: Annotated[CameraRepository, Depends(CameraRepository)],
    ) -> None:
        self.repository = repository

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
        normalized_source_key = _require_text(source_key, "source_key")
        if self.repository.get_by_source_key(normalized_source_key) is not None:
            raise ServiceValidationError(
                f"Camera source_key '{normalized_source_key}' already exists."
            )

        camera = self.repository.create(
            Camera(
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

    def get_camera_by_source_key(self, source_key: str) -> CameraDTO:
        normalized_source_key = _require_text(source_key, "source_key")
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


def get_camera_service(
    session: Annotated[Session, Depends(get_session)],
) -> CameraService:
    return CameraService(CameraRepository(session))


def _to_dto(camera: Camera) -> CameraDTO:
    if camera.id is None:
        raise ServiceValidationError("Persisted camera is missing an ID.")
    return CameraDTO(
        id=camera.id,
        name=camera.name,
        source_key=camera.source_key,
        source_uri=camera.source_uri,
        calibration_source_points=camera.calibration_source_points,
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
