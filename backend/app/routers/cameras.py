from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.camera import CameraEnsure, CameraRead, HomeZoneUpdate
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_service import CameraDTO, CameraService, get_camera_service

router = APIRouter(tags=["cameras"])


def _to_read(camera: CameraDTO) -> CameraRead:
    return CameraRead(
        id=camera.id,
        name=camera.name,
        source_key=camera.source_key,
        source_uri=camera.source_uri,
        home_zone_id=camera.home_zone_id,
        is_active=camera.is_active,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
    )


@router.get("/cameras", response_model=list[CameraRead])
async def get_cameras(
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> list[CameraRead]:
    return [_to_read(camera) for camera in service.list_cameras()]


@router.post("/cameras", response_model=CameraRead)
async def ensure_camera(
    body: CameraEnsure,
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> CameraRead:
    """Get-or-create a camera by source_key, so the frontend can bind a
    configured stream to a backend camera before any incident has occurred
    on it (incidents also lazily create cameras via the same source_key)."""
    try:
        return _to_read(service.get_or_create_camera(name=body.name, source_key=body.source_key))
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/cameras/{camera_id}/home-zone", response_model=CameraRead)
async def set_camera_home_zone(
    camera_id: int,
    body: HomeZoneUpdate,
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> CameraRead:
    try:
        return _to_read(service.set_home_zone(camera_id, body.zone_id))
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/cameras/{camera_id}", response_model=dict[str, bool])
async def delete_camera(
    camera_id: int,
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> dict[str, bool]:
    try:
        service.delete_camera(camera_id)
        return {"success": True}
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
