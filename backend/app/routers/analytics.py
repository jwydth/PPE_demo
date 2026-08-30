from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsRange,
    AnalyticsSummary,
    AnalyticsTrend,
    CompareMode,
)
from app.schemas.incident import IncidentSeverity, UnifiedIncidentRead
from app.services import ServiceValidationError
from app.services.analytics_service import AnalyticsService, get_analytics_service
from app.services.incident_service import (
    UnifiedIncidentService,
    get_unified_incident_service,
)

router = APIRouter(tags=["analytics"])

# These handlers are deliberately `def`, not `async def`.
#
# AnalyticsService does synchronous SQLAlchemy work and then aggregates the
# rows in Python (see its module docstring for why aggregation is in-process).
# Declared `async def`, that ran directly on the asyncio event loop and blocked
# the entire server for the duration — a single /analytics/summary call pushed
# /health from a 4ms median to 1549ms, and every camera WebSocket stalled with
# it. Selecting a zone fires two of these at once, which is what made the Zone
# Pulse feel unresponsive.
#
# FastAPI runs `def` handlers in its threadpool instead, so the blocking work
# happens off the loop. Each request still gets its own Session from
# get_session, so nothing is shared across threads.


@router.get("/analytics/incidents", response_model=list[UnifiedIncidentRead])
def list_unified_incidents(
    service: Annotated[UnifiedIncidentService, Depends(get_unified_incident_service)],
    limit: int = Query(default=30, ge=1, le=500),
    zone_id: int | None = Query(default=None, ge=1),
    camera_id: int | None = Query(default=None, ge=1),
    severity: IncidentSeverity | None = Query(default=None),
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
        for i in service.list_incidents(
            zone_id=zone_id,
            camera_id=camera_id,
            severity=severity,
            limit=limit,
            # A feed read is truncated by design — it asks for the newest
            # `limit` rows and nothing aggregates over them, so the service's
            # undercount warnings would be pure noise on every poll.
            warn_on_truncation=False,
        )
    ]


@router.get("/analytics/summary", response_model=AnalyticsSummary)
def get_analytics_summary(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsSummary:
    try:
        return service.get_summary(range_=range, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/trend", response_model=AnalyticsTrend)
def get_analytics_trend(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsTrend:
    try:
        return service.get_trend(range_=range, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/compare", response_model=AnalyticsCompare)
def get_analytics_compare(
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    mode: CompareMode = Query(default="week"),
    zone_id: int | None = Query(default=None, ge=1),
) -> AnalyticsCompare:
    try:
        return service.get_compare(mode=mode, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
