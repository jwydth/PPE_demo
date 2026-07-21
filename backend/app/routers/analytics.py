from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsRange,
    AnalyticsSummary,
    AnalyticsTrend,
    CompareMode,
)
from app.schemas.incident import UnifiedIncidentRead
from app.services import ServiceValidationError
from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.incident_service import (
    UnifiedIncidentService,
    get_unified_incident_service,
)

router = APIRouter(tags=["analytics"])


@router.get("/analytics/incidents", response_model=list[UnifiedIncidentRead])
async def list_unified_incidents(
    service: Annotated[UnifiedIncidentService, Depends(get_unified_incident_service)],
    limit: int = Query(default=30, ge=1, le=500),
    zone_id: int | None = Query(default=None, ge=1),
) -> list[UnifiedIncidentRead]:
    """Normalized (category, severity, zone) incident feed — the Live Feed
    panel needs zone/severity fields getSafetyEvents() doesn't carry."""
    return [
        UnifiedIncidentRead(
            id=i.id,
            category=i.category,
            type=i.type,
            severity=i.severity,
            timestamp=i.timestamp.isoformat(),
            camera_id=i.camera_id,
            zone_id=i.zone_id,
            zone_name=i.zone_name,
            camera_label=i.camera_label,
            snapshot_url=i.snapshot_url,
        )
        for i in service.list_incidents(zone_id=zone_id, limit=limit)
    ]


@router.get("/analytics/summary", response_model=AnalyticsSummary)
async def get_analytics_summary(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsSummary:
    try:
        return service.get_summary(range_=range, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/trend", response_model=AnalyticsTrend)
async def get_analytics_trend(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsTrend:
    try:
        return service.get_trend(range_=range, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/compare", response_model=AnalyticsCompare)
async def get_analytics_compare(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    mode: CompareMode = Query(default="week"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsCompare:
    try:
        return service.get_compare(mode=mode, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
