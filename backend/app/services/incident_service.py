"""Unified, normalized read layer over PPE/zone/behavior incidents (see §2 of
INCIDENT_ANALYTICS_IMPLEMENTATION_PLAN.md). Feed, detail, and analytics reads
should all go through UnifiedIncidentService rather than re-deriving
severity/type/zone normalization at each call site.

This module only reads models/repositories — it deliberately does not import
app/services/ppe/ or app/services/zone_service.py (see REFACTOR_NOTES.md's
import boundary rule); those are detection-pipeline internals, not incident
storage.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.db.session import get_session
from app.models.behavior_incident import BehaviorIncident
from app.models.camera import Camera
from app.models.physical_zone import PhysicalZone
from app.models.ppe_violation import PPEViolation
from app.models.zone_violation import ZoneViolation
from app.repositories.behavior_incident_repository import BehaviorIncidentRepository
from app.repositories.camera_repository import CameraRepository
from app.repositories.physical_zone_repository import PhysicalZoneRepository
from app.repositories.ppe_violation_repository import PPEViolationRepository
from app.repositories.zone_violation_repository import ZoneViolationRepository
from app.services import ServiceValidationError
from app.services.incident_normalization import (
    behavior_type_label,
    normalize_behavior_severity,
    normalize_ppe_severity,
    normalize_zone_severity,
    ppe_type_label,
    zone_type_label,
)
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

UNASSIGNED_ZONE_NAME = "Unassigned"


@dataclass(frozen=True)
class UnifiedIncident:
    id: int
    category: str  # "ppe" | "zone" | "behavior"
    type: str
    severity: str  # "Critical" | "High" | "Medium" | "Low"
    timestamp: datetime
    camera_id: int | None
    zone_id: int | None  # resolved via camera.home_zone_id
    zone_name: str  # physical zone name, "Unassigned" when zone_id is None
    camera_label: str
    snapshot_url: str | None


class UnifiedIncidentService:
    def __init__(
        self,
        ppe_repository: Annotated[
            PPEViolationRepository, Depends(PPEViolationRepository)
        ],
        zone_repository: Annotated[
            ZoneViolationRepository, Depends(ZoneViolationRepository)
        ],
        behavior_repository: Annotated[
            BehaviorIncidentRepository, Depends(BehaviorIncidentRepository)
        ],
        camera_repository: Annotated[CameraRepository, Depends(CameraRepository)],
        physical_zone_repository: Annotated[
            PhysicalZoneRepository, Depends(PhysicalZoneRepository)
        ],
        storage: EvidenceStorage | None = None,
    ) -> None:
        self.ppe_repository = ppe_repository
        self.zone_repository = zone_repository
        self.behavior_repository = behavior_repository
        self.camera_repository = camera_repository
        self.physical_zone_repository = physical_zone_repository
        self.storage = storage

    def list_incidents(
        self,
        *,
        zone_id: int | None = None,
        category: str | None = None,
        severity: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 100,
    ) -> list[UnifiedIncident]:
        normalized_limit = _validate_limit(limit)
        # Per-call caches: dedup camera/zone lookups across rows sharing the
        # same camera, without persisting stale data across separate calls.
        camera_cache: dict[int, Camera | None] = {}
        zone_cache: dict[int, PhysicalZone | None] = {}

        incidents: list[UnifiedIncident] = []
        if category is None or category == "ppe":
            for violation in self.ppe_repository.list_between(
                date_from=date_from, date_to=date_to, limit=normalized_limit
            ):
                incidents.append(
                    self._ppe_to_unified(violation, camera_cache, zone_cache)
                )
        if category is None or category == "zone":
            for violation in self.zone_repository.list_between(
                date_from=date_from, date_to=date_to, limit=normalized_limit
            ):
                incidents.append(
                    self._zone_to_unified(violation, camera_cache, zone_cache)
                )
        if category is None or category == "behavior":
            for incident in self.behavior_repository.list_between(
                date_from=date_from, date_to=date_to, limit=normalized_limit
            ):
                incidents.append(
                    self._behavior_to_unified(incident, camera_cache, zone_cache)
                )

        if zone_id is not None:
            incidents = [i for i in incidents if i.zone_id == zone_id]
        if severity is not None:
            incidents = [i for i in incidents if i.severity == severity]

        incidents.sort(key=lambda i: i.timestamp, reverse=True)
        return incidents[:normalized_limit]

    def _ppe_to_unified(
        self,
        violation: PPEViolation,
        camera_cache: dict[int, Camera | None],
        zone_cache: dict[int, PhysicalZone | None],
    ) -> UnifiedIncident:
        camera = self._camera_for(violation.camera_id, camera_cache)
        zone = self._zone_for(camera, zone_cache)
        return UnifiedIncident(
            id=_require_id(violation.id),
            category="ppe",
            type=ppe_type_label(violation.violation_type),
            severity=normalize_ppe_severity(violation.violation_type),
            timestamp=_ensure_tz(violation.occurred_at),
            camera_id=violation.camera_id,
            zone_id=zone.id if zone else None,
            zone_name=zone.name if zone else UNASSIGNED_ZONE_NAME,
            camera_label=_camera_label(camera, violation.source_key),
            snapshot_url=self._local_snapshot_url(violation.snapshot_path),
        )

    def _zone_to_unified(
        self,
        violation: ZoneViolation,
        camera_cache: dict[int, Camera | None],
        zone_cache: dict[int, PhysicalZone | None],
    ) -> UnifiedIncident:
        camera = self._camera_for(violation.camera_id, camera_cache)
        zone = self._zone_for(camera, zone_cache)
        return UnifiedIncident(
            id=_require_id(violation.id),
            category="zone",
            type=zone_type_label(violation.zone_type),
            severity=normalize_zone_severity(violation.severity),
            timestamp=_ensure_tz(violation.occurred_at),
            camera_id=violation.camera_id,
            zone_id=zone.id if zone else None,
            zone_name=zone.name if zone else UNASSIGNED_ZONE_NAME,
            camera_label=_camera_label(camera, violation.source_key),
            snapshot_url=self._local_snapshot_url(violation.snapshot_path),
        )

    def _behavior_to_unified(
        self,
        incident: BehaviorIncident,
        camera_cache: dict[int, Camera | None],
        zone_cache: dict[int, PhysicalZone | None],
    ) -> UnifiedIncident:
        camera = self._camera_for(incident.camera_id, camera_cache)
        zone = self._zone_for(camera, zone_cache)
        incident_id = _require_id(incident.id)
        newest_evidence = self.behavior_repository.get_evidence(incident_id)
        object_key = newest_evidence[0].object_key if newest_evidence else None
        return UnifiedIncident(
            id=incident_id,
            category="behavior",
            type=behavior_type_label(incident.behavior_type),
            severity=normalize_behavior_severity(incident.severity),
            timestamp=_ensure_tz(incident.started_at),
            camera_id=incident.camera_id,
            zone_id=zone.id if zone else None,
            zone_name=zone.name if zone else UNASSIGNED_ZONE_NAME,
            camera_label=_camera_label(camera, incident.source_key),
            snapshot_url=self._behavior_snapshot_url(object_key),
        )

    def _camera_for(
        self,
        camera_id: int | None,
        camera_cache: dict[int, Camera | None],
    ) -> Camera | None:
        if camera_id is None:
            return None
        if camera_id not in camera_cache:
            camera_cache[camera_id] = self.camera_repository.get_by_id(camera_id)
        return camera_cache[camera_id]

    def _zone_for(
        self,
        camera: Camera | None,
        zone_cache: dict[int, PhysicalZone | None],
    ) -> PhysicalZone | None:
        if camera is None or camera.home_zone_id is None:
            return None
        zone_id = camera.home_zone_id
        if zone_id not in zone_cache:
            zone_cache[zone_id] = self.physical_zone_repository.get_by_id(zone_id)
        return zone_cache[zone_id]

    def _local_snapshot_url(self, snapshot_path: str | None) -> str | None:
        """ppe/zone snapshots: MinIO URL when storage is configured, else a
        servable local /snapshots/ path (matches ppe/zone violation services)."""
        if snapshot_path is None:
            return None
        if self.storage is None:
            return _to_local_snapshot_url(snapshot_path)
        return self.storage.get_object_url(snapshot_path)

    def _behavior_snapshot_url(self, object_key: str | None) -> str | None:
        """behavior evidence only ever lives in MinIO; no local-path fallback
        (matches BehaviorIncidentService._snapshot_url)."""
        if object_key is None or self.storage is None:
            return None
        return self.storage.get_object_url(object_key)


def _camera_label(camera: Camera | None, source_key: str | None) -> str:
    if camera is not None:
        return camera.name
    if source_key:
        return source_key
    return "Unknown"


def _ensure_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _to_local_snapshot_url(snapshot_path: str) -> str:
    if snapshot_path.startswith(("http://", "https://", "/")):
        return snapshot_path
    return f"/snapshots/{snapshot_path}"


def _require_id(value: int | None) -> int:
    if value is None:
        raise ServiceValidationError("Persisted incident is missing an ID.")
    return value


def _validate_limit(limit: int) -> int:
    # Upper bound covers both small feed reads (~30-100) and analytics
    # aggregation reads (up to a few thousand rows per category per range).
    # This service aggregates in Python rather than in SQL (see module
    # docstring), so it isn't built for unbounded scale past this cap.
    if not 1 <= limit <= 5000:
        raise ServiceValidationError("limit must be between 1 and 5000.")
    return limit


def get_unified_incident_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> UnifiedIncidentService:
    return UnifiedIncidentService(
        PPEViolationRepository(session),
        ZoneViolationRepository(session),
        BehaviorIncidentRepository(session),
        CameraRepository(session),
        PhysicalZoneRepository(session),
        storage,
    )
