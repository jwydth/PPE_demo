import time
from collections import deque
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.schemas.analytics import AnalyticsRange
from app.schemas.report import ReportEmailRequest, ReportEmailResponse, ReportPreview
from app.schemas.report_schedule import ReportScheduleRequest, ReportScheduleResponse
from app.services import ServiceValidationError
from app.services.reporting import ReportEmailError, ReportEmailTimeoutError
from app.services.reporting.report_data import ReportDataBuilder, get_report_data_builder
from app.services.reporting.report_service import ReportService, get_report_service
from app.services.reporting.schedule import resolve_timezone, timezone_label
from app.services.reporting.schedule_service import (
    ReportScheduleService,
    ScheduleView,
    get_report_schedule_service,
)
from app.storage import StorageError

router = APIRouter(tags=["reports"])

# Module-level, in-process only — resets on restart and does not survive
# multi-worker uvicorn (each worker keeps its own counter). Adequate for a
# single-process internal deployment; see plan §5.5 item 3.
_send_timestamps: deque[float] = deque()
_RATE_LIMIT_WINDOW_SECONDS = 3600


def _check_rate_limit() -> None:
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    while _send_timestamps and _send_timestamps[0] < window_start:
        _send_timestamps.popleft()
    if len(_send_timestamps) >= settings.REPORT_EMAIL_RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Report email rate limit exceeded. Try again later.",
        )
    _send_timestamps.append(now)


def _content_disposition(filename: str) -> str:
    # `filename` is already ASCII (report_service._slugify strips non-ASCII
    # from the factory name), but a non-ASCII fallback + RFC 5987 form is
    # kept for defense in depth — a raw non-ASCII Content-Disposition header
    # raises UnicodeEncodeError in Starlette.
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "safety-report.pdf"
    return f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename)}'


@router.get("/reports/incidents/preview", response_model=ReportPreview)
async def get_report_preview(
    builder: Annotated[ReportDataBuilder, Depends(get_report_data_builder)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
) -> ReportPreview:
    try:
        data = builder.build(range_=range, zone_id=zone_id)
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReportPreview(
        range=data.range_param,
        zone_scope_label=data.zone_scope_label,
        generated_at_local=data.generated_at_local,
        grand_total=data.summary.grand_total,
        severity_counts=data.summary.severity_counts,
        insights=data.insights,
        data_caveats=data.data_caveats,
    )


@router.get("/reports/incidents.pdf")
async def download_incident_report_pdf(
    service: Annotated[ReportService, Depends(get_report_service)],
    range: AnalyticsRange = Query(default="7D"),
    zone_id: int | None = Query(default=None, ge=1),
    include_snapshots: bool = Query(default=True),
) -> Response:
    # PDF rendering is blocking (ReportLab) — running it directly in this
    # async handler would stall every open camera WebSocket the app holds
    # (see streaming.py). Mandatory per plan §5.3.
    try:
        pdf_bytes, filename = await run_in_threadpool(
            service.build_pdf,
            range_=range,
            zone_id=zone_id,
            include_snapshots=include_snapshots,
        )
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/reports/incidents/email", response_model=ReportEmailResponse)
async def email_incident_report(
    payload: ReportEmailRequest,
    service: Annotated[ReportService, Depends(get_report_service)],
) -> ReportEmailResponse:
    if not settings.REPORT_EMAIL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not enabled on this server.",
        )
    _check_rate_limit()
    try:
        # Blocking (smtplib + ReportLab) — same event-loop concern as the PDF
        # endpoint above.
        result = await run_in_threadpool(
            service.email_report,
            range_=payload.range,
            zone_id=payload.zone_id,
            recipients=[str(recipient) for recipient in payload.recipients],
            subject=payload.subject,
            message=payload.message,
            include_snapshots=payload.include_snapshots,
        )
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReportEmailTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except ReportEmailError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ReportEmailResponse(
        status="sent",
        recipients=result.recipients,
        filename=result.filename,
        size_bytes=result.size_bytes,
        object_key=result.object_key,
        sent_at=result.sent_at,
    )


@router.get("/reports/schedule", response_model=ReportScheduleResponse)
async def get_report_schedule(
    service: Annotated[ReportScheduleService, Depends(get_report_schedule_service)],
) -> ReportScheduleResponse:
    return _schedule_to_response(service.get())


@router.put("/reports/schedule", response_model=ReportScheduleResponse)
async def update_report_schedule(
    payload: ReportScheduleRequest,
    service: Annotated[ReportScheduleService, Depends(get_report_schedule_service)],
) -> ReportScheduleResponse:
    try:
        view = service.update(
            frequency=payload.frequency,
            day_of_week=payload.day_of_week,
            day_of_month=payload.day_of_month,
            hour=payload.hour,
            minute=payload.minute,
            zone_id=payload.zone_id,
            recipients=[str(recipient) for recipient in payload.recipients],
            include_snapshots=payload.include_snapshots,
            subject=payload.subject,
            message=payload.message,
        )
    except ServiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _schedule_to_response(view)


def _schedule_to_response(view: ScheduleView) -> ReportScheduleResponse:
    schedule = view.schedule
    recipients = [r.strip() for r in schedule.recipients.split(",") if r.strip()]
    return ReportScheduleResponse(
        frequency=schedule.frequency,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        hour=schedule.hour,
        minute=schedule.minute,
        zone_id=schedule.zone_id,
        recipients=recipients,
        include_snapshots=schedule.include_snapshots,
        subject=schedule.subject,
        message=schedule.message,
        last_sent_at=schedule.last_sent_at.isoformat() if schedule.last_sent_at else None,
        next_run_at=view.next_run_at.isoformat() if view.next_run_at else None,
        timezone_label=timezone_label(resolve_timezone()),
    )
