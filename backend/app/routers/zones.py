from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.violation import ZoneViolation
from app.schemas.zone import CameraCalibration, Zone
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.camera_service import CameraService, get_camera_service
from app.services.zone_service import ZoneService, get_zone_service
from app.services.violation_store import (
    list_zone_violations,
)

router = APIRouter(tags=["zones"])


@router.post("/zones", response_model=Zone)
async def create_zone(
    zone: Zone,
    service: Annotated[ZoneService, Depends(get_zone_service)],
) -> Zone:
    try:
        return service.create_zone(zone)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/zones/{video_name}", response_model=list[Zone])
async def get_zones(
    video_name: str,
    service: Annotated[ZoneService, Depends(get_zone_service)],
) -> list[Zone]:
    return service.get_zones_by_source_key(video_name)


@router.put("/zones/{zone_id}", response_model=Zone)
async def modify_zone(
    zone_id: int,
    zone: Zone,
    service: Annotated[ZoneService, Depends(get_zone_service)],
) -> Zone:
    try:
        return service.update_zone(zone_id, zone)
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/zones/video/{video_name}")
async def remove_zones_for_video(
    video_name: str,
    service: Annotated[ZoneService, Depends(get_zone_service)],
) -> dict[str, str | int]:
    deleted = service.delete_zones_by_source_key(video_name)
    return {"status": "success", "deleted": deleted}


@router.delete("/zones/{zone_id}")
async def remove_zone(
    zone_id: int,
    service: Annotated[ZoneService, Depends(get_zone_service)],
) -> dict[str, str]:
    try:
        service.delete_zone(zone_id)
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success"}


@router.get("/calibration/{video_name}", response_model=CameraCalibration)
async def fetch_calibration(
    video_name: str,
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> CameraCalibration:
    try:
        return service.get_calibration(video_name)
    except ServiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/calibration", response_model=CameraCalibration)
async def update_calibration(
    calibration: CameraCalibration,
    service: Annotated[CameraService, Depends(get_camera_service)],
) -> CameraCalibration:
    try:
        return service.save_calibration(calibration)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/zone-violations", response_model=list[ZoneViolation])
async def get_zone_violations(limit: int = 100) -> list[ZoneViolation]:
    return list_zone_violations(limit=limit)
