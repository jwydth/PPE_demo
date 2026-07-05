from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.violation import ZoneViolation
from app.schemas.zone import Zone
from app.services import ServiceNotFoundError, ServiceValidationError
from app.services.zone_service import ZoneService, get_zone_service
from app.services.zone_violation_service import (
    ZoneViolationService,
    get_zone_violation_service,
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


# video_name is a query param (not a path param) so source keys containing
# slashes — e.g. RTSP URLs like "rtsp://localhost:8554/mystream" — are handled
# correctly. As a path param, the ASGI server decodes %2F back to "/" and the
# extra segments break route matching (404).
@router.get("/zones", response_model=list[Zone])
async def get_zones(
    service: Annotated[ZoneService, Depends(get_zone_service)],
    video_name: str = Query(...),
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


# Defined before /zones/{zone_id} so "/zones/video" never gets matched as a
# zone_id. video_name is a query param for the same slash-handling reason as the
# GET route above.
@router.delete("/zones/video")
async def remove_zones_for_video(
    service: Annotated[ZoneService, Depends(get_zone_service)],
    video_name: str = Query(...),
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


@router.get("/zone-violations", response_model=list[ZoneViolation])
async def get_zone_violations(
    service: Annotated[
        ZoneViolationService,
        Depends(get_zone_violation_service),
    ],
    limit: int = 100,
) -> list[ZoneViolation]:
    return service.get_recent_zone_violations(limit=limit)
