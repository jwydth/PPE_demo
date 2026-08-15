"""Assembles the payload the PDF renderer and email templates read from.

Reads through AnalyticsService / UnifiedIncidentService rather than
re-deriving aggregation or normalization here — see the package docstring
for the import-boundary rule this must respect.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends
from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.schemas.analytics import AnalyticsCompare, AnalyticsSummary, AnalyticsTrend
from app.services.analytics_service import (
    AnalyticsService,
    get_analytics_service,
    range_to_dates,
)
from app.services.incident_service import (
    UnifiedIncidentService,
    get_unified_incident_service,
)
from app.services.reporting.i18n import normalize_language, t
from app.services.reporting.insights import build_caveats, build_insights
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
# PPE is kept untranslated in both languages — it's a widely-recognized
# acronym in Vietnamese HSE/industrial contexts (see plan §0.2 scope note).
_CATEGORY_LABELS = {
    "en": {"ppe": "PPE", "zone": "Zone", "behavior": "Behavior"},
    "vi": {"ppe": "PPE", "zone": "Khu Vực", "behavior": "Hành Vi"},
}
_RANGE_LABELS = {
    "en": {"24H": "Last 24 hours", "7D": "Last 7 days", "30D": "Last 30 days"},
    "vi": {"24H": "24 giờ qua", "7D": "7 ngày qua", "30D": "30 ngày qua"},
}


@dataclass(frozen=True)
class ReportIncidentRow:
    timestamp_local: str      # already formatted in REPORT_TIMEZONE
    category: str              # "PPE" | "Zone" | "Behavior"
    type: str
    severity: str
    zone_name: str
    camera_label: str
    snapshot_url: str | None


@dataclass(frozen=True)
class ReportData:
    company_name: str
    factory_name: str
    factory_location: str | None
    range_label: str                  # "Last 7 days (22 Jul - 29 Jul 2026)"
    range_param: str                  # "24H" | "7D" | "30D"
    zone_id: int | None               # None = report is site-wide (all zones) — no default,
                                       # must stay in this required-fields block, not below
    zone_scope_label: str             # "All zones" | "Production Floor"
    generated_at_local: str
    timezone_label: str               # "UTC+07:00 (Asia/Ho_Chi_Minh)"
    summary: AnalyticsSummary
    trend: AnalyticsTrend
    compare: AnalyticsCompare
    top_incidents: list[ReportIncidentRow] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    data_caveats: list[str] = field(default_factory=list)
    language: str = "en"              # "en" | "vi" — see i18n.py. Must stay LAST: a defaulted
                                       # field before a non-default one is a TypeError at import.


def to_local(ts: datetime, tz: ZoneInfo) -> datetime:
    return ts.astimezone(tz)


def _format_datetime(dt: datetime, language: str) -> str:
    """dd/mm/yyyy, HH:MM for Vietnamese; dd Mon yyyy, HH:MM for English.
    Numeric for `vi` deliberately — see plan §1 D3 (avoids locale.setlocale,
    which is process-global and unsafe in an async web server)."""
    if language == "vi":
        return dt.strftime("%d/%m/%Y, %H:%M")
    return dt.strftime("%d %b %Y, %H:%M")


def format_delta_label(compare: AnalyticsCompare) -> str:
    """"n/a" when there's no prior-period baseline — AnalyticsService returns
    delta_pct=0.0 in that case, which reads as "no change" and is misleading
    (see plan §3.4); callers must not render the raw delta_pct directly."""
    if compare.prior_total == 0:
        return "n/a"
    sign = "+" if compare.delta_pct >= 0 else ""
    return f"{sign}{compare.delta_pct:.1f}%"


class ReportDataBuilder:
    def __init__(
        self,
        analytics_service: Annotated[AnalyticsService, Depends(get_analytics_service)],
        incident_service: Annotated[
            UnifiedIncidentService, Depends(get_unified_incident_service)
        ],
        factory_repository: Annotated[FactoryRepository, Depends(FactoryRepository)],
        physical_zone_repository: Annotated[
            PhysicalZoneRepository, Depends(PhysicalZoneRepository)
        ],
    ) -> None:
        self.analytics_service = analytics_service
        self.incident_service = incident_service
        self.factory_repository = factory_repository
        self.physical_zone_repository = physical_zone_repository

    def build(self, *, range_: str, zone_id: int | None, language: str = "en") -> ReportData:
        language = normalize_language(language)
        tz, tz_fallback = _resolve_timezone(settings.REPORT_TIMEZONE)

        summary = self.analytics_service.get_summary(range_=range_, zone_id=zone_id)
        trend = self.analytics_service.get_trend(range_=range_, zone_id=zone_id)
        compare_mode = "week" if range_ in ("24H", "7D") else "month"
        compare = self.analytics_service.get_compare(mode=compare_mode, zone_id=zone_id)

        date_from, date_to = range_to_dates(range_)
        # Reuses AnalyticsService's per-request range cache — get_summary()
        # and get_trend() above already fetched this exact (date_from, date_to)
        # window, so this is a cache hit rather than a 4th/5th full incident
        # query. sorted() (not .sort()) because the returned list is shared —
        # mutating it in place would reorder it for any other cache reader.
        incidents = sorted(
            self.analytics_service.get_incidents(
                date_from=date_from, date_to=date_to, zone_id=zone_id
            ),
            key=lambda i: (_SEVERITY_RANK.get(i.severity, 4), -i.timestamp.timestamp()),
        )
        top_incidents = [
            ReportIncidentRow(
                timestamp_local=_format_datetime(to_local(i.timestamp, tz), language),
                category=_CATEGORY_LABELS[language].get(i.category, i.category),
                type=i.type,
                severity=i.severity,
                zone_name=i.zone_name,
                camera_label=i.camera_label,
                snapshot_url=i.snapshot_url,
            )
            for i in incidents[: settings.REPORT_MAX_INCIDENT_ROWS]
        ]

        factory = self.factory_repository.get_or_create_default_factory()
        zone_scope_label = _zone_scope_label(summary, zone_id, language)

        now_local = to_local(_utc_now_tz(), tz)
        generated_at_local = _format_datetime(now_local, language)
        timezone_label = _timezone_label(tz, tz_fallback)

        insights = build_insights(summary, trend, compare, language)
        data_caveats = build_caveats(
            summary=summary,
            zone_id=zone_id,
            zone_scope_label=zone_scope_label,
            tz_fallback=tz_fallback,
            language=language,
        )

        return ReportData(
            company_name=settings.REPORT_COMPANY_NAME,
            factory_name=factory.name,
            factory_location=factory.location,
            range_label=_range_label(range_, date_from, date_to, tz, language),
            range_param=range_,
            zone_id=zone_id,
            zone_scope_label=zone_scope_label,
            generated_at_local=generated_at_local,
            timezone_label=timezone_label,
            summary=summary,
            trend=trend,
            compare=compare,
            top_incidents=top_incidents,
            insights=insights,
            data_caveats=data_caveats,
            language=language,
        )


def _utc_now_tz() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_timezone(tz_name: str) -> tuple[ZoneInfo, bool]:
    try:
        return ZoneInfo(tz_name), False
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC"), True


def _timezone_label(tz: ZoneInfo, tz_fallback: bool) -> str:
    if tz_fallback:
        return "UTC"
    offset = datetime.now(tz).utcoffset()
    total_minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d} ({tz.key})"


def _zone_scope_label(summary: AnalyticsSummary, zone_id: int | None, language: str) -> str:
    if zone_id is None:
        return t("all_zones", language)
    for zone_total in summary.zone_totals:
        if zone_total.zone_id == zone_id:
            return zone_total.zone_name
    zone_word = "Khu vực" if language == "vi" else "Zone"
    return f"{zone_word} #{zone_id}"


def _range_label(
    range_: str, date_from: datetime, date_to: datetime, tz: ZoneInfo, language: str
) -> str:
    local_from = to_local(date_from, tz)
    local_to = to_local(date_to, tz)
    label = _RANGE_LABELS[language].get(range_, range_)
    day_fmt, day_time_fmt, day_month_year_fmt = (
        ("%d/%m", "%d/%m %H:%M", "%d/%m/%Y") if language == "vi" else ("%d %b", "%d %b %H:%M", "%d %b %Y")
    )
    if range_ == "24H":
        return f"{label} ({local_from.strftime(day_time_fmt)} - {local_to.strftime(day_time_fmt)})"
    return f"{label} ({local_from.strftime(day_fmt)} - {local_to.strftime(day_month_year_fmt)})"


def get_report_data_builder(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> ReportDataBuilder:
    analytics_service = get_analytics_service(session, storage)
    incident_service = get_unified_incident_service(session, storage)
    return ReportDataBuilder(
        analytics_service,
        incident_service,
        FactoryRepository(session),
        PhysicalZoneRepository(session),
    )
