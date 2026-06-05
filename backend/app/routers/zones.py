from fastapi import APIRouter, HTTPException
from app.models.schemas import CameraCalibration, Zone, ZoneViolation
from app.services.violation_store import (
    delete_zone,
    get_calibration,
    list_zone_violations,
    list_zones,
    save_calibration,
    save_zone,
    update_zone,
)

router = APIRouter(tags=["zones"])


@router.post("/zones", response_model=Zone)
async def create_zone(zone: Zone) -> Zone:
    return save_zone(zone)


@router.get("/zones/{video_name}", response_model=list[Zone])
async def get_zones(video_name: str) -> list[Zone]:
    return list_zones(video_name)


@router.put("/zones/{zone_id}", response_model=Zone)
async def modify_zone(zone_id: int, zone: Zone) -> Zone:
    zone.id = zone_id
    try:
        return update_zone(zone)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/zones/{zone_id}")
async def remove_zone(zone_id: int):
    success = delete_zone(zone_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
    return {"status": "success"}


@router.get("/calibration/{video_name}", response_model=CameraCalibration)
async def fetch_calibration(video_name: str) -> CameraCalibration:
    calib = get_calibration(video_name)
    if not calib:
        raise HTTPException(status_code=404, detail="Calibration not found")
    return calib


@router.post("/calibration", response_model=CameraCalibration)
async def update_calibration(calibration: CameraCalibration) -> CameraCalibration:
    return save_calibration(calibration)


@router.get("/zone-violations", response_model=list[ZoneViolation])
async def get_zone_violations(limit: int = 100) -> list[ZoneViolation]:
    return list_zone_violations(limit=limit)
