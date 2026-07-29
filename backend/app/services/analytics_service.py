"""Aggregation endpoints read through UnifiedIncidentService (Phase 2), so
severity/type normalization is never re-derived here — this module only
buckets and counts already-normalized UnifiedIncident rows.

Aggregation happens in Python (fetch once per call, bucket/group in memory)
rather than in SQL, per the plan's documented tradeoff — see
UnifiedIncidentService's module docstring for why, and its `_validate_limit`
for the resulting scale cap (settings.ANALYTICS_LIMIT). Past that cap, rows
are dropped and list_incidents logs a warning rather than returning
silently-wrong aggregates — raise the setting if real incident volume
approaches it.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.repositories.camera_repository import CameraRepository
from app.repositories.factory_repository import FactoryRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.schemas.analytics import (
    AnalyticsCompare,
    AnalyticsSummary,
    AnalyticsTrend,
    ComparePoint,
    SeverityCounts,
    SeverityDelta,
    TrendPoint,
    TypeCount,
    ZoneTotal,
)
from app.services import ServiceValidationError
from app.services.incident_service import (
    UnifiedIncident,
    UnifiedIncidentService,
    get_unified_incident_service,
)
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

_ANALYTICS_LIMIT = settings.ANALYTICS_LIMIT
# A zone counts as "active" if it recorded an incident within this window.
_ACTIVE_ZONE_WINDOW = timedelta(minutes=15)

_SEVERITIES = ("Critical", "High", "Medium", "Low")


class AnalyticsService:
    def __init__(
        self,
        incident_service: Annotated[
            UnifiedIncidentService, Depends(get_unified_incident_service)
        ],
        physical_zone_repository: Annotated[
            PhysicalZoneRepository, Depends(PhysicalZoneRepository)
        ],
        camera_repository: Annotated[CameraRepository, Depends(CameraRepository)],
        factory_repository: Annotated[FactoryRepository, Depends(FactoryRepository)],
    ) -> None:
        self.incident_service = incident_service
        self.physical_zone_repository = physical_zone_repository
        self.camera_repository = camera_repository
        self.factory_repository = factory_repository
        # Per-instance only — a fresh AnalyticsService is built per request
        # (see get_analytics_service), so this never serves stale data across
        # requests. get_summary/get_trend/get_compare/get_incidents can all
        # be called with overlapping (date_from, date_to) pairs for the same
        # report; without this cache each one re-runs the same 3-table
        # incident query.
        self._range_cache: dict[tuple[datetime, datetime], list[UnifiedIncident]] = {}

    def get_summary(self, *, range_: str, zone_id: int | None) -> AnalyticsSummary:
        date_from, date_to = range_to_dates(range_)
        all_incidents = self._fetch(date_from, date_to)
        filtered = _filter_by_zone(all_incidents, zone_id)

        now = datetime.now(timezone.utc)
        active_zone_ids = sorted(
            {i.zone_id for i in all_incidents if now - i.timestamp <= _ACTIVE_ZONE_WINDOW},
            key=lambda z: (z is None, z),
        )

        cameras = self.camera_repository.list_all()
        total_cameras = sum(1 for c in cameras if c.is_active)
        active_camera_ids = {i.camera_id for i in filtered if i.camera_id is not None}

        severity_counts = _count_severities(filtered)
        open_incidents = severity_counts.Critical + severity_counts.High

        return AnalyticsSummary(
            range=range_,
            zone_id=zone_id,
            grand_total=len(filtered),
            zone_totals=self._zone_totals(all_incidents),
            severity_counts=severity_counts,
            type_counts=_count_types(filtered),
            active_zone_ids=active_zone_ids,
            open_incidents=open_incidents,
            active_cameras=len(active_camera_ids),
            total_cameras=total_cameras,
        )

    def get_trend(self, *, range_: str, zone_id: int | None) -> AnalyticsTrend:
        date_from, date_to = range_to_dates(range_)
        bucket_width, bucket, num_buckets = _bucket_config(range_)
        incidents = _filter_by_zone(self._fetch(date_from, date_to), zone_id)

        bucket_starts = [date_from + bucket_width * i for i in range(num_buckets)]
        points = [
            TrendPoint(date=_bucket_label(start, bucket), zone_totals={})
            for start in bucket_starts
        ]
        for incident in incidents:
            idx = _bucket_index(incident.timestamp, date_from, bucket_width, num_buckets)
            key = _zone_key(incident.zone_id)
            points[idx].zone_totals[key] = points[idx].zone_totals.get(key, 0) + 1

        return AnalyticsTrend(
            range=range_,
            bucket=bucket,
            zones=self._zone_totals(incidents),
            points=points,
        )

    def get_compare(self, *, mode: str, zone_id: int | None) -> AnalyticsCompare:
        period = timedelta(days=7) if mode == "week" else timedelta(days=30)
        if mode not in ("week", "month"):
            raise ServiceValidationError("mode must be 'week' or 'month'.")

        now = datetime.now(timezone.utc)
        current_from, current_to = now - period, now
        prior_from, prior_to = now - 2 * period, now - period

        current = _filter_by_zone(self._fetch(current_from, current_to), zone_id)
        prior = _filter_by_zone(self._fetch(prior_from, prior_to), zone_id)

        current_total, prior_total = len(current), len(prior)
        delta_pct = (
            round(((current_total - prior_total) / prior_total) * 1000) / 10
            if prior_total
            else 0.0
        )

        num_days = period.days
        current_daily = _daily_counts(current, current_from, num_days)
        prior_daily = _daily_counts(prior, prior_from, num_days)
        points = [
            ComparePoint(label=f"Day {i + 1}", current=current_daily[i], prior=prior_daily[i])
            for i in range(num_days)
        ]

        current_sev = _count_severities(current)
        prior_sev = _count_severities(prior)
        severity_breakdown = [
            SeverityDelta(
                severity=sev,
                prior=getattr(prior_sev, sev),
                current=getattr(current_sev, sev),
            )
            for sev in _SEVERITIES
        ]

        return AnalyticsCompare(
            mode=mode,
            zone_id=zone_id,
            current_total=current_total,
            prior_total=prior_total,
            delta_pct=delta_pct,
            points=points,
            severity_breakdown=severity_breakdown,
        )

    def _fetch(self, date_from: datetime, date_to: datetime) -> list[UnifiedIncident]:
        key = (date_from, date_to)
        cached = self._range_cache.get(key)
        if cached is None:
            cached = self.incident_service.list_incidents(
                date_from=date_from, date_to=date_to, limit=_ANALYTICS_LIMIT
            )
            self._range_cache[key] = cached
        return cached

    def get_incidents(
        self, *, date_from: datetime, date_to: datetime, zone_id: int | None = None
    ) -> list[UnifiedIncident]:
        """Same underlying fetch as get_summary/get_trend for this range —
        callers must not mutate the returned list (it's shared/cached)."""
        return _filter_by_zone(self._fetch(date_from, date_to), zone_id)

    def _zone_totals(self, incidents: list[UnifiedIncident]) -> list[ZoneTotal]:
        factory = self.factory_repository.get_or_create_default_factory()
        catalog = (
            self.physical_zone_repository.get_by_factory(factory.id)
            if factory.id is not None
            else []
        )
        counts: dict[int | None, int] = defaultdict(int)
        by_zone: dict[int | None, list[UnifiedIncident]] = defaultdict(list)
        for incident in incidents:
            counts[incident.zone_id] += 1
            by_zone[incident.zone_id].append(incident)

        totals = [
            ZoneTotal(
                zone_id=zone.id,
                zone_name=zone.name,
                total=counts.get(zone.id, 0),
                severity_counts=_count_severities(by_zone.get(zone.id, [])),
            )
            for zone in catalog
            if zone.zone_type == "AREA"
        ]
        unassigned_total = counts.get(None, 0)
        if unassigned_total > 0:
            totals.append(
                ZoneTotal(
                    zone_id=None,
                    zone_name="Unassigned",
                    total=unassigned_total,
                    severity_counts=_count_severities(by_zone.get(None, [])),
                )
            )
        totals.sort(key=lambda z: z.total, reverse=True)
        return totals


def _filter_by_zone(
    incidents: list[UnifiedIncident], zone_id: int | None
) -> list[UnifiedIncident]:
    if zone_id is None:
        return incidents
    return [i for i in incidents if i.zone_id == zone_id]


def _count_severities(incidents: list[UnifiedIncident]) -> SeverityCounts:
    counts = {sev: 0 for sev in _SEVERITIES}
    for incident in incidents:
        if incident.severity in counts:
            counts[incident.severity] += 1
    return SeverityCounts(**counts)


def _count_types(incidents: list[UnifiedIncident]) -> list[TypeCount]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for incident in incidents:
        counts[(incident.category, incident.type)] += 1
    rows = [
        TypeCount(category=category, type=type_, count=count)
        for (category, type_), count in counts.items()
    ]
    rows.sort(key=lambda r: r.count, reverse=True)
    return rows


def _daily_counts(incidents: list[UnifiedIncident], start: datetime, num_days: int) -> list[int]:
    counts = [0] * num_days
    for incident in incidents:
        day_index = (incident.timestamp - start).days
        if 0 <= day_index < num_days:
            counts[day_index] += 1
    return counts


def _zone_key(zone_id: int | None) -> str:
    return "unassigned" if zone_id is None else str(zone_id)


def range_to_dates(range_: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    delta, _bucket, _n = _bucket_config(range_)
    return now - delta * _n, now


def _bucket_config(range_: str) -> tuple[timedelta, str, int]:
    if range_ == "24H":
        return timedelta(hours=1), "hour", 24
    if range_ == "7D":
        return timedelta(days=1), "day", 7
    if range_ == "30D":
        return timedelta(days=1), "day", 30
    raise ServiceValidationError(f"Unsupported range '{range_}'. Use 24H, 7D, or 30D.")


def _bucket_index(
    ts: datetime, date_from: datetime, bucket_width: timedelta, num_buckets: int
) -> int:
    idx = int((ts - date_from) / bucket_width)
    return max(0, min(idx, num_buckets - 1))


def _bucket_label(bucket_start: datetime, bucket: str) -> str:
    if bucket == "hour":
        return bucket_start.strftime("%H:00")
    return f"{bucket_start.strftime('%b')} {bucket_start.day}"


def get_analytics_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> AnalyticsService:
    incident_service = UnifiedIncidentService(
        PPEViolationRepository(session),
        ZoneViolationRepository(session),
        BehaviorIncidentRepository(session),
        CameraRepository(session),
        PhysicalZoneRepository(session),
        storage,
    )
    return AnalyticsService(
        incident_service,
        PhysicalZoneRepository(session),
        CameraRepository(session),
        FactoryRepository(session),
    )
